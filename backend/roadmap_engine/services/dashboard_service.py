from backend.roadmap_engine.storage import goals_repo, roadmap_repo, students_repo
from backend.roadmap_engine.utils import utc_today


def _assert_student(student_id: int) -> dict:
    student = students_repo.get_student(student_id)
    if student is None:
        raise ValueError("Student not found.")
    return student


def _active_goal_and_plan(student_id: int) -> tuple[dict, dict]:
    goal = goals_repo.get_active_goal(student_id)
    if goal is None:
        raise ValueError("No active goal found for this student.")

    plan = roadmap_repo.get_active_plan(goal["id"])
    if plan is None:
        raise ValueError("No active roadmap plan found.")
    return goal, plan


def _task_progress(tasks: list[dict]) -> dict:
    if not tasks:
        return {
            "completed_tasks": 0,
            "total_tasks": 0,
            "completion_percent": 0.0,
        }

    total_tasks = len(tasks)
    completed_tasks = sum(1 for task in tasks if task["is_completed"] == 1)
    completion_percent = (completed_tasks / total_tasks) * 100

    return {
        "completed_tasks": completed_tasks,
        "total_tasks": total_tasks,
        "completion_percent": completion_percent,
    }


def _active_skill(goal_skills: list[dict]) -> dict | None:
    pending = [item for item in goal_skills if item["status"] != "completed"]
    return pending[0] if pending else None


def get_dashboard(student_id: int) -> dict:
    student = _assert_student(student_id)
    goal, plan = _active_goal_and_plan(student_id)
    profile_skills = students_repo.list_student_skills(student_id)

    from backend.roadmap_engine.services import roadmap_adjustment_service

    replan_info = roadmap_adjustment_service.auto_replan_if_behind(student_id)
    if replan_info.get("applied"):
        plan = roadmap_repo.get_active_plan(goal["id"]) or plan

    today = utc_today()
    all_tasks = roadmap_repo.list_tasks(plan["id"])
    all_window_tasks = roadmap_repo.list_tasks(plan["id"], today.isoformat(), None)
    goal_skills = goals_repo.list_goal_skills(goal["id"])
    active_skill = _active_skill(goal_skills)

    # lazy imports to avoid circular references
    from backend.roadmap_engine.services import chatbot_service, matching_service, youtube_learning_service

    matches = matching_service.refresh_opportunity_matches(student_id)
    forecast_7_days = matching_service.forecast_eligible_in_days(student_id, days=7)
    notifications = matching_service.list_notifications(student_id)

    selected_playlist = None
    recommendations = []
    playlist_recommendation_error = ""
    ready_for_test_ids: set[int] = set()

    if active_skill:
        recommendations, playlist_recommendation_error = youtube_learning_service.get_or_create_recommendations(
            goal_id=goal["id"],
            goal_skill_id=active_skill["id"],
            skill_name=active_skill["skill_name"],
        )
        selected_playlist = youtube_learning_service.get_selected_playlist(
            goal_id=goal["id"],
            goal_skill_id=active_skill["id"],
        )
        active_tasks = roadmap_repo.list_tasks_for_skill(plan["id"], active_skill["id"])
        if selected_playlist and active_tasks and all(task["is_completed"] == 1 for task in active_tasks):
            ready_for_test_ids.add(active_skill["id"])

    chatbot_panel = chatbot_service.get_chat_panel(student_id)

    today_tasks = [
        task for task in all_window_tasks
        if task["task_date"] == today.isoformat() and (active_skill is None or task["goal_skill_id"] == active_skill["id"])
    ]
    upcoming_tasks = [
        task for task in all_window_tasks
        if active_skill is None or task["goal_skill_id"] == active_skill["id"]
    ]

    return {
        "student": student,
        "goal": goal,
        "plan": plan,
        "today": today.isoformat(),
        "known_skills": [item["skill_name"] for item in profile_skills],
        "required_skills": goal.get("requirements", {}).get("required_skills", []),
        "goal_skills": [
            {
                **skill,
                "ready_for_test": skill["id"] in ready_for_test_ids,
                "is_active": bool(active_skill and skill["id"] == active_skill["id"]),
                "is_locked": bool(active_skill and skill["id"] != active_skill["id"] and skill["status"] != "completed"),
            }
            for skill in goal_skills
        ],
        "replan_info": replan_info,
        "progress": _task_progress(all_tasks),
        "today_tasks": today_tasks,
        "upcoming_tasks": upcoming_tasks,
        "opportunities": matches,
        "opportunity_forecast_7_days": forecast_7_days,
        "notifications": notifications,
        "active_skill": active_skill,
        "active_skill_recommendations": recommendations,
        "playlist_recommendation_error": playlist_recommendation_error,
        "selected_playlist": selected_playlist,
        "chatbot": chatbot_panel,
    }


def set_task_completion(student_id: int, task_id: int, completed: bool) -> None:
    _assert_student(student_id)
    goal, plan = _active_goal_and_plan(student_id)

    goal_skills = goals_repo.list_goal_skills(goal["id"])
    active_skill = _active_skill(goal_skills)
    if active_skill is None:
        raise ValueError("All skills are already completed.")

    from backend.roadmap_engine.services import youtube_learning_service

    selected_playlist = youtube_learning_service.get_selected_playlist(goal["id"], active_skill["id"])
    if selected_playlist is None:
        raise ValueError(
            f"Select one of the top 3 playlists for {active_skill['skill_name']} before marking tasks."
        )

    task = roadmap_repo.get_task(task_id)
    if task is None or task["plan_id"] != plan["id"]:
        raise ValueError("Task not found for this student.")
    if task.get("goal_skill_id") != active_skill["id"]:
        raise ValueError(
            f"Only {active_skill['skill_name']} tasks are unlocked right now. Complete this skill first."
        )

    roadmap_repo.set_task_completed(task_id, completed)

    if task["goal_skill_id"]:
        skill_tasks = roadmap_repo.list_tasks_for_skill(plan["id"], task["goal_skill_id"])
        if skill_tasks and all(item["is_completed"] == 1 for item in skill_tasks):
            goals_repo.set_goal_skill_status(task["goal_skill_id"], "in_progress", None)

    from backend.roadmap_engine.services import matching_service

    matching_service.refresh_opportunity_matches(student_id)

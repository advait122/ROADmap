import json
import os
from collections import defaultdict
from datetime import timedelta

from backend.roadmap_engine.constants import PASS_PERCENT_FOR_SKILL_TEST
from backend.roadmap_engine.services.skill_normalizer import normalize_skill
from backend.roadmap_engine.storage import (
    assessment_repo,
    goals_repo,
    matching_repo,
    roadmap_repo,
    students_repo,
)
from backend.roadmap_engine.utils import utc_now_iso, utc_today


GROQ_MODEL = "llama-3.1-8b-instant"


def _extract_json(raw_text: str) -> dict | None:
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _fallback_questions(skill_name: str) -> tuple[list[dict], list[int]]:
    questions = [
        {
            "topic": "Purpose",
            "question": f"Which statement best describes the purpose of {skill_name}?",
            "options": [
                f"It is used to solve practical software problems using {skill_name}.",
                f"It is only useful for hardware design.",
                f"It cannot be applied in internships/jobs.",
                "It is unrelated to programming.",
            ],
        },
        {
            "topic": "Practice Strategy",
            "question": f"What is a good way to improve in {skill_name}?",
            "options": [
                "Practice with projects and problems consistently.",
                "Avoid practice and read theory only once.",
                "Skip revision and testing.",
                "Use random tools without understanding basics.",
            ],
        },
        {
            "topic": "Fundamentals",
            "question": f"When learning {skill_name}, what should come first?",
            "options": [
                "Core concepts and fundamentals.",
                "Only advanced edge cases.",
                "Memorizing interview answers without understanding.",
                "Ignoring syntax and logic.",
            ],
        },
        {
            "topic": "Application",
            "question": f"How do you know your {skill_name} learning is effective?",
            "options": [
                "You can explain concepts and apply them in tasks.",
                "You only watched videos without coding.",
                "You skipped all exercises.",
                "You rely only on copied solutions.",
            ],
        },
        {
            "topic": "Recovery",
            "question": f"What is the best behavior after failing a {skill_name} test?",
            "options": [
                "Review weak topics, practice again, and retest.",
                "Stop learning the skill.",
                "Ignore mistakes completely.",
                "Jump to unrelated topics immediately.",
            ],
        },
    ]
    answer_key = [0, 0, 0, 0, 0]
    return questions, answer_key


def _context_aware_fallback_questions(skill_name: str, selected_playlist: dict) -> tuple[list[dict], list[int]]:
    summary = selected_playlist.get("summary", {}) or {}
    playlist_title = str(selected_playlist.get("title", "")).strip()
    channel_title = str(selected_playlist.get("channel_title", "")).strip()
    topic_overview = str(summary.get("topic_overview", "")).strip()
    learning_experience = str(summary.get("learning_experience", "")).strip()
    topics_covered = str(summary.get("topics_covered_summary", "")).strip()
    channel_url = str(summary.get("channel_url", "")).strip()
    playlist_url = str(selected_playlist.get("playlist_url", "")).strip()

    questions = [
        {
            "topic": "Topic Overview",
            "question": (
                f"Based on playlist '{playlist_title or skill_name}' for {skill_name}, "
                "what is the core focus area?"
            ),
            "options": [
                topic_overview[:120] + ("..." if len(topic_overview) > 120 else "")
                if topic_overview
                else f"Building practical understanding of {skill_name}",
                "Unrelated hardware-only workflows",
                "Random topics unrelated to career goals",
                "Non-technical communication-only training",
            ],
        },
        {
            "topic": "Learning Experience",
            "question": (
                f"What learning flow should the student expect from channel "
                f"'{channel_title or 'selected channel'}'?"
            ),
            "options": [
                learning_experience[:120] + ("..." if len(learning_experience) > 120 else "")
                if learning_experience
                else "Concept-to-practice progression with structured learning",
                "No progression or sequence at all",
                "Only motivational content with no technical depth",
                "Only one-shot advanced topics with no basics",
            ],
        },
        {
            "topic": "Source Link",
            "question": "Which link corresponds to the selected channel source?",
            "options": [
                channel_url if channel_url else f"Channel: {selected_playlist.get('channel_title', '')}",
                "https://example.com/random-resource",
                "https://docs.python.org/",
                "https://github.com/",
            ],
        },
        {
            "topic": "Topics Covered",
            "question": "Which option best matches the concepts covered summary?",
            "options": [
                topics_covered[:120] + ("..." if len(topics_covered) > 120 else "")
                if topics_covered
                else f"Key concepts and practical applications of {skill_name}",
                "Only interview HR behavior questions",
                "Only non-programming tool tutorials",
                "No conceptual coverage mentioned",
            ],
        },
        {
            "topic": "Practical Application",
            "question": f"After this playlist ({playlist_url if playlist_url else 'selected URL'}), how should {skill_name} be validated?",
            "options": [
                "Solve practice tasks and explain concepts in your own words.",
                "Skip practice and rely on passive watching.",
                "Ignore weak topics and move on immediately.",
                "Avoid connecting the skill to your roadmap tasks.",
            ],
        },
    ]
    answer_key = [0, 0, 0, 0, 0]
    return questions, answer_key


def _llm_questions(skill_name: str, selected_playlist: dict) -> tuple[list[dict], list[int]] | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        summary = selected_playlist.get("summary", {}) or {}
        channel_url = summary.get("channel_url", "")
        prompt = (
            f"Generate 5 MCQ questions for checking understanding of {skill_name} "
            "strictly using the selected playlist context. "
            "Return strict JSON with keys: questions (array). "
            "Each question item must have: topic, question, options (exactly 4 strings), correct_option_index (0-3). "
            "topic should be a short phrase.\n\n"
            f"Playlist title: {selected_playlist.get('title', '')}\n"
            f"Channel: {selected_playlist.get('channel_title', '')}\n"
            f"Channel URL: {channel_url}\n"
            f"Playlist URL: {selected_playlist.get('playlist_url', '')}\n"
            f"Topic overview: {summary.get('topic_overview', '')}\n"
            f"Learning experience: {summary.get('learning_experience', '')}\n"
            f"Topics covered summary: {summary.get('topics_covered_summary', '')}\n"
        )
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": "You generate practical MCQ tests in strict JSON.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        parsed = _extract_json(response.choices[0].message.content or "")
        if not parsed:
            return None

        raw_questions = parsed.get("questions", [])
        if not isinstance(raw_questions, list) or len(raw_questions) == 0:
            return None

        questions = []
        answer_key = []
        for item in raw_questions[:5]:
            topic = str(item.get("topic", "")).strip() or "General"
            question = str(item.get("question", "")).strip()
            options = item.get("options", [])
            answer = item.get("correct_option_index")
            if not question or not isinstance(options, list) or len(options) != 4:
                continue
            try:
                answer_int = int(answer)
            except (TypeError, ValueError):
                continue
            if answer_int < 0 or answer_int > 3:
                continue
            questions.append(
                {
                    "topic": topic,
                    "question": question,
                    "options": [str(opt) for opt in options],
                }
            )
            answer_key.append(answer_int)

        if len(questions) < 3:
            return None

        return questions, answer_key
    except Exception:
        return None


def _skill_is_ready_for_test(goal_id: int, goal_skill_id: int) -> bool:
    plan = roadmap_repo.get_active_plan(goal_id)
    if not plan:
        return False

    tasks = roadmap_repo.list_tasks_for_skill(plan["id"], goal_skill_id)
    if not tasks:
        return False

    return all(task["is_completed"] == 1 for task in tasks)


def _topic_breakdown(questions: list[dict], answer_key: list[int], answers: list[int]) -> dict[str, dict]:
    summary: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})
    for idx, expected in enumerate(answer_key):
        topic = str(questions[idx].get("topic", "General")) if idx < len(questions) else "General"
        if not topic.strip():
            topic = "General"
        summary[topic]["total"] += 1
        if idx < len(answers) and answers[idx] == expected:
            summary[topic]["correct"] += 1
    return dict(summary)


def _weak_and_strong_topics(topic_stats: dict[str, dict]) -> tuple[list[str], list[str]]:
    weak: list[str] = []
    strong: list[str] = []
    for topic, values in topic_stats.items():
        total = max(int(values["total"]), 1)
        ratio = values["correct"] / total
        if ratio < 0.5:
            weak.append(topic)
        elif ratio >= 0.8:
            strong.append(topic)
    return weak, strong


def _build_feedback(score_percent: float, passed: bool, weak_topics: list[str], strong_topics: list[str]) -> str:
    weak_text = ", ".join(weak_topics) if weak_topics else "None"
    strong_text = ", ".join(strong_topics) if strong_topics else "None"
    status = "Passed" if passed else "Failed"
    action = "Move to next skill." if passed else "Revision tasks added. Complete them and retake."
    return (
        f"Score: {score_percent:.1f}% ({status}). "
        f"Weak topics: {weak_text}. "
        f"Strong topics: {strong_text}. "
        f"{action}"
    )


def _insert_revision_tasks(goal_id: int, goal_skill: dict, weak_topics: list[str]) -> int:
    if not weak_topics:
        weak_topics = ["Core Concepts"]

    plan = roadmap_repo.get_active_plan(goal_id)
    if not plan:
        return 0

    today = utc_today()
    existing = roadmap_repo.list_tasks(plan["id"], today.isoformat(), None)
    existing_titles = {
        task["title"]
        for task in existing
        if task.get("goal_skill_id") == goal_skill["id"] and task["is_completed"] == 0
    }

    tasks_to_add = []
    for idx, topic in enumerate(weak_topics[:3]):
        title = f"Revision: {goal_skill['skill_name']} - {topic}"
        if title in existing_titles:
            continue
        task_date = (today + timedelta(days=idx + 1)).isoformat()
        tasks_to_add.append(
            {
                "goal_skill_id": goal_skill["id"],
                "task_date": task_date,
                "title": title,
                "description": (
                    f"Revise topic '{topic}' for {goal_skill['skill_name']}, "
                    "practice questions, then retake the skill test."
                ),
                "target_minutes": 45,
            }
        )

    roadmap_repo.append_tasks(plan["id"], tasks_to_add)
    return len(tasks_to_add)


def generate_assessment(student_id: int, goal_skill_id: int) -> dict:
    goal = goals_repo.get_active_goal(student_id)
    if goal is None:
        raise ValueError("Active goal not found.")

    goal_skill = goals_repo.get_goal_skill(goal_skill_id)
    if goal_skill is None or goal_skill["goal_id"] != goal["id"]:
        raise ValueError("Skill not found for current goal.")

    goal_skills = goals_repo.list_goal_skills(goal["id"])
    active_skill = next((item for item in goal_skills if item["status"] != "completed"), None)
    if active_skill is None:
        raise ValueError("All skills are already completed.")
    if active_skill["id"] != goal_skill_id:
        raise ValueError(
            f"Complete and pass {active_skill['skill_name']} before unlocking this skill test."
        )

    from backend.roadmap_engine.services import youtube_learning_service

    selected_playlist = youtube_learning_service.get_selected_playlist(goal["id"], goal_skill_id)
    if selected_playlist is None:
        raise ValueError(
            f"Select one of the top 3 playlists for {goal_skill['skill_name']} before taking the test."
        )

    if goal_skill["status"] == "completed":
        raise ValueError("Skill already completed.")

    if not _skill_is_ready_for_test(goal["id"], goal_skill_id):
        raise ValueError("Complete all roadmap tasks for this skill before taking the test.")

    latest = assessment_repo.get_latest_assessment(goal_skill_id)
    if latest and latest.get("passed") in (0, 1):
        # Allow retake only if failed, otherwise no-op.
        if latest["passed"] == 1:
            return latest

    generated = _llm_questions(goal_skill["skill_name"], selected_playlist)
    if generated:
        questions, answer_key = generated
    else:
        questions, answer_key = _context_aware_fallback_questions(
            goal_skill["skill_name"],
            selected_playlist,
        )
    assessment_id = assessment_repo.create_assessment(
        goal_id=goal["id"],
        goal_skill_id=goal_skill_id,
        questions=questions,
        answer_key=answer_key,
    )
    assessment = assessment_repo.get_assessment(assessment_id)
    if assessment is None:
        raise ValueError("Failed to create assessment.")
    return assessment


def submit_assessment(student_id: int, assessment_id: int, answers: list[int]) -> dict:
    goal = goals_repo.get_active_goal(student_id)
    if goal is None:
        raise ValueError("Active goal not found.")

    assessment = assessment_repo.get_assessment(assessment_id)
    if assessment is None:
        raise ValueError("Assessment not found.")
    if assessment["goal_id"] != goal["id"]:
        raise ValueError("Assessment does not belong to your current goal.")
    if assessment.get("submitted_at"):
        return assessment

    answer_key = assessment["answer_key"]
    if len(answers) != len(answer_key):
        raise ValueError("Please answer all questions.")

    total = len(answer_key)
    correct = sum(1 for idx, answer in enumerate(answers) if answer == answer_key[idx])
    score_percent = (correct / total) * 100 if total else 0.0
    passed = score_percent >= PASS_PERCENT_FOR_SKILL_TEST
    topic_stats = _topic_breakdown(assessment["questions"], answer_key, answers)
    weak_topics, strong_topics = _weak_and_strong_topics(topic_stats)

    feedback = _build_feedback(score_percent, passed, weak_topics, strong_topics)
    assessment_repo.submit_assessment(
        assessment_id=assessment_id,
        student_answers=answers,
        score_percent=score_percent,
        passed=passed,
        feedback_text=feedback,
    )

    if passed:
        goal_skill = goals_repo.get_goal_skill(assessment["goal_skill_id"])
        if goal_skill:
            completed_at = utc_now_iso()
            goals_repo.set_goal_skill_status(goal_skill["id"], "completed", completed_at)
            students_repo.add_student_skill(
                student_id=student_id,
                skill_name=goal_skill["skill_name"],
                normalized_skill=normalize_skill(goal_skill["skill_name"]),
                skill_source="roadmap_mastered",
            )
            matching_repo.create_notification(
                student_id=student_id,
                goal_id=goal["id"],
                notification_type="skill_test_passed",
                title="Skill Test Passed",
                body=(
                    f"You passed {goal_skill['skill_name']} ({score_percent:.1f}%). "
                    "Skill marked as completed."
                ),
            )
    else:
        goals_repo.set_goal_skill_status(assessment["goal_skill_id"], "in_progress", None)
        goal_skill = goals_repo.get_goal_skill(assessment["goal_skill_id"])
        added = 0
        if goal_skill:
            added = _insert_revision_tasks(goal["id"], goal_skill, weak_topics)
        matching_repo.create_notification(
            student_id=student_id,
            goal_id=goal["id"],
            notification_type="skill_test_failed",
            title="Skill Test Failed",
            body=(
                f"You scored {score_percent:.1f}%. Weak topics: "
                f"{', '.join(weak_topics) if weak_topics else 'General'}. "
                f"{added} revision task(s) added."
            ),
        )

    updated = assessment_repo.get_assessment(assessment_id)
    if updated is None:
        raise ValueError("Failed to load updated assessment.")
    return updated

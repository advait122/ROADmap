from pathlib import Path
import time
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.roadmap_engine.constants import (
    BRANCH_OPTIONS,
    DEFAULT_WEEKLY_STUDY_HOURS,
    PREDEFINED_SKILLS,
    TIMELINE_MONTH_OPTIONS,
    YEAR_OPTIONS,
)
from backend.roadmap_engine.services import (
    assessment_service,
    chatbot_service,
    dashboard_service,
    matching_service,
    onboarding_service,
)
from backend.roadmap_engine.storage import students_repo


router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ALLOWED_DASHBOARD_SECTIONS = {
    "roadmap",
    "tasks",
    "tests",
    "doubtbot",
    "opportunities",
}


def _asset_version() -> str:
    # Force fresh CSS fetch on each request across devices/browsers.
    return str(int(time.time()))


def _normalize_dashboard_section(section: str, default: str = "roadmap") -> str:
    normalized = (section or "").lower().strip()
    if normalized in ALLOWED_DASHBOARD_SECTIONS:
        return normalized
    return default


def _student_or_404(student_id: int) -> dict:
    student = students_repo.get_student(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")
    return student


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> RedirectResponse:
    students = students_repo.list_students()
    if students:
        return RedirectResponse(url=f"/students/{students[0]['id']}/dashboard", status_code=303)
    return RedirectResponse(url="/onboarding", status_code=303)


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request, error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        "onboarding.html",
        {
            "request": request,
            "asset_version": _asset_version(),
            "error": error,
            "branch_options": BRANCH_OPTIONS,
            "year_options": YEAR_OPTIONS,
            "timeline_options": TIMELINE_MONTH_OPTIONS,
            "predefined_skills": PREDEFINED_SKILLS,
            "default_weekly_hours": DEFAULT_WEEKLY_STUDY_HOURS,
        },
    )


@router.post("/onboarding")
def onboarding_submit(
    name: str = Form(...),
    branch: str = Form(...),
    current_year: int = Form(...),
    weekly_study_hours: int = Form(DEFAULT_WEEKLY_STUDY_HOURS),
    selected_skills: list[str] = Form(default=[]),
    custom_skills: str = Form(default=""),
    goal_text: str = Form(...),
    target_duration_months: int = Form(...),
) -> RedirectResponse:
    try:
        result = onboarding_service.create_student_goal_plan(
            name=name,
            branch=branch,
            current_year=current_year,
            weekly_study_hours=weekly_study_hours,
            selected_skills=selected_skills,
            custom_skills_text=custom_skills,
            goal_text=goal_text,
            target_duration_months=target_duration_months,
        )
        student_id = result["student"]["id"]
        matching_service.refresh_opportunity_matches(student_id)
    except ValueError as error:
        escaped = quote_plus(str(error))
        return RedirectResponse(f"/onboarding?error={escaped}", status_code=303)

    return RedirectResponse(url=f"/students/{student_id}/dashboard", status_code=303)


@router.post("/students/{student_id}/roadmap/replan")
def manual_replan(student_id: int) -> RedirectResponse:
    _student_or_404(student_id)
    try:
        from backend.roadmap_engine.services import roadmap_adjustment_service

        result = roadmap_adjustment_service.auto_replan_if_behind(student_id)
        if result.get("applied"):
            msg = quote_plus(
                f"Roadmap replanned. {result['updated_task_count']} task(s) rescheduled."
            )
        else:
            msg = quote_plus("No replan needed right now.")
    except ValueError as error:
        msg = quote_plus(str(error))

    return RedirectResponse(
        url=f"/students/{student_id}/dashboard?error={msg}",
        status_code=303,
    )


@router.get("/students/{student_id}/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    student_id: int,
    error: str = "",
    section: str = "roadmap",
) -> HTMLResponse:
    student = _student_or_404(student_id)
    active_section = _normalize_dashboard_section(section, "roadmap")

    try:
        dashboard = dashboard_service.get_dashboard(student_id)
    except ValueError as exc:
        escaped = quote_plus(str(exc))
        return RedirectResponse(url=f"/onboarding?error={escaped}", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "asset_version": _asset_version(),
            "student": student,
            "dashboard": dashboard,
            "error": error,
            "active_section": active_section,
        },
    )


@router.post("/students/{student_id}/tasks/{task_id}/completion")
def update_task_completion(
    student_id: int,
    task_id: int,
    is_completed: int = Form(...),
    section: str = "tasks",
) -> RedirectResponse:
    _student_or_404(student_id)
    active_section = _normalize_dashboard_section(section, "tasks")
    try:
        dashboard_service.set_task_completion(student_id, task_id, completed=bool(is_completed))
    except ValueError as error:
        escaped = quote_plus(str(error))
        return RedirectResponse(
            url=f"/students/{student_id}/dashboard?section={active_section}&error={escaped}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/students/{student_id}/dashboard?section={active_section}",
        status_code=303,
    )


@router.post("/students/{student_id}/skills/{goal_skill_id}/playlist/select")
def select_playlist(
    student_id: int,
    goal_skill_id: int,
    recommendation_id: str = Form(default=""),
    section: str = "tasks",
) -> RedirectResponse:
    _student_or_404(student_id)
    active_section = _normalize_dashboard_section(section, "tasks")
    try:
        from backend.roadmap_engine.storage import goals_repo
        from backend.roadmap_engine.services import youtube_learning_service

        goal = goals_repo.get_active_goal(student_id)
        if goal is None:
            raise ValueError("No active goal found.")

        goal_skill = goals_repo.get_goal_skill(goal_skill_id)
        if goal_skill is None or goal_skill["goal_id"] != goal["id"]:
            raise ValueError("Skill not found for active goal.")

        goal_skills = goals_repo.list_goal_skills(goal["id"])
        active_skill = next((item for item in goal_skills if item["status"] != "completed"), None)
        if active_skill is None:
            raise ValueError("All skills are already completed.")
        if active_skill["id"] != goal_skill_id:
            raise ValueError(
                f"Playlist selection is currently open for {active_skill['skill_name']} only."
            )
        recommendation_id_clean = recommendation_id.strip()
        if not recommendation_id_clean:
            raise ValueError("Playlist option is missing. Refresh the dashboard and try selecting again.")
        try:
            recommendation_id_int = int(recommendation_id_clean)
        except ValueError as error:
            raise ValueError("Invalid playlist option. Refresh the dashboard and try again.") from error

        youtube_learning_service.select_playlist(
            goal["id"],
            goal_skill_id,
            recommendation_id_int,
            goal_skill["skill_name"],
        )
    except ValueError as error:
        escaped = quote_plus(str(error))
        return RedirectResponse(
            url=f"/students/{student_id}/dashboard?section={active_section}&error={escaped}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/students/{student_id}/dashboard?section={active_section}",
        status_code=303,
    )


@router.post("/students/{student_id}/chat/send")
def chatbot_send(
    student_id: int,
    question: str = Form(...),
    section: str = "doubtbot",
) -> RedirectResponse:
    _student_or_404(student_id)
    active_section = _normalize_dashboard_section(section, "doubtbot")
    try:
        chatbot_service.ask_question(student_id, question)
    except ValueError as error:
        escaped = quote_plus(str(error))
        return RedirectResponse(
            url=f"/students/{student_id}/dashboard?section={active_section}&error={escaped}#chatbot",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/students/{student_id}/dashboard?section={active_section}#chatbot",
        status_code=303,
    )


@router.get("/students/{student_id}/skills/{goal_skill_id}/test", response_class=HTMLResponse)
def skill_test_page(request: Request, student_id: int, goal_skill_id: int) -> HTMLResponse:
    student = _student_or_404(student_id)
    try:
        assessment = assessment_service.generate_assessment(student_id, goal_skill_id)
    except ValueError as error:
        escaped = quote_plus(str(error))
        return RedirectResponse(
            url=f"/students/{student_id}/dashboard?section=tests&error={escaped}",
            status_code=303,
        )

    return templates.TemplateResponse(
        "skill_test.html",
        {
            "request": request,
            "asset_version": _asset_version(),
            "student": student,
            "assessment": assessment,
            "active_section": "tests",
        },
    )


@router.post("/students/{student_id}/skills/tests/{assessment_id}/submit")
async def skill_test_submit(
    request: Request,
    student_id: int,
    assessment_id: int,
) -> RedirectResponse:
    _student_or_404(student_id)
    payload = await request.form()

    selected_answers: list[int] = []
    idx = 0
    while True:
        key = f"answer_{idx}"
        if key not in payload:
            break
        selected_answers.append(int(payload[key]))
        idx += 1

    try:
        assessment_service.submit_assessment(student_id, assessment_id, selected_answers)
        matching_service.refresh_opportunity_matches(student_id)
    except ValueError as error:
        escaped = quote_plus(str(error))
        return RedirectResponse(
            url=f"/students/{student_id}/dashboard?section=tests&error={escaped}",
            status_code=303,
        )

    return RedirectResponse(url=f"/students/{student_id}/dashboard?section=tests", status_code=303)

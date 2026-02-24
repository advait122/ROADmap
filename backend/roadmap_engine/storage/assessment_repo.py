import json

from backend.roadmap_engine.storage.database import get_connection, transaction
from backend.roadmap_engine.utils import utc_now_iso


def get_attempt_count(goal_skill_id: int) -> int:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM skill_assessments
            WHERE goal_skill_id = ?
            """,
            (goal_skill_id,),
        ).fetchone()
    finally:
        connection.close()

    return int(row["total"]) if row else 0


def create_assessment(
    *,
    goal_id: int,
    goal_skill_id: int,
    questions: list[dict],
    answer_key: list[int],
) -> int:
    now = utc_now_iso()
    attempt_no = get_attempt_count(goal_skill_id) + 1

    with transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO skill_assessments (
                goal_id,
                goal_skill_id,
                attempt_no,
                questions_json,
                answer_key_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                goal_id,
                goal_skill_id,
                attempt_no,
                json.dumps(questions, ensure_ascii=False),
                json.dumps(answer_key),
                now,
            ),
        )
        return int(cursor.lastrowid)


def get_assessment(assessment_id: int) -> dict | None:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT
                id,
                goal_id,
                goal_skill_id,
                attempt_no,
                questions_json,
                answer_key_json,
                student_answers_json,
                score_percent,
                passed,
                feedback_text,
                created_at,
                submitted_at
            FROM skill_assessments
            WHERE id = ?
            """,
            (assessment_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    assessment = dict(row)
    assessment["questions"] = json.loads(assessment["questions_json"])
    assessment["answer_key"] = json.loads(assessment["answer_key_json"])
    assessment["student_answers"] = (
        json.loads(assessment["student_answers_json"]) if assessment["student_answers_json"] else []
    )
    return assessment


def get_latest_assessment(goal_skill_id: int) -> dict | None:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT
                id,
                goal_id,
                goal_skill_id,
                attempt_no,
                questions_json,
                answer_key_json,
                student_answers_json,
                score_percent,
                passed,
                feedback_text,
                created_at,
                submitted_at
            FROM skill_assessments
            WHERE goal_skill_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (goal_skill_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    assessment = dict(row)
    assessment["questions"] = json.loads(assessment["questions_json"])
    assessment["answer_key"] = json.loads(assessment["answer_key_json"])
    assessment["student_answers"] = (
        json.loads(assessment["student_answers_json"]) if assessment["student_answers_json"] else []
    )
    return assessment


def submit_assessment(
    *,
    assessment_id: int,
    student_answers: list[int],
    score_percent: float,
    passed: bool,
    feedback_text: str,
) -> None:
    now = utc_now_iso()
    with transaction() as connection:
        connection.execute(
            """
            UPDATE skill_assessments
            SET
                student_answers_json = ?,
                score_percent = ?,
                passed = ?,
                feedback_text = ?,
                submitted_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(student_answers),
                score_percent,
                1 if passed else 0,
                feedback_text,
                now,
                assessment_id,
            ),
        )

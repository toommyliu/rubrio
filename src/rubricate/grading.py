import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Literal

from rubricate.errors import NeedsConfirmation, NotFound


@dataclass(frozen=True)
class RubricItem:
    id: int
    description: str
    points: float
    position: int
    uses: int
    whole_answer: bool


@dataclass(frozen=True)
class QuestionInfo:
    id: int
    version: str
    number: str
    prompt: str
    points: float
    bonus: bool
    kind: str
    key: list[str]
    parent: int | None
    scoring: Literal["negative", "positive"]
    graded: int
    total: int
    rubric: list[RubricItem]


@dataclass(frozen=True)
class Grade:
    submission: int
    question: int
    applied: list[int]
    adjustment: float
    comment: str
    revision: int
    score: float
    extra_credit: bool
    updated_by: str
    updated_at: str


def _question(db: sqlite3.Connection, question_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM question WHERE id=?", (question_id,)).fetchone()
    if row is None:
        raise NotFound("Question not found.")
    return row


def _whole_answer(points: float, scoring: Literal["negative", "positive"], question_points: float) -> bool:
    return points == 0 or points == (question_points if scoring == "positive" else -question_points)


def _rubric(db: sqlite3.Connection, question_id: int) -> list[RubricItem]:
    rows = db.execute(
        """
                SELECT r.id, r.description, r.points, r.position, count(a.submission) AS uses,
                    q.points AS question_points
                FROM rubric_item r
                JOIN question q ON q.id=r.question
                LEFT JOIN applied_item a ON a.rubric_item=r.id
                WHERE r.question=? AND NOT r.deleted
                GROUP BY r.id
                ORDER BY r.position, r.id
            """,
        (question_id,),
    ).fetchall()
    scoring = "positive" if any(r["points"] > 0 for r in rows) else "negative"
    return [
        RubricItem(
            r["id"],
            r["description"],
            r["points"],
            r["position"],
            r["uses"],
            _whole_answer(r["points"], scoring, r["question_points"]),
        )
        for r in rows
    ]


def _rubric_total(points: float, items: dict[int, float], applied: list[int]) -> float:
    start = 0 if any(p > 0 for p in items.values()) else points
    return start + sum(items.get(i, 0) for i in applied)


def _score(points: float, items: dict[int, float], applied: list[int], adjustment: float) -> float:
    rubric_score = min(points, max(0, _rubric_total(points, items, applied)))
    return max(0, rubric_score + adjustment)


def _event(db: sqlite3.Connection, actor: str, kind: str, before: object, after: object) -> None:
    data = {}
    for key, value in (("before", before), ("after", after)):
        if isinstance(value, Grade):
            data[key] = {
                field: content
                for field, content in asdict(value).items()
                if field not in {"score", "extra_credit"}
            }
        else:
            data[key] = value
    db.execute(
        "INSERT INTO event (actor, kind, data) VALUES (?, ?, ?)",
        (actor, kind, json.dumps(data, ensure_ascii=False)),
    )


def _grade(db: sqlite3.Connection, submission_id: int, question_id: int) -> Grade | None:
    row = db.execute(
        "SELECT * FROM grade WHERE submission=? AND question=?", (submission_id, question_id)
    ).fetchone()
    if row is None:
        return None
    applied = [
        r[0]
        for r in db.execute(
            """
                SELECT rubric_item
                FROM applied_item
                WHERE submission=? AND question=?
                ORDER BY rubric_item
            """,
            (submission_id, question_id),
        )
    ]
    items = {r.id: r.points for r in _rubric(db, question_id)}
    points = _question(db, question_id)["points"]
    score = _score(points, items, applied, row["adjustment"])
    return Grade(
        row["submission"],
        row["question"],
        applied,
        row["adjustment"],
        row["comment"],
        row["revision"],
        score,
        score > points,
        row["updated_by"],
        row["updated_at"],
    )


def _question_grades(db: sqlite3.Connection, question_id: int) -> list[Grade]:
    grades = []
    for row in db.execute("SELECT submission FROM grade WHERE question=?", (question_id,)):
        grade = _grade(db, row[0], question_id)
        if grade is not None:
            grades.append(grade)
    return grades


def _record_grade_changes(db: sqlite3.Connection, before: list[Grade], actor: str) -> None:
    for grade in before:
        after = _grade(db, grade.submission, grade.question)
        if after is None or (after.applied == grade.applied and after.score == grade.score):
            continue
        db.execute(
            """
            UPDATE grade
            SET revision=revision + 1, updated_by=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE submission=? AND question=?
            """,
            (actor, grade.submission, grade.question),
        )
        _event(db, actor, "grade_updated", grade, _grade(db, grade.submission, grade.question))


def questions(db: sqlite3.Connection, assignment_id: int) -> list[QuestionInfo]:
    totals = dict(
        db.execute(
            """
                SELECT version, count(*)
                FROM submission
                WHERE assignment=?
                GROUP BY version
            """,
            (assignment_id,),
        ).fetchall()
    )
    result = []
    for q in db.execute("SELECT * FROM question WHERE assignment=? ORDER BY position", (assignment_id,)):
        items = _rubric(db, q["id"])
        graded = db.execute(
            """
                SELECT count(*)
                FROM grade g
                JOIN submission s ON s.id=g.submission
                WHERE g.question=? AND s.version=?
            """,
            (q["id"], q["version"]),
        ).fetchone()[0]
        result.append(
            QuestionInfo(
                q["id"],
                q["version"],
                q["number"],
                q["prompt"],
                q["points"],
                bool(q["bonus"]),
                q["kind"],
                json.loads(q["key"]),
                q["parent"],
                "positive" if any(i.points > 0 for i in items) else "negative",
                graded,
                totals.get(q["version"], 0),
                items,
            )
        )
    return result


def _affected(
    db: sqlite3.Connection,
    question_id: int,
    new_points: float,
    items: dict[int, float],
    removed: int | None = None,
    bonus: bool | None = None,
) -> int:
    question = _question(db, question_id)
    old_points = question["points"]
    old_items = {r.id: r.points for r in _rubric(db, question_id)}
    count = 0
    for row in db.execute("SELECT * FROM grade WHERE question=?", (question_id,)):
        applied = [
            r[0]
            for r in db.execute(
                "SELECT rubric_item FROM applied_item WHERE submission=? AND question=?",
                (row["submission"], question_id),
            )
        ]
        if (
            (bonus is not None and bonus != bool(question["bonus"]))
            or removed in applied
            or _score(old_points, old_items, applied, row["adjustment"])
            != _score(new_points, items, applied, row["adjustment"])
        ):
            count += 1
    return count


def replace_rubric(db: sqlite3.Connection, question_id: int, items: list[tuple[str, float]]) -> None:
    before = [asdict(i) for i in _rubric(db, question_id)]
    db.execute("DELETE FROM rubric_item WHERE question=?", (question_id,))
    db.executemany(
        """
            INSERT INTO rubric_item (question, description, points, position)
            VALUES (?, ?, ?, ?)
        """,
        [
            (question_id, description, points, position)
            for position, (description, points) in enumerate(items)
        ],
    )
    _event(
        db,
        "assignment file",
        "rubric_replaced",
        {"question": question_id, "items": before},
        {"question": question_id, "items": [asdict(i) for i in _rubric(db, question_id)]},
    )


def remove_question(db: sqlite3.Connection, question_id: int) -> None:
    before = dict(_question(db, question_id))
    _event(
        db,
        "assignment file",
        "question_deleted",
        {**before, "rubric": [asdict(i) for i in _rubric(db, question_id)]},
        None,
    )
    db.execute("DELETE FROM question WHERE id=?", (question_id,))


def confirm_question_points(
    db: sqlite3.Connection,
    assignment_id: int,
    points: dict[tuple[str, str], tuple[float, bool]],
    confirm: bool,
) -> None:
    affected = 0
    for q in db.execute("SELECT * FROM question WHERE assignment=?", (assignment_id,)):
        value, bonus = points.get((q["version"], q["number"]), (float(q["points"]), bool(q["bonus"])))
        if value != q["points"] or bonus != bool(q["bonus"]):
            affected += _affected(
                db, q["id"], value, {i.id: i.points for i in _rubric(db, q["id"])}, bonus=bonus
            )
    if affected and not confirm:
        raise NeedsConfirmation("Changing question points changes scores already given.", affected)


def change_question_points(
    db: sqlite3.Connection, question_id: int, points: float, bonus: bool, confirm: bool
) -> None:
    q = _question(db, question_id)
    if q["points"] == points and bool(q["bonus"]) == bonus:
        return
    affected = _affected(
        db, question_id, points, {i.id: i.points for i in _rubric(db, question_id)}, bonus=bonus
    )
    if affected and not confirm:
        raise NeedsConfirmation("Changing question points changes scores already given.", affected)
    before = _question_grades(db, question_id) if affected else []
    db.execute("UPDATE question SET points=?, bonus=? WHERE id=?", (points, bonus, question_id))
    _record_grade_changes(db, before, "assignment file")
    _event(
        db,
        "assignment file",
        "question_points_updated",
        {"question": question_id, "points": q["points"], "bonus": bool(q["bonus"])},
        {"question": question_id, "points": points, "bonus": bonus},
    )


def remove_submission(db: sqlite3.Connection, submission_id: int) -> None:
    question_ids = [
        r[0] for r in db.execute("SELECT question FROM grade WHERE submission=?", (submission_id,))
    ]
    for question_id in question_ids:
        grade = _grade(db, submission_id, question_id)
        if grade is not None:
            _event(db, "scan fix", "grade_deleted", grade, None)
    db.execute("DELETE FROM submission WHERE id=?", (submission_id,))


def remove_assignment_grades(db: sqlite3.Connection, assignment_id: int) -> None:
    db.execute(
        """
        DELETE FROM grade
        WHERE question IN (SELECT id FROM question WHERE assignment=?)
        """,
        (assignment_id,),
    )

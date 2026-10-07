import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from typing import Literal

from rubricate.errors import NeedsConfirmation, NotFound, StaleRevision, UserError
from rubricate.home import transaction


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


@dataclass(frozen=True)
class ResponseInfo:
    submission: int
    student: str | None
    student_name: str | None
    grade: Grade | None


@dataclass(frozen=True)
class SubmissionScores:
    submission: int
    student: str | None
    student_name: str | None
    version: str | None
    total: float | None
    possible: float
    scores: dict[int, float | None]


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


def _combined_items(db: sqlite3.Connection, question_id: int) -> set[int]:
    return {
        r[0]
        for r in db.execute(
            """
                SELECT DISTINCT a.rubric_item
                FROM applied_item a
                JOIN applied_item b ON b.submission=a.submission AND b.question=a.question
                    AND b.rubric_item<>a.rubric_item
                WHERE a.question=?
            """,
            (question_id,),
        )
    }


def combines_whole_answer(db: sqlite3.Connection, question_id: int, question_points: float) -> bool:
    items = {i.id: i.points for i in _rubric(db, question_id)}
    scoring = "positive" if any(p > 0 for p in items.values()) else "negative"
    return any(_whole_answer(items[i], scoring, question_points) for i in _combined_items(db, question_id))


def _item(db: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM rubric_item WHERE id=? AND NOT deleted", (item_id,)).fetchone()
    if row is None:
        raise NotFound("Rubric item not found.")
    return row


def _validate_points(points: float) -> None:
    if not math.isfinite(points):
        raise UserError("Points must be a finite number.")


def _signs(points: list[float]) -> None:
    if any(p < 0 for p in points) and any(p > 0 for p in points):
        raise UserError("Rubric items cannot mix positive and negative points.")


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


def add_item(
    db: sqlite3.Connection, question_id: int, description: str, points: float, actor: str
) -> RubricItem:
    _validate_points(points)
    if not description.strip():
        raise UserError("Enter a rubric item description.")
    with transaction(db):
        q = _question(db, question_id)
        if q["kind"] == "parts":
            raise UserError("Add rubric items to a part, not its parent question.")
        items = _rubric(db, question_id)
        _signs([i.points for i in items] + [points])
        proposed = {i.id: i.points for i in items}
        proposed[-1] = points
        affected = _affected(db, question_id, q["points"], proposed)
        if affected:
            raise NeedsConfirmation(
                "Adding this item would change the scoring of existing grades. "
                "Edit an existing rubric item's points instead, then confirm the score change.",
                affected,
            )
        position = max((i.position for i in items), default=-1) + 1
        item_id = db.execute(
            """
                INSERT INTO rubric_item (question, description, points, position)
                VALUES (?, ?, ?, ?)
                RETURNING id
            """,
            (question_id, description, points, position),
        ).fetchone()[0]
        item = next(i for i in _rubric(db, question_id) if i.id == item_id)
        _event(db, actor, "rubric_item_added", None, {"question": question_id, **asdict(item)})
    return item


def update_item(
    db: sqlite3.Connection, item_id: int, description: str, points: float, actor: str, confirm: bool = False
) -> RubricItem:
    _validate_points(points)
    if not description.strip():
        raise UserError("Enter a rubric item description.")
    with transaction(db):
        old = _item(db, item_id)
        items = {i.id: points if i.id == item_id else i.points for i in _rubric(db, old["question"])}
        _signs(list(items.values()))
        question_points = _question(db, old["question"])["points"]
        scoring = "positive" if any(p > 0 for p in items.values()) else "negative"
        if _whole_answer(points, scoring, question_points) and item_id in _combined_items(
            db, old["question"]
        ):
            raise UserError(
                f'Some grades combine "{old["description"]}" with other rubric items, '
                "so it can't cover the whole answer. Change those grades first."
            )
        affected = _affected(db, old["question"], question_points, items)
        if affected and not confirm:
            raise NeedsConfirmation("Changing these points changes scores already given.", affected)
        before = _question_grades(db, old["question"]) if affected else []
        db.execute(
            "UPDATE rubric_item SET description=?, points=? WHERE id=?", (description, points, item_id)
        )
        _record_grade_changes(db, before, actor)
        result = next(i for i in _rubric(db, old["question"]) if i.id == item_id)
        _event(db, actor, "rubric_item_updated", dict(old), asdict(result))
    return result


def delete_item(db: sqlite3.Connection, item_id: int, actor: str, confirm: bool = False) -> None:
    with transaction(db):
        old = _item(db, item_id)
        items = {i.id: i.points for i in _rubric(db, old["question"]) if i.id != item_id}
        affected = _affected(
            db, old["question"], _question(db, old["question"])["points"], items, removed=item_id
        )
        if affected and not confirm:
            raise NeedsConfirmation("Deleting this rubric item changes grades already given.", affected)
        before = _question_grades(db, old["question"]) if affected else []
        applied = [dict(r) for r in db.execute("SELECT * FROM applied_item WHERE rubric_item=?", (item_id,))]
        db.execute("DELETE FROM applied_item WHERE rubric_item=?", (item_id,))
        db.execute("UPDATE rubric_item SET deleted=1 WHERE id=?", (item_id,))
        _record_grade_changes(db, before, actor)
        _event(db, actor, "rubric_item_deleted", {**dict(old), "applied": applied}, None)


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


def responses(db: sqlite3.Connection, question_id: int) -> list[ResponseInfo]:
    q = _question(db, question_id)
    return [
        ResponseInfo(r["id"], r["student"], r["name"], _grade(db, r["id"], question_id))
        for r in db.execute(
            """
                SELECT s.id, s.student, t.name
                FROM submission s
                JOIN assignment a ON a.id=s.assignment
                LEFT JOIN student t ON t.course=a.course AND t.sid=s.student
                WHERE s.assignment=? AND s.version=?
                ORDER BY s.id
            """,
            (q["assignment"], q["version"]),
        )
    ]


def save_grade(
    db: sqlite3.Connection,
    submission_id: int,
    question_id: int,
    applied: list[int],
    adjustment: float,
    comment: str,
    revision: int,
    actor: str,
    score: float | None = None,
) -> Grade:
    if score is None:
        _validate_points(adjustment)
    with transaction(db):
        q = _question(db, question_id)
        submission = db.execute("SELECT * FROM submission WHERE id=?", (submission_id,)).fetchone()
        if submission is None:
            raise NotFound("Submission not found.")
        if q["kind"] == "parts":
            raise UserError("Grade each part, not its parent question.")
        if submission["assignment"] != q["assignment"] or submission["version"] != q["version"]:
            raise UserError("This question is not on the submission's version.")
        rubric = _rubric(db, question_id)
        items = {i.id: i.points for i in rubric}
        applied = sorted(set(applied))
        if not set(applied) <= items.keys():
            raise UserError("An applied rubric item does not belong to this question.")
        if len(applied) > 1:
            for item in rubric:
                if item.id in applied and item.whole_answer:
                    raise UserError(
                        f'"{item.description}" covers the whole answer, '
                        "so it can't be combined with other rubric items."
                    )
        if score is not None:
            if not math.isfinite(score) or score < 0:
                raise UserError(f"A score for question {q['number']} must be a finite number of 0 or more.")
            rubric_score = _score(q["points"], items, applied, 0)
            adjustment = 0 if score == rubric_score else round(score - rubric_score, 9)
        before = _grade(db, submission_id, question_id)
        if revision != (before.revision if before else 0):
            raise StaleRevision("This grade changed. Reload it before saving.")
        db.execute(
            """
                INSERT INTO grade (submission, question, adjustment, comment, revision, updated_by,
                    updated_at)
                VALUES (?, ?, ?, ?, ?, ?, strftime( '%Y-%m-%dT%H:%M:%fZ' , 'now' ))
                ON CONFLICT(submission, question)
                DO UPDATE
                SET adjustment=excluded.adjustment, comment=excluded.comment,
                    revision=excluded.revision, updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
            """,
            (submission_id, question_id, adjustment, comment, revision + 1, actor),
        )
        db.execute("DELETE FROM applied_item WHERE submission=? AND question=?", (submission_id, question_id))
        db.executemany(
            """
                INSERT INTO applied_item (submission, question, rubric_item)
                VALUES (?, ?, ?)
            """,
            [(submission_id, question_id, i) for i in applied],
        )
        result = _grade(db, submission_id, question_id)
        assert result is not None
        _event(
            db,
            actor,
            "grade_saved",
            before,
            result
            if score is None
            else {
                **{field: value for field, value in asdict(result).items() if field != "extra_credit"},
                "score": score,
            },
        )
    return result


def scores(db: sqlite3.Connection, assignment_id: int) -> list[SubmissionScores]:
    by_version: dict[str, list[QuestionInfo]] = {}
    for q in questions(db, assignment_id):
        if q.kind != "parts":
            by_version.setdefault(q.version, []).append(q)
    result = []
    for r in db.execute(
        """
            SELECT s.*, t.name
            FROM submission s
            JOIN assignment a ON a.id=s.assignment
            LEFT JOIN student t ON t.course=a.course AND t.sid=s.student
            WHERE s.assignment=?
            ORDER BY s.id
        """,
        (assignment_id,),
    ):
        values = {
            q.id: grade.score if (grade := _grade(db, r["id"], q.id)) else None
            for q in by_version.get(r["version"], [])
        }
        graded = [v for v in values.values() if v is not None]
        result.append(
            SubmissionScores(
                r["id"],
                r["student"],
                r["name"],
                r["version"],
                sum(graded) if graded else None,
                sum(q.points for q in by_version.get(r["version"], []) if not q.bonus),
                values,
            )
        )
    return result


def remove_submission(db: sqlite3.Connection, submission_id: int) -> None:
    question_ids = [
        r[0] for r in db.execute("SELECT question FROM grade WHERE submission=?", (submission_id,))
    ]
    for question_id in question_ids:
        grade = _grade(db, submission_id, question_id)
        if grade is not None:
            _event(db, "scan fix", "grade_deleted", grade, None)
    db.execute("DELETE FROM submission WHERE id=?", (submission_id,))


def remove_submissions(db: sqlite3.Connection, submission_ids: list[int], confirm: bool) -> None:
    graded = db.execute(
        f"SELECT count(*) FROM grade WHERE submission IN ({','.join('?' * len(submission_ids))})",
        submission_ids,
    ).fetchone()[0]
    if graded and not confirm:
        raise NeedsConfirmation(
            f"This deletes {graded} {'grade' if graded == 1 else 'grades'} on the submissions it removes.",
            graded,
        )
    for submission_id in submission_ids:
        remove_submission(db, submission_id)


def remove_assignment_grades(db: sqlite3.Connection, assignment_id: int) -> None:
    db.execute(
        """
        DELETE FROM grade
        WHERE question IN (SELECT id FROM question WHERE assignment=?)
        """,
        (assignment_id,),
    )

import json
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from rubrio import grading
from rubrio._assignment_file import AssignmentFile, AssignmentFileError, Problem, parse
from rubrio.courses import available_slug, validate_slug
from rubrio.errors import NotFound, UserError
from rubrio.home import transaction

__all__ = [
    "AssignmentFileError",
    "AssignmentInfo",
    "CourseAssignment",
    "Problem",
    "VersionSummary",
    "check",
    "create",
    "delete",
    "edit",
    "get",
    "list_for_course",
]


@dataclass(frozen=True)
class VersionSummary:
    name: str
    questions: int
    points: float
    bonus: float
    pages: int


@dataclass(frozen=True)
class AssignmentInfo:
    id: int
    course: str
    slug: str
    title: str
    source: str
    versions: list[str]
    has_scans: bool
    has_grades: bool


@dataclass(frozen=True)
class CourseAssignment:
    id: int
    course: str
    slug: str
    title: str
    has_scans: bool


def check(source: str) -> list[VersionSummary]:
    file = parse(source)
    return [
        VersionSummary(
            v,
            sum(q.kind != "parts" for q in file.questions if q.version == v),
            sum(
                q.points or 0 for q in file.questions if q.version == v and q.kind != "parts" and not q.bonus
            ),
            sum(q.points or 0 for q in file.questions if q.version == v and q.kind != "parts" and q.bonus),
            file.pages.get(v, 1),
        )
        for v in file.versions
    ]


def _info(db: sqlite3.Connection, assignment_id: int) -> AssignmentInfo:
    row = db.execute(
        """
            SELECT a.*, c.slug AS course_slug
            FROM assignment a
            JOIN course c ON c.id=a.course
            WHERE a.id=?
        """,
        (assignment_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Assignment not found.")
    versions = [
        r[0]
        for r in db.execute(
            """
                SELECT version
                FROM question
                WHERE assignment=?
                GROUP BY version
                ORDER BY min(position)
            """,
            (assignment_id,),
        )
    ]
    return AssignmentInfo(
        row["id"],
        row["course_slug"],
        row["slug"],
        row["title"],
        row["source"],
        versions,
        bool(db.execute("SELECT 1 FROM scan WHERE assignment=?", (assignment_id,)).fetchone()),
        bool(
            db.execute(
                """
                    SELECT 1
                    FROM grade g
                    JOIN question q ON q.id=g.question
                    WHERE q.assignment=?
                """,
                (assignment_id,),
            ).fetchone()
        ),
    )


def _write_questions(
    db: sqlite3.Connection,
    assignment_id: int,
    file: AssignmentFile,
    previous: AssignmentFile | None,
    confirm: bool,
) -> None:
    if previous is not None:
        before = previous.printed_by_version
        after = file.printed_by_version
        for version in before.keys() | after.keys():
            if before.get(version) == after.get(version):
                continue
            db.execute("DELETE FROM template_page WHERE assignment=? AND version=?", (assignment_id, version))
            questions = db.execute(
                "SELECT id FROM question WHERE assignment=? AND version=? ORDER BY position DESC",
                (assignment_id, version),
            ).fetchall()
            for question in questions:
                grading.remove_question(db, question["id"])
    ids = {
        (r["version"], r["number"]): r["id"]
        for r in db.execute("SELECT * FROM question WHERE assignment=?", (assignment_id,))
    }
    old = {(q.version, q.number): q for q in previous.questions} if previous else {}
    wanted = {(q.version, q.number) for q in file.questions}
    for key, question_id in reversed(list(ids.items())):
        if key not in wanted:
            grading.remove_question(db, question_id)
    for position, q in enumerate(file.questions):
        key = (q.version, q.number)
        question_id = ids.get(key)
        prior = old.get(key) if question_id is not None else None
        parent_id = ids[(q.version, q.parent)] if q.parent else None
        if question_id is None:
            row = db.execute(
                """
                    INSERT INTO question (assignment, version, parent, number, prompt, points, bonus, key,
                        kind, position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """,
                (
                    assignment_id,
                    q.version,
                    parent_id,
                    q.number,
                    q.prompt,
                    q.points,
                    q.bonus,
                    json.dumps(q.key),
                    q.kind,
                    position,
                ),
            ).fetchone()
            question_id = ids[key] = row[0]
        else:
            grading.change_question_points(db, question_id, q.points or 0, q.bonus, confirm)
            db.execute(
                """
                    UPDATE question
                    SET parent=?, prompt=?, key=?, kind=?, position=?
                    WHERE id=?
                """,
                (parent_id, q.prompt, json.dumps(q.key), q.kind, position, question_id),
            )
        if (
            prior is None
            or prior.rubric != q.rubric
            or (not q.rubric and prior.points != q.points)
            or prior.kind != q.kind
        ):
            graded = db.execute("SELECT 1 FROM grade WHERE question=?", (question_id,)).fetchone()
            if not graded:
                if q.kind == "parts":
                    items = []
                elif q.rubric_line:
                    items = q.rubric
                else:
                    items = [("Correct", 0.0), ("Incorrect", -(q.points or 0))]
                grading.replace_rubric(db, question_id, items)


def create(db: sqlite3.Connection, course_id: int, source: str, slug: str | None = None) -> AssignmentInfo:
    file = parse(source)
    with transaction(db):
        if db.execute("SELECT 1 FROM course WHERE id=?", (course_id,)).fetchone() is None:
            raise NotFound("Course not found.")
        if slug is None:
            taken = {row[0] for row in db.execute("SELECT slug FROM assignment WHERE course=?", (course_id,))}
            taken.add("new")
            slug = available_slug(file.title if file.title is not None else "assignment", taken)
        else:
            validate_slug(slug)
            if slug == "new":
                raise UserError("An assignment can't use the short name 'new'.")
            if db.execute("SELECT 1 FROM assignment WHERE course=? AND slug=?", (course_id, slug)).fetchone():
                raise UserError(f"Assignment '{slug}' already exists.")
        assignment_id = db.execute(
            """
                INSERT INTO assignment (course, slug, title, source)
                VALUES (?, ?, ?, ?)
                RETURNING id
            """,
            (course_id, slug, file.title or slug, source),
        ).fetchone()[0]
        _write_questions(db, assignment_id, file, None, False)
    return _info(db, assignment_id)


def edit(db: sqlite3.Connection, assignment_id: int, source: str, confirm: bool = False) -> AssignmentInfo:
    file = parse(source)
    with transaction(db):
        info = _info(db, assignment_id)
        previous = parse(info.source)
        problems = []
        if info.has_scans:
            matcher = SequenceMatcher(
                None,
                [line.text for line in previous.printed],
                [line.text for line in file.printed],
                autojunk=False,
            )
            for tag, a, _b, c, d in matcher.get_opcodes():
                if tag != "equal":
                    numbers = [line.line for line in file.printed[c:d]] or [
                        min(len(source.splitlines()) or 1, previous.printed[a].line)
                    ]
                    problems.extend(
                        Problem(n, "Printed content cannot change after scans are uploaded.") for n in numbers
                    )
        before = {(q.version, q.number): q for q in previous.questions}
        for q in file.questions:
            old = before.get((q.version, q.number))
            if (
                old is not None
                and old.rubric != q.rubric
                and db.execute(
                    """
                        SELECT 1
                        FROM grade g
                        JOIN question q ON q.id=g.question
                        WHERE q.assignment=? AND q.version=? AND q.number=?
                    """,
                    (assignment_id, q.version, q.number),
                ).fetchone()
            ):
                problems.append(
                    Problem(
                        q.rubric_line or q.line,
                        "This question has grades. Edit its rubric in the grading page.",
                    )
                )
            if old is not None and old.points != q.points:
                question_id = db.execute(
                    "SELECT id FROM question WHERE assignment=? AND version=? AND number=?",
                    (assignment_id, q.version, q.number),
                ).fetchone()[0]
                if grading.combines_whole_answer(db, question_id, q.points or 0):
                    problems.append(
                        Problem(
                            q.line,
                            "With these points a rubric item would cover the whole answer, "
                            "but some grades combine it with other items. Change those grades first.",
                        )
                    )
        if problems:
            raise AssignmentFileError(problems)
        grading.confirm_question_points(
            db,
            assignment_id,
            {(q.version, q.number): (q.points or 0, q.bonus) for q in file.questions},
            confirm,
        )
        _write_questions(db, assignment_id, file, previous, True)
        db.execute(
            "UPDATE assignment SET title=?, source=? WHERE id=?",
            (file.title or info.slug, source, assignment_id),
        )
    return _info(db, assignment_id)


def get(db: sqlite3.Connection, course_slug: str, assignment_slug: str) -> AssignmentInfo:
    row = db.execute(
        """
            SELECT a.id
            FROM assignment a
            JOIN course c ON c.id=a.course
            WHERE c.slug=? AND a.slug=?
        """,
        (course_slug, assignment_slug),
    ).fetchone()
    if row is None:
        raise NotFound("Assignment not found.")
    return _info(db, row[0])


def list_for_course(db: sqlite3.Connection, course_id: int) -> list[CourseAssignment]:
    return [
        CourseAssignment(r["id"], r["course"], r["slug"], r["title"], bool(r["has_scans"]))
        for r in db.execute(
            """
                SELECT a.id, c.slug AS course, a.slug, a.title,
                    EXISTS(SELECT 1 FROM scan WHERE assignment=a.id) AS has_scans
                FROM assignment a JOIN course c ON c.id=a.course
                WHERE a.course=? ORDER BY a.id
            """,
            (course_id,),
        )
    ]


def delete(db: sqlite3.Connection, assignment_id: int) -> None:
    with transaction(db):
        info = _info(db, assignment_id)
        active_job = db.execute(
            """
            SELECT 1 FROM job
            WHERE assignment=? AND state IN ('queued', 'running')
            """,
            (assignment_id,),
        ).fetchone()
        if active_job:
            raise UserError(
                "Scans are still being processed for this assignment. "
                "Wait for that to finish, then delete it."
            )
        grading.remove_assignment_grades(db, assignment_id)
        db.execute("DELETE FROM assignment WHERE id=?", (assignment_id,))
        db.execute(
            "INSERT INTO event (actor, kind, data) VALUES (?, ?, ?)",
            (
                "local",
                "assignment_deleted",
                json.dumps({"course": info.course, "assignment": info.slug, "title": info.title}),
            ),
        )

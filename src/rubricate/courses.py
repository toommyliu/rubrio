import csv
import io
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, replace

from rubricate.errors import NotFound, StaleRevision, UserError
from rubricate.home import transaction


@dataclass(frozen=True)
class Course:
    id: int
    slug: str
    name: str
    term: str


@dataclass(frozen=True)
class Student:
    sid: str
    name: str
    email: str
    section: str
    dropped: bool


@dataclass(frozen=True)
class RosterChange:
    added: list[Student]
    removed: list[Student]
    changed: list[Student]
    dropped: list[Student]


def validate_slug(slug: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise UserError("Use lowercase letters, digits and hyphens for the slug.")


def available_slug(text: str, taken: set[str]) -> str:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", text) if not unicodedata.combining(character)
    )
    base = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not base:
        raise UserError("Enter a name with letters or digits.")
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create_course(db: sqlite3.Connection, name: str, term: str, slug: str | None = None) -> Course:
    if not name.strip():
        raise UserError("Enter a course name with letters or digits.")
    with transaction(db):
        if slug is None:
            taken = {row[0] for row in db.execute("SELECT slug FROM course")}
            slug = available_slug(f"{name} {term}", taken)
        else:
            validate_slug(slug)
            if db.execute("SELECT 1 FROM course WHERE slug=?", (slug,)).fetchone():
                raise UserError(f"Course '{slug}' already exists.")
        db.execute("INSERT INTO course (slug, name, term) VALUES (?, ?, ?)", (slug, name, term))
    return get_course(db, slug)


def list_courses(db: sqlite3.Connection) -> list[Course]:
    return [Course(**dict(r)) for r in db.execute("SELECT * FROM course ORDER BY slug")]


def get_course(db: sqlite3.Connection, slug: str) -> Course:
    row = db.execute("SELECT * FROM course WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise NotFound("Course not found.")
    return Course(**dict(row))


def roster(db: sqlite3.Connection, course_id: int) -> list[Student]:
    if db.execute("SELECT 1 FROM course WHERE id = ?", (course_id,)).fetchone() is None:
        raise NotFound("Course not found.")
    return [
        Student(r["sid"], r["name"], r["email"], r["section"], bool(r["dropped"]))
        for r in db.execute(
            "SELECT * FROM student WHERE course = ? ORDER BY name COLLATE NOCASE, sid", (course_id,)
        )
    ]


def import_roster(
    db: sqlite3.Connection,
    course_id: int,
    csv_text: str,
    dry_run: bool = False,
    expected: RosterChange | None = None,
) -> RosterChange:
    reader = csv.reader(io.StringIO(csv_text.lstrip("\ufeff")), strict=True)
    line = 1
    try:
        while True:
            line = reader.line_num + 1
            fieldnames = next(reader, None)
            if fieldnames != []:
                break
        headers = {h.strip().lower(): h for h in fieldnames or []}
        sid_header = next(
            (headers[h] for h in ("sid", "id", "student id", "sis user id") if h in headers), None
        )
        if sid_header is None or not ("name" in headers or {"first name", "last name"} <= headers.keys()):
            found = ", ".join(fieldnames or []) or "(none)"
            raise UserError(f"The roster needs sid and name columns. Headers found: {found}.")
        incoming: dict[str, Student] = {}
        while True:
            line = reader.line_num + 1
            fields = next(reader, None)
            if fields is None:
                break
            if not fields:
                continue
            row = {h: fields[i] if i < len(fields) else "" for i, h in enumerate(fieldnames or [])}
            sid = (row.get(sid_header) or "").strip()
            name = (
                (row.get(headers["name"]) or "").strip()
                if "name" in headers
                else " ".join(
                    (row.get(headers[h]) or "").strip() for h in ("first name", "last name")
                ).strip()
            )
            if not sid or not name:
                raise UserError(f"Line {line}: sid and name cannot be empty.")
            if sid in incoming:
                raise UserError(f"Line {line}: duplicate sid '{sid}'.")
            incoming[sid] = Student(
                sid,
                name,
                (row.get(headers.get("email", "")) or "").strip(),
                (row.get(headers.get("section", "")) or "").strip(),
                False,
            )
    except csv.Error as error:
        raise UserError(
            f"Line {line}: this row can't be read. "
            "Check for a missing closing quotation mark or a value that's too long."
        ) from error
    with transaction(db):
        existing = {s.sid: s for s in roster(db, course_id)}
        matched = {
            r[0]
            for r in db.execute(
                """
                    SELECT s.student
                    FROM submission s
                    JOIN assignment a ON a.id = s.assignment
                    WHERE a.course = ? AND s.student IS NOT NULL
                """,
                (course_id,),
            )
        }
        change = RosterChange(
            [s for sid, s in incoming.items() if sid not in existing],
            [s for sid, s in existing.items() if sid not in incoming and sid not in matched],
            [s for sid, s in incoming.items() if sid in existing and s != existing[sid]],
            [
                replace(s, dropped=True)
                for sid, s in existing.items()
                if sid not in incoming and sid in matched
            ],
        )
        if expected is not None and change != expected:
            raise StaleRevision(
                "The roster changed after the preview. Check the new preview, then import again."
            )
        if not dry_run:
            for student in change.added + change.changed + change.dropped:
                db.execute(
                    """
                        INSERT INTO student (course, sid, name, email, section, dropped)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(course, sid)
                        DO UPDATE
                        SET name=excluded.name, email=excluded.email, section=excluded.section,
                            dropped=excluded.dropped
                    """,
                    (course_id, student.sid, student.name, student.email, student.section, student.dropped),
                )
            for student in change.removed:
                db.execute("DELETE FROM student WHERE course = ? AND sid = ?", (course_id, student.sid))
    return change

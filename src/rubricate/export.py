import csv
import io
import sqlite3
from dataclasses import dataclass

from rubricate import courses, grading
from rubricate.errors import NotFound


@dataclass(frozen=True)
class GradebookExport:
    csv: str
    ungraded: int
    unmatched: int


def gradebook_csv(db: sqlite3.Connection, assignment_id: int) -> GradebookExport:
    assignment = db.execute("SELECT * FROM assignment WHERE id=?", (assignment_id,)).fetchone()
    if assignment is None:
        raise NotFound("Assignment not found.")
    students = {s.sid: s for s in courses.roster(db, assignment["course"])}
    questions = [q for q in grading.questions(db, assignment_id) if q.kind != "parts"]
    columns: list[tuple[str, float]] = []
    question_columns: dict[int, tuple[str, float]] = {}
    headers: list[str] = []
    for q in questions:
        prompt = q.prompt.splitlines()[0]
        key = (prompt, q.points)
        if key in question_columns.values() and any(
            old.version == q.version and question_columns.get(old.id) == key
            for old in questions
            if old.id in question_columns
        ):
            key = (f"{q.version}:{q.number}", q.points)
        question_columns[q.id] = key
        if key not in columns:
            columns.append(key)
            headers.append(f"{key[0]} ({key[1]:g})")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["sid", "name", "email", "section", "total", *headers])
    unmatched = ungraded = 0
    for row in grading.scores(db, assignment_id):
        ungraded += sum(value is None for value in row.scores.values())
        if row.student is None:
            unmatched += 1
            continue
        student = students[row.student]
        values = {question_columns[qid]: value for qid, value in row.scores.items()}
        writer.writerow(
            [
                student.sid,
                student.name,
                student.email,
                student.section,
                "" if row.total is None else f"{row.total:g}",
                *("" if values.get(key) is None else f"{values[key]:g}" for key in columns),
            ]
        )
    return GradebookExport(output.getvalue(), ungraded, unmatched)

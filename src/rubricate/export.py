import csv
import io
import json
import math
import re
import sqlite3
import zipfile
from dataclasses import dataclass

import numpy as np
import pymupdf

from rubricate import courses, grading
from rubricate._images import DPI, PDF_LOCK
from rubricate.errors import NotFound
from rubricate.home import Home

DATE = "D:19700101000000Z"


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
    taken: set[tuple[str, str, float]] = set()
    headers: list[str] = []
    for q in questions:
        label = q.prompt.partition("\n")[0] or f"{q.version}:{q.number}"
        while (q.version, label, q.points) in taken:
            label = f"{q.version}:{q.number} {label}"
        taken.add((q.version, label, q.points))
        key = (label, q.points)
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


def _note(db: sqlite3.Connection, submission_id: int, q: grading.QuestionInfo) -> str:
    grade = grading._grade(db, submission_id, q.id)
    if grade is None:
        return f"Question {q.number}: Ungraded"
    lines = [f"Question {q.number}: {grade.score:g} / {q.points:g}"]
    if grade.extra_credit:
        lines[0] += " (extra credit)"
    lines.extend(f"{i.description} ({i.points:+g})" for i in q.rubric if i.id in grade.applied)
    if grade.adjustment:
        lines.append(f"Adjustment: {grade.adjustment:+g}")
    if grade.comment:
        lines.append(grade.comment)
    return "\n".join(lines)


def _add_note(
    document: pymupdf.Document, page: pymupdf.Page, subject: str, name: str, content: str, x: float, y: float
) -> None:
    annot = page.add_text_annot(pymupdf.Point(x, y), content, icon="Comment")
    annot.set_info(subject=subject, creationDate=DATE, modDate=DATE)
    width, height = min(300, page.rect.width - 24), min(200, page.rect.height - 24)
    left = min(x + 24, page.rect.width - width - 12)
    top = min(y, page.rect.height - height - 12)
    annot.set_popup(pymupdf.Rect(left, top, left + width, top + height))
    annot.update()
    document.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str(name))


def feedback_pdfs(home: Home, db: sqlite3.Connection, assignment_id: int) -> bytes:
    assignment = db.execute("SELECT * FROM assignment WHERE id=?", (assignment_id,)).fetchone()
    if assignment is None:
        raise NotFound("Assignment not found.")
    questions = [q for q in grading.questions(db, assignment_id) if q.kind != "parts"]
    boxes: dict[int, list[sqlite3.Row]] = {}
    for box in db.execute(
        """
            SELECT b.* FROM box b
            JOIN template_page t ON t.id=b.template_page
            WHERE t.assignment=? AND b.question IS NOT NULL
            ORDER BY b.position, b.id
        """,
        (assignment_id,),
    ):
        boxes.setdefault(box["template_page"], []).append(box)
    output = io.BytesIO()
    scans: dict[str, pymupdf.Document] = {}
    taken: set[str] = set()
    try:
        with zipfile.ZipFile(output, "w") as archive:
            for scores in grading.scores(db, assignment_id):
                if scores.student is None:
                    continue
                rows = db.execute(
                    """
                        SELECT s.file, p.page_index, p.template_page, p.homography, p.extra,
                               t.width, t.height
                        FROM submission_page sp
                        JOIN scan_page p ON p.id=sp.scan_page
                        JOIN scan s ON s.id=p.scan
                        LEFT JOIN template_page t ON t.id=p.template_page
                        WHERE sp.submission=?
                        ORDER BY sp.position, p.id
                    """,
                    (scores.submission,),
                ).fetchall()
                notes = {
                    q.id: (f"Question {q.number}", f"question-{q.id}", _note(db, scores.submission, q))
                    for q in questions
                    if q.version == scores.version
                }
                with PDF_LOCK, pymupdf.open() as document:
                    annotated: set[int] = set()
                    for row in rows:
                        if row["file"] not in scans:
                            scans[row["file"]] = pymupdf.open(home.file(row["file"]))
                        document.insert_pdf(
                            scans[row["file"]],
                            from_page=row["page_index"],
                            to_page=row["page_index"],
                            final=0,
                        )
                        page = document[-1]
                        page.remove_rotation()
                        homography = (
                            np.asarray(json.loads(row["homography"])) if row["homography"] else np.eye(3)
                        )
                        if row["homography"]:
                            angle = math.degrees(math.atan2(homography[1, 0], homography[0, 0]))
                            page.set_rotation(-90 * round(angle / 90))
                        elif row["template_page"] is not None:
                            homography[0, 0] = page.rect.width / row["width"]
                            homography[1, 1] = page.rect.height / row["height"]
                        rotation = page.rotation_matrix
                        page.remove_rotation()
                        for box in boxes.get(row["template_page"], []) if not row["extra"] else []:
                            if box["question"] not in notes or box["question"] in annotated:
                                continue
                            annotated.add(box["question"])
                            corners = (
                                np.array(
                                    [
                                        [x * DPI / 72, y * DPI / 72, 1]
                                        for x in (box["x0"], box["x1"])
                                        for y in (box["y0"], box["y1"])
                                    ],
                                    dtype=float,
                                )
                                @ homography.T
                            )
                            points = [
                                pymupdf.Point(x / w * 72 / DPI, y / w * 72 / DPI) * rotation
                                for x, y, w in corners
                            ]
                            x = min(max(12, min(p.x for p in points) - 28), page.rect.width - 24)
                            y = min(max(12, min(p.y for p in points) + 4), page.rect.height - 24)
                            _add_note(document, page, *notes[box["question"]], x, y)
                    if document.page_count:
                        page = document[0]
                        total = (
                            f"Total: {scores.total:g} / {scores.possible:g}"
                            if scores.total is not None
                            else "Total: Ungraded"
                        )
                        fontsize = min(
                            14, (page.rect.width - 28) / pymupdf.get_text_length(total, fontsize=1)
                        )
                        width = pymupdf.get_text_length(total, fontsize=fontsize) + 8
                        annot = page.add_freetext_annot(
                            pymupdf.Rect(page.rect.width - width - 10, 12, page.rect.width - 10, 36),
                            total,
                            fontsize=fontsize,
                            text_color=(0.8, 0, 0),
                        )
                        annot.set_info(subject="Total", creationDate=DATE, modDate=DATE)
                        annot.update()
                        document.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str("total"))
                        missing = [note[2] for qid, note in notes.items() if qid not in annotated]
                        if missing:
                            icons = [a.rect for a in page.annots()]
                            x, y = next(
                                (
                                    (x, y)
                                    for y in range(12, int(page.rect.height) - 24, 20)
                                    for x in range(12, int(page.rect.width) - 24, 20)
                                    if not any(
                                        pymupdf.Rect(x, y, x + 20, y + 20).intersects(r) for r in icons
                                    )
                                ),
                                (12, 12),
                            )
                            _add_note(
                                document,
                                page,
                                "Questions without boxes",
                                "questions-without-boxes",
                                "\n\n".join(missing),
                                x,
                                y,
                            )
                    document.set_metadata(
                        {"title": assignment["title"], "creationDate": DATE, "modDate": DATE}
                    )
                    pdf = document.tobytes(garbage=3, deflate=True, no_new_id=True)
                stem = re.sub(r"[^\w .-]", "_", f"{scores.student}-{scores.student_name}")
                filename = f"{stem}.pdf"
                number = 1
                while filename.casefold() in taken:
                    number += 1
                    filename = f"{stem} ({number}).pdf"
                taken.add(filename.casefold())
                archive.writestr(zipfile.ZipInfo(filename), pdf, compress_type=zipfile.ZIP_DEFLATED)
    finally:
        with PDF_LOCK:
            for scan in scans.values():
                scan.close()
    return output.getvalue()

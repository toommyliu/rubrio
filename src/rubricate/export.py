import csv
import io
import json
import math
import re
import sqlite3
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass

import numpy as np
import pymupdf

from rubricate import courses, grading
from rubricate._images import DPI, PDF_LOCK
from rubricate.errors import NotFound
from rubricate.home import Home


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


def feedback_pdfs(home: Home, db: sqlite3.Connection, assignment_id: int) -> bytes:
    assignment = db.execute("SELECT * FROM assignment WHERE id=?", (assignment_id,)).fetchone()
    if assignment is None:
        raise NotFound("Assignment not found.")
    questions = {q.id: q for q in grading.questions(db, assignment_id) if q.kind != "parts"}
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
    date = "D:19700101000000Z"
    with PDF_LOCK, ExitStack() as opened, zipfile.ZipFile(output, "w") as archive:
        scans: dict[str, pymupdf.Document] = {}
        for scores in grading.scores(db, assignment_id):
            if scores.student is None:
                continue
            with pymupdf.open() as document:
                annotated: set[int] = set()
                for row in db.execute(
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
                ):
                    if row["file"] not in scans:
                        scans[row["file"]] = opened.enter_context(pymupdf.open(home.file(row["file"])))
                    document.insert_pdf(
                        scans[row["file"]], from_page=row["page_index"], to_page=row["page_index"], final=0
                    )
                    page = document[-1]
                    page.remove_rotation()
                    homography = np.asarray(json.loads(row["homography"])) if row["homography"] else np.eye(3)
                    if row["homography"]:
                        angle = math.degrees(math.atan2(homography[1, 0], homography[0, 0]))
                        page.set_rotation(-90 * round(angle / 90))
                    elif row["template_page"] is not None:
                        homography[0, 0] = page.rect.width / row["width"]
                        homography[1, 1] = page.rect.height / row["height"]
                    rotation = page.rotation_matrix
                    page.remove_rotation()
                    for box in boxes.get(row["template_page"], []) if not row["extra"] else []:
                        q = questions[box["question"]]
                        if q.version != scores.version or q.id in annotated:
                            continue
                        annotated.add(q.id)
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
                        grade = grading._grade(db, scores.submission, q.id)
                        lines = [f"Question {q.number}: Ungraded"]
                        if grade is not None:
                            lines = [f"Question {q.number}: {grade.score:g} / {q.points:g}"]
                            if grade.extra_credit:
                                lines[0] += " (extra credit)"
                            lines.extend(
                                f"{i.description} ({i.points:+g})" for i in q.rubric if i.id in grade.applied
                            )
                            if grade.adjustment:
                                lines.append(f"Adjustment: {grade.adjustment:+g}")
                            if grade.comment:
                                lines.append(grade.comment)
                        content = "\n".join(lines)
                        annot = page.add_text_annot(pymupdf.Point(x, y), content, icon="Comment")
                        annot.set_info(subject=f"Question {q.number}", creationDate=date, modDate=date)
                        width, height = min(300, page.rect.width - 24), min(200, page.rect.height - 24)
                        left = min(x + 24, page.rect.width - width - 12)
                        top = min(y, page.rect.height - height - 12)
                        annot.set_popup(pymupdf.Rect(left, top, left + width, top + height))
                        annot.update()
                        document.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str(f"question-{q.id}"))
                if document.page_count:
                    page = document[0]
                    total = (
                        f"Total: {scores.total:g} / {scores.possible:g}"
                        if scores.total is not None
                        else "Total: Ungraded"
                    )
                    fontsize = min(14, (page.rect.width - 28) / pymupdf.get_text_length(total, fontsize=1))
                    width = pymupdf.get_text_length(total, fontsize=fontsize) + 8
                    annot = page.add_freetext_annot(
                        pymupdf.Rect(page.rect.width - width - 10, 12, page.rect.width - 10, 36),
                        total,
                        fontsize=fontsize,
                        text_color=(0.8, 0, 0),
                    )
                    annot.set_info(subject="Total", creationDate=date, modDate=date)
                    annot.update()
                    document.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str("total"))
                document.set_metadata({"title": assignment["title"], "creationDate": date, "modDate": date})
                filename = re.sub(r"[^\w .-]", "_", f"{scores.student}-{scores.student_name}") + ".pdf"
                archive.writestr(
                    zipfile.ZipInfo(filename),
                    document.tobytes(garbage=3, deflate=True, no_new_id=True),
                    compress_type=zipfile.ZIP_DEFLATED,
                )
    return output.getvalue()

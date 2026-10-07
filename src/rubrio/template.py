import math
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

import pymupdf

from rubrio import names
from rubrio._images import PDF_LOCK, pdf_document, rendered
from rubrio.errors import NotFound, UserError
from rubrio.home import Home, transaction


@dataclass(frozen=True)
class TemplatePage:
    id: int
    version: str
    page: int
    width: float
    height: float


@dataclass(frozen=True)
class Box:
    id: int
    template_page: int
    question: int | None
    field: Literal["name", "sid"] | None
    x0: float
    y0: float
    x1: float
    y1: float
    suggested: bool


@dataclass(frozen=True)
class Outline:
    pages: list[TemplatePage]
    boxes: list[Box]


LABEL = re.compile(r"^(?:(?i:Question\s+|Q)(\d+)\b[.:)]?|([0-9]+)[.)]|\(?([a-z])\))\s*(.*)")


def _box(row: sqlite3.Row) -> Box:
    return Box(
        row["id"],
        row["template_page"],
        row["question"],
        row["field"],
        row["x0"],
        row["y0"],
        row["x1"],
        row["y1"],
        bool(row["suggested"]),
    )


def _suggest(
    db: sqlite3.Connection, document: Any, pages: list[TemplatePage], questions: list[sqlite3.Row]
) -> None:
    next_question = 0
    for page, info in zip(document, pages, strict=True):
        lines = sorted(
            [
                (
                    "".join(s["text"] for s in line["spans"]).strip(),
                    pymupdf.Rect(line["bbox"]) * page.rotation_matrix,
                )
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
            ],
            key=lambda line: (line[1][1], line[1][0]),
        )
        found: list[tuple[float, sqlite3.Row]] = []
        for text, rect in lines:
            match = LABEL.match(text)
            if match is None or rect[0] > info.width / 3:
                continue
            label = match[1] or match[2] or match[3].lower()
            candidates = []
            for index in range(next_question, len(questions)):
                q = questions[index]
                expected = q["number"][-1] if q["parent"] else q["number"]
                number_score = 2 if label == expected else 0
                similarity = SequenceMatcher(
                    None,
                    re.sub(r"[^a-z0-9]", "", match[4].lower()),
                    re.sub(r"[^a-z0-9]", "", q["prompt"].partition("\n")[0].lower()),
                ).ratio()
                points = re.search(r"\((\d+(?:\.\d+)?)\s*(?:pts|points?)\)", text)
                points_score = 0.5 if points and float(points[1]) == q["points"] else 0
                candidates.append((number_score + similarity + points_score, -index, q))
            if not candidates:
                break
            score, negative_index, q = max(candidates, key=lambda c: (c[0], c[1]))
            if score < 2:
                continue
            next_question = -negative_index + 1
            found.append((rect[1], q))
        for index, (y, question) in enumerate(found):
            if question["kind"] == "parts":
                continue
            bottom = found[index + 1][0] if index + 1 < len(found) else info.height - 50
            _suggest_box(db, info, question["id"], None, 0, max(0, y - 6), info.width, bottom - 6)
        for text, rect in lines:
            field: Literal["name", "sid"]
            if re.match(r"^Name\b", text, re.IGNORECASE):
                field = "name"
            elif re.match(r"^(Student ID|ID|SID)\b", text, re.IGNORECASE):
                field = "sid"
            else:
                continue
            after = min((r[1] for _, r in lines if r[1] > rect[3] + 5), default=rect[3] + 35)
            _suggest_box(
                db,
                info,
                None,
                field,
                rect[2] + 8,
                max(0, rect[1] - 22),
                info.width - 52,
                min(info.height, after - 6),
            )


def _suggest_box(
    db: sqlite3.Connection,
    page: TemplatePage,
    question: int | None,
    field: Literal["name", "sid"] | None,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> None:
    if x0 < x1 and y0 < y1:
        _insert_box(db, page.id, question, field, x0, y0, x1, y1, True)


def upload(
    home: Home, db: sqlite3.Connection, assignment_id: int, version: str, pdf: bytes
) -> list[TemplatePage]:
    with transaction(db):
        questions = db.execute(
            "SELECT * FROM question WHERE assignment=? AND version=? ORDER BY position",
            (assignment_id, version),
        ).fetchall()
        if not questions:
            raise UserError("This version is not in the assignment file.")
        if db.execute("SELECT 1 FROM scan WHERE assignment=?", (assignment_id,)).fetchone():
            raise UserError("Templates cannot change after scans are uploaded.")
        with PDF_LOCK, pdf_document(pdf) as document:
            file = home.store(pdf, ".pdf")
            db.execute("DELETE FROM template_page WHERE assignment=? AND version=?", (assignment_id, version))
            pages = []
            for index, page in enumerate(document):
                row = db.execute(
                    """
                        INSERT INTO template_page (assignment, version, page, file, width, height)
                        VALUES (?, ?, ?, ?, ?, ?)
                        RETURNING id
                    """,
                    (assignment_id, version, index + 1, file, page.rect.width, page.rect.height),
                ).fetchone()
                pages.append(TemplatePage(row[0], version, index + 1, page.rect.width, page.rect.height))
            _suggest(db, document, pages, questions)
    return pages


def outline(db: sqlite3.Connection, assignment_id: int) -> Outline:
    pages = [
        TemplatePage(r["id"], r["version"], r["page"], r["width"], r["height"])
        for r in db.execute(
            "SELECT * FROM template_page WHERE assignment=? ORDER BY version, page", (assignment_id,)
        )
    ]
    boxes = [
        _box(r)
        for r in db.execute(
            """
                SELECT b.*
                FROM box b
                JOIN template_page t ON t.id=b.template_page
                WHERE t.assignment=?
                ORDER BY t.version, t.page, b.position, b.id
            """,
            (assignment_id,),
        )
    ]
    return Outline(pages, boxes)


def page_image(home: Home, db: sqlite3.Connection, template_page_id: int) -> Path:
    page = db.execute("SELECT * FROM template_page WHERE id=?", (template_page_id,)).fetchone()
    if page is None:
        raise NotFound("Template page not found.")
    return rendered(home, page["file"], page["page"] - 1)


def _geometry(page: sqlite3.Row, x0: float, y0: float, x1: float, y1: float) -> None:
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or not (
        0 <= x0 < x1 <= page["width"] and 0 <= y0 < y1 <= page["height"]
    ):
        raise UserError("Draw a box with positive width and height inside the page.")


def _insert_box(
    db: sqlite3.Connection,
    template_page_id: int,
    question: int | None,
    field: str | None,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    suggested: bool,
) -> Box:
    position = db.execute(
        "SELECT coalesce(max(position), -1) + 1 FROM box WHERE template_page=?", (template_page_id,)
    ).fetchone()[0]
    row = db.execute(
        """
            INSERT INTO box (template_page, question, field, x0, y0, x1, y1, position, suggested)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
        """,
        (template_page_id, question, field, x0, y0, x1, y1, position, suggested),
    ).fetchone()
    return _box(row)


def add_box(
    db: sqlite3.Connection,
    template_page_id: int,
    question: int | None,
    field: str | None,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> Box:
    with transaction(db):
        page = db.execute("SELECT * FROM template_page WHERE id=?", (template_page_id,)).fetchone()
        if page is None:
            raise NotFound("Template page not found.")
        _geometry(page, x0, y0, x1, y1)
        if (question is None) == (field is None) or field not in {None, "name", "sid"}:
            raise UserError("Choose either a question or a name or sid field for the box.")
        if question is not None:
            q = db.execute("SELECT * FROM question WHERE id=?", (question,)).fetchone()
            if (
                q is None
                or q["assignment"] != page["assignment"]
                or q["version"] != page["version"]
                or q["kind"] == "parts"
            ):
                raise UserError("Choose a gradable question on this template's version.")
        result = _insert_box(db, template_page_id, question, field, x0, y0, x1, y1, False)
        if field:
            _invalidate_names(db, template_page_id)
        return result


def update_box(db: sqlite3.Connection, box_id: int, x0: float, y0: float, x1: float, y1: float) -> Box:
    with transaction(db):
        page = db.execute(
            """
                SELECT t.*, b.field
                FROM box b
                JOIN template_page t ON t.id=b.template_page
                WHERE b.id=?
            """,
            (box_id,),
        ).fetchone()
        if page is None:
            raise NotFound("Box not found.")
        _geometry(page, x0, y0, x1, y1)
        if page["field"]:
            _invalidate_names(db, page["id"])
        return _box(
            db.execute(
                "UPDATE box SET x0=?, y0=?, x1=?, y1=?, suggested=0 WHERE id=? RETURNING *",
                (x0, y0, x1, y1, box_id),
            ).fetchone()
        )


def delete_box(db: sqlite3.Connection, box_id: int) -> None:
    with transaction(db):
        box = db.execute("SELECT * FROM box WHERE id=?", (box_id,)).fetchone()
        if box is None:
            raise NotFound("Box not found.")
        if box["field"]:
            _invalidate_names(db, box["template_page"])
        db.execute("DELETE FROM box WHERE id=?", (box_id,))


def _invalidate_names(db: sqlite3.Connection, template_page_id: int) -> None:
    submissions = {
        r[0]
        for r in db.execute(
            """
            SELECT sp.submission
            FROM submission_page sp
            JOIN scan_page p ON p.id=sp.scan_page
            WHERE p.template_page=?
            """,
            (template_page_id,),
        )
    }
    names.invalidate(db, submissions)

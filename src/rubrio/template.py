import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

import pymupdf

from rubrio import names
from rubrio._images import PDF_LOCK, pdf_document, rendered
from rubrio.errors import NotFound, UserError
from rubrio.home import Home, read_snapshot, transaction


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


FIELD = re.compile(r"^(?:(Name)|Student ID|SID|ID)\s*[:#]?(?=[\s_]*$)", re.IGNORECASE)
LABEL = re.compile(r"^(?:(?i:Question\s+|Q)(\d+)\b[.:)]?|([0-9]+)[.)]|\(?([a-z])\))\s*(.*)")


@dataclass(frozen=True)
class FoundPart:
    label: int
    prompt: str
    points: float | None
    page: int


@dataclass(frozen=True)
class FoundQuestion:
    label: int
    prompt: str
    points: float | None
    page: int
    parts: list[FoundPart]


@dataclass(frozen=True)
class FoundTemplate:
    title: str
    pages: int
    has_text: bool
    questions: list[FoundQuestion]


@dataclass(frozen=True)
class _Line:
    text: str
    rect: pymupdf.Rect
    size: float


@dataclass(frozen=True)
class _Label:
    page: int
    number: int
    part: bool
    rect: pymupdf.Rect
    prompt: str
    points: float | None


LABEL = re.compile(r"^(?:(?i:Question\s+|Q)(\d+)\b[.:)]?|([0-9]+)[.)]|\(?([a-z])\))\s*(.*)")
UNIT = r"(?:pts?|points?|marks?)"
BRACKETED_POINTS = re.compile(rf"[(\[]\s*(\d+(?:\.\d+)?)\s*{UNIT}\.?\s*[)\]]", re.IGNORECASE)
BARE_POINTS = re.compile(
    rf"^\s*(\d+(?:\.\d+)?)\s*{UNIT}\b\.?|(?:^|\s)(\d+(?:\.\d+)?)\s*{UNIT}\.?\s*$", re.IGNORECASE
)
PAGE_NUMBER = re.compile(r"(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?", re.IGNORECASE)
ALIGNED = 8


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


def _lines(page: Any) -> list[_Line]:
    lines = [
        _Line(
            "".join(s["text"] for s in line["spans"]).strip(),
            pymupdf.Rect(line["bbox"]) * page.rotation_matrix,
            max((s["size"] for s in line["spans"]), default=0),
        )
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
    ]
    return sorted((line for line in lines if line.text), key=lambda line: (line.rect.y0, line.rect.x0))


def _footers(page_lines: list[list[_Line]], pages: list[TemplatePage]) -> list[float | None]:
    def shape(text: str) -> str:
        return " ".join(re.sub(r"\d+", "#", text.lower()).split())

    near_bottom = [
        [line for line in lines if line.rect.y0 > page.height * 0.85 and not LABEL.match(line.text)]
        for lines, page in zip(page_lines, pages, strict=True)
    ]
    repeated = Counter(text for lines in near_bottom for text in {shape(line.text) for line in lines})
    return [
        min(
            (
                line.rect.y0
                for line in lines
                if PAGE_NUMBER.fullmatch(line.text)
                or (repeated[shape(line.text)] >= 2 and repeated[shape(line.text)] * 2 >= len(pages))
            ),
            default=None,
        )
        for lines in near_bottom
    ]


def _printed_points(text: str) -> tuple[float | None, str]:
    match = BRACKETED_POINTS.search(text) or BARE_POINTS.search(text)
    if match is None:
        return None, text
    return float(match[1] or match[2]), " ".join((text[: match.start()] + " " + text[match.end() :]).split())


def _labels(page_lines: list[list[_Line]], pages: list[TemplatePage]) -> list[_Label]:
    candidates: list[_Label] = []
    for index, (lines, page) in enumerate(zip(page_lines, pages, strict=True)):
        for line in lines:
            match = LABEL.match(line.text)
            if match is None or line.rect.x0 > page.width / 3:
                continue
            row = [
                other.text
                for other in lines
                if other is not line
                and other.rect.x0 >= line.rect.x1 - 1
                and line.rect.y0 <= (other.rect.y0 + other.rect.y1) / 2 <= line.rect.y1
            ]
            points, prompt = _printed_points(" ".join([match[4], *row]))
            prompt = prompt.lstrip(" :.-)").rstrip(" -")
            if not prompt:
                below = next(
                    (
                        other
                        for other in lines
                        if other.rect.y0 >= line.rect.y1 - 1
                        and other.rect.y0 - line.rect.y1 < 2 * line.rect.height
                        and not LABEL.match(other.text)
                    ),
                    None,
                )
                if below is not None:
                    found, prompt = _printed_points(below.text)
                    points = points if points is not None else found
            part = match[3] is not None
            candidates.append(
                _Label(
                    index,
                    ord(match[3]) - 96 if part else int(match[1] or match[2]),
                    part,
                    line.rect,
                    prompt.lstrip(" :.-)").rstrip(" -")[:200],
                    points,
                )
            )
    tops = [i for i, c in enumerate(candidates) if not c.part]
    starts = [i for i in tops if candidates[i].number == 1] or tops
    chain: list[int] = []
    for start in starts:
        attempt = [start]
        for i in tops:
            if (
                i > attempt[-1]
                and candidates[i].number == candidates[attempt[-1]].number + 1
                and abs(candidates[i].rect.x0 - candidates[start].rect.x0) <= ALIGNED
            ):
                attempt.append(i)
        if len(attempt) >= len(chain):
            chain = attempt
    chosen: list[_Label] = []
    for position, i in enumerate(chain):
        question = candidates[i]
        end = chain[position + 1] if position + 1 < len(chain) else len(candidates)
        parts: list[_Label] = []
        for c in candidates[i + 1 : end]:
            if (
                c.part
                and c.number == len(parts) + 1
                and c.rect.x0 >= question.rect.x0 - ALIGNED
                and (not parts or abs(c.rect.x0 - parts[0].rect.x0) <= ALIGNED)
            ):
                parts.append(c)
        chosen.append(question)
        if any(p.points is not None for p in parts):
            chosen.extend(parts)
    return chosen


def _read(pdf: bytes) -> tuple[list[list[_Line]], list[TemplatePage]]:
    with PDF_LOCK, pdf_document(pdf) as document:
        page_lines = [_lines(page) for page in document]
        pages = [TemplatePage(0, "", i + 1, p.rect.width, p.rect.height) for i, p in enumerate(document)]
    return page_lines, pages


def label_pages(pdf: bytes) -> list[int]:
    return [label.page + 1 for label in _labels(*_read(pdf))]


def find_questions(pdf: bytes) -> FoundTemplate:
    page_lines, pages = _read(pdf)
    questions: list[FoundQuestion] = []
    for index, label in enumerate(_labels(page_lines, pages)):
        if label.part:
            questions[-1].parts.append(FoundPart(index, label.prompt, label.points, label.page + 1))
        else:
            questions.append(FoundQuestion(index, label.prompt, label.points, label.page + 1, []))
    title = max(page_lines[0], key=lambda line: line.size).text[:200] if page_lines[0] else ""
    return FoundTemplate(title, len(pages), any(page_lines), questions)


def _question_boxes(
    db: sqlite3.Connection, page: TemplatePage, found: list[tuple[float, int | None]], footer: float | None
) -> None:
    for index, (top, question_id) in enumerate(found):
        if question_id is None:
            continue
        if index + 1 < len(found):
            bottom = found[index + 1][0]
        elif footer is not None and footer > top:
            bottom = footer
        else:
            bottom = page.height - 50
        _suggest_box(db, page, question_id, None, 0, max(0, top - 6), page.width, bottom - 6)


def _field_boxes(db: sqlite3.Connection, page: TemplatePage, lines: list[_Line]) -> None:
    for line in lines:
        match = FIELD.match(line.text)
        if match is None:
            continue
        rect = line.rect
        label_end = rect.x0 + rect.width * match.end() / len(line.text)
        after = min((o.rect.y0 for o in lines if o.rect.y0 > rect.y1 + 5), default=rect.y1 + 35)
        _suggest_box(
            db,
            page,
            None,
            "name" if match[1] else "sid",
            label_end + 8,
            max(0, rect.y0 - 22),
            page.width - 52,
            min(page.height, after - 6),
        )


def _suggest(
    db: sqlite3.Connection,
    page_lines: list[list[_Line]],
    footers: list[float | None],
    pages: list[TemplatePage],
    questions: list[sqlite3.Row],
) -> None:
    next_question = 0
    for lines, footer, info in zip(page_lines, footers, pages, strict=True):
        found: list[tuple[float, int | None]] = []
        for line in lines:
            match = LABEL.match(line.text)
            if match is None or line.rect.x0 > info.width / 3:
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
                points = _printed_points(line.text)[0]
                points_score = 0.5 if points == q["points"] else 0
                candidates.append((number_score + similarity + points_score, -index, q))
            if not candidates:
                break
            score, negative_index, q = max(candidates, key=lambda c: (c[0], c[1]))
            if score < 2:
                continue
            next_question = -negative_index + 1
            found.append((line.rect.y0, None if q["kind"] == "parts" else q["id"]))
        _question_boxes(db, info, found, footer)
        _field_boxes(db, info, lines)


def _suggest_from_labels(
    db: sqlite3.Connection,
    page_lines: list[list[_Line]],
    footers: list[float | None],
    pages: list[TemplatePage],
    questions: list[sqlite3.Row],
    labels: dict[int, int],
) -> None:
    found_labels = _labels(page_lines, pages)
    if any(not 0 <= index < len(found_labels) for index in labels):
        raise UserError("These questions don't match the PDF. Upload the PDF again.")
    kinds = {q["id"]: q["kind"] for q in questions}
    for index, (lines, footer, info) in enumerate(zip(page_lines, footers, pages, strict=True)):
        found: list[tuple[float, int | None]] = sorted(
            (
                (found_labels[label].rect.y0, None if kinds[question] == "parts" else question)
                for label, question in labels.items()
                if found_labels[label].page == index
            ),
            key=lambda f: f[0],
        )
        _question_boxes(db, info, found, footer)
        _field_boxes(db, info, lines)


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
    home: Home,
    db: sqlite3.Connection,
    assignment_id: int,
    version: str,
    pdf: bytes,
    labels: dict[int, int] | None = None,
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
            page_lines = [_lines(page) for page in document]
        footers = _footers(page_lines, pages)
        if labels is None:
            _suggest(db, page_lines, footers, pages, questions)
        else:
            _suggest_from_labels(db, page_lines, footers, pages, questions, labels)
    return pages


def outline(db: sqlite3.Connection, assignment_id: int) -> Outline:
    with read_snapshot(db):
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

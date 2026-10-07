from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from rubricate import courses
from rubricate.courses import Student
from rubricate.errors import NotFound, UserError
from rubricate.home import Home, transaction

if TYPE_CHECKING:
    from rubricate.scans import Progress

AUTO_MATCH_SCORE, AUTO_MATCH_MARGIN = 0.8, 0.3


@dataclass(frozen=True)
class Candidate:
    sid: str
    name: str
    score: float


@dataclass(frozen=True)
class NameRow:
    submission: int
    student: Student | None
    suggested: Candidate | None
    candidates: list[Candidate]
    name_read: str
    sid_read: str
    automatic: bool
    names_revision: int


_ocr: Any = None
_ocr_lock = Lock()


def _read(path: Path | None) -> str:
    global _ocr
    if path is None:
        return ""
    with _ocr_lock:
        if _ocr is None:
            import onnxruntime

            onnxruntime.disable_telemetry_events()
            from rapidocr import RapidOCR

            _ocr = RapidOCR(
                params={
                    "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                }
            )
        result = _ocr(str(path))
        return " ".join(result.txts) if result.txts else ""


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def _score(student: Student, name_read: str, sid_read: str) -> float:
    best = 0.0
    normalized_name = _norm(student.name)
    last = normalized_name.split()[-1] if normalized_name else ""
    for name, sid in ((name_read, sid_read), (sid_read, name_read)):
        digits = re.sub(r"\D", "", sid)
        sid_score = SequenceMatcher(None, digits, student.sid).ratio() if len(digits) >= 4 else 0.0
        name_score = (
            SequenceMatcher(None, _norm(name), normalized_name).ratio()
            if re.search(r"[A-Za-z]", name)
            else 0.0
        )
        if last and re.search(rf"(?<!\w){re.escape(last)}(?!\w)", _norm(name)):
            name_score = max(name_score, 0.8)
        best = max(best, 0.6 * sid_score + 0.4 * name_score)
    return best


def _key(db: sqlite3.Connection, submission_id: int) -> str:
    return json.dumps(
        [
            tuple(row)
            for row in db.execute(
                """
                    SELECT p.id, p.template_page, p.homography, b.field, b.x0, b.y0, b.x1, b.y1
                    FROM submission_page sp
                    JOIN scan_page p ON p.id=sp.scan_page
                    JOIN box b ON b.template_page=p.template_page
                    WHERE sp.submission=? AND NOT p.extra AND b.field IN ('name', 'sid')
                    ORDER BY b.field, sp.position, p.id, b.position, b.id
                """,
                (submission_id,),
            )
        ]
    )


def match_names(home: Home, db: sqlite3.Connection, assignment_id: int, progress: Progress) -> None:
    from rubricate.scans import field_crop

    assignment = db.execute("SELECT course FROM assignment WHERE id=?", (assignment_id,)).fetchone()
    if assignment is None:
        raise NotFound("Assignment not found.")
    with transaction(db):
        invalidate(
            db,
            {
                r[0]
                for r in db.execute(
                    "SELECT id FROM submission WHERE assignment=? AND matched_by='auto'", (assignment_id,)
                )
            },
            only_changed=True,
        )
    students = [s for s in courses.roster(db, assignment[0]) if not s.dropped]
    rows = db.execute(
        """
            SELECT *
            FROM submission
            WHERE assignment=? AND student IS NULL
            ORDER BY id
        """,
        (assignment_id,),
    ).fetchall()
    progress(0, len(rows), "Reading names and IDs.")
    for index, row in enumerate(rows):
        key = _key(db, row["id"])
        if row["names_key"] != key:
            name_path = field_crop(home, db, row["id"], "name")
            sid_path = field_crop(home, db, row["id"], "sid")
            name_read = _read(name_path) if students else ""
            sid_read = _read(sid_path) if students else ""
            with transaction(db):
                db.execute(
                    """
                        UPDATE submission
                        SET name_read=?, sid_read=?, names_key=?, names_read=1
                        WHERE id=? AND student IS NULL AND names_revision=?
                    """,
                    (name_read, sid_read, key if students else None, row["id"], row["names_revision"]),
                )
        progress(index + 1, len(rows), f"Read names and IDs for {index + 1} of {len(rows)} submissions.")
    with transaction(db):
        previous = {
            r["id"]: r["student"]
            for r in db.execute(
                "SELECT id, student FROM submission WHERE assignment=? AND matched_by='auto'",
                (assignment_id,),
            )
        }
        db.execute(
            "UPDATE submission SET student=NULL, matched_by=NULL WHERE assignment=? AND matched_by='auto'",
            (assignment_id,),
        )
        taken = {
            r[0]
            for r in db.execute(
                "SELECT student FROM submission WHERE assignment=? AND student IS NOT NULL", (assignment_id,)
            )
        }
        students = [s for s in courses.roster(db, assignment[0]) if not s.dropped]
        ranked: dict[int, list[Candidate]] = {}
        person_cleared: set[int] = set()
        for row in db.execute(
            """
                SELECT *
                FROM submission
                WHERE assignment=? AND student IS NULL AND names_read=1
                ORDER BY id
            """,
            (assignment_id,),
        ):
            if row["matched_by"] == "person":
                person_cleared.add(row["id"])
            ranked[row["id"]] = sorted(
                [
                    Candidate(s.sid, s.name, _score(s, row["name_read"] or "", row["sid_read"] or ""))
                    for s in students
                    if s.sid not in taken
                ],
                key=lambda c: (c.score, c.name, c.sid),
                reverse=True,
            )
        order = sorted(
            ranked, key=lambda sid: (ranked[sid][0].score if ranked[sid] else 0, sid), reverse=True
        )
        confirmed = taken.copy()
        matches: dict[int, tuple[Candidate | None, float, bool]] = {}
        for submission_id in order:
            candidates = ranked[submission_id]
            available = [c for c in candidates if c.sid not in taken]
            choice = available[0] if available else None
            runner = available[1].score if len(available) > 1 else 0
            suggested = choice if choice and choice.score - runner >= 0.15 else None
            rival = next((c.score for c in candidates if c.sid not in confirmed and c != choice), 0)
            automatic = (
                choice is not None
                and choice.score >= AUTO_MATCH_SCORE
                and choice.score - rival >= AUTO_MATCH_MARGIN
                and submission_id not in person_cleared
            )
            if suggested:
                taken.add(suggested.sid)
                if automatic:
                    confirmed.add(suggested.sid)
            matches[submission_id] = (suggested, rival, automatic)
        for submission_id, (suggested, rival, automatic) in matches.items():
            matched = suggested if automatic else None
            candidates = [
                c
                for c in ranked[submission_id]
                if c.sid not in confirmed or (matched and c.sid == matched.sid)
            ]
            db.execute(
                """
                    UPDATE submission
                    SET student=?, matched_by=?, suggested=?, suggested_score=?, candidates=?
                    WHERE id=? AND student IS NULL
                """,
                (
                    matched.sid if matched else None,
                    "auto" if matched else "person" if submission_id in person_cleared else None,
                    suggested.sid if suggested and not matched else None,
                    suggested.score if suggested and not matched else None,
                    json.dumps([asdict(c) for c in candidates[:3]], ensure_ascii=False),
                    submission_id,
                ),
            )
            if matched and previous.get(submission_id) != matched.sid:
                db.execute(
                    "INSERT INTO event (actor, kind, data) VALUES (?, ?, ?)",
                    (
                        "rubricate",
                        "name_matched_automatically",
                        json.dumps(
                            {
                                "submission": submission_id,
                                "sid": matched.sid,
                                "score": matched.score,
                                "runner_up_score": rival,
                            }
                        ),
                    ),
                )


def names(db: sqlite3.Connection, assignment_id: int) -> list[NameRow]:
    assignment = db.execute("SELECT course FROM assignment WHERE id=?", (assignment_id,)).fetchone()
    if assignment is None:
        raise NotFound("Assignment not found.")
    students = {s.sid: s for s in courses.roster(db, assignment[0])}
    result = []
    for row in db.execute("SELECT * FROM submission WHERE assignment=? ORDER BY id", (assignment_id,)):
        suggested = students.get(row["suggested"])
        result.append(
            NameRow(
                row["id"],
                students.get(row["student"]),
                Candidate(suggested.sid, suggested.name, row["suggested_score"]) if suggested else None,
                [Candidate(**c) for c in json.loads(row["candidates"]) if c["sid"] in students],
                row["name_read"] or "",
                row["sid_read"] or "",
                row["student"] is not None and row["matched_by"] == "auto",
                row["names_revision"],
            )
        )
    return result


def confirm(db: sqlite3.Connection, submission_id: int, sid: str | None) -> None:
    with transaction(db):
        row = db.execute(
            """
                SELECT s.assignment, a.course
                FROM submission s
                JOIN assignment a ON a.id=s.assignment
                WHERE s.id=?
            """,
            (submission_id,),
        ).fetchone()
        if row is None:
            raise NotFound("Submission not found.")
        if sid is not None:
            if (
                db.execute("SELECT 1 FROM student WHERE course=? AND sid=?", (row["course"], sid)).fetchone()
                is None
            ):
                raise UserError("That sid is not on the course roster.")
            if db.execute(
                "SELECT 1 FROM submission WHERE assignment=? AND student=? AND id != ?",
                (row["assignment"], sid, submission_id),
            ).fetchone():
                raise UserError("That student is already matched to another submission in this assignment.")
        db.execute(
            """
                UPDATE submission
                SET student=?, matched_by='person', suggested=NULL, suggested_score=NULL
                WHERE id=?
            """,
            (sid, submission_id),
        )
        if sid is not None:
            db.execute(
                """
                    UPDATE submission
                    SET suggested=NULL, suggested_score=NULL
                    WHERE assignment=? AND suggested=?
                """,
                (row["assignment"], sid),
            )


def invalidate(db: sqlite3.Connection, submission_ids: set[int], *, only_changed: bool = False) -> None:
    if only_changed:
        submission_ids = {
            submission_id
            for submission_id in submission_ids
            if (row := db.execute("SELECT names_key FROM submission WHERE id=?", (submission_id,)).fetchone())
            is not None
            and row["names_key"] != _key(db, submission_id)
        }
    db.executemany(
        """
        UPDATE submission
        SET suggested=NULL, suggested_score=NULL, candidates='[]', names_key=NULL,
            name_read=NULL, sid_read=NULL, names_read=0, names_revision=names_revision + 1,
            student=NULL, matched_by=CASE WHEN matched_by='auto' THEN NULL ELSE matched_by END
        WHERE id=? AND (student IS NULL OR matched_by='auto')
        """,
        [(submission_id,) for submission_id in submission_ids],
    )

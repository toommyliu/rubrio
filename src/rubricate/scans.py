import json
import multiprocessing
import os
import sqlite3
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from rubricate import _alignment, grading, names
from rubricate._grouping import segment
from rubricate._images import DPI, PDF_LOCK, cache_path, pdf_document, read_image, rendered, write_image
from rubricate.errors import NotFound, UserError
from rubricate.home import Home, transaction

Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class IngestReport:
    scan: int
    already_uploaded: bool
    pages: int
    matched: int
    extra: int
    submissions: int
    flags: int


@dataclass(frozen=True)
class Flag:
    kind: Literal["missing_page", "repeated_page", "out_of_order", "mixed_versions", "extra_page"]
    scan_pages: list[int]
    message: str


@dataclass(frozen=True)
class ScanPage:
    id: int
    scan: int
    scan_name: str
    page_index: int
    version: str | None
    page: int | None
    extra: bool
    by_hand: bool


@dataclass(frozen=True)
class SubmissionInfo:
    id: int
    version: str | None
    student: str | None
    student_name: str | None
    pages: list[ScanPage]
    flags: list[Flag]
    grades: int


@dataclass(frozen=True)
class ScanInfo:
    id: int
    name: str
    pages: int


@dataclass(frozen=True)
class ScansOverview:
    scans: list[ScanInfo]
    submissions: list[SubmissionInfo]
    unassigned: list[ScanPage]


def _version(pages: list[ScanPage]) -> str | None:
    counts = Counter(p.version for p in pages if p.version is not None and not p.extra)
    return max(counts, key=lambda v: counts[v]) if counts else None


def _pages(db: sqlite3.Connection, assignment_id: int) -> tuple[dict[int, list[ScanPage]], list[ScanPage]]:
    grouped: dict[int, list[ScanPage]] = {}
    unassigned = []
    for row in db.execute(
        """
            SELECT p.*, s.name, sp.submission, t.version, t.page
            FROM scan_page p
            JOIN scan s ON s.id=p.scan
            LEFT JOIN submission_page sp ON sp.scan_page=p.id
            LEFT JOIN template_page t ON t.id=p.template_page
            WHERE s.assignment=?
            ORDER BY sp.submission, sp.position, s.id, p.page_index
        """,
        (assignment_id,),
    ):
        page = ScanPage(
            row["id"],
            row["scan"],
            row["name"],
            row["page_index"],
            row["version"],
            row["page"],
            bool(row["extra"]),
            bool(row["by_hand"]),
        )
        if row["submission"] is None:
            unassigned.append(page)
        else:
            grouped.setdefault(row["submission"], []).append(page)
    return grouped, unassigned


def _flags(pages: list[ScanPage], counts: dict[str, int]) -> list[Flag]:
    matched = [p for p in pages if not p.extra and p.page is not None]
    printed = [p.page for p in matched if p.page is not None]
    ids = [p.id for p in matched]
    flags = []
    version = _version(pages)
    if version is not None:
        for missing in sorted(set(range(1, counts[version] + 1)) - set(printed)):
            flags.append(Flag("missing_page", ids, f"Page {missing} is missing."))
    if len(printed) != len(set(printed)):
        flags.append(
            Flag(
                "repeated_page",
                ids,
                "A printed page appears more than once. Check which pages belong together.",
            )
        )
    if printed != sorted(printed):
        flags.append(Flag("out_of_order", ids, "These pages are out of order."))
    if len({p.version for p in matched}) > 1:
        flags.append(Flag("mixed_versions", ids, "These pages belong to different versions."))
    for p in pages:
        if p.version is None and not p.by_hand:
            flags.append(
                Flag(
                    "extra_page",
                    [p.id],
                    "This page matches no template. Move it or mark it as an extra page.",
                )
            )
    return flags


def overview(db: sqlite3.Connection, assignment_id: int) -> ScansOverview:
    grouped, unassigned = _pages(db, assignment_id)
    counts = dict(
        db.execute(
            """
                SELECT version, count(*)
                FROM template_page
                WHERE assignment=?
                GROUP BY version
            """,
            (assignment_id,),
        ).fetchall()
    )
    submissions = []
    for row in db.execute(
        """
            SELECT s.*, t.name, (SELECT count(*) FROM grade WHERE submission=s.id) AS grades
            FROM submission s
            JOIN assignment a ON a.id=s.assignment
            LEFT JOIN student t ON t.course=a.course AND t.sid=s.student
            WHERE s.assignment=?
            ORDER BY s.id
        """,
        (assignment_id,),
    ):
        pages = grouped.get(row["id"], [])
        submissions.append(
            SubmissionInfo(
                row["id"],
                _version(pages),
                row["student"],
                row["name"],
                pages,
                _flags(pages, counts),
                row["grades"],
            )
        )
    return ScansOverview(
        [
            ScanInfo(r["id"], r["name"], r["pages"])
            for r in db.execute("SELECT * FROM scan WHERE assignment=? ORDER BY id", (assignment_id,))
        ],
        submissions,
        unassigned,
    )


def _refresh(
    db: sqlite3.Connection, assignment_id: int, changed: set[int] | None = None, *, confirm: bool = True
) -> None:
    grouped, _ = _pages(db, assignment_id)
    if changed:
        names.invalidate(db, changed, only_changed=True)
    submission_ids = [
        row[0] for row in db.execute("SELECT id FROM submission WHERE assignment=?", (assignment_id,))
    ]
    grading.remove_submissions(db, [sid for sid in submission_ids if sid not in grouped], confirm)
    for sid in submission_ids:
        if sid in grouped:
            db.execute("UPDATE submission SET version=? WHERE id=?", (_version(grouped[sid]), sid))


def _report(db: sqlite3.Connection, assignment_id: int, scan_id: int, already: bool) -> IngestReport:
    state = overview(db, assignment_id)
    pages = [p for s in state.submissions for p in s.pages if p.scan == scan_id] + [
        p for p in state.unassigned if p.scan == scan_id
    ]
    submissions = [s for s in state.submissions if any(p.scan == scan_id for p in s.pages)]
    matched = sum(p.version is not None for p in pages)
    return IngestReport(
        scan_id,
        already,
        len(pages),
        matched,
        len(pages) - matched,
        len(submissions),
        sum(len(s.flags) for s in submissions)
        + sum(not p.by_hand for p in state.unassigned if p.scan == scan_id),
    )


def ingest(
    home: Home,
    db: sqlite3.Connection,
    assignment_id: int,
    pdf: bytes,
    filename: str,
    progress: Progress,
    workers: int | None = None,
) -> IngestReport:
    if workers is not None and workers < 1:
        raise UserError("Workers must be at least 1.")
    if db.execute("SELECT 1 FROM assignment WHERE id=?", (assignment_id,)).fetchone() is None:
        raise NotFound("Assignment not found.")
    file = home.store(pdf, ".pdf")
    existing = db.execute(
        "SELECT id FROM scan WHERE assignment=? AND file=?", (assignment_id, file)
    ).fetchone()
    if existing:
        names.match_names(home, db, assignment_id, progress)
        return _report(db, assignment_id, existing[0], True)
    templates = db.execute(
        "SELECT * FROM template_page WHERE assignment=? ORDER BY version, page", (assignment_id,)
    ).fetchall()
    versions = {
        r[0] for r in db.execute("SELECT DISTINCT version FROM question WHERE assignment=?", (assignment_id,))
    }
    if versions != {t["version"] for t in templates}:
        raise UserError("Upload a template for every version before uploading scans.")
    with PDF_LOCK, pdf_document(pdf) as document:
        page_count = len(document)
    progress(0, page_count, "Preparing templates.")
    features = [(t["id"], str(_alignment.template_features(home, t["file"], t["page"]))) for t in templates]
    signature = [(t["id"], t["file"], t["page"]) for t in templates]
    jobs = [
        (
            str(home.path),
            file,
            index,
            str(cache_path(home, "alignment", [file, index, signature, DPI, 4000, 0.7, 4, 60, 1.5], ".json")),
        )
        for index in range(page_count)
    ]
    alignments = []
    with ProcessPoolExecutor(
        max_workers=min(workers or os.cpu_count() or 1, page_count),
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_alignment.initialize,
        initargs=(features,),
    ) as pool:
        for alignment in pool.map(_alignment.match_page, jobs):
            alignments.append(alignment)
            progress(len(alignments), page_count, f"Matched page {len(alignments)} of {page_count}.")
    by_id = {t["id"]: t for t in templates}
    counts = dict(Counter(t["version"] for t in templates))
    with transaction(db):
        current = [
            (r["id"], r["file"], r["page"])
            for r in db.execute(
                "SELECT * FROM template_page WHERE assignment=? ORDER BY version, page", (assignment_id,)
            )
        ]
        if current != signature:
            raise UserError("Templates changed while the scan was processing. Upload the scan again.")
        existing = db.execute(
            "SELECT id FROM scan WHERE assignment=? AND file=?", (assignment_id, file)
        ).fetchone()
        if existing:
            scan_id = existing[0]
        else:
            scan_id = db.execute(
                """
                    INSERT INTO scan (assignment, file, name, pages)
                    VALUES (?, ?, ?, ?)
                    RETURNING id
                """,
                (assignment_id, file, filename, page_count),
            ).fetchone()[0]
            pages = []
            for alignment in alignments:
                row = db.execute(
                    """
                        INSERT INTO scan_page (scan, page_index, template_page, homography, inliers,
                            runner_up, extra)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        RETURNING id
                    """,
                    (
                        scan_id,
                        alignment.page_index,
                        alignment.template_page,
                        json.dumps(alignment.homography) if alignment.homography is not None else None,
                        alignment.inliers,
                        alignment.runner_up,
                        alignment.template_page is None,
                    ),
                ).fetchone()
                t = by_id.get(alignment.template_page)
                pages.append((row[0], t["version"] if t else None, t["page"] if t else None))
            for group in segment(pages, counts):
                submission_id = db.execute(
                    "INSERT INTO submission (assignment) VALUES (?) RETURNING id", (assignment_id,)
                ).fetchone()[0]
                db.executemany(
                    """
                        INSERT INTO submission_page (scan_page, submission, template_page, position)
                        SELECT id, ?, template_page, ?
                        FROM scan_page
                        WHERE id=?
                    """,
                    [(submission_id, position, sid) for position, sid in enumerate(group)],
                )
            _refresh(db, assignment_id)
    names.match_names(home, db, assignment_id, progress)
    return _report(db, assignment_id, scan_id, bool(existing))


def scan_page_image(home: Home, db: sqlite3.Connection, scan_page_id: int) -> Path:
    row = db.execute(
        """
            SELECT p.*, s.file, t.width, t.height, t.file AS template_file, t.page AS template_index
            FROM scan_page p
            JOIN scan s ON s.id=p.scan
            LEFT JOIN template_page t ON t.id=p.template_page
            WHERE p.id=?
        """,
        (scan_page_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Scan page not found.")
    source = rendered(home, row["file"], row["page_index"])
    if row["homography"] is None:
        return source
    path = cache_path(
        home,
        "upright",
        [
            row["file"],
            row["page_index"],
            row["template_file"],
            row["template_index"],
            row["width"],
            row["height"],
            row["homography"],
            DPI,
        ],
    )
    if not path.exists():
        image = read_image(source)
        template_image = read_image(rendered(home, row["template_file"], row["template_index"] - 1))
        height, width = template_image.shape
        upright = cv2.warpPerspective(
            image,
            np.linalg.inv(np.asarray(json.loads(row["homography"]))),
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderValue=255,
        )
        write_image(path, upright)
    return path


def _submission(db: sqlite3.Connection, submission_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM submission WHERE id=?", (submission_id,)).fetchone()
    if row is None:
        raise NotFound("Submission not found.")
    return row


def _page_ids(db: sqlite3.Connection, submission_id: int) -> list[int]:
    return [
        row[0]
        for row in db.execute(
            """
            SELECT scan_page FROM submission_page
            WHERE submission=? ORDER BY position, scan_page
            """,
            (submission_id,),
        )
    ]


def reorder(db: sqlite3.Connection, submission_id: int, scan_page_ids: list[int]) -> None:
    with transaction(db):
        submission = _submission(db, submission_id)
        current = _page_ids(db, submission_id)
        if len(scan_page_ids) != len(current) or set(scan_page_ids) != set(current):
            raise UserError("Include every page in this submission exactly once.")
        if current == scan_page_ids:
            return
        moved = {page_id for position, page_id in enumerate(scan_page_ids) if current[position] != page_id}
        db.executemany(
            """
            UPDATE submission_page
            SET position=?, by_hand=CASE WHEN ? THEN 1 ELSE by_hand END
            WHERE scan_page=?
            """,
            [(position, page_id in moved, page_id) for position, page_id in enumerate(scan_page_ids)],
        )
        db.executemany("UPDATE scan_page SET by_hand=1 WHERE id=?", [(page_id,) for page_id in moved])
        _refresh(db, submission["assignment"], {submission_id})


def move_page(
    db: sqlite3.Connection,
    scan_page_id: int,
    submission_id: int | None,
    position: int | None = None,
    confirm: bool = False,
) -> None:
    if position is not None and position < 0:
        raise UserError("Page position must be at least 0.")
    with transaction(db):
        page = db.execute(
            """
                SELECT p.*, s.assignment
                FROM scan_page p
                JOIN scan s ON s.id=p.scan
                WHERE p.id=?
            """,
            (scan_page_id,),
        ).fetchone()
        if page is None:
            raise NotFound("Scan page not found.")
        previous = db.execute(
            "SELECT submission FROM submission_page WHERE scan_page=?", (scan_page_id,)
        ).fetchone()
        changed = {previous[0]} if previous else set()
        if submission_id is None:
            if previous and page["by_hand"] and _page_ids(db, previous[0]) == [scan_page_id]:
                return
            submission_id = int(
                db.execute(
                    "INSERT INTO submission (assignment) VALUES (?) RETURNING id", (page["assignment"],)
                ).fetchone()[0]
            )
        elif _submission(db, submission_id)["assignment"] != page["assignment"]:
            raise UserError("Move the page to a submission in this assignment.")
        ordered = [page_id for page_id in _page_ids(db, submission_id) if page_id != scan_page_id]
        position = len(ordered) if position is None else min(position, len(ordered))
        ordered.insert(position, scan_page_id)
        if previous and previous[0] == submission_id:
            reorder(db, submission_id, ordered)
            return
        db.execute(
            """
                INSERT INTO submission_page (scan_page, submission, template_page, position, by_hand)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(scan_page)
                DO UPDATE SET submission=excluded.submission, position=excluded.position, by_hand=1
            """,
            (scan_page_id, submission_id, page["template_page"], position),
        )
        db.execute("UPDATE scan_page SET by_hand=1 WHERE id=?", (scan_page_id,))
        db.executemany(
            "UPDATE submission_page SET position=? WHERE scan_page=?",
            [(index, page_id) for index, page_id in enumerate(ordered)],
        )
        if previous:
            db.executemany(
                "UPDATE submission_page SET position=? WHERE scan_page=?",
                [(index, page_id) for index, page_id in enumerate(_page_ids(db, previous[0]))],
            )
        changed.add(submission_id)
        _refresh(db, page["assignment"], changed, confirm=confirm)


def _require_no_active_job(db: sqlite3.Connection, assignment_id: int) -> None:
    if db.execute(
        "SELECT 1 FROM job WHERE assignment=? AND state IN ('queued', 'running')",
        (assignment_id,),
    ).fetchone():
        raise UserError(
            "Scans are still being processed for this assignment. Wait for that to finish, then delete it."
        )


def delete_scan(db: sqlite3.Connection, scan_id: int) -> None:
    with transaction(db):
        scan = db.execute("SELECT * FROM scan WHERE id=?", (scan_id,)).fetchone()
        if scan is None:
            raise NotFound("Scan not found.")
        _require_no_active_job(db, scan["assignment"])
        changed = {
            row[0]
            for row in db.execute(
                """
                SELECT DISTINCT sp.submission FROM submission_page sp
                JOIN scan_page p ON p.id=sp.scan_page WHERE p.scan=?
                """,
                (scan_id,),
            )
        }
        db.execute("DELETE FROM scan WHERE id=?", (scan_id,))
        removed = []
        for submission_id in sorted(changed):
            pages = _page_ids(db, submission_id)
            if not pages:
                removed.append(submission_id)
            db.executemany(
                "UPDATE submission_page SET position=? WHERE scan_page=?",
                [(position, page_id) for position, page_id in enumerate(pages)],
            )
        _refresh(db, scan["assignment"], changed)
        db.execute(
            "INSERT INTO event (actor, kind, data) VALUES (?, ?, ?)",
            (
                "local",
                "scan_deleted",
                json.dumps(
                    {
                        "assignment": scan["assignment"],
                        "scan": scan_id,
                        "name": scan["name"],
                        "pages": scan["pages"],
                        "submissions": removed,
                    }
                ),
            ),
        )


def remove_submission(db: sqlite3.Connection, submission_id: int) -> None:
    with transaction(db):
        submission = _submission(db, submission_id)
        _require_no_active_job(db, submission["assignment"])
        pages = _page_ids(db, submission_id)
        grades = db.execute("SELECT count(*) FROM grade WHERE submission=?", (submission_id,)).fetchone()[0]
        db.executemany("UPDATE scan_page SET by_hand=1 WHERE id=?", [(page_id,) for page_id in pages])
        grading.remove_submission(db, submission_id)
        db.execute(
            "INSERT INTO event (actor, kind, data) VALUES (?, ?, ?)",
            (
                "local",
                "submission_removed",
                json.dumps(
                    {
                        "assignment": submission["assignment"],
                        "submission": submission_id,
                        "pages": pages,
                        "grades": grades,
                    }
                ),
            ),
        )


def mark_extra(db: sqlite3.Connection, scan_page_id: int, extra: bool) -> None:
    with transaction(db):
        row = db.execute(
            """
                SELECT s.assignment
                FROM scan_page p
                JOIN scan s ON s.id=p.scan
                WHERE p.id=?
            """,
            (scan_page_id,),
        ).fetchone()
        if row is None:
            raise NotFound("Scan page not found.")
        db.execute("UPDATE scan_page SET extra=?, by_hand=1 WHERE id=?", (extra, scan_page_id))
        db.execute(
            """
                UPDATE submission_page
                SET by_hand=1, template_page=CASE WHEN ? THEN NULL ELSE (SELECT template_page
                FROM scan_page
                WHERE id=?) END
                WHERE scan_page=?
            """,
            (extra, scan_page_id, scan_page_id),
        )
        owner = db.execute(
            "SELECT submission FROM submission_page WHERE scan_page=?", (scan_page_id,)
        ).fetchone()
        _refresh(db, row[0], {owner[0]} if owner else set())


def split(db: sqlite3.Connection, submission_id: int, first_scan_page_id: int) -> int:
    with transaction(db):
        submission = _submission(db, submission_id)
        pages = _page_ids(db, submission_id)
        if first_scan_page_id not in pages:
            raise UserError("Choose a page in this submission.")
        moved = pages[pages.index(first_scan_page_id) :]
        new_id = db.execute(
            "INSERT INTO submission (assignment) VALUES (?) RETURNING id", (submission["assignment"],)
        ).fetchone()[0]
        for position, page_id in enumerate(moved):
            db.execute(
                """
                UPDATE submission_page SET submission=?, position=?, by_hand=1
                WHERE scan_page=?
                """,
                (new_id, position, page_id),
            )
            db.execute("UPDATE scan_page SET by_hand=1 WHERE id=?", (page_id,))
        _refresh(db, submission["assignment"], {submission_id, new_id})
    return new_id


def merge(db: sqlite3.Connection, submission_ids: list[int], confirm: bool = False) -> int:
    ids = list(dict.fromkeys(submission_ids))
    if len(ids) < 2:
        raise UserError("Choose at least two submissions to merge.")
    with transaction(db):
        submissions = [_submission(db, sid) for sid in ids]
        if len({s["assignment"] for s in submissions}) != 1:
            raise UserError("Merge submissions from the same assignment.")
        students = {s["student"] for s in submissions if s["student"]}
        if len(students) > 1:
            raise UserError("Unmatch the different students before merging their submissions.")
        pages = [page_id for sid in ids for page_id in _page_ids(db, sid)]
        db.executemany(
            "UPDATE scan_page SET by_hand=1 WHERE id=?",
            [(page_id,) for page_id in pages],
        )
        db.executemany(
            """
            UPDATE submission_page SET submission=?, position=?, by_hand=1
            WHERE scan_page=?
            """,
            [(ids[0], position, page_id) for position, page_id in enumerate(pages)],
        )
        _refresh(db, submissions[0]["assignment"], set(ids), confirm=confirm)
        person = next(
            (s["student"] for s in submissions if s["student"] and s["matched_by"] == "person"), None
        )
        if person:
            db.execute("UPDATE submission SET student=?, matched_by='person' WHERE id=?", (person, ids[0]))
    return ids[0]


def _crop(
    home: Home, db: sqlite3.Connection, submission_id: int, question_id: int | None, field: str | None
) -> Path | None:
    _submission(db, submission_id)
    rows = db.execute(
        """
            SELECT p.id AS scan_page, b.*
            FROM submission_page sp
            JOIN scan_page p ON p.id=sp.scan_page
            JOIN box b ON b.template_page=p.template_page
            WHERE sp.submission=? AND NOT p.extra AND ((? IS NOT NULL AND b.question=?) OR (? IS NOT
                NULL AND b.field=?))
            ORDER BY sp.position, p.id, b.position, b.id
        """,
        (submission_id, question_id, question_id, field, field),
    ).fetchall()
    if not rows:
        return None
    sources = [(scan_page_image(home, db, r["scan_page"]), r) for r in rows]
    path = cache_path(
        home, "crop", [(str(source.name), [r[k] for k in ("x0", "y0", "x1", "y1")]) for source, r in sources]
    )
    if not path.exists():
        crops = []
        for source, r in sources:
            image = read_image(source)
            x0, y0, x1, y1 = [int(r[k] * DPI / 72) for k in ("x0", "y0", "x1", "y1")]
            crops.append(image[y0 : max(y0 + 1, y1), x0 : max(x0 + 1, x1)])
        width = max(c.shape[1] for c in crops)
        padded = [
            cv2.copyMakeBorder(c, 0, 0, 0, width - c.shape[1], cv2.BORDER_CONSTANT, value=255) for c in crops
        ]
        write_image(path, np.vstack(padded))
    return path


def crop(home: Home, db: sqlite3.Connection, submission_id: int, question_id: int) -> Path | None:
    return _crop(home, db, submission_id, question_id, None)


def field_crop(
    home: Home, db: sqlite3.Connection, submission_id: int, field: Literal["name", "sid"]
) -> Path | None:
    return _crop(home, db, submission_id, None, field)

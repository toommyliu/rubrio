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


def _refresh(db: sqlite3.Connection, assignment_id: int, changed: set[int] | None = None) -> None:
    grouped, _ = _pages(db, assignment_id)
    if changed:
        names.invalidate(db, changed, only_changed=True)
    for row in db.execute(
        "SELECT id, version FROM submission WHERE assignment=?", (assignment_id,)
    ).fetchall():
        pages = grouped.get(row["id"], [])
        if pages:
            db.execute("UPDATE submission SET version=? WHERE id=?", (_version(pages), row["id"]))
        else:
            grading.remove_submission(db, row["id"])


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

from __future__ import annotations

import json
import sqlite3


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

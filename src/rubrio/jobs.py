import logging
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from rubrio.home import Home
from rubrio.scans import Progress

log = logging.getLogger(__name__)

JobKind = Literal["scan", "names"]
JobState = Literal["queued", "running", "done", "failed"]


@dataclass(frozen=True)
class Job:
    id: int
    kind: JobKind
    state: JobState
    done: int
    total: int
    message: str
    error: str | None


Work = Callable[[sqlite3.Connection, Progress], str]


class Runner:
    def __init__(self, home: Home):
        self.home = home
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rubrio-job")
        db = home.connect()
        try:
            db.execute(
                "UPDATE job SET state = 'failed', error = ? WHERE state IN ('queued', 'running')",
                ("Rubrio stopped before this finished. Run it again. Work it saved is kept.",),
            )
        finally:
            db.close()

    def start(self, db: sqlite3.Connection, assignment_id: int, kind: JobKind, work: Work) -> Job:
        job_id = db.execute(
            "INSERT INTO job (kind, assignment, state) VALUES (?, ?, 'queued')", (kind, assignment_id)
        ).lastrowid
        assert job_id is not None
        self.pool.submit(self._run, job_id, work)
        return get(db, job_id)

    def _run(self, job_id: int, work: Work) -> None:
        db = self.home.connect()
        last = 0.0

        def progress(done: int, total: int, message: str) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last < 0.2 and done < total:
                return
            last = now
            db.execute(
                "UPDATE job SET done = ?, total = ?, message = ? WHERE id = ?", (done, total, message, job_id)
            )

        try:
            db.execute("UPDATE job SET state = 'running' WHERE id = ?", (job_id,))
            message = work(db, progress)
            db.execute(
                "UPDATE job SET state = 'done', done = total, message = ? WHERE id = ?", (message, job_id)
            )
        except Exception as e:
            log.exception("Job %s failed", job_id)
            db.execute("UPDATE job SET state = 'failed', error = ? WHERE id = ?", (str(e) or repr(e), job_id))
        finally:
            db.close()

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def get(db: sqlite3.Connection, job_id: int) -> Job:
    row = db.execute(
        "SELECT id, kind, state, done, total, message, error FROM job WHERE id = ?", (job_id,)
    ).fetchone()
    return Job(**row)


def recent(db: sqlite3.Connection, assignment_id: int) -> list[Job]:
    rows = db.execute(
        """
            SELECT id, kind, state, done, total, message, error
            FROM job
            WHERE assignment = ? AND (state IN ('queued', 'running') OR id IN (
                SELECT id FROM job WHERE assignment = ? AND state IN ('done', 'failed') ORDER BY id DESC LIMIT 10
            ))
            ORDER BY id DESC
        """,
        (assignment_id, assignment_id),
    ).fetchall()
    return [Job(**row) for row in rows]

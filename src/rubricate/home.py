import hashlib
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from rubricate.errors import UserError

MIGRATIONS = [
    """
    CREATE TABLE course (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        term TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE student (
        course INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
        sid TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        section TEXT NOT NULL DEFAULT '',
        dropped INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (course, sid)
    );
    CREATE TABLE assignment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT NOT NULL,
        UNIQUE (course, slug)
    );
    CREATE TABLE question (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment INTEGER NOT NULL REFERENCES assignment(id) ON DELETE CASCADE,
        version TEXT NOT NULL,
        parent INTEGER REFERENCES question(id) ON DELETE CASCADE,
        number TEXT NOT NULL,
        prompt TEXT NOT NULL,
        points REAL NOT NULL,
        bonus INTEGER NOT NULL DEFAULT 0,
        key TEXT NOT NULL DEFAULT '[]',
        kind TEXT NOT NULL CHECK (kind IN ('written', 'choice', 'blank', 'parts')),
        position INTEGER NOT NULL,
        UNIQUE (assignment, version, number)
    );
    CREATE TABLE template_page (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment INTEGER NOT NULL REFERENCES assignment(id) ON DELETE CASCADE,
        version TEXT NOT NULL,
        page INTEGER NOT NULL,
        file TEXT NOT NULL,
        width REAL NOT NULL,
        height REAL NOT NULL,
        UNIQUE (assignment, version, page)
    );
    CREATE TABLE box (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_page INTEGER NOT NULL REFERENCES template_page(id) ON DELETE CASCADE,
        question INTEGER REFERENCES question(id) ON DELETE CASCADE,
        field TEXT CHECK (field IN ('name', 'sid')),
        x0 REAL NOT NULL,
        y0 REAL NOT NULL,
        x1 REAL NOT NULL,
        y1 REAL NOT NULL,
        position INTEGER NOT NULL,
        suggested INTEGER NOT NULL DEFAULT 0,
        CHECK ((question IS NULL) != (field IS NULL))
    );
    CREATE TABLE scan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment INTEGER NOT NULL REFERENCES assignment(id) ON DELETE CASCADE,
        file TEXT NOT NULL,
        name TEXT NOT NULL,
        pages INTEGER NOT NULL,
        UNIQUE (assignment, file)
    );
    CREATE TABLE scan_page (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
        page_index INTEGER NOT NULL,
        template_page INTEGER REFERENCES template_page(id) ON DELETE CASCADE,
        homography TEXT,
        inliers INTEGER NOT NULL,
        runner_up INTEGER NOT NULL,
        extra INTEGER NOT NULL DEFAULT 0,
        by_hand INTEGER NOT NULL DEFAULT 0,
        UNIQUE (scan, page_index)
    );
    CREATE TABLE submission (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment INTEGER NOT NULL REFERENCES assignment(id) ON DELETE CASCADE,
        version TEXT,
        student TEXT,
        matched_by TEXT CHECK (matched_by IN ('auto', 'person')),
        suggested TEXT,
        suggested_score REAL,
        name_read TEXT,
        sid_read TEXT,
        candidates TEXT NOT NULL DEFAULT '[]',
        names_read INTEGER NOT NULL DEFAULT 0,
        names_key TEXT,
        names_revision INTEGER NOT NULL DEFAULT 0
    );
    CREATE UNIQUE INDEX submission_student ON submission(assignment, student) WHERE student IS NOT NULL;
    CREATE TABLE submission_page (
        scan_page INTEGER PRIMARY KEY REFERENCES scan_page(id) ON DELETE CASCADE,
        submission INTEGER NOT NULL REFERENCES submission(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK (position >= 0),
        template_page INTEGER REFERENCES template_page(id) ON DELETE CASCADE,
        by_hand INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX submission_page_submission ON submission_page(submission, position);
    CREATE TABLE rubric_item (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question INTEGER NOT NULL REFERENCES question(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        points REAL NOT NULL,
        position INTEGER NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE grade (
        submission INTEGER NOT NULL REFERENCES submission(id) ON DELETE CASCADE,
        question INTEGER NOT NULL REFERENCES question(id) ON DELETE CASCADE,
        adjustment REAL NOT NULL DEFAULT 0,
        comment TEXT NOT NULL DEFAULT '',
        revision INTEGER NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (submission, question)
    );
    CREATE TABLE applied_item (
        submission INTEGER NOT NULL,
        question INTEGER NOT NULL,
        rubric_item INTEGER NOT NULL REFERENCES rubric_item(id) ON DELETE CASCADE,
        PRIMARY KEY (submission, question, rubric_item),
        FOREIGN KEY (submission, question) REFERENCES grade(submission, question) ON DELETE CASCADE
    );
    CREATE TABLE event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        actor TEXT NOT NULL,
        kind TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE TABLE job (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        assignment INTEGER NOT NULL REFERENCES assignment(id) ON DELETE CASCADE,
        state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'done', 'failed')),
        done INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        error TEXT
    );
    """,
]


@dataclass(frozen=True)
class Home:
    path: Path

    @property
    def db_path(self) -> Path:
        return self.path / "rubricate.db"

    @property
    def files(self) -> Path:
        return self.path / "files"

    @property
    def cache(self) -> Path:
        return self.path / "cache"

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        deadline = time.monotonic() + 30
        while True:
            try:
                db.execute("PRAGMA journal_mode = WAL")
                return db
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode & 0xFF != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                    db.close()
                    raise
                time.sleep(0.01)

    def store(self, data: bytes, suffix: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.files / f"{digest}{suffix}"
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=self.files, delete=False) as stream:
                partial = Path(stream.name)
                stream.write(data)
            partial.replace(path)
        return path.name

    def file(self, name: str) -> Path:
        return self.files / name


def default_path() -> Path:
    return Path(os.environ.get("RUBRICATE_HOME") or Path.home() / "Rubricate")


def _schema(db: sqlite3.Connection) -> dict[tuple[str, ...], tuple[str | int | None, ...]]:
    result = {}
    for kind, name, sql in db.execute("SELECT type, name, sql FROM sqlite_master"):
        result[(kind, name)] = (" ".join((sql or "").split()),)
        if kind == "table":
            for column in db.execute("SELECT * FROM pragma_table_info(?)", (name,)):
                result[("column", name, column[1])] = tuple(column[2:])
    return result


def open_home(path: Path | None = None) -> Home:
    home = Home((path or default_path()).expanduser().resolve())
    home.files.mkdir(parents=True, exist_ok=True)
    home.cache.mkdir(parents=True, exist_ok=True)
    db = home.connect()
    db.autocommit = True
    try:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current > len(MIGRATIONS):
            raise UserError(f"{home.path} is from a newer build of Rubricate. Update Rubricate to open it.")
        with closing(sqlite3.connect(":memory:")) as expected:
            for script in MIGRATIONS[:current]:
                expected.executescript(script)
            expected_schema = _schema(expected)
        actual_schema = _schema(db)
        for entry in sorted(expected_schema.keys() | actual_schema.keys()):
            if entry not in actual_schema:
                difference = f"missing {entry[0]} {'.'.join(entry[1:])}"
            elif entry not in expected_schema:
                difference = f"unexpected {entry[0]} {'.'.join(entry[1:])}"
            elif actual_schema[entry] != expected_schema[entry]:
                difference = f"{entry[0]} {'.'.join(entry[1:])} differs"
            else:
                continue
            raise UserError(
                f"{home.path} is from an older build of Rubricate ({difference}). Move it aside to start fresh."
            )
        for number, script in enumerate(MIGRATIONS[current:], start=current + 1):
            db.executescript(f"{script}; PRAGMA user_version = {number};")
        db.execute("COMMIT")
    finally:
        db.close()
    return home


@contextmanager
def transaction(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    nested = db.in_transaction
    savepoint = "write_" + uuid4().hex
    db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
    try:
        yield db
        db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
    except BaseException:
        if nested:
            db.execute(f"ROLLBACK TO {savepoint}")
            db.execute(f"RELEASE {savepoint}")
        elif db.in_transaction:
            db.execute("ROLLBACK")
        raise

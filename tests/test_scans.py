import csv
import io
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from rubricate import assignment, courses, grading, names, scans, template
from rubricate.errors import NeedsConfirmation, NotFound, StaleRevision, UserError
from rubricate.home import Home, open_home

SAMPLE = Path(__file__).resolve().parents[1] / "samples/cs101-quiz5"
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts/scans"
TRUTH = json.loads((SAMPLE / "ground-truth.json").read_text())["assignments"][0]
VARIANTS = {
    path.parent.name: json.loads(path.read_text())
    for path in sorted((SAMPLE / "variants").glob("*/ground-truth.json"))
}
ROSTER_CSV = (SAMPLE / "roster.csv").read_text()
ROSTER = {
    f"{row['First Name']} {row['Last Name']}": row["SID"] for row in csv.DictReader(io.StringIO(ROSTER_CSV))
}
POSSIBLE = {
    version: sum(q["possible_points"] for q in questions)
    for version, questions in TRUTH["questions_by_version"].items()
}
BASELINE = TRUTH["submissions"][0]
REVERSED = next(
    s
    for s in TRUTH["submissions"]
    if (pages := [p["printed_page"] for p in s["scan"]["pages"] if p["printed_page"]]) != sorted(pages)
)
EXTRA = next(s for s in TRUTH["submissions"] if any(p.get("extra_work") for p in s["scan"]["pages"]))
EXTRA_PAGE = next(p["scan_page"] for p in EXTRA["scan"]["pages"] if p.get("extra_work"))
MARGIN_SUBMISSION, MARGIN_QUESTION = next(
    (
        (s, number.removeprefix("q"))
        for s in TRUTH["submissions"]
        for number, response in s["responses"].items()
        if "right margin" in response.get("notes", "")
    )
)
AUTOMATIC_MATCHES = 6
SUGGESTED = "Grace Park"
ASKED = "Alex Kim"


def test_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    truth = TRUTH
    original_pages = {
        p["scan_page"]: (s["assignment_version"] if p["printed_page"] else None, p["printed_page"])
        for s in truth["submissions"]
        for p in s["scan"]["pages"]
    }
    runs = [("original", SAMPLE), *((tag, SAMPLE / "variants" / tag) for tag in VARIANTS)]
    report = {}
    exact = 0
    for tag, folder in runs:
        home = open_home(tmp_path / tag)
        db = home.connect()
        course = courses.create_course(db, "CS 101", "Fall 2026")
        assert len(courses.import_roster(db, course.id, ROSTER_CSV).added) == len(ROSTER)
        assert {s.name for s in courses.roster(db, course.id)} - {
            s["student"]["name"] for s in truth["submissions"]
        } == set(truth["roster_absent"])
        info = assignment.create(db, course.id, (SAMPLE / "assignment.md").read_text())
        if tag == "original":
            blank_info = assignment.create(
                db,
                course.id,
                "## Fill in the result. (10 points)\n\n9 + 6 = ____\n\nAnswer: 15\n## Complete: ____ (10 points)\n\nAnswer: 32\n",
            )
            assert [(q.prompt, q.kind) for q in grading.questions(db, blank_info.id)] == [
                ("Fill in the result.", "blank"),
                ("Complete: ____", "blank"),
            ]
            assignment.delete(db, blank_info.id)
        assert [(v.name, v.questions, v.points, v.bonus) for v in assignment.check(info.source)] == [
            (version, len(questions), POSSIBLE[version], 0)
            for version, questions in truth["questions_by_version"].items()
        ]
        for version in info.versions:
            template.upload(home, db, info.id, version, (SAMPLE / f"template-v{version}.pdf").read_bytes())
        outline = template.outline(db, info.id)
        questions = [q for q in grading.questions(db, info.id) if q.kind != "parts"]
        assert {
            version: [q.prompt.replace("`", "") for q in questions if q.version == version]
            for version in info.versions
        } == {
            version: [q["prompt"].replace("`", "") for q in expected]
            for version, expected in truth["questions_by_version"].items()
        }
        assert {b.question for b in outline.boxes if b.question is not None} == {q.id for q in questions}
        for version in info.versions:
            page_ids = {p.id for p in outline.pages if p.version == version}
            assert {b.field for b in outline.boxes if b.template_page in page_ids and b.field} == {
                "name",
                "sid",
            }
        started = time.perf_counter()
        result = scans.ingest(
            home,
            db,
            info.id,
            (folder / "submissions.pdf").read_bytes(),
            "submissions.pdf",
            lambda *_: None,
            workers=4,
        )
        elapsed = time.perf_counter() - started
        overview = scans.overview(db, info.id)
        pages = [p for s in overview.submissions for p in s.pages] + overview.unassigned
        page_numbers = {p.id: p.page_index + 1 for p in pages}
        got_pages = {p.page_index + 1: (p.version, p.page) for p in pages}
        gt = truth if tag == "original" else VARIANTS[tag]
        expected_pages = (
            original_pages
            if tag == "original"
            else {
                p["scan_page"]: (p["version"] if p["printed_page"] else None, p["printed_page"])
                for p in gt["scan_pages"]
            }
        )
        assert got_pages == expected_pages
        groups = sorted(sorted(page_numbers[p.id] for p in s.pages) for s in overview.submissions)
        expected_groups = sorted(sorted(s["scan_pages"]) for s in gt["submissions"])
        same = groups == expected_groups
        exact += same
        if not any(f["kind"] in {"interleaved", "mixed_versions"} for f in gt.get("expected_flags", [])):
            assert same, (tag, groups, expected_groups)
        flags = [
            {"kind": f.kind, "scan_pages": [page_numbers[p] for p in f.scan_pages]}
            for s in overview.submissions
            for f in s.flags
        ]
        for expected in gt.get("expected_flags", []):
            kind = expected["kind"]
            affected = set(expected["scan_pages"])
            if kind in {"identity_unknown", "version_unknown"}:
                continue
            if kind == "extra_work":
                assert affected <= {p.page_index + 1 for p in overview.unassigned}
            elif kind == "interleaved":
                assert any(f["kind"] == "repeated_page" and affected <= set(f["scan_pages"]) for f in flags)
            elif kind == "mixed_versions":
                flagged = {p for f in flags for p in f["scan_pages"]}
                assert affected <= flagged
            else:
                assert any(f["kind"] == kind and affected <= set(f["scan_pages"]) for f in flags), (
                    tag,
                    expected,
                    flags,
                )
        rows = names.names(db, info.id)
        if tag == "original":
            questions_before_edit = grading.questions(db, info.id)
            db.execute(
                "UPDATE question SET prompt=prompt || ? WHERE assignment=? AND kind='choice'",
                ("\n\n- [ ] Dijkstra\n- [ ] Merge sort\n- [ ] TCP\n- [ ] Huffman", info.id),
            )
            assert assignment.edit(db, info.id, info.source).has_scans
            assert grading.questions(db, info.id) == questions_before_edit
            expected_names = {tuple(s["scan_pages"]): s["student"]["name"] for s in truth["submissions"]}
            assert sum(r.automatic for r in rows) == AUTOMATIC_MATCHES
            assert {r.student.name for r in rows if r.automatic and r.student} == set(
                expected_names.values()
            ) - {SUGGESTED, ASKED}
            assert [r.suggested.name for r in rows if r.suggested] == [SUGGESTED]
            for submission, row in zip(overview.submissions, rows, strict=True):
                student = expected_names[tuple(sorted(page_numbers[p.id] for p in submission.pages))]
                if row.student:
                    assert row.student.name == student
                    assert row.suggested is None
                elif row.suggested:
                    assert row.suggested.name == student
                else:
                    assert student in [c.name for c in row.candidates]
            automatic_events = db.execute(
                "SELECT * FROM event WHERE kind='name_matched_automatically' ORDER BY id"
            ).fetchall()
            assert len(automatic_events) == AUTOMATIC_MATCHES
            assert {json.loads(e["data"])["sid"] for e in automatic_events} == {
                r.student.sid for r in rows if r.automatic and r.student
            }
            for event in automatic_events:
                data = json.loads(event["data"])
                assert data["score"] >= 0.8
                assert data["score"] - data["runner_up_score"] >= 0.3
                assert data["submission"] in {r.submission for r in rows if r.automatic}
            for question in questions:
                tiles = []
                for submission in overview.submissions:
                    if submission.version != question.version:
                        continue
                    path = scans.crop(home, db, submission.id, question.id)
                    assert path is not None
                    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    assert img is not None
                    if (
                        question.version == MARGIN_SUBMISSION["assignment_version"]
                        and question.number == MARGIN_QUESTION
                        and ([p.page_index + 1 for p in submission.pages] == MARGIN_SUBMISSION["scan_pages"])
                    ):
                        assert np.count_nonzero(img[:, int(560 * 150 / 72) :] < 120) > 20
                    img = cv2.resize(img, (700, max(1, int(img.shape[0] * 700 / img.shape[1]))))
                    label = np.full((28, 700), 255, np.uint8)
                    student = expected_names[tuple(sorted(page_numbers[p.id] for p in submission.pages))]
                    cv2.putText(
                        label,
                        f"{student} {question.version}:{question.number}",
                        (6, 20),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        0,
                        1,
                    )
                    tiles.extend([label, img, np.full((6, 700), 180, np.uint8)])
                cv2.imwrite(
                    str(ARTIFACTS / f"question-{question.version}-{question.number}.png"), np.vstack(tiles)
                )
            again = scans.ingest(
                home,
                db,
                info.id,
                (folder / "submissions.pdf").read_bytes(),
                "again.pdf",
                lambda *_: None,
                workers=4,
            )
            assert again.already_uploaded
            assert scans.overview(db, info.id) == overview
            assert names.names(db, info.id) == rows
            assert (
                db.execute(
                    "SELECT * FROM event WHERE kind='name_matched_automatically' ORDER BY id"
                ).fetchall()
                == automatic_events
            )
            verify_name_fixes(home, db, info.id, monkeypatch)
            overview = scans.overview(db, info.id)
            verify_fixes_and_grades(home, db, info, overview)
            verify_bonus_and_extra_credit(home, db, info, tmp_path)
        report[tag] = {
            "result": asdict(result),
            "pages": [asdict(p) for p in sorted(pages, key=lambda page: page.page_index)],
            "elapsed_seconds": elapsed,
            "seconds_per_page": elapsed / result.pages,
            "exact_grouping": same,
            "groups": groups,
            "flags": flags,
            "names": [asdict(r) for r in rows],
        }
        (ARTIFACTS / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"{tag}: {result.pages} pages, {elapsed:.2f}s, {elapsed / result.pages:.3f}s/page", flush=True)
        if tag == "missing-page-2-marcus":
            out_of_order = next(
                s for s in overview.submissions if any(f.kind == "out_of_order" for f in s.flags)
            )
            ordered = [p.id for p in sorted(out_of_order.pages, key=lambda p: p.page or 0)]
            scans.reorder(db, out_of_order.id, ordered)
            after = scans.overview(db, info.id)
            corrected = next(s for s in after.submissions if s.id == out_of_order.id)
            assert [p.id for p in corrected.pages] == ordered
            assert not any(f.kind == "out_of_order" for f in corrected.flags)
            changes = db.total_changes
            scans.reorder(db, out_of_order.id, ordered)
            assert db.total_changes == changes
            assert scans.overview(db, info.id) == after
        if tag == "original":
            verify_removals(home, db, info.id)
            stored_files = sorted(p.name for p in home.files.iterdir())
            events_before = db.execute("SELECT * FROM event ORDER BY id").fetchall()
            assignment.delete(db, info.id)
            for table in (
                "assignment",
                "question",
                "rubric_item",
                "template_page",
                "box",
                "scan",
                "scan_page",
                "submission",
                "submission_page",
                "grade",
                "applied_item",
                "job",
            ):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            events_after = db.execute("SELECT * FROM event ORDER BY id").fetchall()
            assert events_after[:-1] == events_before
            assert events_after[-1]["kind"] == "assignment_deleted"
            assert json.loads(events_after[-1]["data"]) == {
                "course": info.course,
                "assignment": info.slug,
                "title": info.title,
            }
            assert sorted(p.name for p in home.files.iterdir()) == stored_files
            recreated = assignment.create(db, course.id, info.source)
            assert recreated.slug == info.slug
            assert recreated.id != info.id
            assert (
                db.execute("SELECT count(*) FROM question WHERE assignment=?", (info.id,)).fetchone()[0] == 0
            )
        db.close()
    assert exact == 1 + sum(
        not any(f["kind"] in {"interleaved", "mixed_versions"} for f in variant["expected_flags"])
        for variant in VARIANTS.values()
    )
    old_home = open_home(tmp_path / "old-home")
    old_db = old_home.connect()
    old_db.execute("ALTER TABLE submission DROP COLUMN names_revision")
    old_db.close()
    with pytest.raises(UserError) as old_schema:
        open_home(old_home.path)
    assert str(old_home.path) in old_schema.value.message
    assert "submission.names_revision" in old_schema.value.message
    assert "is from an older build of Rubricate (missing " in old_schema.value.message
    (ARTIFACTS / "old-home-error.txt").write_text(old_schema.value.message)


def verify_removals(home: Home, db: sqlite3.Connection, assignment_id: int) -> None:
    before = scans.overview(db, assignment_id)
    submission = before.submissions[0]
    assert submission.grades == len(TRUTH["questions_by_version"][BASELINE["assignment_version"]])
    scan = before.scans[0]
    matches = names.names(db, assignment_id)
    scores = grading.scores(db, assignment_id)
    with pymupdf.open(SAMPLE / "submissions.pdf") as source, pymupdf.open() as continuation:
        page_index = BASELINE["scan_pages"][1] - 1
        continuation.insert_pdf(source, from_page=page_index, to_page=page_index)
        continuation_pdf = continuation.tobytes()
    added = scans.ingest(
        home, db, assignment_id, continuation_pdf, "continuation.pdf", lambda *_: None, workers=4
    )
    added_page = next(
        p for s in scans.overview(db, assignment_id).submissions for p in s.pages if p.scan == added.scan
    )
    scans.move_page(db, added_page.id, submission.id, position=1)
    scans.delete_scan(db, added.scan)
    assert scans.overview(db, assignment_id) == before
    assert names.names(db, assignment_id) == matches
    assert grading.scores(db, assignment_id) == scores
    stored_files = {p.name: p.read_bytes() for p in home.files.iterdir()}
    for state in ("queued", "running"):
        job_id = db.execute(
            "INSERT INTO job (kind, assignment, state) VALUES ('scan', ?, ?) RETURNING id",
            (assignment_id, state),
        ).fetchone()[0]
        changes = db.total_changes
        for action, target in ((scans.delete_scan, scan.id), (scans.remove_submission, submission.id)):
            with pytest.raises(UserError) as active_job:
                action(db, target)
            assert (
                active_job.value.message
                == "Scans are still being processed for this assignment. Wait for that to finish, then delete it."
            )
        assert db.total_changes == changes
        assert scans.overview(db, assignment_id) == before
        db.execute("DELETE FROM job WHERE id=?", (job_id,))
    scans.remove_submission(db, submission.id)
    removed = scans.overview(db, assignment_id)
    assert removed.submissions == before.submissions[1:]
    assert {p.id for p in removed.unassigned} == {p.id for p in submission.pages}
    assert all(p.by_hand for p in removed.unassigned)
    assert db.execute("SELECT count(*) FROM grade WHERE submission=?", (submission.id,)).fetchone()[0] == 0
    removal_event = json.loads(
        db.execute("SELECT data FROM event WHERE kind='submission_removed' ORDER BY id DESC").fetchone()[0]
    )
    assert removal_event == {
        "assignment": assignment_id,
        "submission": submission.id,
        "pages": [p.id for p in submission.pages],
        "grades": len(TRUTH["questions_by_version"][BASELINE["assignment_version"]]),
    }
    with pytest.raises(NotFound):
        scans.remove_submission(db, submission.id)
    again = scans.ingest(
        home,
        db,
        assignment_id,
        (SAMPLE / "submissions.pdf").read_bytes(),
        "again.pdf",
        lambda *_: None,
        workers=4,
    )
    assert again.already_uploaded
    assert scans.overview(db, assignment_id) == removed
    scans.delete_scan(db, scan.id)
    assert scans.overview(db, assignment_id) == scans.ScansOverview([], [], [])
    assert db.execute("SELECT count(*) FROM grade").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM applied_item").fetchone()[0] == 0
    deletion_event = json.loads(
        db.execute("SELECT data FROM event WHERE kind='scan_deleted' ORDER BY id DESC").fetchone()[0]
    )
    assert deletion_event == {
        "assignment": assignment_id,
        "scan": scan.id,
        "name": scan.name,
        "pages": scan.pages,
        "submissions": [s.id for s in removed.submissions],
    }
    assert {p.name: p.read_bytes() for p in home.files.iterdir()} == stored_files
    with pytest.raises(NotFound):
        scans.delete_scan(db, scan.id)
    reuploaded = scans.ingest(
        home,
        db,
        assignment_id,
        (SAMPLE / "submissions.pdf").read_bytes(),
        "submissions.pdf",
        lambda *_: None,
        workers=4,
    )
    assert not reuploaded.already_uploaded
    restored = scans.overview(db, assignment_id)
    assert len(restored.submissions) == len(TRUTH["submissions"])
    assert all(s.grades == 0 for s in restored.submissions)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    (ARTIFACTS / "removals.json").write_text(
        json.dumps(
            {
                "submission_removed": removal_event,
                "scan_deleted": deletion_event,
                "restored": asdict(restored),
            },
            indent=2,
        )
    )


def verify_name_fixes(
    home: Home, db: sqlite3.Connection, assignment_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = names.names(db, assignment_id)
    overview = scans.overview(db, assignment_id)
    by_name = {match.name: r.submission for r in before if (match := (r.student or r.suggested)) is not None}
    by_id = {s.id: s for s in overview.submissions}
    reversed_submission = by_id[by_name[REVERSED["student"]["name"]]]
    baseline = by_id[by_name[BASELINE["student"]["name"]]]
    extra_submission = by_id[by_name[EXTRA["student"]["name"]]]
    suggested = by_id[by_name[SUGGESTED]]
    reads: list[str] = []
    read = names._read

    def record_read(path: Path | None) -> str:
        if path is not None:
            reads.append(path.name)
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(names, "_read", record_read)
        page_ids = {p.page_index + 1: p.id for p in reversed_submission.pages}
        scans.reorder(
            db,
            reversed_submission.id,
            [
                page_ids[p["scan_page"]]
                for p in sorted(REVERSED["scan"]["pages"], key=lambda p: p["printed_page"])
            ],
        )
        extra = next(p for p in extra_submission.pages if p.page_index + 1 == EXTRA_PAGE)
        scans.mark_extra(db, extra.id, True)
        assert names.names(db, assignment_id) == before
        scans.move_page(db, extra.id, baseline.id, position=1)
        moved = scans.overview(db, assignment_id)
        assert [p.id for s in moved.submissions if s.id == baseline.id for p in s.pages] == [
            baseline.pages[0].id,
            extra.id,
            baseline.pages[1].id,
        ]
        assert names.names(db, assignment_id) == before
        changes = db.total_changes
        scans.move_page(db, extra.id, baseline.id, position=1)
        assert db.total_changes == changes
        assert scans.overview(db, assignment_id) == moved
        with pytest.raises(UserError):
            scans.move_page(db, extra.id, extra_submission.id, position=-1)
        assert db.total_changes == changes
        scans.move_page(db, extra.id, baseline.id, position=100)
        assert [
            p.id
            for s in scans.overview(db, assignment_id).submissions
            if s.id == baseline.id
            for p in s.pages
        ] == [baseline.pages[0].id, baseline.pages[1].id, extra.id]
        scans.move_page(db, extra.id, extra_submission.id)
        restored_pages = scans.overview(db, assignment_id)
        assert [p.id for s in restored_pages.submissions if s.id == extra_submission.id for p in s.pages] == [
            p.id for p in extra_submission.pages
        ]
        assert [p.id for s in restored_pages.submissions if s.id == baseline.id for p in s.pages] == [
            p.id for p in baseline.pages
        ]
        changes = db.total_changes
        scans.move_page(db, extra.id, extra_submission.id)
        assert db.total_changes == changes
        assert scans.overview(db, assignment_id) == restored_pages
        assert names.names(db, assignment_id) == before
        names.match_names(home, db, assignment_id, lambda *_: None)
        assert reads == []
        assert names.names(db, assignment_id) == before
        assert sum(r.automatic for r in before) == AUTOMATIC_MATCHES
        assert sum(r.suggested is not None for r in before) == 1
        alex = next(r for r in before if r.student is None and r.suggested is None)
        assert ASKED in [c.name for c in alex.candidates]
        second_half = scans.split(db, suggested.id, suggested.pages[1].id)
        assert db.execute("SELECT names_read FROM submission WHERE id=?", (second_half,)).fetchone()[0] == 0
        names.match_names(home, db, assignment_id, lambda *_: None)
        split_rows = names.names(db, assignment_id)
        continuation = next(r for r in split_rows if r.submission == second_half)
        assert continuation.suggested is None
        assert (continuation.name_read, continuation.sid_read) == ("", "")
        assert reads == []
        merged = scans.merge(db, [second_half, suggested.id])
        assert merged == second_half
        assert db.execute("SELECT names_read FROM submission WHERE id=?", (merged,)).fetchone()[0] == 0
        assert reads == []
        names.match_names(home, db, assignment_id, lambda *_: None)
        assert reads == [
            path.name
            for field in ("name", "sid")
            if (path := scans.field_crop(home, db, merged, field)) is not None
        ]
        assert len(reads) == 2
        after = names.names(db, assignment_id)
        assert [r for r in after if r.submission != merged] == [
            r for r in before if r.submission != suggested.id
        ]
        suggested_row = next(r for r in after if r.submission == merged)
        assert suggested_row.suggested is not None and suggested_row.suggested.name == SUGGESTED
        scans.reorder(db, merged, [p.id for p in suggested.pages])
        scans.reorder(db, reversed_submission.id, [p.id for p in reversed_submission.pages])
        names.match_names(home, db, assignment_id, lambda *_: None)
        assert len(reads) == 2
        assert names.names(db, assignment_id) == after
        reversed_row = next(r for r in after if r.submission == reversed_submission.id)
        assert reversed_row.student is not None and reversed_row.automatic
        name_page = next(p for p in reversed_submission.pages if p.page == 1)
        scans.mark_extra(db, name_page.id, True)
        pending = next(r for r in names.names(db, assignment_id) if r.submission == reversed_submission.id)
        assert pending.student is None and (not pending.automatic) and (pending.suggested is None)
        assert pending.candidates == []
        names.match_names(home, db, assignment_id, lambda *_: None)
        missing_name = next(
            r for r in names.names(db, assignment_id) if r.submission == reversed_submission.id
        )
        assert missing_name.student is None and missing_name.suggested is None
        assert (missing_name.name_read, missing_name.sid_read) == ("", "")
        assert len(reads) == 2
        scans.mark_extra(db, name_page.id, False)
        names.match_names(home, db, assignment_id, lambda *_: None)
        restored = next(r for r in names.names(db, assignment_id) if r.submission == reversed_submission.id)
        assert restored.student == reversed_row.student and restored.automatic
        assert len(reads) == 4
        names.confirm(db, reversed_submission.id, None)
        names.match_names(home, db, assignment_id, lambda *_: None)
        cleared = next(r for r in names.names(db, assignment_id) if r.submission == reversed_submission.id)
        assert cleared.student is None and (not cleared.automatic)
        assert cleared.suggested is not None and cleared.suggested.name == REVERSED["student"]["name"]
        assert len(reads) == 4
        names.confirm(db, reversed_submission.id, reversed_row.student.sid)
        person = next(r for r in names.names(db, assignment_id) if r.submission == reversed_submission.id)
        assert person.student == reversed_row.student and (not person.automatic)
        scans.mark_extra(db, name_page.id, True)
        names.match_names(home, db, assignment_id, lambda *_: None)
        assert (
            next(r for r in names.names(db, assignment_id) if r.submission == reversed_submission.id)
            == person
        )
        scans.mark_extra(db, name_page.id, False)
        assert len(reads) == 4
        after = names.names(db, assignment_id)
        names.confirm(db, merged, suggested_row.suggested.sid)
        confirmed = next(r for r in names.names(db, assignment_id) if r.submission == merged)
        scans.mark_extra(db, suggested.pages[0].id, True)
        assert next(r for r in names.names(db, assignment_id) if r.submission == merged) == confirmed
        names.match_names(home, db, assignment_id, lambda *_: None)
        assert len(reads) == 4
        assert next(r for r in names.names(db, assignment_id) if r.submission == merged) == confirmed
        assert all(
            r.suggested is None or r.suggested.sid != suggested_row.suggested.sid
            for r in names.names(db, assignment_id)
        )
        scans.mark_extra(db, suggested.pages[0].id, False)
        names.confirm(db, merged, None)
        names.match_names(home, db, assignment_id, lambda *_: None)
        assert len(reads) == 4
        assert names.names(db, assignment_id) == after
        automatic_events = [
            json.loads(r[0])
            for r in db.execute("SELECT data FROM event WHERE kind='name_matched_automatically' ORDER BY id")
        ]
        assert len(automatic_events) == AUTOMATIC_MATCHES + 1
        assert automatic_events[-1]["submission"] == reversed_submission.id
    (ARTIFACTS / "name-fixes.json").write_text(
        json.dumps(
            {
                "before": [asdict(r) for r in before],
                "split": [asdict(r) for r in split_rows],
                "after": [asdict(r) for r in after],
                "read_crops": reads,
                "automatic_events": automatic_events,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def verify_bonus_and_extra_credit(
    home: Home, db: sqlite3.Connection, info: assignment.AssignmentInfo, tmp_path: Path
) -> None:
    bonus_heading = "## Name a sorting algorithm faster than O(n^2). (5 bonus points)"
    next_version = list(TRUTH["questions_by_version"])[1]
    bonus_source = info.source.replace(
        f"# Version {next_version}", f"{bonus_heading}\n\n# Version {next_version}"
    )
    bonus_source += f"\n{bonus_heading}\n"
    source_path = tmp_path / "bonus-assignment.md"
    source_path.write_text(bonus_source)
    assert [(v.name, v.points, v.bonus) for v in assignment.check(source_path.read_text())] == [
        (version, possible, 5) for version, possible in POSSIBLE.items()
    ]
    parts_source = "## Parts\n### First (1 bonus point)\n### Second (4 bonus points)\n"
    assert [(v.points, v.bonus) for v in assignment.check(parts_source)] == [(0, 5)]
    course = courses.get_course(db, info.course)
    bonus_info = assignment.create(db, course.id, parts_source, slug="bonus-points")
    assert [q.bonus for q in grading.questions(db, bonus_info.id)] == [True, True, True]
    mixed_source = parts_source.replace("(4 bonus points)", "(4 points)")
    assignment.edit(db, bonus_info.id, mixed_source)
    assert [(v.points, v.bonus) for v in assignment.check(mixed_source)] == [(4, 1)]
    assert [q.bonus for q in grading.questions(db, bonus_info.id)] == [False, True, False]
    with pytest.raises(assignment.AssignmentFileError) as invalid_parent:
        assignment.check(parts_source.replace("## Parts", "## Parts (5 bonus points)"))
    assert invalid_parent.value.problems[0].line == 1
    assert "part" in invalid_parent.value.problems[0].message
    bonus_info = assignment.edit(db, bonus_info.id, source_path.read_text())
    for version in bonus_info.versions:
        template.upload(home, db, bonus_info.id, version, (SAMPLE / f"template-v{version}.pdf").read_bytes())
    scans.ingest(
        home,
        db,
        bonus_info.id,
        (SAMPLE / "submissions.pdf").read_bytes(),
        "submissions.pdf",
        lambda *_: None,
        workers=4,
    )
    submission = scans.overview(db, bonus_info.id).submissions[0]
    sid = ROSTER[BASELINE["student"]["name"]]
    possible = POSSIBLE[BASELINE["assignment_version"]]
    names.confirm(db, submission.id, sid)
    questions = {
        q.number: q
        for q in grading.questions(db, bonus_info.id)
        if q.version == submission.version and q.kind != "parts"
    }
    bonus_question = next(q for q in questions.values() if q.bonus)
    assert bonus_question.bonus
    assert [(i.description, i.points, i.whole_answer) for i in bonus_question.rubric] == [
        ("Correct", 0, True),
        ("Incorrect", -5, True),
    ]
    assert scans.crop(home, db, submission.id, bonus_question.id) is None
    grades = {}
    for number, question in questions.items():
        correct = next(i for i in question.rubric if i.description == "Correct")
        grades[number] = grading.save_grade(
            db, submission.id, question.id, [correct.id], 0, "Reviewed.", 0, "grader"
        )
        assert grades[number].score == question.points
        assert not grades[number].extra_credit
    scores = grading.scores(db, bonus_info.id)[0]
    assert (scores.total, scores.possible) == (105, possible)
    regular_source = bonus_source.replace("(5 bonus points)", "(5 points)")
    with pytest.raises(NeedsConfirmation) as change:
        assignment.edit(db, bonus_info.id, regular_source)
    assert change.value.affected == 1
    assert grading.scores(db, bonus_info.id)[0] == scores
    assignment.edit(db, bonus_info.id, regular_source, confirm=True)
    assert grading.scores(db, bonus_info.id)[0].possible == 105
    assignment.edit(db, bonus_info.id, bonus_source, confirm=True)
    assert grading.scores(db, bonus_info.id)[0] == scores
    expected_questions = TRUTH["questions_by_version"][BASELINE["assignment_version"]]
    first_number = expected_questions[0]["qa_question_number"]
    question = questions[first_number]
    grade = grades[first_number]
    grade = grading.save_grade(
        db,
        submission.id,
        question.id,
        grade.applied,
        0,
        "Extra credit for explaining the sum.",
        grade.revision,
        "grader",
        score=12,
    )
    assert (grade.score, grade.adjustment, grade.extra_credit) == (12, 2, True)
    assert grading.scores(db, bonus_info.id)[0].total == 107
    for invalid_score in (-1, float("nan"), float("inf"), -float("inf")):
        with pytest.raises(UserError, match=f"A score for question {first_number} must"):
            grading.save_grade(
                db,
                submission.id,
                question.id,
                grade.applied,
                0,
                "",
                grade.revision,
                "grader",
                score=invalid_score,
            )
    assert grading.scores(db, bonus_info.id)[0].total == 107
    part_number = next(
        q["qa_question_number"] for q in expected_questions if not q["qa_question_number"].isdigit()
    )
    positive_question = questions[part_number]
    partial = next(i for i in positive_question.rubric if i.points == 6)
    added = grading.add_item(db, positive_question.id, "Explains repeated multiplication", 7, "grader")
    positive_grade = grades[part_number]
    for score, adjustment in ((10, 0), (12, 2)):
        positive_grade = grading.save_grade(
            db,
            submission.id,
            positive_question.id,
            [partial.id, added.id],
            0,
            "",
            positive_grade.revision,
            "grader",
            score=score,
        )
        assert (positive_grade.score, positive_grade.adjustment) == (score, adjustment)
    grading.save_grade(
        db,
        submission.id,
        positive_question.id,
        grades[part_number].applied,
        0,
        "Reviewed.",
        positive_grade.revision,
        "grader",
    )
    deduction_number = expected_questions[1]["qa_question_number"]
    deduction_question = questions[deduction_number]
    deductions = [
        grading.add_item(db, deduction_question.id, description, points, "grader").id
        for description, points in (("Wrong words", -8), ("Wrong explanation", -7))
    ]
    deduction = grading.save_grade(
        db,
        submission.id,
        deduction_question.id,
        deductions,
        8,
        "Adjustment after deductions.",
        grades[deduction_number].revision,
        "grader",
    )
    assert (deduction.score, deduction.adjustment, deduction.extra_credit) == (8, 8, False)
    for score, adjustment in ((0, 0), (8, 8)):
        deduction = grading.save_grade(
            db,
            submission.id,
            deduction_question.id,
            deductions,
            0,
            "Adjustment after deductions.",
            deduction.revision,
            "grader",
            score=score,
        )
        assert (deduction.score, deduction.adjustment) == (score, adjustment)
    grading.update_item(db, deductions[0], "Wrong words", -9, "grader")
    assert grading.scores(db, bonus_info.id)[0].total == 105
    with pytest.raises(NeedsConfirmation) as change:
        grading.update_item(db, deductions[0], "Wrong words", -2, "grader")
    assert change.value.affected == 1
    scores = grading.scores(db, bonus_info.id)[0]
    assert (scores.total, scores.possible) == (105, possible)
    (ARTIFACTS / "bonus-scores.json").write_text(json.dumps(asdict(scores), indent=2))
    print(f"Bonus and extra credit: 105 / {possible}; typed 12 / 10; over-deduction +8 = 8", flush=True)
    assignment.delete(db, bonus_info.id)


def verify_fixes_and_grades(
    home: Home, db: sqlite3.Connection, info: assignment.AssignmentInfo, overview: scans.ScansOverview
) -> None:
    truth = TRUTH
    questions = grading.questions(db, info.id)
    by_number = {(q.version, q.number): q for q in questions}
    by_pages = {tuple(s["scan_pages"]): s for s in truth["submissions"]}
    course = courses.get_course(db, info.course)
    roster = ROSTER
    assert {s.name: s.sid for s in courses.roster(db, course.id)} == roster
    for submission in overview.submissions:
        assert submission.version is not None
        student = by_pages[tuple(p.page_index + 1 for p in submission.pages)]
        names.confirm(db, submission.id, roster[student["student"]["name"]])
        for number, response in student["responses"].items():
            question = by_number[submission.version, number.removeprefix("q")]
            description = response["expected_grade"]["rubric"].removeprefix("Partially Correct - ")
            item = next(i for i in question.rubric if i.description.replace("`", "") == description)
            grade = grading.save_grade(db, submission.id, question.id, [item.id], 0, "Reviewed.", 0, "grader")
            assert grade.score == response["expected_grade"]["points"]
            assert grade.revision == 1
    original_scores = grading.scores(db, info.id)
    assert [s.total for s in original_scores] == [s["expected_total"] for s in truth["submissions"]]
    first = overview.submissions[0]
    version = BASELINE["assignment_version"]
    expected_question = truth["questions_by_version"][version][0]
    question = by_number[version, expected_question["qa_question_number"]]
    correct = next(i for i in question.rubric if i.description == "Correct")
    with pytest.raises(StaleRevision):
        grading.save_grade(db, first.id, question.id, [correct.id], 0, "Stale", 0, "other grader")
    box = next(b for b in template.outline(db, info.id).boxes if b.question == question.id)
    before_crop = scans.crop(home, db, first.id, question.id)
    assert before_crop is not None
    template.update_box(db, box.id, box.x0, box.y0, box.x1, box.y1 - 12)
    after_crop = scans.crop(home, db, first.id, question.id)
    assert after_crop is not None and after_crop != before_crop
    before_image = cv2.imread(str(before_crop))
    after_image = cv2.imread(str(after_crop))
    assert before_image is not None and after_image is not None
    assert after_image.shape[0] < before_image.shape[0]
    template.update_box(db, box.id, box.x0, box.y0, box.x1, box.y1)
    assert scans.crop(home, db, first.id, question.id) == before_crop
    second_half = scans.split(db, first.id, first.pages[1].id)
    assert scans.merge(db, [first.id, second_half]) == first.id
    assert grading.scores(db, info.id) == original_scores
    extra = next(p for s in overview.submissions for p in s.pages if p.page_index + 1 == EXTRA_PAGE)
    scans.mark_extra(db, extra.id, True)
    assert not any(f.kind == "extra_page" for s in scans.overview(db, info.id).submissions for f in s.flags)
    saved = scans.overview(db, info.id)
    again = scans.ingest(
        home, db, info.id, (SAMPLE / "submissions.pdf").read_bytes(), "again.pdf", lambda *_: None, workers=4
    )
    assert again.already_uploaded
    assert scans.overview(db, info.id) == saved
    assert all(
        home.file(row[0]).read_bytes() == (SAMPLE / "submissions.pdf").read_bytes()
        for row in db.execute("SELECT file FROM scan WHERE assignment=?", (info.id,))
    )
    outline = template.outline(db, info.id)
    continuation = next(
        p
        for p in outline.pages
        if p.version == BASELINE["assignment_version"]
        and p.page == BASELINE["scan"]["pages"][1]["printed_page"]
    )
    added_box = template.add_box(db, continuation.id, question.id, None, 20, 20, 100, 50)
    adjusted = grading.save_grade(
        db, first.id, question.id, [correct.id], -1, "Check the sum. José", 1, "grader"
    )
    template.delete_box(db, added_box.id)
    grading.save_grade(db, first.id, question.id, [correct.id], 0, "Reviewed.", adjusted.revision, "grader")
    with pytest.raises(NeedsConfirmation) as change:
        grading.delete_item(db, correct.id, "grader")
    assert change.value.affected == sum(
        s["responses"][expected_question["id"]]["expected_grade"]["rubric"] == "Correct"
        for s in truth["submissions"]
        if s["assignment_version"] == BASELINE["assignment_version"]
    )
    grading.update_item(db, correct.id, "Fully correct", 0, "grader")
    assert grading.scores(db, info.id) == original_scores
    changed_source = info.source.replace(
        f"## {expected_question['prompt']} ({expected_question['possible_points']} points)",
        f"## {expected_question['prompt']} ({expected_question['possible_points'] + 1} points)",
        1,
    )
    with pytest.raises(NeedsConfirmation) as change:
        assignment.edit(db, info.id, changed_source)
    assert change.value.affected == sum(
        s["assignment_version"] == BASELINE["assignment_version"] for s in truth["submissions"]
    )
    old_ids = [q.id for q in questions]
    assignment.edit(db, info.id, changed_source, confirm=True)
    assert [q.id for q in grading.questions(db, info.id)] == old_ids
    assert scans.crop(home, db, first.id, question.id) == before_crop
    assert grading.scores(db, info.id)[0].total == BASELINE["expected_total"] + 1
    assignment.edit(db, info.id, info.source, confirm=True)
    assert grading.scores(db, info.id) == original_scores

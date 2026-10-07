import csv
import io
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pytest

from rubricate import assignment, courses, grading, names, scans, template
from rubricate.errors import UserError
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
            assert sum(r.automatic for r in rows) == AUTOMATIC_MATCHES, "docs/design.md: Matching names"
            assert {r.student.name for r in rows if r.automatic and r.student} == set(
                expected_names.values()
            ) - {SUGGESTED, ASKED}
            assert [r.suggested.name for r in rows if r.suggested] == [SUGGESTED], (
                "docs/design.md: Matching names"
            )
            for submission, row in zip(overview.submissions, rows, strict=True):
                student = expected_names[tuple(sorted(page_numbers[p.id] for p in submission.pages))]
                if row.student:
                    assert row.student.name == student
                    assert row.suggested is None
                elif row.suggested:
                    assert row.suggested.name == student
                else:
                    assert student == ASKED, "docs/design.md: Matching names"
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

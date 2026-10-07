import csv
import io
import json
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pytest

from rubricate import assignment, courses, grading, scans, template
from rubricate.errors import UserError
from rubricate.home import open_home

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
        if tag == "original":
            questions_before_edit = grading.questions(db, info.id)
            db.execute(
                "UPDATE question SET prompt=prompt || ? WHERE assignment=? AND kind='choice'",
                ("\n\n- [ ] Dijkstra\n- [ ] Merge sort\n- [ ] TCP\n- [ ] Huffman", info.id),
            )
            assert assignment.edit(db, info.id, info.source).has_scans
            assert grading.questions(db, info.id) == questions_before_edit
            expected_names = {tuple(s["scan_pages"]): s["student"]["name"] for s in truth["submissions"]}
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
            overview = scans.overview(db, info.id)
        report[tag] = {
            "result": asdict(result),
            "pages": [asdict(p) for p in sorted(pages, key=lambda page: page.page_index)],
            "elapsed_seconds": elapsed,
            "seconds_per_page": elapsed / result.pages,
            "exact_grouping": same,
            "groups": groups,
            "flags": flags,
        }
        (ARTIFACTS / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"{tag}: {result.pages} pages, {elapsed:.2f}s, {elapsed / result.pages:.3f}s/page", flush=True)
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

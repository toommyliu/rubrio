import csv
import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page, expect

SAMPLE = Path(__file__).parent.parent / "samples" / "cs101-quiz5"
TRUTH = json.loads((SAMPLE / "ground-truth.json").read_text())["assignments"][0]
SUBMISSIONS = TRUTH["submissions"]
QUESTIONS = TRUTH["questions_by_version"]
ROSTER = list(csv.DictReader(io.StringIO((SAMPLE / "roster.csv").read_text())))
AUTOMATIC = 6
SUGGESTED = "Grace Park"
ASKED = "Alex Kim"
SLOW = 180
type Get = Callable[[str], Any]


def printed(submission: dict) -> list[int | None]:
    return [page["printed_page"] for page in submission["scan"]["pages"]]


def scan_page(submission: dict, printed_page: int | None) -> int:
    return next(p["scan_page"] for p in submission["scan"]["pages"] if p["printed_page"] == printed_page)


REVERSED = next(s for s in SUBMISSIONS if printed(s) != sorted(printed(s), key=lambda p: p or 0))
EXTRA = next(s for s in SUBMISSIONS if None in printed(s))
FIRST = SUBMISSIONS[0]
ROSTER_SID = {f"{row['First Name']} {row['Last Name']}": row["SID"] for row in ROSTER}


def rubric_item(expected: str) -> str:
    return expected.removeprefix("Partially Correct - ")


def until[T](read: Callable[[], T], check: Callable[[T], bool], timeout: float = 10) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if check(value):
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out. Last value: {value!r}")
        time.sleep(0.1)


def test_flow(server: str, page: Page, artifacts: Path) -> None:
    out = artifacts / "flow"
    out.mkdir(parents=True, exist_ok=True)

    def get(path: str) -> Any:
        return page.request.get(f"{server}/api{path}").json()

    page.goto(server)
    page.get_by_label("Name", exact=True).fill("CS 101")
    expect(page.get_by_label("Season")).to_have_text(re.compile("^(Spring|Summer|Fall)\\b"))
    expect(page.get_by_label("Year")).to_have_text(re.compile("^20\\d\\d\\b"))
    page.get_by_role("button", name="Create course").click()
    expect(page).to_have_url(re.compile("/courses/cs-101-(spring|summer|fall)-20\\d\\d$"))
    course = page.url.rsplit("/", 1)[1]
    page.get_by_label("Roster CSV").set_input_files(SAMPLE / "roster.csv")
    page.get_by_role("button", name="Import roster").click()
    roster = until(lambda: get(f"/courses/{course}")["roster"], lambda r: len(r) == len(ROSTER))
    assert {s["sid"] for s in roster} == set(ROSTER_SID.values())
    page.get_by_role("link", name="New assignment").click()
    dropped = page.evaluate_handle(
        '(text) => {\n            const data = new DataTransfer()\n            data.items.add(new File([text], "assignment.md", { type: "text/markdown" }))\n            return data\n        }',
        (SAMPLE / "assignment.md").read_text(),
    )
    page.get_by_label("Assignment file", exact=True).dispatch_event("drop", {"dataTransfer": dropped})
    expect(page.get_by_role("list", name="Versions").get_by_role("listitem")).to_have_count(len(QUESTIONS))
    page.get_by_role("button", name="Create assignment").click()
    expect(page).to_have_url(re.compile(f"/courses/{course}/cs-101-quiz-5/templates$"))
    assignment_url = f"{server}/courses/{course}/cs-101-quiz-5"
    base = f"/courses/{course}/assignments/cs-101-quiz-5"
    questions = get(f"{base}/questions")
    leaves = [q for q in questions if q["kind"] != "parts"]
    for version, expected in QUESTIONS.items():
        mine = [q for q in leaves if q["version"] == version]
        assert [f"q{q['number']}" for q in mine] == [q["id"] for q in expected]
        assert [q["points"] for q in mine] == [q["possible_points"] for q in expected]
    for version in QUESTIONS:
        page.get_by_label(f"Template PDF for version {version}").set_input_files(
            SAMPLE / f"template-v{version}.pdf"
        )
    outline = until(
        lambda: get(f"{base}/outline"), lambda o: {p["version"] for p in o["pages"]} == set(QUESTIONS)
    )
    assert {b["question"] for b in outline["boxes"] if b["question"] is not None} == {q["id"] for q in leaves}
    page.get_by_role("link", name="Outline").click()
    target = next(q for q in leaves if q["version"] == FIRST["assignment_version"])
    suggested = next(b for b in outline["boxes"] if b["question"] == target["id"])
    assert suggested["suggested"]
    page.get_by_role("button", name=f"Box for {target['number']}", exact=True).click()
    page.keyboard.press("Delete")
    until(
        lambda: get(f"{base}/outline")["boxes"],
        lambda boxes: all(b["question"] != target["id"] for b in boxes),
    )
    page.get_by_role("list", name="Questions").get_by_role("button").filter(has_text=target["prompt"]).click()
    template = next(t for t in outline["pages"] if t["id"] == suggested["template_page"])
    surface = page.get_by_label(f"Page {template['page']}", exact=True)
    surface.scroll_into_view_if_needed()
    bounds = surface.bounding_box()
    assert bounds is not None

    def screen(x: float, y: float) -> tuple[float, float]:
        assert bounds is not None
        return (
            bounds["x"] + x / template["width"] * bounds["width"],
            bounds["y"] + y / template["height"] * bounds["height"],
        )

    page.mouse.move(*screen(suggested["x0"], suggested["y0"]))
    page.mouse.down()
    page.mouse.move(*screen(suggested["x1"], suggested["y1"]), steps=8)
    page.mouse.up()
    redrawn = until(
        lambda: [b for b in get(f"{base}/outline")["boxes"] if b["question"] == target["id"]],
        lambda boxes: len(boxes) == 1,
    )[0]
    assert not redrawn["suggested"]
    for edge in ("x0", "y0", "x1", "y1"):
        assert abs(redrawn[edge] - suggested[edge]) < 4, (edge, suggested, redrawn)
    page.screenshot(path=out / "outline.png", full_page=True)
    page.get_by_role("link", name="Scans").click()
    page.get_by_label("Scanned PDFs").set_input_files(SAMPLE / "submissions.pdf")
    scans = until(lambda: get(f"{base}/scans"), lambda o: len(o["submissions"]) == len(SUBMISSIONS), SLOW)
    flagged = {
        n: {f["kind"] for f in s["flags"]} for n, s in enumerate(scans["submissions"], 1) if s["flags"]
    }
    assert flagged == {REVERSED["scan_order"]: {"out_of_order"}, EXTRA["scan_order"]: {"extra_page"}}
    reversed_card = page.get_by_role("listitem", name=f"Submission {REVERSED['scan_order']}")
    drag(page, reversed_card, scan_page(REVERSED, 1), REVERSED["scan_pages"][0])
    extra_card = page.get_by_role("listitem", name=f"Submission {EXTRA['scan_order']}")
    extra_card.get_by_role("listitem", name=f"submissions.pdf, page {scan_page(EXTRA, None)}").get_by_role(
        "button", name="Keep as extra page"
    ).click()
    until(lambda: get(f"{base}/scans")["submissions"], lambda subs: not any(s["flags"] for s in subs))
    first_card = page.get_by_role("listitem", name=f"Submission {FIRST['scan_order']}")
    first_card.get_by_role(
        "button", name=f"View submissions.pdf, page {FIRST['scan_pages'][0]}", exact=True
    ).click()
    viewer = page.get_by_role("dialog")
    expect(viewer).to_contain_text(f"Version {FIRST['assignment_version']}, page 1")
    page.keyboard.press("ArrowRight")
    expect(viewer).to_contain_text(f"Version {FIRST['assignment_version']}, page 2")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    page.screenshot(path=out / "scans.png", full_page=True)
    page.get_by_role("link", name="Names").click()
    names = until(
        lambda: get(f"{base}/names"), lambda rows: all(r["name_read"] or r["sid_read"] for r in rows), SLOW
    )
    assert sum(1 for r in names if r["student"] and r["automatic"]) == AUTOMATIC
    assert [r["suggested"]["name"] for r in names if r["student"] is None and r["suggested"]] == [SUGGESTED]
    page.get_by_role("button", name="Confirm").click()
    page.get_by_role("button", name=re.compile(f"^{ASKED} {ROSTER_SID[ASKED]}")).click()
    names = until(lambda: get(f"{base}/names"), lambda rows: all(r["student"] for r in rows))
    assert [r["student"]["name"] for r in names] == [s["student"]["name"] for s in SUBMISSIONS]
    page.screenshot(path=out / "names.png", full_page=True)
    expected = {(s["assignment_version"], s["student"]["name"]): s["responses"] for s in SUBMISSIONS}
    for question in leaves:
        responses = get(f"/questions/{question['id']}/responses")
        page.goto(f"{assignment_url}/grade/{question['id']}")
        for index, response in enumerate(responses):
            expect(page.get_by_text(response["student_name"], exact=True)).to_be_visible()
            truth = expected[question["version"], response["student_name"]][f"q{question['number']}"]
            grade = truth["expected_grade"]
            if question["id"] == target["id"] and index == 0:
                exercise_grading_panel(page, get, question, response["submission"])
                page.screenshot(path=out / "grading.png", full_page=True)
            page.get_by_role("list", name="Rubric").get_by_text(
                rubric_item(grade["rubric"]), exact=True
            ).click()
            expect(page.get_by_label("Score", exact=True)).to_have_value(f"{grade['points']:g}")
            page.keyboard.press("ArrowRight")
    scores = until(
        lambda: get(f"{base}/scores"), lambda rows: all(None not in r["scores"].values() for r in rows)
    )
    assert {r["student_name"]: r["total"] for r in scores} == {
        s["student"]["name"]: s["expected_total"] for s in SUBMISSIONS
    }
    page.goto(f"{assignment_url}/review")
    grades = page.get_by_role("table", name="Grades")
    first_version = [s for s in SUBMISSIONS if s["assignment_version"] == FIRST["assignment_version"]]
    expect(grades.get_by_role("row")).to_have_count(len(first_version) + 1)
    page.screenshot(path=out / "review.png", full_page=True)
    grades.get_by_role("link", name=FIRST["student"]["name"]).click()
    review = get(f"/submissions/{scores[0]['submission']}/review")
    assert review["total"] == FIRST["expected_total"]
    expect(page.get_by_role("listitem", name=re.compile("^Question "))).to_have_count(
        len([q for q in review["questions"] if q["kind"] != "parts"])
    )
    page.screenshot(path=out / "review-submission.png", full_page=True)
    page.goto(f"{assignment_url}/statistics")
    expect(page.get_by_label("Distribution of submission totals")).to_be_visible()
    questions_table = page.get_by_role("table", name="Question statistics")
    expect(questions_table.get_by_role("row")).to_have_count(len(leaves) + 1)
    questions_table.get_by_role("button").filter(has_text=target["prompt"]).first.click()
    expect(page.get_by_label(f"Rubric item usage for question {target['number']}")).to_be_visible()
    page.screenshot(path=out / "statistics.png", full_page=True)
    page.goto(f"{assignment_url}/export")
    page.screenshot(path=out / "export.png", full_page=True)
    with page.expect_download() as download:
        page.get_by_role("link", name="Download gradebook CSV").click()
    download.value.save_as(out / "gradebook.csv")
    rows = list(csv.DictReader(io.StringIO((out / "gradebook.csv").read_text())))
    assert {row["name"]: float(row["total"]) for row in rows} == {
        s["student"]["name"]: s["expected_total"] for s in SUBMISSIONS
    }
    with page.expect_download() as download:
        page.get_by_role("link", name="Download feedback PDFs").click()
    download.value.save_as(out / "feedback.zip")
    with zipfile.ZipFile(out / "feedback.zip") as feedback:
        assert len(feedback.namelist()) == len(SUBMISSIONS)
    page.goto(assignment_url)
    page.get_by_role("button", name="Delete assignment").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Delete assignment").click()
    expect(page).to_have_url(f"{server}/courses/{course}")
    until(lambda: get(f"/courses/{course}")["assignments"], lambda assignments: assignments == [])


def exercise_grading_panel(page: Page, get: Get, question: dict, submission: int) -> None:

    def grade() -> Any:
        rows = get(f"/questions/{question['id']}/responses")
        return next(r for r in rows if r["submission"] == submission)["grade"]

    rubric = page.get_by_role("list", name="Rubric")
    partial = next(item for item in question["rubric"] if not item["whole_answer"])
    whole = next(item for item in question["rubric"] if item["whole_answer"] and item["points"] == 0)
    score = page.get_by_label("Score", exact=True)
    expect(page.get_by_role("region", name="Answer key")).to_be_visible()
    rubric.get_by_text(partial["description"], exact=True).click()
    until(grade, lambda g: g is not None and g["applied"] == [partial["id"]])
    rubric.get_by_text(whole["description"], exact=True).click()
    until(grade, lambda g: g["applied"] == [whole["id"]])
    expect(rubric.get_by_role("button", pressed=True)).to_have_count(1)
    above = question["points"] + 2
    score.fill(f"{above:g}")
    score.press("Enter")
    saved = until(grade, lambda g: g["score"] == above)
    assert (saved["adjustment"], saved["extra_credit"]) == (2, True)
    page.get_by_role("button", name="Remove it").click()
    cleared = until(grade, lambda g: g["adjustment"] == 0)
    assert (cleared["score"], cleared["extra_credit"]) == (question["points"], False)
    rubric.get_by_text(whole["description"], exact=True).click()
    until(grade, lambda g: g["applied"] == [])


def drag(page: Page, submission: Locator, source: int, target: int) -> None:
    handle = submission.get_by_role("button", name=f"Reorder submissions.pdf, page {source}", exact=True)
    onto = submission.get_by_role("button", name=f"Reorder submissions.pdf, page {target}", exact=True)
    handle.scroll_into_view_if_needed()
    start = handle.bounding_box()
    end = onto.bounding_box()
    assert start is not None and end is not None
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=12)
    page.mouse.up()

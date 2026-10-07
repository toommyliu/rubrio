import csv
import io
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect

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
    expect(page).to_have_url(re.compile(f"/courses/{course}/cs-101-quiz-5$"))
    base = f"/courses/{course}/assignments/cs-101-quiz-5"
    questions = get(f"{base}/questions")
    leaves = [q for q in questions if q["kind"] != "parts"]
    for version, expected in QUESTIONS.items():
        mine = [q for q in leaves if q["version"] == version]
        assert [f"q{q['number']}" for q in mine] == [q["id"] for q in expected]
        assert [q["points"] for q in mine] == [q["possible_points"] for q in expected]

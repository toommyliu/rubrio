import json
import re
from pathlib import Path
from typing import Any

import pymupdf
from playwright.sync_api import Page, expect

SAMPLES = Path(__file__).parent.parent / "samples"
TEMPLATES = SAMPLES / "templates"
EXPECTED = json.loads((TEMPLATES / "expected.json").read_text())
CS101 = SAMPLES / "cs101-quiz5"
QUESTIONS = json.loads((CS101 / "ground-truth.json").read_text())["assignments"][0]["questions_by_version"]
FOOTER = re.compile(r"^(?:page\s*)?\d+(?:\s*of\s*\d+)?$|, page \d+ of \d+$", re.IGNORECASE)


def shape(found: dict) -> dict:
    return {
        "title": found["title"],
        "pages": found["pages"],
        "has_text": found["has_text"],
        "questions": [
            {
                "prompt": q["prompt"],
                "points": q["points"],
                "parts": [{"prompt": p["prompt"], "points": p["points"]} for p in q["parts"]],
            }
            for q in found["questions"]
        ],
    }


def plan(found: dict) -> dict:
    return {
        "questions": [
            {
                "prompt": q["prompt"],
                "points": None if q["parts"] else (q["points"] if q["points"] is not None else 10),
                "label": q["label"],
                "parts": [
                    {"prompt": p["prompt"], "points": p["points"], "label": p["label"]} for p in q["parts"]
                ],
            }
            for q in found["questions"]
        ]
    }


def footer_tops(pdf: Path) -> dict[int, float]:
    tops = {}
    with pymupdf.open(pdf) as document:
        for index in range(document.page_count):
            page, number = document[index], index + 1
            for _, y0, _, _, text, *_ in page.get_text("blocks"):
                if y0 > page.rect.height * 0.85 and FOOTER.search(text.strip()):
                    tops[number] = min(tops.get(number, y0), y0)
    return tops


def field_label(pdf: Path, text: str) -> pymupdf.Rect:
    with pymupdf.open(pdf) as document:
        return document[0].search_for(text)[0]


def draw_outline(pdf: Path, outline: dict, questions: dict[int, str], out: Path) -> None:
    with pymupdf.open(pdf) as document:
        for template_page in outline["pages"]:
            page = document[template_page["page"] - 1]
            for box in outline["boxes"]:
                if box["template_page"] != template_page["id"]:
                    continue
                rect = pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])
                color = (0.85, 0.2, 0.2) if box["field"] else (0.1, 0.4, 0.85)
                page.draw_rect(rect, color=color, width=1.2)
                label = box["field"] or questions[box["question"]]
                page.insert_text((rect.x0 + 4, rect.y0 + 11), label, fontsize=9, color=color)
            page.get_pixmap(dpi=60).save(out / f"{pdf.stem}-page-{template_page['page']}.png")


def test_templates(server: str, page: Page, artifacts: Path) -> None:
    out = artifacts / "templates"
    out.mkdir(parents=True, exist_ok=True)

    def get(path: str) -> Any:
        return page.request.get(f"{server}/api{path}").json()

    course = page.request.post(
        f"{server}/api/courses", data={"name": "Templates", "term": "Fall 2026"}
    ).json()
    report = {}
    for name, expected in EXPECTED.items():
        pdf = TEMPLATES / name
        found = page.request.post(
            f"{server}/api/templates/questions",
            multipart={"file": {"name": name, "mimeType": "application/pdf", "buffer": pdf.read_bytes()}},
        ).json()
        assert shape(found) == {k: v for k, v in expected.items() if k != "description"}, name
        entry: dict[str, Any] = {"questions": len(found["questions"])}
        if found["questions"]:
            created = page.request.post(
                f"{server}/api/courses/{course['slug']}/assignments/from-templates",
                multipart={
                    "files": {"name": name, "mimeType": "application/pdf", "buffer": pdf.read_bytes()},
                    "assignment": json.dumps({"title": found["title"], "versions": [plan(found)]}),
                },
            )
            assert created.ok, created.text()
            base = f"/courses/{course['slug']}/assignments/{created.json()['slug']}"
            questions = {q["id"]: q for q in get(f"{base}/questions")}
            outline = get(f"{base}/outline")
            boxes = outline["boxes"]
            pages = {p["id"]: p["page"] for p in outline["pages"]}
            leaves = [q for q in questions.values() if q["kind"] != "parts"]
            assert sorted(b["question"] for b in boxes if b["question"]) == sorted(q["id"] for q in leaves), (
                name
            )
            assert sorted(b["field"] for b in boxes if b["field"]) == ["name", "sid"], name
            for box in (b for b in boxes if b["field"]):
                label = field_label(pdf, "Name:" if box["field"] == "name" else "Student ID:")
                assert label.x1 < box["x0"] < label.x1 + 20 and box["x1"] > label.x1 + 150, (name, box)
            footers = footer_tops(pdf)
            for page_number, footer in footers.items():
                bottoms = [
                    b["y1"] for b in boxes if b["question"] and pages[b["template_page"]] == page_number
                ]
                assert footer - 10 < max(bottoms) < footer, (name, page_number, footer, bottoms)
            draw_outline(pdf, outline, {q["id"]: f"Q{q['number']}" for q in leaves}, out)
            entry.update(
                boxes=len(boxes),
                footers=footers,
                last_box_bottoms={
                    page_number: max(b["y1"] for b in boxes if pages[b["template_page"]] == page_number)
                    for page_number in sorted(set(pages.values()))
                },
            )
        report[name] = entry
    missing = page.request.post(
        f"{server}/api/courses/{course['slug']}/assignments/from-templates",
        multipart={
            "files": {
                "name": "no-points.pdf",
                "mimeType": "application/pdf",
                "buffer": (TEMPLATES / "no-points.pdf").read_bytes(),
            },
            "assignment": json.dumps(
                {
                    "title": "No points",
                    "versions": [
                        {
                            "questions": [
                                {
                                    "prompt": "What is a mole?",
                                    "points": None,
                                    "label": 1,
                                    "parts": [],
                                }
                            ]
                        }
                    ],
                }
            ),
        },
    )
    assert missing.status == 400 and missing.json()["message"] == "Question 1 needs points."
    report["missing points"] = missing.json()["message"]
    mismatched = page.request.post(
        f"{server}/api/courses/{course['slug']}/assignments/from-templates",
        multipart={
            "files": {
                "name": "no-points.pdf",
                "mimeType": "application/pdf",
                "buffer": (TEMPLATES / "no-points.pdf").read_bytes(),
            },
            "assignment": json.dumps(
                {
                    "title": "Mismatched",
                    "versions": [
                        {"questions": [{"prompt": "What?", "points": 1, "label": 10**9, "parts": []}]}
                    ],
                }
            ),
        },
    )
    assert mismatched.status == 400
    assert mismatched.json()["message"] == "These questions don't match the PDF. Upload the PDF again."
    assert {a["title"] for a in get(f"/courses/{course['slug']}")["assignments"]} == {
        e["title"] for e in EXPECTED.values() if e["questions"]
    }
    report["mismatched label"] = mismatched.json()["message"]

    second = page.request.post(f"{server}/api/courses", data={"name": "CS 101", "term": "Fall 2026"}).json()
    course_url = f"{server}/courses/{second['slug']}"
    page.goto(course_url)
    page.get_by_role("button", name="New assignment").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Start from a PDF")).to_be_visible()
    expect(dialog.get_by_role("heading", name="Write an assignment file")).to_be_visible()
    page.screenshot(path=out / "dialog.png", animations="disabled")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page).to_have_url(course_url)

    page.get_by_role("button", name="New assignment").click()
    page.get_by_role("dialog").get_by_role("link", name="Upload a PDF").click()
    expect(page).to_have_url(f"{course_url}/new/pdf")
    page.get_by_label("Template PDFs").set_input_files(TEMPLATES / "no-points.pdf")
    expect(page.get_by_text("Fill in points for 4 questions to continue.")).to_be_visible()
    expect(page.get_by_role("button", name="Create assignment")).to_be_disabled()
    page.get_by_label("Question 1 points").fill("5")
    expect(page.get_by_text("Fill in points for 3 questions to continue.")).to_be_visible()
    page.get_by_role("button", name="Cancel").click()
    confirm = page.get_by_role("alertdialog")
    expect(confirm).to_contain_text("Discard this assignment?")
    confirm.get_by_role("button", name="Discard").click()
    expect(page).to_have_url(course_url)
    assert get(f"/courses/{second['slug']}")["assignments"] == []

    page.get_by_role("button", name="New assignment").click()
    page.get_by_role("dialog").get_by_role("link", name="Upload a PDF").click()
    page.get_by_label("Template PDFs").set_input_files([CS101 / "template-vB.pdf", CS101 / "template-vA.pdf"])
    for version, questions in QUESTIONS.items():
        for question in questions:
            number = question["id"].removeprefix("q")
            prefix = f"Version {version}, question {number}"
            expect(page.get_by_label(f"{prefix} prompt")).to_have_value(question["prompt"])
            expect(page.get_by_label(f"{prefix} points")).to_have_value(f"{question['possible_points']:g}")
    expect(page.get_by_role("button", name="Create assignment")).to_be_enabled()
    page.get_by_label("Title", exact=True).fill("CS 101: Quiz 5")
    page.screenshot(path=out / "review.png", full_page=True)
    page.get_by_role("button", name="Create assignment").click()
    expect(page).to_have_url(f"{course_url}/cs-101-quiz-5/outline")
    base = f"/courses/{second['slug']}/assignments/cs-101-quiz-5"
    leaves = [q for q in get(f"{base}/questions") if q["kind"] != "parts"]
    for version, expected in QUESTIONS.items():
        mine = [q for q in leaves if q["version"] == version]
        assert [f"q{q['number']}" for q in mine] == [q["id"] for q in expected]
        assert [q["points"] for q in mine] == [q["possible_points"] for q in expected]
    outline = get(f"{base}/outline")
    assert sorted(b["question"] for b in outline["boxes"] if b["question"]) == sorted(q["id"] for q in leaves)
    expect(page.get_by_text("Every question has a box.")).to_be_visible()
    page.screenshot(path=out / "outline.png", full_page=True)
    report["cs101"] = {"questions": len(leaves), "boxes": len(outline["boxes"])}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")

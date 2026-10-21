import json
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pymupdf
from playwright.sync_api import Page, expect
from test_flow import QUESTIONS, ROSTER, SAMPLE, until
from test_templates import draw_outline

STUDENTS = {"A": ROSTER[0], "B": ROSTER[1]}


def scanned(page: pymupdf.Page, angle: float) -> bytes:
    pixmap = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    image = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width)
    matrix = cv2.getRotationMatrix2D((pixmap.width / 2, pixmap.height / 2), angle, 1.0)
    matrix[:, 2] += (14, -9)
    image = cv2.warpAffine(image, matrix, (pixmap.width, pixmap.height), borderValue=255)
    return cv2.imencode(".png", image)[1].tobytes()


def fill(pdf: bytes, outline: dict, questions: dict[int, dict], student: dict[str, str]) -> list[bytes]:
    numbers = {p["id"]: p["page"] for p in outline["pages"]}
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for box in outline["boxes"]:
            page = document[numbers[box["template_page"]] - 1]
            if box["field"]:
                text = (
                    f"{student['First Name']} {student['Last Name']}"
                    if box["field"] == "name"
                    else student["SID"]
                )
                page.insert_text((box["x0"] + 10, box["y1"] - 10), text, fontsize=15, fontname="cour")
            else:
                text = f"Answer to {questions[box['question']]['number']}"
                page.insert_text((box["x1"] - 200, box["y1"] - 24), text, fontsize=14, fontname="cour")
        return [scanned(page, 1.2 if index % 2 else -0.8) for index, page in enumerate(document.pages())]


def test_template(server: str, page: Page, artifacts: Path) -> None:
    out = artifacts / "template"
    out.mkdir(parents=True, exist_ok=True)

    def get(path: str) -> Any:
        return page.request.get(f"{server}/api{path}").json()

    course = page.request.post(f"{server}/api/courses", data={"name": "CS 101", "term": "Fall 2026"}).json()
    slug = course["slug"]
    page.request.post(
        f"{server}/api/courses/{slug}/roster", data={"csv": (SAMPLE / "roster.csv").read_text()}
    )
    page.goto(f"{server}/courses/{slug}")
    page.get_by_role("button", name="New assignment").click()
    page.get_by_role("dialog").get_by_role("link", name="Write a file").click()
    dropped = page.evaluate_handle(
        '(text) => {\n            const data = new DataTransfer()\n            data.items.add(new File([text], "assignment.md", { type: "text/markdown" }))\n            return data\n        }',
        (SAMPLE / "assignment.md").read_text(),
    )
    page.get_by_label("Assignment file", exact=True).dispatch_event("drop", {"dataTransfer": dropped})
    page.get_by_role("button", name="Create assignment").click()
    expect(page).to_have_url(re.compile(f"/courses/{slug}/cs-101-quiz-5/templates$"))
    base = f"/courses/{slug}/assignments/cs-101-quiz-5"
    questions = {q["id"]: q for q in get(f"{base}/questions")}
    leaves = [q for q in questions.values() if q["kind"] != "parts"]

    for version in QUESTIONS:
        page.get_by_role("button", name=f"Generate PDF for version {version}").click()
        expect(page.get_by_role("link", name=f"Download PDF for version {version}")).to_be_visible()
    outline = until(
        lambda: get(f"{base}/outline"), lambda o: {p["version"] for p in o["pages"]} == set(QUESTIONS)
    )
    page.screenshot(path=out / "templates.png", full_page=True)

    pdfs: dict[str, bytes] = {}
    for version in QUESTIONS:
        with page.expect_download() as download:
            page.get_by_role("link", name=f"Download PDF for version {version}").click()
        path = out / f"cs-101-quiz-5-{version}.pdf"
        download.value.save_as(path)
        pdfs[version] = path.read_bytes()

    report: dict[str, Any] = {}
    for version, pdf in pdfs.items():
        pages = {p["id"]: p["page"] for p in outline["pages"] if p["version"] == version}
        boxes = [b for b in outline["boxes"] if b["template_page"] in pages]
        mine = [q for q in leaves if q["version"] == version]
        assert all(b["suggested"] for b in boxes)
        assert sorted(b["question"] for b in boxes if b["question"]) == sorted(q["id"] for q in mine)
        assert sorted((pages[b["template_page"]], b["field"]) for b in boxes if b["field"]) == [
            (1, "name"),
            (1, "sid"),
        ]
        placed = {}
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            for question in mine:
                box = next(b for b in boxes if b["question"] == question["id"])
                hits = [
                    (number, hit)
                    for number in range(1, document.page_count + 1)
                    for hit in document[number - 1].search_for(question["prompt"].replace("`", "")[:30])
                ]
                assert len(hits) == 1, (version, question["number"], hits)
                number, hit = hits[0]
                center = ((hit.x0 + hit.x1) / 2, (hit.y0 + hit.y1) / 2)
                inside = [
                    b["question"]
                    for b in boxes
                    if pages[b["template_page"]] == number
                    and b["x0"] < center[0] < b["x1"]
                    and b["y0"] < center[1] < b["y1"]
                ]
                assert inside == [question["id"]], (version, question["number"], box, hit)
                placed[question["number"]] = {
                    "page": number,
                    "box": [box[k] for k in ("x0", "y0", "x1", "y1")],
                }
        report[version] = {"pages": len(pages), "boxes": placed}
        draw_outline(
            out / f"cs-101-quiz-5-{version}.pdf",
            {"pages": [p for p in outline["pages"] if p["version"] == version], "boxes": boxes},
            {q["id"]: f"Q{q['number']}" for q in mine},
            out,
        )

    for version in QUESTIONS:
        page.get_by_role("button", name=f"Generate PDF for version {version}").click()
        expect(page.get_by_role("button", name=f"Generate PDF for version {version}")).to_be_enabled()
        with page.expect_download() as download:
            page.get_by_role("link", name=f"Download PDF for version {version}").click()
        assert Path(download.value.path()).read_bytes() == pdfs[version]
    assert get(f"{base}/outline") == outline

    scan = pymupdf.open()
    for version, student in STUDENTS.items():
        version_outline = {
            "pages": [p for p in outline["pages"] if p["version"] == version],
            "boxes": [
                b
                for b in outline["boxes"]
                if b["template_page"] in {p["id"] for p in outline["pages"] if p["version"] == version}
            ],
        }
        for image in fill(pdfs[version], version_outline, questions, student):
            scan.new_page(width=612, height=792).insert_image(pymupdf.Rect(0, 0, 612, 792), stream=image)
    scan.save(out / "scan.pdf")
    scan.close()
    page.get_by_role("link", name="Scans").click()
    page.get_by_label("Scanned PDFs").set_input_files(out / "scan.pdf")
    scans = until(lambda: get(f"{base}/scans"), lambda o: len(o["submissions"]) == len(STUDENTS), 180)
    assert scans["unassigned"] == []
    assert [
        (s["version"], [(p["version"], p["page"]) for p in s["pages"]], s["flags"])
        for s in scans["submissions"]
    ] == [(version, [(version, 1), (version, 2)], []) for version in STUDENTS]
    page.screenshot(path=out / "scans.png", full_page=True)
    names = until(
        lambda: get(f"{base}/names"), lambda rows: all(r["name_read"] or r["sid_read"] for r in rows), 180
    )
    assert [r["student"]["sid"] if r["student"] else None for r in names] == [
        s["SID"] for s in STUDENTS.values()
    ]
    report["names"] = [{"name_read": r["name_read"], "sid_read": r["sid_read"]} for r in names]
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")

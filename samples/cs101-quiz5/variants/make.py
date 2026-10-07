from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter

SAMPLE = Path(__file__).resolve().parents[1]
SOURCE = SAMPLE / "submissions.pdf"
TRUTH = SAMPLE / "ground-truth.json"
OUT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1

ALL = list(range(1, 18))


def without(*pages: int) -> list[int]:
    return [p for p in ALL if p not in pages]


def swapped(a: int, b: int) -> list[int]:
    return [b if p == a else a if p == b else p for p in ALL]


VARIANTS = {
    "missing-page-1-priya": {
        "description": "Priya's page 1 is gone. Her page 2 opens the batch with no name, ID or version title in front of it.",
        "source_pages": without(1),
        "submissions": {"Priya Shah": [1]},
        "expected_flags": [
            {"kind": "missing_page", "scan_pages": [1], "student": "Priya Shah", "note": "Page 1 is missing. The group starts at page 2."},
            {"kind": "identity_unknown", "scan_pages": [1], "student": "Priya Shah", "note": "No name or ID field on the surviving page."},
            {"kind": "version_unknown", "scan_pages": [1], "student": "Priya Shah", "note": "Acceptable instead of a version: the title is on the missing page. Reading version A from page 2's question order is also right."},
        ],
    },
    "missing-page-2-marcus": {
        "description": "Marcus's page 2 is gone, so his page 1 is followed by Jon's page 1.",
        "source_pages": without(4),
        "submissions": {"Marcus Bell": [3]},
        "expected_flags": [
            {"kind": "missing_page", "scan_pages": [3], "student": "Marcus Bell", "note": "Page 2 is missing."},
        ],
    },
    "duplicate-page-2-jon": {
        "description": "Jon's page 2 was fed twice, so it appears twice in a row.",
        "source_pages": ALL[:6] + [6] + ALL[6:],
        "submissions": {"Jonathan Wu": [5, 6, 7]},
        "expected_flags": [
            {"kind": "repeated_page", "scan_pages": [6, 7], "student": "Jonathan Wu", "note": "Printed page 2 appears twice. Both copies carry the same answers."},
        ],
    },
    "swapped-page-2-across-versions": {
        "description": "José (B) and Alex (A) have their page 2s swapped, so each group holds one page of each version.",
        "source_pages": swapped(8, 10),
        "submissions": {"José Ramírez": [7, 10], "Alex Kim": [9, 8]},
        "expected_flags": [
            {"kind": "mixed_versions", "scan_pages": [7, 8], "student": "José Ramírez", "note": "Page 1 is version B, the page 2 behind it is version A."},
            {"kind": "mixed_versions", "scan_pages": [9, 10], "student": "Alex Kim", "note": "Page 1 is version A, the page 2 behind it is version B."},
        ],
    },
    "interleaved-priya-jon": {
        "description": "Priya's and Jon's pages alternate: Priya p1, Jon p1, Priya p2, Jon p2. Both are version A, so versions do not give it away.",
        "source_pages": [1, 5, 2, 6, 3, 4] + ALL[6:],
        "submissions": {"Priya Shah": [1, 3], "Jonathan Wu": [2, 4]},
        "expected_flags": [
            {"kind": "interleaved", "scan_pages": [1, 2, 3, 4], "students": ["Priya Shah", "Jonathan Wu"], "note": "Two page 1s then two page 2s. A repeated-page or out-of-sequence flag on these pages is also acceptable."},
        ],
    },
    "scratch-sheet-first": {
        "description": "Omar's scratch sheet is the first page of the batch, before any page 1.",
        "source_pages": [15] + without(15),
        "submissions": {"Omar Haddad": [14, 15]},
        "expected_flags": [
            {"kind": "extra_work", "scan_pages": [1], "student": None, "note": "Blank-template sheet with no student in front of it. It belongs to nobody and must not count as an assignment page."},
        ],
    },
}


def load_pages() -> dict[int, dict]:
    truth = json.loads(TRUTH.read_text())
    assignment = truth["assignments"][0]
    pages: dict[int, dict] = {}
    for submission in assignment["submissions"]:
        for page in submission["scan"]["pages"]:
            pages[page["scan_page"]] = {
                "student": submission["student"]["name"],
                "version": submission["assignment_version"],
                "printed_page": page.get("printed_page"),
                "upside_down": bool(page.get("upside_down", False)),
                "extra_work": bool(page.get("extra_work", False)),
            }
    return pages


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(name: str, spec: dict, pages: dict[int, dict], reader: PdfReader) -> None:
    out_dir = OUT / name
    out_dir.mkdir(exist_ok=True)
    writer = PdfWriter()
    for source_page in spec["source_pages"]:
        writer.add_page(reader.pages[source_page - 1])
    with (out_dir / "submissions.pdf").open("wb") as handle:
        writer.write(handle)

    scan_pages = [
        {"scan_page": index, "source_page": source_page, **pages[source_page]}
        for index, source_page in enumerate(spec["source_pages"], start=1)
    ]
    by_student: dict[str, list[int]] = {}
    for page in scan_pages:
        by_student.setdefault(page["student"], []).append(page["scan_page"])
    by_student.update(spec["submissions"])

    (out_dir / "ground-truth.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "description": spec["description"],
        "derived_from": "samples/cs101-quiz5/submissions.pdf",
        "source_sha256": sha256(SOURCE),
        "labels_from": "samples/cs101-quiz5/ground-truth.json",
        "source_pages": spec["source_pages"],
        "scan_pages": scan_pages,
        "submissions": [
            {"student": student, "scan_pages": sorted(scan)}
            for student, scan in by_student.items()
        ],
        "expected_flags": spec["expected_flags"],
    }, indent=1, ensure_ascii=False) + "\n")


def main() -> None:
    before = sha256(SOURCE)
    pages = load_pages()
    reader = PdfReader(str(SOURCE))
    for name, spec in VARIANTS.items():
        build(name, spec, pages, reader)
        print(f"{name}: {len(spec['source_pages'])} pages")
    assert sha256(SOURCE) == before, "the source scan changed"


if __name__ == "__main__":
    main()

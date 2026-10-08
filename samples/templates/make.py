from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 612, 792
LEFT = 72


@dataclass
class Part:
    prompt: str
    points: float


@dataclass
class Question:
    prompt: str
    points: float | None
    space: float = 200
    parts: list[Part] = field(default_factory=list)
    part_space: float = 100
    after: list[str] = field(default_factory=list)


@dataclass
class Text:
    text: str
    x: float = LEFT
    space: float = 16


type Item = Question | Text


@dataclass
class Template:
    file: str
    description: str
    title: str
    pages: list[list[Item]]
    label: Callable[[int, Question], list[str]]
    part_label: Callable[[str, Part], str] = lambda letter, part: (
        f"({letter}) ({part.points:g} points) {part.prompt}"
    )
    right: Callable[[Question], str | None] = lambda question: None
    footer: Callable[[int, int], str | None] = lambda page, pages: None
    footer_y: float = HEIGHT - 40
    blanks: bool = False


def inline(number: int, question: Question) -> list[str]:
    if question.points is None:
        return [f"{number}. {question.prompt}"]
    return [f"{number}. ({question.points:g} points) {question.prompt}"]


def plain(number: int, question: Question) -> list[str]:
    return [f"{number}. {question.prompt}"]


def word(number: int, question: Question) -> list[str]:
    return [f"Question {number} [{question.points:g} pts]", question.prompt]


def render(template: Template) -> pymupdf.Document:
    document = pymupdf.open()
    number = 0
    for index, items in enumerate(template.pages):
        page = document.new_page(width=WIDTH, height=HEIGHT)
        y = 64
        if index == 0:
            page.insert_text((LEFT, y), template.title, fontsize=18, fontname="hebo")
            y += 40
            for name in ("Name:", "Student ID:"):
                if template.blanks:
                    page.insert_text((LEFT, y), f"{name} {'_' * 40}", fontsize=11)
                else:
                    page.insert_text((LEFT, y), name, fontsize=11)
                    page.draw_line((LEFT + 80, y + 2), (LEFT + 330, y + 2), width=0.6)
                y += 30
            y += 10
        for item in items:
            if isinstance(item, Text):
                page.insert_text((item.x, y), item.text, fontsize=11)
                y += item.space
                continue
            number += 1
            lines = template.label(number, item)
            for i, line in enumerate(lines):
                page.insert_text((LEFT, y), line, fontsize=11, fontname="hebo" if i == 0 else "helv")
                if i == 0 and (right := template.right(item)):
                    page.insert_text((WIDTH - LEFT - 60, y), right, fontsize=11)
                y += 15
            for line in item.after:
                page.insert_text((LEFT + 18, y), line, fontsize=11)
                y += 15
            for letter, part in zip("abcdefghijklmnopqrstuvwxyz", item.parts, strict=False):
                y += 10
                page.insert_text((LEFT + 18, y), template.part_label(letter, part), fontsize=11)
                y += item.part_space
            y += item.space
        if footer := template.footer(index + 1, len(template.pages)):
            page.insert_text((WIDTH / 2 - 3 * len(footer), template.footer_y), footer, fontsize=9)
    document.set_metadata({})
    return document


def scanned(source: pymupdf.Document) -> pymupdf.Document:
    document = pymupdf.open()
    for page in source:
        image = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        document.new_page(width=WIDTH, height=HEIGHT).insert_image(page.rect, pixmap=image)
    document.set_metadata({})
    return document


def expected(template: Template) -> dict:
    questions = [item for items in template.pages for item in items if isinstance(item, Question)]
    return {
        "description": template.description,
        "title": template.title,
        "pages": len(template.pages),
        "has_text": True,
        "questions": [
            {
                "prompt": q.prompt,
                "points": q.points,
                "parts": [{"prompt": p.prompt, "points": p.points} for p in q.parts],
            }
            for q in questions
        ],
    }


PHYSICS = Template(
    "points-in-label.pdf",
    "Points printed after each label, a question with parts, and a footer with the page count on every page, higher than the usual bottom margin.",
    "Physics 7: Midterm 1",
    [
        [
            Text("Show your work. Answers without work get partial credit at most.", space=40),
            Question("A ball is dropped from 20 m. How long does it take to land?", 12, space=220),
            Question("State Newton's second law in words.", 8),
        ],
        [
            Question(
                "A block slides down a frictionless ramp.",
                None,
                space=40,
                parts=[
                    Part("Draw the forces on the block.", 4),
                    Part("Find its acceleration on a 30 degree ramp.", 6),
                ],
                part_space=180,
            ),
            Question("Explain why the sky is blue.", 15),
        ],
        [Question("Name one quantity conserved in an elastic collision.", 5)],
    ],
    inline,
    footer=lambda page, pages: f"Physics 7 Midterm 1, page {page} of {pages}",
    footer_y=HEIGHT - 70,
)

BIOLOGY = Template(
    "question-word.pdf",
    'Labels written as "Question 1 [5 pts]" with the prompt on the next line, and a page number low on the page. A prompt starts with "Name:", which must not become a name box.',
    "Biology 20: Quiz 3",
    [
        [
            Question("What does the mitochondria do?", 5, space=250),
            Question("Name: give the four bases in DNA.", 5),
        ],
        [
            Question("Describe what happens during mitosis.", 10, space=300),
            Question("Why do cells divide?", 10),
        ],
    ],
    word,
    footer=lambda page, pages: str(page),
    footer_y=HEIGHT - 24,
)

CHEMISTRY = Template(
    "no-points.pdf",
    "No points printed anywhere, so every question needs points filled in.",
    "Chemistry 1A: Worksheet 2",
    [
        [
            Question("Balance this equation: H2 + O2 -> H2O", None, space=250),
            Question("What is a mole?", None),
        ],
        [Question("Name two noble gases.", None, space=250), Question("Why does ice float on water?", None)],
    ],
    plain,
)

HISTORY = Template(
    "choices-and-parts.pdf",
    "A multiple-choice question with lowercase choices, which must stay one question, a question with lettered parts that print points, and name and ID fields written as underscores.",
    "History 5: Quiz 1",
    [
        [
            Question(
                "Which year did World War II end?", 5, space=40, after=["a) 1943", "b) 1945", "c) 1947"]
            ),
            Question(
                "Answer both parts.",
                None,
                space=40,
                parts=[
                    Part("Who was the first US president?", 2),
                    Part("Give two causes of the Civil War.", 4),
                ],
                part_space=110,
            ),
            Question("Define federalism.", 4),
        ]
    ],
    inline,
    part_label=lambda letter, part: f"({letter}) [{part.points:g} pts] {part.prompt}",
    blanks=True,
)

MATH = Template(
    "numbered-lists.pdf",
    "Numbered instructions above question 1, and a numbered list inside question 2's prompt. Neither should become questions.",
    "Math 3: Exam 2",
    [
        [
            Text("Instructions"),
            Text("1. No calculators."),
            Text("2. Show your work."),
            Text("3. Box your final answer.", space=30),
            Question("Solve 2x + 3 = 11.", 10, space=150),
            Question(
                "Put the steps of long division in order:",
                10,
                space=150,
                after=["1. Divide", "2. Multiply", "3. Subtract"],
            ),
            Question("Factor x^2 - 9.", 10),
        ],
        [Question("Prove that the sum of two even numbers is even.", 20)],
    ],
    inline,
)

ECONOMICS = Template(
    "points-right-margin.pdf",
    "Points printed at the right margin on the same line as each label.",
    "Economics 10: Problem Set 4",
    [
        [
            Question("What is opportunity cost?", 10, space=170),
            Question("Draw a supply and demand graph.", 15, space=170),
            Question("Explain inflation in one sentence.", 5),
        ]
    ],
    plain,
    right=lambda question: f"{question.points:g} points",
)

SPANISH = [
    Template(
        f"two-versions-{version}.pdf",
        f"Version {version} of a two-version quiz. The versions have the same questions in a different order.",
        f"Spanish 2: Vocabulary Quiz ({version})",
        [[Question(prompt, points) for prompt, points in questions]],
        inline,
    )
    for version, questions in [
        ("A", [("Translate: the library", 4), ("Translate: to run", 3), ("Use 'tener' in a sentence.", 5)]),
        ("B", [("Use 'tener' in a sentence.", 5), ("Translate: the library", 4), ("Translate: to run", 3)]),
    ]
]

TEMPLATES = [PHYSICS, BIOLOGY, CHEMISTRY, HISTORY, MATH, ECONOMICS, *SPANISH]


def main() -> None:
    truth = {}
    for template in TEMPLATES:
        document = render(template)
        document.save(OUT / template.file, garbage=4, deflate=True, no_new_id=True)
        truth[template.file] = expected(template)
        if template is PHYSICS:
            scan = scanned(document)
            scan.save(OUT / "scanned.pdf", garbage=4, deflate=True, no_new_id=True)
            truth["scanned.pdf"] = {
                "description": "The physics midterm rendered to images, so the PDF has no text layer.",
                "title": "",
                "pages": len(template.pages),
                "has_text": False,
                "questions": [],
            }
    (OUT / "expected.json").write_text(json.dumps(truth, indent=2) + "\n")


if __name__ == "__main__":
    main()

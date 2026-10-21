import html
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

import pymupdf
from markdown_it import MarkdownIt

from rubrio._assignment_file import AssignmentFile, FileQuestion, PrintedLine

PAGE = pymupdf.paper_rect("letter")
MARGIN = 54
CONTENT = pymupdf.Rect(MARGIN, MARGIN, PAGE.width - MARGIN, PAGE.height - MARGIN)
MIN_SPACE = 108
PAD = 8
BLANK = re.compile(r"_{4,}")
BLANK_MARK = re.compile(r"\ue000(\d+)\ue001")
CSS = """
body { margin: 0; font-family: sans-serif; font-size: 11pt; line-height: 1.35; }
h1 { font-size: 18pt; text-align: center; margin: 0 0 4pt 0; }
.version { text-align: center; font-size: 12pt; margin: 0; }
.field { margin: 20pt 0 0 0; }
.instructions { margin: 18pt 0 0 0; }
.block { margin: 0; }
.heading { margin: 14pt 0 4pt 0; }
.part { margin-left: 20pt; }
.choice { margin: 3pt 0 3pt 16pt; }
.blank { font-size: 20pt; }
.space { margin: 0; font-size: 1pt; }
pre { font-family: monospace; font-size: 10pt; margin: 6pt 0; }
code { font-family: monospace; }
p { margin: 4pt 0; }
"""
FIELDS: tuple[Literal["name", "sid"], ...] = ("name", "sid")
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")


@dataclass(frozen=True)
class LaidOutBox:
    page: int
    question: str | None
    field: Literal["name", "sid"] | None
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Layout:
    pdf: bytes
    boxes: list[LaidOutBox]


@dataclass
class _Block:
    owner: str | None
    html: str
    space: bool = False


@dataclass
class _Placed:
    tops: dict[str, float] = field(default_factory=dict)
    bottoms: dict[str, float] = field(default_factory=dict)
    spans: dict[str, pymupdf.Rect] = field(default_factory=dict)


def _record(position: Any) -> None:
    placed: _Placed = position.placed
    if not position.id:
        return
    rect = pymupdf.Rect(position.rect)
    if position.open_close & 1:
        placed.tops.setdefault(position.id, rect.y0)
    if position.open_close & 2:
        placed.bottoms[position.id] = rect.y1
    if position.open_close == 3 and rect.width > 0:
        placed.spans[position.id] = placed.spans.get(position.id, rect) | rect


def _blanks(text: str) -> str:
    return BLANK.sub(lambda m: f"\ue000{len(m[0])}\ue001", text)


def _with_blanks(rendered: str, counter: list[int]) -> str:
    def blank(match: re.Match[str]) -> str:
        counter[0] += 1
        return f'<span id="k{counter[0]}" class="blank">{"&#160;" * (3 * int(match[1]))}</span>'

    return BLANK_MARK.sub(blank, rendered)


def _markdown(lines: list[PrintedLine]) -> str:
    text: list[str] = []
    for previous, line in zip([None, *lines], lines, strict=False):
        if previous is not None and line.line > previous.line + 1:
            text.append("")
        text.append(line.text)
    return MARKDOWN.render(_blanks("\n".join(text)))


def _points(points: float | None, bonus: bool) -> str:
    value = f"{points or 0:g}"
    return f"({value} {'bonus ' if bonus else ''}point{'' if value == '1' else 's'})"


def _heading(question: FileQuestion) -> str:
    label = f"{question.number[-1]})" if question.parent else f"{question.number}."
    prompt = MARKDOWN.renderInline(_blanks(question.prompt))
    return (
        f'<p class="heading"><b>{label}</b> {prompt} '
        f"<b>{html.escape(_points(question.points, question.bonus))}</b></p>"
    )


def _blocks(file: AssignmentFile, version: str) -> list[list[_Block]]:
    questions = {q.line: q for q in file.questions if q.version == version}
    pages: list[list[_Block]] = [[]]
    current: FileQuestion | None = None
    pending: list[PrintedLine] = []
    letters = 0

    def owner() -> str | None:
        return current.number if current is not None and current.kind != "parts" else None

    def wrap(content: str) -> _Block:
        indented = current is not None and current.parent is not None
        return _Block(owner(), f'<div class="part">{content}</div>' if indented else content)

    def flush() -> None:
        nonlocal letters
        parts: list[str] = []
        run: list[PrintedLine] = []
        for line in [*pending, None]:
            if line is not None and line.kind == "text":
                run.append(line)
                continue
            if run:
                parts.append(_markdown(run))
                run = []
            if line is not None:
                choice = MARKDOWN.renderInline(_blanks(line.text.removeprefix("- [ ] ")))
                parts.append(f'<p class="choice"><b>{chr(65 + letters)})</b> {choice}</p>')
                letters += 1
        pending.clear()
        if parts:
            pages[-1].append(wrap("".join(parts)))

    for line in file.printed:
        if line.version != version or line.kind == "version":
            continue
        if line.kind == "heading":
            flush()
            current = questions[line.line]
            letters = 0
            pages[-1].append(wrap(_heading(current)))
        elif line.kind == "page":
            flush()
            pages.append([])
        else:
            pending.append(line)
    flush()
    written = {q.number for q in questions.values() if q.kind == "written"}
    for blocks in pages:
        last = {block.owner: block for block in blocks if block.owner in written}
        for block in last.values():
            block.space = True
    return pages


def _header(file: AssignmentFile, version: str) -> str:
    parts = []
    if file.title:
        parts.append(f"<h1>{html.escape(file.title)}</h1>")
    if len(file.versions) > 1:
        parts.append(f'<p class="version">Version {html.escape(version)}</p>')
    parts.append('<p class="field">Name <span id="name">&#160;</span></p>')
    parts.append('<p class="field">Student ID <span id="sid">&#160;</span></p>')
    if file.instructions:
        parts.append(f'<p class="instructions">{MARKDOWN.renderInline(_blanks(file.instructions))}</p>')
    return "".join(parts)


def _html(header: str, blocks: list[_Block], space: float | None, counter: list[int]) -> str:
    parts = [header]
    for index, block in enumerate(blocks):
        parts.append(f'<div id="b{index}" class="block">{_with_blanks(block.html, counter)}</div>')
        if block.space and space is not None:
            parts.append(f'<p id="s{index}" class="space" style="line-height:{space:.2f}pt">&#160;</p>')
    return "".join(parts)


def _space(header: str, blocks: list[_Block]) -> float:
    spaces = sum(block.space for block in blocks)
    if not spaces:
        return 0
    fitted, space = None, 0.0
    for _ in range(3):
        more, filled = pymupdf.Story(_html(header, blocks, space, [0]), user_css=CSS).place(CONTENT)
        if more:
            break
        fitted = space
        space += math.floor((CONTENT.y1 - pymupdf.Rect(filled).y1 - 1) / spaces * 100) / 100
    return MIN_SPACE if fitted is None else fitted


def _field_lines(placed: _Placed) -> dict[Literal["name", "sid"], tuple[float, float]]:
    found: dict[Literal["name", "sid"], pymupdf.Rect] = {
        n: placed.spans[n] for n in FIELDS if n in placed.spans
    }
    start = max((float(rect.x0) for rect in found.values()), default=0.0) + 8
    return {name: (start, float(rect.y1)) for name, rect in found.items()}


def _boxes(index: int, blocks: list[_Block], placed: _Placed) -> list[LaidOutBox]:
    extent: dict[str, list[float]] = defaultdict(list)
    for i, block in enumerate(blocks):
        if block.owner is None:
            continue
        if f"b{i}" in placed.tops:
            extent[block.owner] += [placed.tops[f"b{i}"] - PAD, placed.bottoms[f"b{i}"] + PAD]
        if f"s{i}" in placed.tops:
            extent[block.owner] += [placed.tops[f"s{i}"], placed.bottoms[f"s{i}"]]
    boxes = [
        LaidOutBox(
            index,
            owner,
            None,
            MARGIN / 2,
            max(0, min(ys)),
            PAGE.width - MARGIN / 2,
            min(PAGE.height, max(ys)),
        )
        for owner, ys in extent.items()
    ]
    for name, (start, y) in _field_lines(placed).items():
        boxes.append(LaidOutBox(index, None, name, start - 4, y - 30, CONTENT.x1 + 4, y + 6))
    return boxes


def render(file: AssignmentFile, version: str) -> Layout:
    pages = _blocks(file, version)
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    laid: list[_Placed] = []
    boxes: list[LaidOutBox] = []
    counter = [0]
    for number, blocks in enumerate(pages):
        header = _header(file, version) if number == 0 else ""
        story = pymupdf.Story(_html(header, blocks, _space(header, blocks), counter), user_css=CSS)
        more = 1
        while more:
            placed = _Placed()
            device = writer.begin_page(PAGE)
            more, _ = story.place(CONTENT)
            story.element_positions(_record, {"placed": placed})
            story.draw(device)
            writer.end_page()
            boxes += _boxes(len(laid), blocks, placed)
            laid.append(placed)
    writer.close()
    with pymupdf.open(stream=out.getvalue(), filetype="pdf") as document:
        for page, placed in zip(document.pages(), laid, strict=True):
            for start, y in _field_lines(placed).values():
                page.draw_line((start, y), (CONTENT.x1, y), width=0.75)
            for name, rect in placed.spans.items():
                if name.startswith("k"):
                    page.draw_rect(pymupdf.Rect(rect.x0, rect.y0 + 1, rect.x1, rect.y1 - 1), width=0.75)
            footer = f"Page {page.number + 1} of {len(laid)}"
            if len(file.versions) > 1:
                footer = f"Version {version}, page {page.number + 1} of {len(laid)}"
            page.insert_textbox(
                pymupdf.Rect(MARGIN, PAGE.height - MARGIN + 18, PAGE.width - MARGIN, PAGE.height - 18),
                footer,
                fontsize=9,
                align=pymupdf.TEXT_ALIGN_CENTER,
            )
        pdf = document.tobytes(garbage=3, deflate=True, no_new_id=True)
    return Layout(pdf, boxes)

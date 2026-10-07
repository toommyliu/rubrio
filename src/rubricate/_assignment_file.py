import math
import re
from dataclasses import dataclass, field

from rubricate.errors import UserError


@dataclass(frozen=True)
class Problem:
    line: int
    message: str


class AssignmentFileError(UserError):
    def __init__(self, problems: list[Problem]):
        self.problems = sorted(problems, key=lambda p: p.line)
        super().__init__("\n".join(f"Line {p.line}: {p.message}" for p in self.problems))


@dataclass
class FileQuestion:
    line: int
    version: str
    number: str
    parent: str | None
    prompt: str
    points: float | None
    page: int
    bonus: bool = False
    key: list[str] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    rubric: list[tuple[str, float]] = field(default_factory=list)
    rubric_line: int | None = None
    answer_lines: list[int] = field(default_factory=list)
    kind: str = "written"


@dataclass(frozen=True)
class AssignmentFile:
    title: str | None
    questions: list[FileQuestion]
    versions: list[str]
    printed: list[tuple[int, str]]
    pages: dict[str, int]


POINTS = re.compile(r"\s*\((\d+(?:\.\d+)?) (bonus )?points?\)\s*$")
ITEM_POINTS = re.compile(r"\s*\(([+-]\d+(?:\.\d+)?)\)\s*$")


def parse(source: str) -> AssignmentFile:
    lines = source.splitlines()
    problems: list[Problem] = []
    questions: list[FileQuestion] = []
    versions: list[str] = []
    printed: list[tuple[int, str]] = []
    title = None
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise AssignmentFileError([Problem(1, "Front matter is not closed with ---. ")])
        for i in range(1, end):
            if not lines[i].strip():
                continue
            key, sep, value = lines[i].partition(":")
            if not sep or key.strip() not in {"title", "instructions"}:
                problems.append(Problem(i + 1, f"Unknown front matter key '{key.strip()}'."))
            elif key.strip() == "title":
                title = value.strip().strip("\"'")
            printed.append((i + 1, lines[i]))
        start = end + 1
    version = "A"
    parent: FileQuestion | None = None
    current: FileQuestion | None = None
    fence: str | None = None
    rubric_mode = False
    page = 1
    number = 0
    part = 0
    outside: list[int] = []
    version_lines: dict[str, int] = {}
    pages: dict[str, int] = {}
    for i in range(start, len(lines)):
        line_no, raw = i + 1, lines[i]
        line = raw.strip()
        if fence is not None:
            if current and current.kind == "written" and "____" in raw:
                current.kind = "blank"
            if re.fullmatch(rf" {{0,3}}{fence[0]}{{{len(fence)},}}[ \t]*", raw):
                fence = None
            printed.append((line_no, raw))
            continue
        opener = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", raw)
        if opener and (opener[1][0] == "~" or "`" not in opener[2]):
            fence = opener[1]
            rubric_mode = False
            if current is None:
                outside.append(line_no)
            elif current.kind == "written" and "____" in raw:
                current.kind = "blank"
            printed.append((line_no, raw))
            continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+)", line)
        if heading:
            level, text = len(heading[1]), heading[2]
            rubric_mode = False
            if level == 1:
                match = re.fullmatch(r"Version\s+(.+)", text)
                if not match:
                    problems.append(Problem(line_no, "Use '# Version X' for a version heading."))
                    continue
                version = match[1].strip()
                if version in {".", ".."}:
                    problems.append(Problem(line_no, "A version cannot be named '.' or '..'."))
                if version in versions:
                    problems.append(Problem(line_no, f"Version '{version}' appears more than once."))
                versions.append(version)
                version_lines[version] = line_no
                parent = current = None
                number = part = 0
                page = pages[version] = 1
                printed.append((line_no, raw))
                continue
            if not versions:
                outside.append(line_no)
            points_match = POINTS.search(text)
            points = float(points_match[1]) if points_match else None
            bonus = bool(points_match and points_match[2])
            prompt = text[: points_match.start()].strip() if points_match else text
            if level == 2:
                number += 1
                part = 0
                current = FileQuestion(line_no, version, str(number), None, prompt, points, page, bonus)
                parent = current
            else:
                if parent is None:
                    problems.append(Problem(line_no, "A part needs a question heading before it."))
                    continue
                part += 1
                parent.kind = "parts"
                current = FileQuestion(
                    line_no, version, f"{number}{chr(96 + part)}", parent.number, prompt, points, page, bonus
                )
            questions.append(current)
            printed.append((line_no, "#" * level + " " + prompt))
            continue
        if line == "---":
            page = pages[version] = page + 1
            rubric_mode = False
            printed.append((line_no, raw))
            continue
        if current is None:
            if line:
                outside.append(line_no)
                printed.append((line_no, raw))
            continue
        if line.startswith("Answer:"):
            current.key.append(raw[raw.index("Answer:") + 7 :].strip())
            current.answer_lines.append(line_no)
            rubric_mode = False
            continue
        if line == "Rubric:":
            current.rubric_line = line_no
            rubric_mode = True
            continue
        if rubric_mode and line.startswith("- "):
            description = line[2:].strip()
            match = ITEM_POINTS.search(description)
            points = float(match[1]) if match else 0.0
            description = description[: match.start()].strip() if match else description
            current.rubric.append((description, points))
            continue
        if not line:
            continue
        rubric_mode = False
        choice = re.fullmatch(r"- \[([ xX])\]\s+(.+)", line)
        if choice:
            current.kind = "choice"
            letter = chr(65 + len(current.choices))
            current.choices.append(choice[2])
            if choice[1].lower() == "x":
                current.key.append(f"{letter}) {choice[2]}")
            printed.append((line_no, f"- [ ] {choice[2]}"))
        else:
            if current.kind == "written" and "____" in raw:
                current.kind = "blank"
            printed.append((line_no, raw))
    if versions and outside:
        problems.extend(Problem(n, "Put all content under a version heading.") for n in outside)
    if not versions:
        versions = ["A"]
    if not questions:
        problems.append(Problem(1, "The assignment has no questions."))
    for name, line_no in version_lines.items():
        if not any(q.version == name for q in questions):
            problems.append(Problem(line_no, f"Version '{name}' has no questions."))
    for q in questions:
        if q.points is not None and not math.isfinite(q.points):
            problems.append(Problem(q.line, "Question points must be a finite number."))
        if any(not math.isfinite(points) for _, points in q.rubric):
            problems.append(Problem(q.rubric_line or q.line, "Rubric points must be finite numbers."))
        if q.kind == "parts":
            children = [c for c in questions if c.version == q.version and c.parent == q.number]
            if q.bonus:
                problems.append(Problem(q.line, "Put bonus points on the parts, not their parent question."))
            q.bonus = all(c.bonus for c in children)
            total = round(sum(c.points or 0 for c in children), 9)
            if q.points is not None and not math.isclose(q.points, total, rel_tol=0, abs_tol=1e-9):
                problems.append(Problem(q.line, "A question's points must equal the sum of its parts."))
            q.points = total
            problems.extend(
                Problem(n, "Put the answer key on the part, not its parent question.") for n in q.answer_lines
            )
            if q.rubric_line:
                problems.append(
                    Problem(q.rubric_line, "Put the rubric on the part, not its parent question.")
                )
        elif q.points is None:
            problems.append(Problem(q.line, "Add points at the end of the heading, such as '(10 points)'."))
        if q.kind == "choice" and not any(
            k.startswith(tuple(f"{chr(65 + j)}) " for j in range(len(q.choices)))) for k in q.key
        ):
            problems.append(Problem(q.line, "Mark at least one correct choice with [x]."))
        if q.kind == "written" and "____" in q.prompt:
            q.kind = "blank"
        if q.rubric_line is not None and not q.rubric:
            problems.append(Problem(q.rubric_line, "Rubric: needs at least one item."))
        if any(p < 0 for _, p in q.rubric) and any(p > 0 for _, p in q.rubric):
            problems.append(
                Problem(q.rubric_line or q.line, "Rubric items cannot mix positive and negative points.")
            )
    if problems:
        raise AssignmentFileError(problems)
    return AssignmentFile(title, questions, versions, printed, pages)

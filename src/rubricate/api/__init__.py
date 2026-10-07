import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from rubricate import assignment, courses, export, grading, jobs, names, scans, template
from rubricate.assignment import AssignmentFileError, AssignmentInfo, Problem, VersionSummary
from rubricate.courses import Course, RosterChange, Student
from rubricate.errors import NeedsConfirmation, NotFound, StaleRevision, UserError
from rubricate.grading import (
    Grade,
    QuestionInfo,
    ResponseInfo,
    RubricItem,
    SubmissionReview,
    SubmissionScores,
)
from rubricate.home import Home
from rubricate.jobs import Job, Runner
from rubricate.names import NameRow
from rubricate.scans import ScansOverview
from rubricate.template import Box, Outline, TemplatePage

STATIC = Path(__file__).parent.parent / "static"

ACTOR = "local"


class ErrorBody(BaseModel):
    kind: Literal["invalid", "not_found", "stale", "needs_confirmation", "file"]
    message: str
    problems: list[Problem] = []
    affected: int = 0


router = APIRouter(
    prefix="/api",
    responses={400: {"model": ErrorBody}, 404: {"model": ErrorBody}, 409: {"model": ErrorBody}},
)


def get_home(request: Request) -> Home:
    return request.app.state.home


def get_db(home: Annotated[Home, Depends(get_home)]) -> Iterator[sqlite3.Connection]:
    db = home.connect()
    try:
        yield db
    finally:
        db.close()


def get_runner(request: Request) -> Runner:
    return request.app.state.runner


HomeDep = Annotated[Home, Depends(get_home)]

Db = Annotated[sqlite3.Connection, Depends(get_db)]

RunnerDep = Annotated[Runner, Depends(get_runner)]


PNG_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}
}


def png(path: Path | None) -> FileResponse:
    if path is None:
        raise NotFound("There's nothing to show here yet.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


class About(BaseModel):
    version: str


@router.get("/about")
def about() -> About:
    return About(version=version("rubricate"))


@router.get("/courses")
def list_courses(db: Db) -> list[Course]:
    return courses.list_courses(db)


class NewCourse(BaseModel):
    name: str
    term: str = ""
    slug: str | None = None


@router.post("/courses")
def create_course(body: NewCourse, db: Db) -> Course:
    return courses.create_course(db, body.name, body.term, body.slug)


class CourseDetail(BaseModel):
    course: Course
    roster: list[Student]
    assignments: list[AssignmentInfo]


@router.get("/courses/{course}")
def get_course(course: str, db: Db) -> CourseDetail:
    found = courses.get_course(db, course)
    return CourseDetail(
        course=found,
        roster=courses.roster(db, found.id),
        assignments=assignment.list_for_course(db, found.id),
    )


class RosterImport(BaseModel):
    csv: str
    dry_run: bool = False
    expected: RosterChange | None = None


@router.post("/courses/{course}/roster")
def import_roster(course: str, body: RosterImport, db: Db, home: HomeDep, runner: RunnerDep) -> RosterChange:
    found = courses.get_course(db, course)
    change = courses.import_roster(db, found.id, body.csv, body.dry_run, body.expected)
    if not body.dry_run:
        for info in assignment.list_for_course(db, found.id):
            if info.has_scans:
                runner.start(db, info.id, "names", names_work(home, info.id))
    return change


class Source(BaseModel):
    source: str


@router.post("/check")
def check(body: Source) -> list[VersionSummary]:
    return assignment.check(body.source)


class NewAssignment(BaseModel):
    source: str
    slug: str | None = None


@router.post("/courses/{course}/assignments")
def create_assignment(course: str, body: NewAssignment, db: Db) -> AssignmentInfo:
    return assignment.create(db, courses.get_course(db, course).id, body.source, body.slug)


@router.get("/courses/{course}/assignments/{slug}")
def get_assignment(course: str, slug: str, db: Db) -> AssignmentInfo:
    return assignment.get(db, course, slug)


class AssignmentEdit(BaseModel):
    source: str
    confirm: bool = False


@router.put("/courses/{course}/assignments/{slug}")
def edit_assignment(course: str, slug: str, body: AssignmentEdit, db: Db) -> AssignmentInfo:
    return assignment.edit(db, assignment.get(db, course, slug).id, body.source, body.confirm)


@router.delete("/courses/{course}/assignments/{slug}")
def delete_assignment(course: str, slug: str, db: Db) -> None:
    assignment.delete(db, assignment.get(db, course, slug).id)


@router.post("/courses/{course}/assignments/{slug}/templates/{version:path}")
def upload_template(
    course: str, slug: str, version: str, file: UploadFile, db: Db, home: HomeDep
) -> list[TemplatePage]:
    return template.upload(home, db, assignment.get(db, course, slug).id, version, file.file.read())


@router.get("/courses/{course}/assignments/{slug}/outline")
def get_outline(course: str, slug: str, db: Db) -> Outline:
    return template.outline(db, assignment.get(db, course, slug).id)


@router.get("/template-pages/{page}/image", response_class=Response, responses=PNG_RESPONSE)
def template_page_image(page: int, db: Db, home: HomeDep) -> FileResponse:
    return png(template.page_image(home, db, page))


class Rect(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class NewBox(Rect):
    template_page: int
    question: int | None = None
    field: Literal["name", "sid"] | None = None


@router.post("/boxes")
def add_box(body: NewBox, db: Db, home: HomeDep, runner: RunnerDep) -> Box:
    added = template.add_box(
        db, body.template_page, body.question, body.field, body.x0, body.y0, body.x1, body.y1
    )
    if added.field:
        rematch_scanned(db, home, runner, added.template_page)
    return added


@router.put("/boxes/{box}")
def update_box(box: int, body: Rect, db: Db, home: HomeDep, runner: RunnerDep) -> Box:
    updated = template.update_box(db, box, body.x0, body.y0, body.x1, body.y1)
    if updated.field:
        rematch_scanned(db, home, runner, updated.template_page)
    return updated


@router.delete("/boxes/{box}")
def delete_box(box: int, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    row = db.execute("SELECT template_page, field FROM box WHERE id = ?", (box,)).fetchone()
    template.delete_box(db, box)
    if row["field"]:
        rematch_scanned(db, home, runner, row["template_page"])


def rematch_scanned(db: sqlite3.Connection, home: Home, runner: Runner, template_page: int) -> None:
    row = db.execute(
        """
        SELECT t.assignment
        FROM template_page t
        WHERE t.id = ? AND EXISTS (SELECT 1 FROM scan WHERE scan.assignment = t.assignment)
        """,
        (template_page,),
    ).fetchone()
    if row:
        rematch(db, home, runner, row[0])


def names_work(home: Home, assignment_id: int) -> jobs.Work:
    def work(db: sqlite3.Connection, progress: scans.Progress) -> str:
        names.match_names(home, db, assignment_id, progress)
        return "Matched names against the roster."

    return work


@router.post("/courses/{course}/assignments/{slug}/scans")
def upload_scans(
    course: str, slug: str, files: list[UploadFile], db: Db, home: HomeDep, runner: RunnerDep
) -> Job:
    assignment_id = assignment.get(db, course, slug).id
    uploads = [(file.filename or "scan.pdf", home.store(file.file.read(), ".pdf")) for file in files]
    if not uploads:
        raise UserError("Choose at least one PDF to upload.")

    def work(db: sqlite3.Connection, progress: scans.Progress) -> str:
        lines = []
        for name, file in uploads:
            report = scans.ingest(home, db, assignment_id, home.file(file).read_bytes(), name, progress)
            if report.already_uploaded:
                lines.append(f"{name} was already uploaded, so nothing changed.")
            else:
                lines.append(
                    f"{name}: {report.matched} of {report.pages} pages matched, "
                    f"{report.submissions} submissions, {report.flags} flags."
                )
        return " ".join(lines)

    return runner.start(db, assignment_id, "scan", work)


@router.post("/courses/{course}/assignments/{slug}/names/match")
def rematch_names(course: str, slug: str, db: Db, home: HomeDep, runner: RunnerDep) -> Job:
    assignment_id = assignment.get(db, course, slug).id
    return runner.start(db, assignment_id, "names", names_work(home, assignment_id))


@router.get("/courses/{course}/assignments/{slug}/jobs")
def list_jobs(course: str, slug: str, db: Db) -> list[Job]:
    return jobs.recent(db, assignment.get(db, course, slug).id)


@router.get("/courses/{course}/assignments/{slug}/scans")
def get_scans(course: str, slug: str, db: Db) -> ScansOverview:
    return scans.overview(db, assignment.get(db, course, slug).id)


@router.get("/scan-pages/{page}/image", response_class=Response, responses=PNG_RESPONSE)
def scan_page_image(page: int, db: Db, home: HomeDep) -> FileResponse:
    return png(scans.scan_page_image(home, db, page))


def assignment_of_page(db: sqlite3.Connection, page: int) -> int:
    row = db.execute(
        "SELECT scan.assignment FROM scan_page JOIN scan ON scan.id = scan_page.scan WHERE scan_page.id = ?",
        (page,),
    ).fetchone()
    if row is None:
        raise NotFound("That scan page doesn't exist.")
    return row[0]


def assignment_of_submission(db: sqlite3.Connection, submission: int) -> int:
    row = db.execute("SELECT assignment FROM submission WHERE id = ?", (submission,)).fetchone()
    if row is None:
        raise NotFound("That submission doesn't exist.")
    return row[0]


def rematch(db: sqlite3.Connection, home: Home, runner: Runner, assignment_id: int) -> None:
    runner.start(db, assignment_id, "names", names_work(home, assignment_id))


@router.delete("/scans/{scan}")
def delete_scan(scan: int, db: Db) -> None:
    scans.delete_scan(db, scan)


@router.delete("/submissions/{submission}")
def remove_submission(submission: int, db: Db) -> None:
    scans.remove_submission(db, submission)


class PageMove(BaseModel):
    submission: int | None
    position: int | None = None
    confirm: bool = False


@router.post("/scan-pages/{page}/move")
def move_page(page: int, body: PageMove, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    assignment_id = assignment_of_page(db, page)
    scans.move_page(db, page, body.submission, body.position, body.confirm)
    rematch(db, home, runner, assignment_id)


class ExtraMark(BaseModel):
    extra: bool


@router.post("/scan-pages/{page}/extra")
def mark_extra(page: int, body: ExtraMark, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    scans.mark_extra(db, page, body.extra)
    rematch(db, home, runner, assignment_of_page(db, page))


class Split(BaseModel):
    first_scan_page: int


class Created(BaseModel):
    id: int


@router.post("/submissions/{submission}/split")
def split_submission(submission: int, body: Split, db: Db, home: HomeDep, runner: RunnerDep) -> Created:
    assignment_id = assignment_of_submission(db, submission)
    created = Created(id=scans.split(db, submission, body.first_scan_page))
    rematch(db, home, runner, assignment_id)
    return created


class PageOrder(BaseModel):
    scan_pages: list[int]


@router.post("/submissions/{submission}/order")
def reorder_pages(submission: int, body: PageOrder, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    scans.reorder(db, submission, body.scan_pages)
    rematch(db, home, runner, assignment_of_submission(db, submission))


class Merge(BaseModel):
    submissions: list[int]
    confirm: bool = False


@router.post("/submissions/merge")
def merge_submissions(body: Merge, db: Db, home: HomeDep, runner: RunnerDep) -> Created:
    if not body.submissions:
        raise UserError("Choose the submissions to merge.")
    assignment_id = assignment_of_submission(db, body.submissions[0])
    created = Created(id=scans.merge(db, body.submissions, body.confirm))
    rematch(db, home, runner, assignment_id)
    return created


@router.get("/courses/{course}/assignments/{slug}/names")
def get_names(course: str, slug: str, db: Db) -> list[NameRow]:
    return names.names(db, assignment.get(db, course, slug).id)


class StudentChoice(BaseModel):
    sid: str | None


@router.post("/submissions/{submission}/student")
def confirm_student(submission: int, body: StudentChoice, db: Db) -> None:
    names.confirm(db, submission, body.sid)


@router.get("/submissions/{submission}/fields/{field}/image", response_class=Response, responses=PNG_RESPONSE)
def field_image(submission: int, field: Literal["name", "sid"], db: Db, home: HomeDep) -> FileResponse:
    return png(scans.field_crop(home, db, submission, field))


@router.get("/courses/{course}/assignments/{slug}/questions")
def get_questions(course: str, slug: str, db: Db) -> list[QuestionInfo]:
    return grading.questions(db, assignment.get(db, course, slug).id)


class ItemEdit(BaseModel):
    description: str
    points: float


class ItemUpdate(ItemEdit):
    confirm: bool = False


@router.post("/questions/{question}/rubric")
def add_rubric_item(question: int, body: ItemEdit, db: Db) -> RubricItem:
    return grading.add_item(db, question, body.description, body.points, ACTOR)


@router.put("/rubric-items/{item}")
def update_rubric_item(item: int, body: ItemUpdate, db: Db) -> RubricItem:
    return grading.update_item(db, item, body.description, body.points, ACTOR, body.confirm)


@router.delete("/rubric-items/{item}")
def delete_rubric_item(item: int, db: Db, confirm: bool = False) -> None:
    grading.delete_item(db, item, ACTOR, confirm)


@router.get("/questions/{question}/responses")
def get_responses(question: int, db: Db) -> list[ResponseInfo]:
    return grading.responses(db, question)


@router.get(
    "/submissions/{submission}/questions/{question}/crop", response_class=Response, responses=PNG_RESPONSE
)
def crop_image(submission: int, question: int, db: Db, home: HomeDep) -> FileResponse:
    return png(scans.crop(home, db, submission, question))


class GradeSave(BaseModel):
    applied: list[int]
    adjustment: float = 0
    comment: str = ""
    revision: int
    score: float | None = None


@router.put("/submissions/{submission}/questions/{question}/grade")
def save_grade(submission: int, question: int, body: GradeSave, db: Db) -> Grade:
    return grading.save_grade(
        db,
        submission,
        question,
        body.applied,
        body.adjustment,
        body.comment,
        body.revision,
        ACTOR,
        score=body.score,
    )


@router.get("/submissions/{submission}/review")
def get_review(submission: int, db: Db) -> SubmissionReview:
    return grading.review(db, submission)


@router.get("/courses/{course}/assignments/{slug}/scores")
def get_scores(course: str, slug: str, db: Db) -> list[SubmissionScores]:
    return grading.scores(db, assignment.get(db, course, slug).id)


@router.get("/courses/{course}/assignments/{slug}/export/gradebook.csv", response_class=Response)
def gradebook_csv(course: str, slug: str, db: Db) -> Response:
    result = export.gradebook_csv(db, assignment.get(db, course, slug).id)
    return Response(
        result.csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{course}-{slug}.csv"'},
    )


@router.get("/courses/{course}/assignments/{slug}/export/feedback.zip", response_class=Response)
def feedback_zip(course: str, slug: str, db: Db, home: HomeDep) -> Response:
    data = export.feedback_pdfs(home, db, assignment.get(db, course, slug).id)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{course}-{slug}-feedback.zip"'},
    )


def error(status: int, body: ErrorBody) -> JSONResponse:
    return JSONResponse(body.model_dump(), status_code=status)


async def on_user_error(_: Request, e: Exception) -> JSONResponse:
    match e:
        case AssignmentFileError():
            return error(400, ErrorBody(kind="file", message=e.message, problems=e.problems))
        case NotFound():
            return error(404, ErrorBody(kind="not_found", message=e.message))
        case StaleRevision():
            return error(409, ErrorBody(kind="stale", message=e.message))
        case NeedsConfirmation():
            return error(409, ErrorBody(kind="needs_confirmation", message=e.message, affected=e.affected))
        case UserError():
            return error(400, ErrorBody(kind="invalid", message=e.message))
    raise e


class SinglePageApp(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(home: Home) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = Runner(home)
        yield
        app.state.runner.shutdown()

    app = FastAPI(title="Rubricate", lifespan=lifespan)
    app.state.home = home
    app.add_exception_handler(UserError, on_user_error)
    app.include_router(router)
    if (STATIC / "index.html").is_file():
        app.mount("/", SinglePageApp(directory=STATIC, html=True))
    return app

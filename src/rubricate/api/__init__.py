import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from rubricate import assignment, courses, grading, jobs, scans, template
from rubricate.assignment import AssignmentFileError, AssignmentInfo, Problem, VersionSummary
from rubricate.courses import Course, RosterChange, Student
from rubricate.errors import NeedsConfirmation, NotFound, StaleRevision, UserError
from rubricate.grading import QuestionInfo
from rubricate.home import Home
from rubricate.jobs import Job, Runner
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
def import_roster(course: str, body: RosterImport, db: Db) -> RosterChange:
    found = courses.get_course(db, course)
    return courses.import_roster(db, found.id, body.csv, body.dry_run, body.expected)


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


@router.get("/template-pages/{page}/image")
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
def add_box(body: NewBox, db: Db) -> Box:
    return template.add_box(
        db, body.template_page, body.question, body.field, body.x0, body.y0, body.x1, body.y1
    )


@router.put("/boxes/{box}")
def update_box(box: int, body: Rect, db: Db) -> Box:
    return template.update_box(db, box, body.x0, body.y0, body.x1, body.y1)


@router.delete("/boxes/{box}")
def delete_box(box: int, db: Db) -> None:
    template.delete_box(db, box)


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


@router.get("/courses/{course}/assignments/{slug}/jobs")
def list_jobs(course: str, slug: str, db: Db) -> list[Job]:
    return jobs.recent(db, assignment.get(db, course, slug).id)


@router.get("/courses/{course}/assignments/{slug}/scans")
def get_scans(course: str, slug: str, db: Db) -> ScansOverview:
    return scans.overview(db, assignment.get(db, course, slug).id)


@router.get("/scan-pages/{page}/image")
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


@router.delete("/scans/{scan}")
def delete_scan(scan: int, db: Db) -> None:
    scans.delete_scan(db, scan)


@router.delete("/submissions/{submission}")
def remove_submission(submission: int, db: Db) -> None:
    scans.remove_submission(db, submission)


class PageMove(BaseModel):
    submission: int | None
    position: int | None = None


@router.post("/scan-pages/{page}/move")
def move_page(page: int, body: PageMove, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    scans.move_page(db, page, body.submission, body.position)


class ExtraMark(BaseModel):
    extra: bool


@router.post("/scan-pages/{page}/extra")
def mark_extra(page: int, body: ExtraMark, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    scans.mark_extra(db, page, body.extra)


class Split(BaseModel):
    first_scan_page: int


class Created(BaseModel):
    id: int


@router.post("/submissions/{submission}/split")
def split_submission(submission: int, body: Split, db: Db, home: HomeDep, runner: RunnerDep) -> Created:
    created = Created(id=scans.split(db, submission, body.first_scan_page))
    return created


class PageOrder(BaseModel):
    scan_pages: list[int]


@router.post("/submissions/{submission}/order")
def reorder_pages(submission: int, body: PageOrder, db: Db, home: HomeDep, runner: RunnerDep) -> None:
    scans.reorder(db, submission, body.scan_pages)


class Merge(BaseModel):
    submissions: list[int]


@router.post("/submissions/merge")
def merge_submissions(body: Merge, db: Db, home: HomeDep, runner: RunnerDep) -> Created:
    if not body.submissions:
        raise UserError("Choose the submissions to merge.")
    created = Created(id=scans.merge(db, body.submissions))
    return created


@router.get("/courses/{course}/assignments/{slug}/questions")
def get_questions(course: str, slug: str, db: Db) -> list[QuestionInfo]:
    return grading.questions(db, assignment.get(db, course, slug).id)


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

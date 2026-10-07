import sqlite3
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from rubricate import assignment, courses, grading
from rubricate.assignment import AssignmentFileError, AssignmentInfo, Problem, VersionSummary
from rubricate.courses import Course, RosterChange, Student
from rubricate.errors import NeedsConfirmation, NotFound, StaleRevision, UserError
from rubricate.grading import QuestionInfo
from rubricate.home import Home

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


HomeDep = Annotated[Home, Depends(get_home)]

Db = Annotated[sqlite3.Connection, Depends(get_db)]


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
    app = FastAPI(title="Rubricate")
    app.state.home = home
    app.add_exception_handler(UserError, on_user_error)
    app.include_router(router)
    if (STATIC / "index.html").is_file():
        app.mount("/", SinglePageApp(directory=STATIC, html=True))
    return app

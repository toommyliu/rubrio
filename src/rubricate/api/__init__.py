from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from rubricate.home import Home

STATIC = Path(__file__).parent.parent / "static"

router = APIRouter(prefix="/api")


class About(BaseModel):
    version: str


@router.get("/about")
def about() -> About:
    return About(version=version("rubricate"))


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
    app.include_router(router)
    if (STATIC / "index.html").is_file():
        app.mount("/", SinglePageApp(directory=STATIC, html=True))
    return app

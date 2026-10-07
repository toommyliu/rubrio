import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

import cv2
import pymupdf
from numpy.typing import NDArray

from rubricate.errors import UserError
from rubricate.home import Home

DPI = 150
MAX_PIXELS = 50_000_000
PDF_LOCK = Lock()


def cache_path(home: Home, kind: str, dependencies: object, suffix: str = ".png") -> Path:
    digest = hashlib.sha256(json.dumps(dependencies, sort_keys=True).encode()).hexdigest()
    return home.cache / f"{kind}-{digest}{suffix}"


def write_bytes(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        partial = Path(stream.name)
        stream.write(data)
    os.replace(partial, path)


def write_image(path: Path, image: NDArray[Any]) -> None:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise UserError("Could not save the page image.")
    write_bytes(path, encoded.tobytes())


def pdf_document(data: bytes) -> Any:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
        if not document.is_pdf or document.needs_pass or not document.page_count:
            document.close()
            raise UserError("Upload a PDF with at least one page and no password.")
        for index in range(document.page_count):
            rect = document[index].rect
            width, height = rect.width / 72, rect.height / 72
            if width * height * DPI * DPI > MAX_PIXELS:
                document.close()
                raise UserError(
                    f"Page {index + 1} is {width:.0f} by {height:.0f} inches, too large to show. "
                    "Check the PDF's page size."
                )
        return document
    except (RuntimeError, ValueError) as exc:
        raise UserError("The file could not be read as a PDF.") from exc


def rendered(home: Home, file: str, page_index: int) -> Path:
    path = cache_path(home, "page", [file, page_index, DPI])
    if not path.exists():
        with PDF_LOCK, pymupdf.open(home.file(file)) as document:
            pix = document[page_index].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
            write_bytes(path, pix.tobytes("png"))
    return path


def read_image(path: Path) -> NDArray[Any]:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        path.unlink(missing_ok=True)
        raise UserError("The page image could not be read. Reload this page to recreate it.")
    return image

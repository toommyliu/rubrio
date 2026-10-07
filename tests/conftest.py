import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

ARTIFACTS = Path(__file__).parent.parent / "artifacts"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    rubricate = Path(sys.executable).parent / "rubricate"
    proc = subprocess.Popen(
        [rubricate, "--home", str(tmp_path / "home"), "serve", "--no-open", "--port", str(port)]
    )
    try:
        deadline = time.monotonic() + 180
        while True:
            try:
                urllib.request.urlopen(f"{url}/api/about")
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        yield url
    finally:
        proc.terminate()
        proc.wait()


@pytest.fixture
def artifacts() -> Path:
    ARTIFACTS.mkdir(exist_ok=True)
    return ARTIFACTS

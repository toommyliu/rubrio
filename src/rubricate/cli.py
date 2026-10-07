import ipaddress
import json
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import click
import uvicorn

from rubricate.api import STATIC, create_app

WEB = Path(__file__).parents[2] / "web"


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_web_app() -> None:
    manager = json.loads((WEB / "package.json").read_text())["packageManager"].split("@")[0]
    print(f"The web app isn't built yet, so Rubricate is building it with {manager}.", file=sys.stderr)
    try:
        subprocess.run([manager, "install"], cwd=WEB, check=True)
        subprocess.run([manager, "run", "build"], cwd=WEB, check=True)
    except FileNotFoundError as e:
        raise click.ClickException(f"Building the web app needs {manager}, which isn't installed.") from e
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"Building the web app failed. The {manager} output above says why."
        ) from e
    print("Built the web app.", file=sys.stderr)


def open_when_started(server: uvicorn.Server, url: str) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.1)
    webbrowser.open(url)


@click.group()
@click.version_option(package_name="rubricate")
def main() -> None:
    pass


@main.command()
@click.option(
    "--host",
    default="127.0.0.1",
    envvar="RUBRICATE_HOST",
    show_default=True,
    show_envvar=True,
    help="Address to listen on.",
)
@click.option(
    "--port",
    default=8765,
    envvar="RUBRICATE_PORT",
    show_default=True,
    show_envvar=True,
    help="Port to listen on.",
)
@click.option("--no-open", is_flag=True, help="Don't open a browser.")
def serve(host: str, port: int, no_open: bool) -> None:
    """Run the web app until stopped."""
    if not is_loopback(host):
        raise click.ClickException(
            f"Listening on {host} is hosted mode, which needs Google sign-in. "
            "Rubricate doesn't support it yet, so use --host 127.0.0.1."
        )
    if not (STATIC / "index.html").is_file():
        build_web_app()
    server = uvicorn.Server(uvicorn.Config(create_app(), host=host, port=port))
    if not no_open:
        threading.Thread(
            target=open_when_started, args=(server, f"http://{host}:{port}"), daemon=True
        ).start()
    server.run()

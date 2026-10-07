import json
import sys
from pathlib import Path

from rubrio.api import create_app
from rubrio.home import Home


def main(path: str) -> None:
    Path(path).write_text(json.dumps(create_app(Home(Path("."))).openapi(), indent=1))


if __name__ == "__main__":
    main(sys.argv[1])

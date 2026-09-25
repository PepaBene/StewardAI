"""Write the API's OpenAPI spec: `uv run python -m api.export_openapi [path]`.

The committed openapi.json is the contract the frontend generates its types from;
a test fails if it drifts from what the code produces.
"""

import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def build_spec() -> dict[str, Any]:
    from api.main import app

    return app.openapi()


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    path.write_text(render(build_spec()), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

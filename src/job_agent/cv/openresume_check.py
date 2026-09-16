"""Invokes the vendored OpenResume parser (see cv/openresume/, NOTICE.md) as
a subprocess. Only used by `job render-test` on template change (PROJECT.md
§5.6 step 3). Requires `npm install` to have been run once in
cv/openresume/ (see README) -- this module never runs npm itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

OPENRESUME_DIR = Path(__file__).resolve().parent / "openresume"


class OpenResumeCheckError(Exception):
    pass


def run_openresume_parser(pdf_path: Path | str) -> dict[str, Any]:
    if not (OPENRESUME_DIR / "node_modules").exists():
        raise OpenResumeCheckError(
            f"{OPENRESUME_DIR}/node_modules missing -- run `npm install` in "
            f"{OPENRESUME_DIR} first (see README)."
        )

    result = subprocess.run(
        ["npx", "tsx", "run-parser.ts", str(Path(pdf_path).resolve())],
        cwd=OPENRESUME_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise OpenResumeCheckError(
            f"OpenResume parser failed:\n{result.stderr or result.stdout}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OpenResumeCheckError(
            f"OpenResume parser did not return valid JSON: {result.stdout!r}"
        ) from exc

"""Renders CV content (see content.py) to PDF via the typst CLI.

See PROJECT.md §5.6 steps 2-3, §8. Typst syntax verified live against
https://typst.app/docs on 2026-09-16 for the installed typst 0.15.1.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TEMPLATE_PATH = Path(__file__).resolve().parent / "template.typ"


class RenderError(Exception):
    pass


def render_cv(content: dict[str, Any], output_path: Path | str) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(content, tmp)
        data_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                "typst",
                "compile",
                "--root=/",  # data_path is an absolute filesystem path, not project-relative
                f"--input=data_path={data_path}",
                str(TEMPLATE_PATH),
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RenderError(f"typst compile failed:\n{result.stderr}")
    finally:
        data_path.unlink(missing_ok=True)

    return output_path

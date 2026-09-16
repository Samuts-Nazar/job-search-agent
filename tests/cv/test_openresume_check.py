import shutil

import pytest
import yaml

from job_agent.cv.content import build_cv_content
from job_agent.cv.openresume_check import (
    OPENRESUME_DIR,
    OpenResumeCheckError,
    run_openresume_parser,
)
from job_agent.cv.render import render_cv

FACTS = yaml.safe_load(open("data.example/facts.example.yaml", encoding="utf-8"))

_ready = shutil.which("typst") is not None and (OPENRESUME_DIR / "node_modules").exists()
pytestmark = pytest.mark.skipif(not _ready, reason="typst and/or openresume node_modules missing")


def test_run_openresume_parser_extracts_identity_fields(tmp_path):
    content = build_cv_content(FACTS, track_id="qa_automation")
    pdf_path = render_cv(content, tmp_path / "cv.pdf")

    resume = run_openresume_parser(pdf_path)

    assert resume["profile"]["email"] == content["contact"]["email"]
    assert resume["profile"]["name"] == content["name"]


def test_run_openresume_parser_missing_file_raises(tmp_path):
    with pytest.raises(OpenResumeCheckError):
        run_openresume_parser(tmp_path / "does-not-exist.pdf")

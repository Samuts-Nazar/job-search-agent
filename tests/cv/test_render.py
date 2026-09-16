import shutil

import pytest
import yaml

from job_agent.cv.ats_checks import check_ats
from job_agent.cv.content import build_cv_content
from job_agent.cv.render import RenderError, render_cv

FACTS = yaml.safe_load(open("data.example/facts.example.yaml", encoding="utf-8"))

pytestmark = pytest.mark.skipif(shutil.which("typst") is None, reason="typst CLI not on PATH")


def test_render_cv_produces_a_pdf_that_passes_ats_checks(tmp_path):
    content = build_cv_content(FACTS, track_id="qa_automation")
    output_path = tmp_path / "cv.pdf"

    result_path = render_cv(content, output_path)

    assert result_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    ats_result = check_ats(output_path, content)
    assert ats_result.ok, ats_result.problems


def test_render_cv_raises_on_invalid_content(tmp_path):
    output_path = tmp_path / "cv.pdf"
    with pytest.raises(RenderError):
        render_cv({"name": "Missing Required Fields"}, output_path)

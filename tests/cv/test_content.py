import yaml

from job_agent.cv.content import build_cv_content

FACTS = yaml.safe_load(open("data.example/facts.example.yaml", encoding="utf-8"))


def test_build_cv_content_basic_fields():
    content = build_cv_content(FACTS)
    assert content["name"] == "Jane Example"
    assert content["contact"]["email"] == "jane.example@example.com"
    assert content["contact"]["linkedin"] == "https://linkedin.com/in/jane-example"
    assert content["languages"] == [
        {"name": "English", "cefr": "C1"},
        {"name": "Ukrainian", "cefr": "native"},
    ]


def test_build_cv_content_filters_by_track():
    content = build_cv_content(FACTS, track_id="manual_qa")
    # "Playwright" is tagged qa_automation only, "Manual testing" is manual_qa only
    assert "Manual testing" in content["skills"]
    assert "Playwright" not in content["skills"]


def test_build_cv_content_no_track_includes_everything():
    content = build_cv_content(FACTS, track_id=None)
    all_skill_names = {s["name"] for s in FACTS["skills"]}
    assert set(content["skills"]) == all_skill_names


def test_build_cv_content_projects_and_experience_shape():
    content = build_cv_content(FACTS, track_id="qa_automation")
    assert content["projects"][0]["name"] == "Example Test Automation Framework"
    assert content["projects"][0]["bullets"] == ["Reduced regression run time from 4h to 40m"]
    assert content["experience"][0]["employer"] == "Example Corp"
    assert content["education"][0]["institution"] == "Example State University"

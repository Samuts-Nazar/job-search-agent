import yaml

from job_agent.cv.content import build_cv_content, validate_cv_content

FACTS = yaml.safe_load(open("data.example/facts.example.yaml", encoding="utf-8"))


def test_build_cv_content_basic_fields():
    content = build_cv_content(FACTS, track_id="qa_automation")
    assert content["name"] == "Jane Example"
    assert content["contact"]["email"] == "jane.example@example.com"
    assert content["contact"]["linkedin"] == "https://linkedin.com/in/jane-example"


def test_build_cv_content_no_summary_or_languages_keys():
    # PROJECT.md §8: no summary paragraph, no separate Languages section
    # (English level folds into the headline instead).
    content = build_cv_content(FACTS, track_id="qa_automation")
    assert "summary" not in content
    assert "languages" not in content


def test_headline_mirrors_posting_title_and_appends_english_level():
    content = build_cv_content(FACTS, track_id="qa_automation", posting_title="QA Lead")
    assert content["headline"].startswith("QA Lead |")
    assert content["headline"].endswith("English C1")


def test_headline_falls_back_to_track_title_without_a_posting():
    content = build_cv_content(FACTS, track_id="qa_automation")
    assert content["headline"].startswith("QA Automation Engineer |")


def test_headline_prioritizes_required_tech_tags():
    content = build_cv_content(
        FACTS,
        track_id="qa_automation",
        posting_title="QA Engineer",
        posting_tech_tags=[
            {"tag": "pytest", "priority": "required"},
            {"tag": "playwright", "priority": "nice_to_have"},
        ],
    )
    tools_part = content["headline"].split(" | ")[1]
    assert tools_part.index("pytest") < tools_part.index("Playwright")


def test_build_cv_content_filters_skills_by_track():
    content = build_cv_content(FACTS, track_id="manual_qa")
    all_items = [name for group in content["skills"] for name in group["items"]]
    # "Playwright" is tagged qa_automation only, "Manual testing" is manual_qa only
    assert "Manual testing" in all_items
    assert "Playwright" not in all_items


def test_build_cv_content_skills_grouped_by_category():
    content = build_cv_content(FACTS, track_id="qa_automation")
    categories = {group["category"] for group in content["skills"]}
    assert "QA & Testing" in categories
    assert "Databases" in categories


def test_build_cv_content_skills_prioritize_matched_tech_tags():
    content = build_cv_content(
        FACTS,
        track_id="qa_automation",
        posting_tech_tags=[{"tag": "SQL", "priority": "required"}],
    )
    # the category containing the matched tag should be ordered first
    assert content["skills"][0]["category"] == "Databases"


def test_build_cv_content_projects_follow_track_order():
    content = build_cv_content(FACTS, track_id="qa_automation")
    assert content["projects"][0]["name"] == "Example Test Automation Framework"
    assert content["projects"][0]["bullets"] == ["Reduced regression run time from 4h to 40m"]


def test_build_cv_content_growth_project_only_when_automation_mentioned():
    without = build_cv_content(FACTS, track_id="manual_qa")
    with_growth = build_cv_content(FACTS, track_id="manual_qa", mentions_automation=True)

    assert without["projects"] == []
    assert with_growth["projects"][0]["name"] == "Example Test Automation Framework"


def test_build_cv_content_experience_and_education_shape():
    content = build_cv_content(FACTS, track_id="qa_automation")
    assert content["experience"][0]["employer"] == "Example Corp"
    assert content["education"][0]["institution"] == "Example State University"


def test_validate_cv_content_passes_for_generated_content():
    content = build_cv_content(FACTS, track_id="qa_automation", mentions_automation=True)
    assert validate_cv_content(content, FACTS) == []


def test_validate_cv_content_flags_invented_skill():
    content = build_cv_content(FACTS, track_id="qa_automation")
    content["skills"][0]["items"].append("Kubernetes")  # not in FACTS
    problems = validate_cv_content(content, FACTS)
    assert any("Kubernetes" in p for p in problems)


def test_validate_cv_content_flags_renamed_experience_title():
    content = build_cv_content(FACTS, track_id="qa_automation")
    content["experience"][0]["title"] = "Senior QA Automation Engineer"
    problems = validate_cv_content(content, FACTS)
    assert any("Senior QA Automation Engineer" in p for p in problems)

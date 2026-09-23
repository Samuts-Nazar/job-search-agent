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


# Synthetic facts exercising all 4 real track ids with distinct project
# sets per track, mirroring PROJECT.md §8.3's exact rule shape (manual_qa's
# growth_projects conditional, and each track's own fixed order) --
# data.example/facts.example.yaml only has one project, too thin to prove
# per-track ordering is actually followed rather than coincidental.
MULTI_TRACK_FACTS = {
    "candidate": {
        "name": "Test Candidate",
        "email": "test@example.com",
        "phone": "+1 555 0100",
        "city": "Kyiv",
        "country": "Ukraine",
        "timezone": "UTC+2",
        "links": {"linkedin": "https://linkedin.com/in/test"},
    },
    "cv_tracks": [
        {
            "id": "manual_qa",
            "title": "Manual QA Engineer",
            "project_order": ["p_rbp", "p_agent"],
            "growth_projects": ["p_web"],
        },
        {
            "id": "qa_automation",
            "title": "QA Automation Engineer",
            "project_order": ["p_web", "p_rbp", "p_agent"],
        },
        {
            "id": "ai_automation",
            "title": "AI Automation Engineer",
            "project_order": ["p_agent", "p_nefk", "p_rbp"],
        },
        {
            "id": "ml_ai",
            "title": "ML/AI Engineer",
            "project_order": ["p_lm", "p_nefk", "p_agent"],
        },
    ],
    "skills": [
        {
            "name": "Python",
            "tracks": ["manual_qa", "qa_automation", "ai_automation", "ml_ai"],
            "category": "Programming",
        },
    ],
    "projects": [
        {"id": "p_rbp", "name": "RBP Framework", "tracks": ["manual_qa", "qa_automation"]},
        {"id": "p_web", "name": "Web QA Framework", "tracks": ["manual_qa", "qa_automation"]},
        {
            "id": "p_agent",
            "name": "Job Agent",
            "tracks": ["manual_qa", "qa_automation", "ai_automation", "ml_ai"],
        },
        {"id": "p_nefk", "name": "NEFK Assistant", "tracks": ["ai_automation", "ml_ai"]},
        {"id": "p_lm", "name": "Witcher LM", "tracks": ["ml_ai"]},
    ],
    "experience": [],
    "education": [],
    "languages": [{"name": "English", "cefr": "B2"}],
}


def test_manual_qa_project_order_without_automation_mention():
    content = build_cv_content(MULTI_TRACK_FACTS, track_id="manual_qa")
    assert [p["name"] for p in content["projects"]] == ["RBP Framework", "Job Agent"]


def test_manual_qa_project_order_with_automation_mention_appends_growth_project():
    content = build_cv_content(MULTI_TRACK_FACTS, track_id="manual_qa", mentions_automation=True)
    assert [p["name"] for p in content["projects"]] == [
        "RBP Framework",
        "Job Agent",
        "Web QA Framework",
    ]


def test_qa_automation_project_order():
    content = build_cv_content(MULTI_TRACK_FACTS, track_id="qa_automation")
    assert [p["name"] for p in content["projects"]] == [
        "Web QA Framework",
        "RBP Framework",
        "Job Agent",
    ]


def test_ai_automation_project_order():
    content = build_cv_content(MULTI_TRACK_FACTS, track_id="ai_automation")
    assert [p["name"] for p in content["projects"]] == [
        "Job Agent",
        "NEFK Assistant",
        "RBP Framework",
    ]


def test_ml_ai_project_order():
    content = build_cv_content(MULTI_TRACK_FACTS, track_id="ml_ai")
    assert [p["name"] for p in content["projects"]] == [
        "Witcher LM",
        "NEFK Assistant",
        "Job Agent",
    ]

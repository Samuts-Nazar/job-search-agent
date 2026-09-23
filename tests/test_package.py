import re
import shutil

import pytest
import yaml

from job_agent import package

FACTS = yaml.safe_load(open("data.example/facts.example.yaml", encoding="utf-8"))


def test_mentions_automation_true_for_english_keyword():
    assert package.mentions_automation("We'd love to grow your role into automation.")


def test_mentions_automation_true_for_ukrainian_keyword():
    assert package.mentions_automation("Розуміння automation testing або бажання розвиватись")


def test_mentions_automation_false_when_absent():
    assert not package.mentions_automation("Manual testing of the web platform.")


def test_build_outreach_message_includes_company_and_name():
    message = package.build_outreach_message(
        FACTS, posting_title="QA Engineer", company="Acme Corp"
    )
    assert "QA Engineer" in message
    assert "Acme Corp" in message
    assert "Jane Example" in message
    assert len(message) <= package.MAX_OUTREACH_MESSAGE_CHARS


def test_build_outreach_message_without_company():
    message = package.build_outreach_message(FACTS, posting_title="QA Engineer", company="")
    assert "QA Engineer" in message
    assert " at " not in message  # no dangling "at" with an empty company


def test_build_outreach_message_truncates_long_titles():
    message = package.build_outreach_message(
        FACTS, posting_title="X" * 400, company="Acme"
    )
    assert len(message) <= package.MAX_OUTREACH_MESSAGE_CHARS
    assert message.endswith("…")


def test_build_outreach_message_defaults_to_english_greeting():
    message = package.build_outreach_message(
        FACTS, posting_title="QA Engineer", company="Acme"
    )
    assert message.startswith("Dear Hiring Team,")


def test_build_outreach_message_ukrainian_greeting_and_no_signoff():
    message = package.build_outreach_message(
        FACTS, posting_title="QA Engineer", company="Acme", language="Ukrainian"
    )
    assert message.startswith("Доброго дня")
    assert "Дякую" not in message
    assert "З повагою" not in message
    assert "QA Engineer" in message
    assert "Jane Example" in message


BANNED_PHRASES = [
    "excited to apply",
    "passionate about",
    "great fit",
    "з великим ентузіазмом",
]


def test_build_outreach_message_has_no_banned_phrases_or_dash_punctuation():
    for language in ("English", "Ukrainian"):
        message = package.build_outreach_message(
            FACTS, posting_title="QA Engineer", company="Acme Corp", language=language
        )
        lowered = message.lower()
        for phrase in BANNED_PHRASES:
            assert phrase not in lowered
        assert "—" not in message and "–" not in message
        assert not re.search(r"\S \-\-? \S", message)


def test_build_package_raises_on_invalid_content(monkeypatch, tmp_path):
    def poisoned_build_cv_content(facts_data, **kwargs):
        content = {
            "name": facts_data["candidate"]["name"],
            "contact": {},
            "headline": "x",
            "skills": [{"category": "Fake", "items": ["Invented Skill"]}],
            "projects": [],
            "experience": [],
            "education": [],
        }
        return content

    monkeypatch.setattr(package, "build_cv_content", poisoned_build_cv_content)

    with pytest.raises(package.PackageError, match="Invented Skill"):
        package.build_package(
            FACTS,
            track_id="qa_automation",
            posting_title="QA Engineer",
            company="Acme",
            description="manual testing role",
            posting_tech_tags=None,
            output_path=tmp_path / "cv.pdf",
        )


@pytest.mark.skipif(shutil.which("typst") is None, reason="typst CLI not on PATH")
def test_build_package_end_to_end(tmp_path):
    pkg = package.build_package(
        FACTS,
        track_id="qa_automation",
        posting_title="QA Automation Lead",
        company="Acme Corp",
        description="Looking for automation testing growth mindset.",
        posting_tech_tags=[{"tag": "pytest", "priority": "required"}],
        output_path=tmp_path / "cv.pdf",
    )

    assert pkg.pdf_path.exists()
    assert pkg.ats_result.ok, pkg.ats_result.problems
    assert "Acme+Corp" in pkg.outreach_url
    assert "QA Automation Lead" in pkg.outreach_message

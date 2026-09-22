import shutil
from pathlib import Path

from job_agent import data_guard

REAL_FACTS_YAML = """
example: false
candidate:
  name: "Real Person"
  email: "real.person@realmail.com"
  phone: "+380 44 000 0000"
  city: "Kyiv"
  country: "Ukraine"
  timezone: "UTC+2"
  links:
    linkedin: "https://linkedin.com/in/real-person"
    github: "https://github.com/real-person"
cv_tracks:
  - id: qa_automation
    title: "QA Automation Engineer"
experience:
  - id: job1
    employer: "Real Employer LLC"
    title: "QA Engineer"
    dates: "2023 to present"
    bullets: ["Did real things."]
languages:
  - name: "English"
    cefr: "B2"
"""

REAL_ANSWERS_YAML = """
example: false
work_authorization:
  eu: "authorized"
  us: "requires_sponsorship"
  uk: "requires_sponsorship"
  ukraine: "authorized"
relocation:
  willing: false
notice_period_days: 14
salary_expectation:
  currency: "USD"
  monthly_min: 3000
  monthly_max: 4000
timezone: "UTC+2"
languages:
  - name: "English"
    cefr: "B2"
links:
  linkedin: "https://linkedin.com/in/real-person"
"""


def test_check_facts_missing_file(tmp_path):
    result = data_guard.check_facts(tmp_path / "does-not-exist.yaml")
    assert result.ok is False
    assert "file not found" in result.problems[0]


def test_check_facts_unedited_example_copy_fails(tmp_path):
    dest = tmp_path / "facts.yaml"
    shutil.copy("data.example/facts.example.yaml", dest)

    result = data_guard.check_facts(dest)

    assert result.ok is False
    assert any("example: true" in p for p in result.problems)
    assert any(p.startswith("candidate.email:") for p in result.problems)
    assert any(p.startswith("candidate.name:") for p in result.problems)
    assert any(p.startswith("candidate.links.linkedin:") for p in result.problems)


def test_check_facts_edited_real_data_passes(tmp_path):
    dest = tmp_path / "facts.yaml"
    dest.write_text(REAL_FACTS_YAML, encoding="utf-8")

    result = data_guard.check_facts(dest)

    assert result.ok is True
    assert result.problems == []


def test_check_facts_edited_data_missing_required_field(tmp_path):
    dest = tmp_path / "facts.yaml"
    dest.write_text(
        REAL_FACTS_YAML.replace('  phone: "+380 44 000 0000"\n', ""), encoding="utf-8"
    )

    result = data_guard.check_facts(dest)

    assert result.ok is False
    assert any(p.startswith("candidate.phone:") for p in result.problems)


def test_check_facts_forgot_to_change_email_only(tmp_path):
    dest = tmp_path / "facts.yaml"
    text = REAL_FACTS_YAML.replace(
        'email: "real.person@realmail.com"', 'email: "jane.example@example.com"'
    )
    dest.write_text(text, encoding="utf-8")

    result = data_guard.check_facts(dest)

    assert result.ok is False
    assert result.problems == ["candidate.email: still has the example value"]


def test_check_facts_city_country_overlap_with_example_is_not_flagged(tmp_path):
    # data.example/facts.example.yaml also has city=Kyiv, country=Ukraine --
    # those aren't identity fields, so a real Ukrainian candidate isn't
    # penalized for sharing them.
    dest = tmp_path / "facts.yaml"
    dest.write_text(REAL_FACTS_YAML, encoding="utf-8")

    result = data_guard.check_facts(dest)

    assert result.ok is True


def test_check_answers_missing_file(tmp_path):
    result = data_guard.check_answers(tmp_path / "does-not-exist.yaml")
    assert result.ok is False
    assert "file not found" in result.problems[0]


def test_check_answers_unedited_example_copy_fails(tmp_path):
    dest = tmp_path / "answers.yaml"
    shutil.copy("data.example/answers.example.yaml", dest)

    result = data_guard.check_answers(dest)

    assert result.ok is False
    assert any("example: true" in p for p in result.problems)
    assert any(p.startswith("links.linkedin:") for p in result.problems)


def test_check_answers_edited_real_data_passes(tmp_path):
    dest = tmp_path / "answers.yaml"
    dest.write_text(REAL_ANSWERS_YAML, encoding="utf-8")

    result = data_guard.check_answers(dest)

    assert result.ok is True
    assert result.problems == []


def test_check_answers_missing_knockout_field(tmp_path):
    dest = tmp_path / "answers.yaml"
    text = REAL_ANSWERS_YAML.replace("notice_period_days: 14\n", "")
    dest.write_text(text, encoding="utf-8")

    result = data_guard.check_answers(dest)

    assert result.ok is False
    assert any(p.startswith("notice_period_days:") for p in result.problems)


def test_check_answers_warns_on_missing_optional_knockout_fields(tmp_path):
    dest = tmp_path / "answers.yaml"
    dest.write_text(REAL_ANSWERS_YAML, encoding="utf-8")

    result = data_guard.check_answers(dest)

    assert result.ok is True  # missing optional fields never fail the check
    warned_paths = {w.split(":")[0] for w in result.warnings}
    assert warned_paths == {
        "work_format",
        "availability",
        "employment_types",
        "test_task",
        "salary_expectation.monthly_target",
        "work_authorization.per_country",
    }


def test_check_answers_no_warnings_when_optional_fields_filled(tmp_path):
    dest = tmp_path / "answers.yaml"
    dest.write_text(
        REAL_ANSWERS_YAML
        + """
work_format:
  remote: true
  hybrid: false
  onsite: false
availability:
  night_shifts: false
  weekends: false
employment_types:
  - "employment_contract"
test_task:
  willing: true
""",
        encoding="utf-8",
    )
    # give the two nested optional fields a value too
    text = dest.read_text(encoding="utf-8")
    text = text.replace(
        "  monthly_max: 4000\n", "  monthly_max: 4000\n  monthly_target: 3500\n"
    )
    text = text.replace(
        '  ukraine: "authorized"\n', '  ukraine: "authorized"\n  per_country:\n    pl: "ok"\n'
    )
    dest.write_text(text, encoding="utf-8")

    result = data_guard.check_answers(dest)

    assert result.ok is True
    assert result.warnings == []


def test_format_check_report_includes_warnings(tmp_path):
    dest = tmp_path / "answers.yaml"
    dest.write_text(REAL_ANSWERS_YAML, encoding="utf-8")
    result = data_guard.check_answers(dest)

    text = data_guard.format_check_report([result])

    assert f"{dest}: OK" in text
    assert "! work_format: not set" in text


def test_check_data_safety_runs_both(tmp_path):
    facts_path = tmp_path / "facts.yaml"
    answers_path = tmp_path / "answers.yaml"
    facts_path.write_text(REAL_FACTS_YAML, encoding="utf-8")
    answers_path.write_text(REAL_ANSWERS_YAML, encoding="utf-8")

    results = data_guard.check_data_safety(facts_path, answers_path)

    assert len(results) == 2
    assert all(r.ok for r in results)


def test_format_check_report():
    results = data_guard.check_data_safety(
        Path("does-not-exist-1.yaml"), Path("does-not-exist-2.yaml")
    )
    text = data_guard.format_check_report(results)
    assert "does-not-exist-1.yaml: FAILED" in text
    assert "does-not-exist-2.yaml: FAILED" in text
    assert "file not found" in text

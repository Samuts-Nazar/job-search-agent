import yaml
from pydantic import ValidationError

from job_agent.data_schemas import Answers, Facts


def load_yaml(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))


def test_facts_schema_accepts_example_file_structure():
    data = load_yaml("data.example/facts.example.yaml")
    facts = Facts.model_validate(data)
    assert facts.example is True
    assert facts.candidate.name == "Jane Example"
    assert len(facts.cv_tracks) == 4
    assert len(facts.languages) == 2


def test_facts_schema_rejects_missing_required_field():
    data = load_yaml("data.example/facts.example.yaml")
    del data["candidate"]["email"]
    try:
        Facts.model_validate(data)
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        locs = {".".join(str(p) for p in e["loc"]) for e in exc.errors()}
        assert "candidate.email" in locs


def test_facts_schema_requires_skill_category():
    data = load_yaml("data.example/facts.example.yaml")
    del data["skills"][0]["category"]
    try:
        Facts.model_validate(data)
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        locs = {".".join(str(p) for p in e["loc"]) for e in exc.errors()}
        assert "skills.0.category" in locs


def test_facts_schema_accepts_skill_category():
    data = load_yaml("data.example/facts.example.yaml")
    facts = Facts.model_validate(data)
    assert facts.skills[0].category == "Programming & Tools"


def test_facts_schema_rejects_empty_cv_tracks():
    data = load_yaml("data.example/facts.example.yaml")
    data["cv_tracks"] = []
    try:
        Facts.model_validate(data)
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_answers_schema_accepts_example_file_structure():
    data = load_yaml("data.example/answers.example.yaml")
    answers = Answers.model_validate(data)
    assert answers.example is True
    assert answers.notice_period_days == 30
    assert answers.links.linkedin.endswith("jane-example")


def test_answers_schema_rejects_missing_work_authorization():
    data = load_yaml("data.example/answers.example.yaml")
    del data["work_authorization"]
    try:
        Answers.model_validate(data)
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        locs = {".".join(str(p) for p in e["loc"]) for e in exc.errors()}
        assert "work_authorization" in locs


def test_answers_schema_accepts_optional_extension_fields():
    data = load_yaml("data.example/answers.example.yaml")
    answers = Answers.model_validate(data)
    assert answers.work_authorization.per_country == {}
    assert answers.salary_expectation.monthly_target == 3000
    assert answers.work_format.remote is True
    assert answers.work_format.onsite_salary_floor is None
    assert answers.availability.night_shifts is False
    assert answers.employment_types == ["employment_contract"]
    assert answers.test_task.willing is True
    assert answers.years_of_experience.ai_automation == 0


def test_answers_schema_optional_extension_fields_all_omittable():
    data = load_yaml("data.example/answers.example.yaml")
    for key in ("work_format", "availability", "employment_types", "test_task"):
        del data[key]
    del data["salary_expectation"]["monthly_target"]
    del data["work_authorization"]["per_country"]

    answers = Answers.model_validate(data)
    assert answers.work_format is None
    assert answers.availability is None
    assert answers.employment_types == []
    assert answers.test_task is None
    assert answers.salary_expectation.monthly_target is None
    assert answers.work_authorization.per_country == {}


def test_answers_schema_accepts_per_country_work_authorization():
    data = load_yaml("data.example/answers.example.yaml")
    data["work_authorization"]["per_country"] = {"pl": "temporary_protection"}
    answers = Answers.model_validate(data)
    assert answers.work_authorization.per_country == {"pl": "temporary_protection"}

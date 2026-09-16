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

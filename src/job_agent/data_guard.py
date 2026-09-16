"""Refuses to run against example data or data missing required fields.

`job` and `job check-data` both call check_data_safety() before touching
candidate data. Three failure modes, each reported with the exact field:
  1. The file still has `example: true` (the example files ship with this).
  2. A required field (Pydantic schema) is missing or invalid -- this is
     how "missing knockout field" (PROJECT.md §5.7) is caught for
     answers.yaml, and missing contact/CV-track/language fields for
     facts.yaml.
  3. An identity-like field (name, email, phone, links, employer,
     institution, project name) still literally equals the shipped
     example's value. Fields like city/country/CEFR level are
     deliberately excluded from this check: real candidates can
     legitimately share those values with the example, unlike a name or
     a LinkedIn URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from job_agent.data_schemas import Answers, Facts

FACTS_PATH = "data/facts.yaml"
ANSWERS_PATH = "data/answers.yaml"
EXAMPLE_FACTS_PATH = "data.example/facts.example.yaml"
EXAMPLE_ANSWERS_PATH = "data.example/answers.example.yaml"

FACTS_IDENTITY_PATHS: list[tuple[str, ...]] = [
    ("candidate", "name"),
    ("candidate", "email"),
    ("candidate", "phone"),
    ("candidate", "links", "linkedin"),
    ("candidate", "links", "github"),
    ("candidate", "links", "portfolio"),
]
ANSWERS_IDENTITY_PATHS: list[tuple[str, ...]] = [
    ("links", "linkedin"),
    ("links", "github"),
    ("links", "portfolio"),
]


@dataclass
class DataCheckResult:
    path: str
    ok: bool
    problems: list[str] = field(default_factory=list)


def _load_yaml_or_none(path: Path | str) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _find_identity_leaks(
    data: dict[str, Any], example: dict[str, Any], paths: list[tuple[str, ...]]
) -> list[str]:
    leaks = []
    for path in paths:
        real_value = _get_nested(data, path)
        example_value = _get_nested(example, path)
        if real_value is not None and example_value is not None and real_value == example_value:
            leaks.append(".".join(path))
    return leaks


def _find_list_identity_leaks(
    data: dict[str, Any], example: dict[str, Any], list_key: str, item_field: str
) -> list[str]:
    example_values = {
        item.get(item_field) for item in example.get(list_key, []) if item.get(item_field)
    }
    leaks = []
    for index, item in enumerate(data.get(list_key, [])):
        value = item.get(item_field)
        if value and value in example_values:
            leaks.append(f"{list_key}[{index}].{item_field}")
    return leaks


def _validation_problems(data: dict[str, Any], schema: type) -> list[str]:
    try:
        schema.model_validate(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            field_path = ".".join(str(part) for part in error["loc"]) or "(root)"
            problems.append(f"{field_path}: {error['msg']}")
        return problems
    return []


def check_facts(path: Path | str = FACTS_PATH) -> DataCheckResult:
    path = str(path)
    data = _load_yaml_or_none(path)
    if data is None:
        return DataCheckResult(
            path=path,
            ok=False,
            problems=[f"file not found -- copy {EXAMPLE_FACTS_PATH} to {path} and edit it"],
        )

    problems: list[str] = []
    if data.get("example") is True:
        problems.append("example: true -- this is still the example file, copy and edit it")

    problems += _validation_problems(data, Facts)

    example_data = _load_yaml_or_none(EXAMPLE_FACTS_PATH) or {}
    leaks = _find_identity_leaks(data, example_data, FACTS_IDENTITY_PATHS)
    leaks += _find_list_identity_leaks(data, example_data, "experience", "employer")
    leaks += _find_list_identity_leaks(data, example_data, "education", "institution")
    leaks += _find_list_identity_leaks(data, example_data, "projects", "name")
    problems += [f"{leak}: still has the example value" for leak in leaks]

    return DataCheckResult(path=path, ok=not problems, problems=problems)


def check_answers(path: Path | str = ANSWERS_PATH) -> DataCheckResult:
    path = str(path)
    data = _load_yaml_or_none(path)
    if data is None:
        return DataCheckResult(
            path=path,
            ok=False,
            problems=[f"file not found -- copy {EXAMPLE_ANSWERS_PATH} to {path} and edit it"],
        )

    problems: list[str] = []
    if data.get("example") is True:
        problems.append("example: true -- this is still the example file, copy and edit it")

    problems += _validation_problems(data, Answers)

    example_data = _load_yaml_or_none(EXAMPLE_ANSWERS_PATH) or {}
    leaks = _find_identity_leaks(data, example_data, ANSWERS_IDENTITY_PATHS)
    problems += [f"{leak}: still has the example value" for leak in leaks]

    return DataCheckResult(path=path, ok=not problems, problems=problems)


def check_data_safety(
    facts_path: Path | str = FACTS_PATH, answers_path: Path | str = ANSWERS_PATH
) -> list[DataCheckResult]:
    return [check_facts(facts_path), check_answers(answers_path)]


def format_check_report(results: list[DataCheckResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"{result.path}: {'OK' if result.ok else 'FAILED'}")
        for problem in result.problems:
            lines.append(f"  - {problem}")
    return "\n".join(lines)

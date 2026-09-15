import pytest

from job_agent.facts import load_facts, render_facts_summary


def test_load_facts_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_facts(tmp_path / "does-not-exist.yaml")


def test_load_and_render_example_facts():
    facts = load_facts("data.example/facts.example.yaml")
    summary = render_facts_summary(facts)

    assert "qa_automation" in summary
    assert "Playwright" in summary
    assert "Example Test Automation Framework" in summary
    assert "Reduced regression run time from 4h to 40m" in summary
    assert "Example Corp" in summary
    assert "English: C1" in summary

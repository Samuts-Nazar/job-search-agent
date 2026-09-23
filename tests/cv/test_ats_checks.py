from job_agent.cv import ats_checks


def test_check_section_order_all_present_in_order():
    text = "Technical Skills\nbar\nProjects\nExperience\nEducation"
    assert ats_checks._check_section_order(text) == []


def test_check_section_order_missing_section():
    text = "Technical Skills\nProjects\nExperience"
    problems = ats_checks._check_section_order(text)
    assert any("Education" in p for p in problems)


def test_check_section_order_out_of_order():
    text = "Projects\nTechnical Skills\nExperience\nEducation"
    problems = ats_checks._check_section_order(text)
    assert any("out of order" in p for p in problems)


def test_check_no_garbled_text_clean():
    assert ats_checks._check_no_garbled_text("Hello World") == []


def test_check_no_garbled_text_replacement_char():
    problems = ats_checks._check_no_garbled_text("Hello � World")
    assert any("U+FFFD" in p for p in problems)


def test_check_no_garbled_text_private_use_area():
    problems = ats_checks._check_no_garbled_text("Hello  World")
    assert any("private-use-area" in p for p in problems)


def test_check_identity_fields_all_present():
    content = {"name": "Jane Doe", "contact": {"email": "jane@x.com", "phone": "+1 555"}}
    text = "Jane Doe jane@x.com +1 555"
    assert ats_checks._check_identity_fields(text, content) == []


def test_check_identity_fields_missing_email():
    content = {"name": "Jane Doe", "contact": {"email": "jane@x.com", "phone": "+1 555"}}
    text = "Jane Doe +1 555"
    problems = ats_checks._check_identity_fields(text, content)
    assert any("email" in p for p in problems)


def test_check_links_all_present():
    content = {
        "contact": {
            "email": "jane@x.com",
            "linkedin": "https://linkedin.com/in/jane",
            "github": "https://github.com/jane",
        }
    }
    uris = ["mailto:jane@x.com", "https://linkedin.com/in/jane", "https://github.com/jane"]
    assert ats_checks._check_links(uris, content) == []


def test_check_links_missing_linkedin():
    content = {
        "contact": {"email": "jane@x.com", "linkedin": "https://linkedin.com/in/jane", "github": ""}
    }
    uris = ["mailto:jane@x.com"]
    problems = ats_checks._check_links(uris, content)
    assert any("linkedin" in p for p in problems)


def test_check_required_terms():
    problems = ats_checks._check_required_terms("Python and SQL", ["Python", "Kubernetes"])
    assert problems == ["required term 'Kubernetes' not found verbatim in PDF text"]


def test_check_no_split_words_clean():
    assert ats_checks._check_no_split_words("Chrome DevTools and Telegram integration") == []


def test_check_no_split_words_flags_kerning_artifact():
    # found live 2026-09-24: DejaVu Sans's kerning after "T" made pypdf
    # extraction insert a stray space -- see template.typ's kerning: false.
    problems = ats_checks._check_no_split_words("Chrome DevT ools and T elegram integration")
    assert len(problems) == 1
    assert "DevT ools" in problems[0] or "T elegram" in problems[0]


FACTS = {
    "candidate": {"name": "Jane Doe", "city": "Kyiv"},
    "skills": [{"name": "Python", "category": "Programming"}],
    "projects": [
        {
            "name": "RBP Framework",
            "summary": "Built with Docker and SQL verification.",
            "metrics": ["7 services covered"],
            "tools": ["Python", "SQL"],
        }
    ],
}


def test_validate_pdf_tokens_passes_for_facts_derived_text():
    text = "Jane Doe Kyiv. Technical Skills: Python. Projects: RBP Framework, 7 services covered."
    assert ats_checks.validate_pdf_tokens(text, FACTS) == []


def test_validate_pdf_tokens_allows_the_posting_title():
    text = "Senior Platform Engineer"
    assert (
        ats_checks.validate_pdf_tokens(text, FACTS, posting_title="Senior Platform Engineer")
        == []
    )


def test_validate_pdf_tokens_flags_invented_word():
    text = "Jane Doe knows Kubernetes."
    problems = ats_checks.validate_pdf_tokens(text, FACTS)
    assert any("kubernetes" in p for p in problems)


def test_validate_pdf_tokens_flags_invented_number():
    text = "Jane Doe has 42 years of experience."
    problems = ats_checks.validate_pdf_tokens(text, FACTS)
    assert any("'42'" in p for p in problems)

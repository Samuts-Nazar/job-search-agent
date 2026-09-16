from job_agent.cv import ats_checks


def test_check_section_order_all_present_in_order():
    text = "Summary\nfoo\nTechnical Skills\nbar\nProjects\nExperience\nEducation\nLanguages"
    assert ats_checks._check_section_order(text) == []


def test_check_section_order_missing_section():
    text = "Summary\nTechnical Skills\nProjects\nExperience\nEducation"
    problems = ats_checks._check_section_order(text)
    assert any("Languages" in p for p in problems)


def test_check_section_order_out_of_order():
    text = "Technical Skills\nSummary\nProjects\nExperience\nEducation\nLanguages"
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

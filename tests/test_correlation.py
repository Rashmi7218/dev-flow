from app.correlation import extract_ticket_key


def test_extracts_from_branch_name():
    assert extract_ticket_key("feature/KAN-42-add-login") == "KAN-42"


def test_extracts_from_title():
    assert extract_ticket_key(None, "KAN-7: fix the thing") == "KAN-7"


def test_prefers_first_non_empty_source():
    assert extract_ticket_key(None, None, "mentions KAN-9 here") == "KAN-9"


def test_returns_none_when_no_match():
    assert extract_ticket_key("no-ticket-here", "still nothing") is None


def test_ignores_lowercase_key():
    assert extract_ticket_key("kan-42-lowercase-branch") is None

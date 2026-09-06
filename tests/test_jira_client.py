from app.integrations.jira_client import _doc


def test_single_line_produces_one_paragraph():
    doc = _doc("hello world")
    assert doc["content"] == [
        {"type": "paragraph", "content": [{"type": "text", "text": "hello world"}]}
    ]


def test_double_newline_splits_paragraphs():
    doc = _doc("first paragraph\n\nsecond paragraph")
    assert len(doc["content"]) == 2
    assert doc["content"][0]["content"][0]["text"] == "first paragraph"
    assert doc["content"][1]["content"][0]["text"] == "second paragraph"


def test_single_newline_becomes_hard_break():
    doc = _doc("line one\nline two")
    paragraph = doc["content"][0]
    assert paragraph["content"] == [
        {"type": "text", "text": "line one"},
        {"type": "hardBreak"},
        {"type": "text", "text": "line two"},
    ]


def test_empty_line_does_not_produce_empty_text_node():
    doc = _doc("line one\n\nline two")
    for paragraph in doc["content"]:
        for node in paragraph["content"]:
            if node["type"] == "text":
                assert node["text"] != ""

import json

import respx
from httpx import Response

from app.integrations.jira_client import _doc, get_transitions, transition_issue


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


@respx.mock
async def test_get_transitions_returns_transition_list():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions").mock(
        return_value=Response(
            200,
            json={
                "transitions": [
                    {"id": "11", "name": "In Progress"},
                    {"id": "31", "name": "Done"},
                ]
            },
        )
    )

    transitions = await get_transitions("KAN-1")

    assert transitions == [{"id": "11", "name": "In Progress"}, {"id": "31", "name": "Done"}]


@respx.mock
async def test_transition_issue_posts_transition_id():
    route = respx.post("https://test.atlassian.net/rest/api/3/issue/KAN-1/transitions").mock(
        return_value=Response(204)
    )

    await transition_issue("KAN-1", "31")

    body = json.loads(route.calls.last.request.content)
    assert body == {"transition": {"id": "31"}}

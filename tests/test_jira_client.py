import json

import respx
from httpx import Response

from app.integrations.jira_client import _doc, _flatten_adf, append_description, get_transitions, transition_issue


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


def test_flatten_adf_handles_missing_description():
    assert _flatten_adf(None) == ""


def test_flatten_adf_round_trips_doc_produced_by_doc_helper():
    assert _flatten_adf(_doc("hello world")) == "hello world"


def test_flatten_adf_round_trips_multiple_paragraphs():
    original = "first paragraph\n\nsecond paragraph"
    assert _flatten_adf(_doc(original)) == original


def test_flatten_adf_renders_bullet_list_items():
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "first"}]}],
                    },
                    {
                        "type": "listItem",
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "second"}]}],
                    },
                ],
            }
        ],
    }
    flattened = _flatten_adf(doc)
    assert "- first" in flattened
    assert "- second" in flattened


@respx.mock
async def test_append_description_combines_existing_and_new_text():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(200, json={"fields": {"description": _doc("Old text")}})
    )
    put_route = respx.put("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(204)
    )

    await append_description("KAN-1", "New text")

    body = json.loads(put_route.calls.last.request.content)
    combined = _flatten_adf(body["fields"]["description"])
    assert combined == "Old text\n\nNew text"


@respx.mock
async def test_append_description_with_no_existing_description():
    respx.get("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(200, json={"fields": {"description": None}})
    )
    put_route = respx.put("https://test.atlassian.net/rest/api/3/issue/KAN-1").mock(
        return_value=Response(204)
    )

    await append_description("KAN-1", "New text")

    body = json.loads(put_route.calls.last.request.content)
    assert _flatten_adf(body["fields"]["description"]) == "New text"


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

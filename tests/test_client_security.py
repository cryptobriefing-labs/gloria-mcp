"""Security tests: the API token must never reach a URL or an error message."""

import asyncio
import json

import httpx
import pytest

from gloria_mcp import server
from gloria_mcp.client import GloriaClient, InvalidInputError, UpstreamError

TOKEN = "test-token-value-0123456789abcdef"
BASE_URL = "https://upstream.invalid"
NEWS_ID = "3f2b8c1e-9d4a-4e6b-8f1a-2c3d4e5f6a7b"


class Recorder:
    """httpx MockTransport handler that records every request it sees."""

    def __init__(self, status=200, body=None, exc=None):
        self.status = status
        self.body = body if body is not None else {}
        self.exc = exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        # Echo the full URL in the body, the way a verbose upstream error might.
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": f"denied for {request.url}"})
        return httpx.Response(200, json=self.body)


def make_client(recorder: Recorder) -> GloriaClient:
    return GloriaClient(BASE_URL, TOKEN, transport=httpx.MockTransport(recorder))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mcp_with(monkeypatch):
    """Point the FastMCP server at a mocked client."""

    def _install(recorder: Recorder):
        monkeypatch.setattr(server, "_client", make_client(recorder))
        return recorder

    yield _install
    monkeypatch.setattr(server, "_client", None)


def call_tool_error_text(name: str, args: dict) -> str:
    """Call a tool through FastMCP and return the error text it would send."""
    try:
        run(server.mcp.call_tool(name, args))
    except Exception as exc:  # FastMCP raises ToolError; its text goes to the caller
        return str(exc)
    raise AssertionError("expected the tool call to fail")


# Token placement -----------------------------------------------------------


def test_token_sent_in_authorization_header_not_query():
    rec = Recorder(body=[{"id": NEWS_ID}])
    run(make_client(rec).get_news(category="bitcoin", limit=3))
    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(req.url)
    assert "token" not in req.url.params
    assert req.url.params["feed_categories"] == "bitcoin"


def test_token_in_header_for_every_route():
    for body, call in (
        ([], lambda c: c.get_news(keyword="ETF")),
        ({}, lambda c: c.get_news_by_id(NEWS_ID)),
        ({}, lambda c: c.get_recap("bitcoin", "12h")),
        ([], lambda c: c.get_categories()),
    ):
        rec = Recorder(body=body)
        run(call(make_client(rec)))
        req = rec.requests[0]
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(req.url)


# Error hygiene -------------------------------------------------------------


def test_403_error_contains_no_token_and_no_url():
    rec = Recorder(status=403)
    with pytest.raises(UpstreamError) as info:
        run(make_client(rec).get_news_by_id(NEWS_ID))
    message = str(info.value)
    assert message == "Upstream request failed (status 403)"
    assert TOKEN not in message
    assert BASE_URL not in message
    assert "upstream.invalid" not in message
    assert "/news" not in message


def test_403_through_mcp_tool_contains_no_token_and_no_url(mcp_with):
    mcp_with(Recorder(status=403))
    text = call_tool_error_text("get_news_item", {"id": NEWS_ID})
    assert "status 403" in text
    assert TOKEN not in text
    assert "upstream.invalid" not in text
    assert "http" not in text.lower()


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connect failed to https://upstream.invalid/?token=" + TOKEN),
        httpx.ReadTimeout("timed out https://upstream.invalid/?token=" + TOKEN),
    ],
)
def test_transport_errors_are_generic(exc, mcp_with):
    mcp_with(Recorder(exc=exc))
    text = call_tool_error_text("get_latest_news", {"category": "bitcoin"})
    assert "Upstream request failed" in text
    assert TOKEN not in text
    assert "upstream.invalid" not in text


def test_invalid_json_is_generic():
    def handler(request):
        return httpx.Response(200, content=b"not json " + TOKEN.encode())

    client = GloriaClient(BASE_URL, TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError) as info:
        run(client.get_categories())
    assert TOKEN not in str(info.value)


def test_unexpected_exception_is_generic(mcp_with, monkeypatch):
    mcp_with(Recorder())

    async def boom(*a, **k):
        raise RuntimeError("secret detail " + TOKEN)

    monkeypatch.setattr(server._client, "get_categories", boom)
    text = call_tool_error_text("get_categories", {})
    assert "Internal error" in text
    assert TOKEN not in text
    assert "secret detail" not in text


# Input validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "../settings",
        "%2e%2e/settings",
        "..%2fsettings",
        "https://evil.example/news",
        "//evil.example",
        NEWS_ID + "/../../settings",
        NEWS_ID + "?x=1",
        "a" * 5000,
        "",
        " " + NEWS_ID,
    ],
)
def test_bad_news_ids_rejected_without_upstream_request(bad_id, mcp_with):
    rec = mcp_with(Recorder(body={"id": NEWS_ID}))
    with pytest.raises(InvalidInputError):
        run(server._client.get_news_by_id(bad_id))
    text = call_tool_error_text("get_news_item", {"id": bad_id})
    assert "Invalid news id" in text
    assert rec.requests == []


def test_valid_news_id_reaches_only_the_news_route():
    rec = Recorder(body={"id": NEWS_ID, "signal": "x"})
    item = run(make_client(rec).get_news_by_id(NEWS_ID.upper()))
    assert item["id"] == NEWS_ID
    assert rec.requests[0].url.path == f"/news/{NEWS_ID}"
    assert rec.requests[0].url.query == b""


@pytest.mark.parametrize("bad", ["../settings", "bit coin", "a/b", "x" * 100, "bitcoin&token=1", ""])
def test_bad_category_rejected_without_upstream_request(bad):
    rec = Recorder(body={})
    client = make_client(rec)
    with pytest.raises(InvalidInputError):
        run(client.get_recap(bad, "12h"))
    assert rec.requests == []


@pytest.mark.parametrize("bad", ["../x", "12", "0h", "12h&token=1", "9999h", "", "1d"])
def test_bad_timeframe_rejected_without_upstream_request(bad):
    rec = Recorder(body={})
    with pytest.raises(InvalidInputError):
        run(make_client(rec).get_recap("bitcoin", bad))
    assert rec.requests == []


def test_keyword_goes_through_params_not_path():
    rec = Recorder(body=[])
    run(make_client(rec).get_news(keyword="ETF & SEC/../settings"))
    req = rec.requests[0]
    assert req.url.path == "/news"
    assert req.url.params["keyword"] == "ETF & SEC/../settings"


def test_over_long_keyword_rejected():
    rec = Recorder(body=[])
    with pytest.raises(InvalidInputError):
        run(make_client(rec).get_news(keyword="x" * 1000))
    assert rec.requests == []


# Route scope ---------------------------------------------------------------


def test_client_uses_only_reader_routes():
    """The service token is a reader token: only these four routes are allowed."""
    allowed = {"/news", "/recaps", "/available-feed-categories"}
    rec = Recorder(body=[])
    client = make_client(rec)
    run(client.get_news(category="bitcoin"))
    run(client.get_news(keyword="ETF"))
    run(client.get_categories())
    rec.body = {}
    run(client.get_news_by_id(NEWS_ID))
    run(client.get_recap("bitcoin"))
    for req in rec.requests:
        assert req.method == "GET"
        path = req.url.path
        assert path in allowed or path == f"/news/{NEWS_ID}", path


def test_tool_payload_is_json_serializable(mcp_with):
    mcp_with(Recorder(body=[{"id": NEWS_ID, "signal": "x", "long_context": "paid"}]))
    result = run(server.mcp.call_tool("get_latest_news", {"category": "bitcoin"}))
    json.dumps(result, default=str)

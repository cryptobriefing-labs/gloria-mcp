"""Async HTTP client wrapping the ai-hub API.

Security rules this module enforces:

* The API token travels only in the ``Authorization: Bearer`` header. It is
  never placed in a URL, so it cannot appear in an exception message, an
  access log or a redirect.
* Every upstream failure (HTTP status, timeout, transport, invalid JSON) is
  converted into :class:`UpstreamError`, whose message carries at most the
  HTTP status code. No URL, header or upstream body ever reaches the MCP
  caller.
* Every caller-supplied value is validated before a request is built. Path
  segments are never built from unvalidated input, and query values go
  through httpx ``params`` only.

The service needs only these four read-only ai-hub routes, so its token
should be a "reader" role token:

    GET /news
    GET /news/{id}
    GET /recaps
    GET /available-feed-categories
"""

from __future__ import annotations

import logging
import re
import uuid

import httpx

logger = logging.getLogger("gloria_mcp.client")

# ai-hub news ids are UUIDs (the `news.id` column is a Postgres uuid).
# Only the canonical hyphenated form is accepted.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# Feed category codes are lowercase slugs such as "bitcoin" or "ai_agents".
_CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
# A comma-separated list of categories, for the /news filter.
_MAX_CATEGORIES = 10
# Recap timeframes look like "1h", "12h" or "24h".
_TIMEFRAME_RE = re.compile(r"^[1-9][0-9]{0,2}h$")
_MAX_KEYWORD_LEN = 200


class GloriaClientError(Exception):
    """Base class. Its message is always safe to show to the MCP caller."""


class InvalidInputError(GloriaClientError, ValueError):
    """A caller-supplied value failed validation. No request was sent."""


class UpstreamError(GloriaClientError):
    """The ai-hub request failed. The message never contains a URL or a token."""

    def __init__(self, status_code: int | None = None):
        self.status_code = status_code
        if status_code is None:
            message = "Upstream request failed"
        else:
            message = f"Upstream request failed (status {status_code})"
        super().__init__(message)


def validate_news_id(news_id: object) -> str:
    """Return the canonical lowercase UUID string, or raise InvalidInputError."""
    if not isinstance(news_id, str) or not _UUID_RE.fullmatch(news_id):
        raise InvalidInputError("Invalid news id: expected a UUID")
    return str(uuid.UUID(news_id))


def validate_category(category: object) -> str:
    """Return a normalized category code, or raise InvalidInputError."""
    if not isinstance(category, str):
        raise InvalidInputError("Invalid category")
    normalized = category.strip().lower()
    if not _CATEGORY_RE.fullmatch(normalized):
        raise InvalidInputError("Invalid category: use a code from get_categories")
    return normalized


def validate_category_list(categories: object) -> str:
    """Validate a comma-separated list of category codes."""
    if not isinstance(categories, str):
        raise InvalidInputError("Invalid category")
    parts = [p for p in (s.strip() for s in categories.split(",")) if p]
    if not parts or len(parts) > _MAX_CATEGORIES:
        raise InvalidInputError("Invalid category: use a code from get_categories")
    return ",".join(validate_category(p) for p in parts)


def validate_timeframe(timeframe: object) -> str:
    if not isinstance(timeframe, str):
        raise InvalidInputError("Invalid timeframe")
    normalized = timeframe.strip().lower()
    if not _TIMEFRAME_RE.fullmatch(normalized):
        raise InvalidInputError("Invalid timeframe: use a value such as '1h', '12h' or '24h'")
    return normalized


def validate_keyword(keyword: object) -> str:
    if not isinstance(keyword, str):
        raise InvalidInputError("Invalid query")
    normalized = keyword.strip()
    if not normalized or len(normalized) > _MAX_KEYWORD_LEN:
        raise InvalidInputError(f"Invalid query: use 1 to {_MAX_KEYWORD_LEN} characters")
    if any(ord(c) < 32 for c in normalized):
        raise InvalidInputError("Invalid query: control characters are not allowed")
    return normalized


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidInputError("Invalid limit")
    return max(1, min(10, limit))


class GloriaClient:
    """Thin async wrapper around ai-hub REST endpoints."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport  # injectable for tests
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=30.0,
                follow_redirects=False,
                headers={"Authorization": f"Bearer {self._token}"},
                transport=self._transport,
            )
        return self._http

    async def _get(
        self,
        path: str,
        params: dict | None = None,
        *,
        route: str,
    ) -> dict | list | None:
        """GET `path` and return parsed JSON, None on 404.

        `route` is the path template (for example "/news/{id}") used in log
        lines, so logs never carry ids, query strings or credentials.
        """
        client = await self._client()
        try:
            resp = await client.get(path, params=params or None)
        except httpx.TimeoutException:
            logger.warning("ai-hub GET %s timed out", route)
            raise UpstreamError() from None
        except httpx.HTTPError as exc:
            logger.warning("ai-hub GET %s failed: %s", route, type(exc).__name__)
            raise UpstreamError() from None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning("ai-hub GET %s returned status %s", route, resp.status_code)
            raise UpstreamError(resp.status_code)
        try:
            return resp.json()
        except ValueError:
            logger.warning("ai-hub GET %s returned a body that is not JSON", route)
            raise UpstreamError(resp.status_code) from None

    async def get_news(
        self,
        category: str | None = None,
        keyword: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        params: dict = {"limit": _validate_limit(limit), "page": 1}
        if category:
            params["feed_categories"] = validate_category_list(category)
        if keyword is not None:
            params["keyword"] = validate_keyword(keyword)
        result = await self._get("/news", params, route="/news")
        if result is None:
            return []
        if not isinstance(result, list):
            raise UpstreamError()
        return result

    async def get_news_by_id(self, news_id: str) -> dict | None:
        canonical = validate_news_id(news_id)
        result = await self._get(f"/news/{canonical}", route="/news/{id}")
        if result is not None and not isinstance(result, dict):
            raise UpstreamError()
        return result

    async def get_recap(self, category: str, timeframe: str = "12h") -> dict | None:
        params = {
            "feed_category": validate_category(category),
            "timeframe": validate_timeframe(timeframe),
        }
        result = await self._get("/recaps", params, route="/recaps")
        if result is not None and not isinstance(result, dict):
            raise UpstreamError()
        return result

    async def get_categories(self) -> list[dict]:
        result = await self._get(
            "/available-feed-categories", route="/available-feed-categories"
        )
        if result is None:
            return []
        if not isinstance(result, list):
            raise UpstreamError()
        return result

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()

# Gloria AI MCP Server

MCP server providing real-time curated crypto news from [Gloria AI](https://itsgloria.ai) to AI agents.

## Tools

### Free tier
| Tool | Description |
|------|-------------|
| `get_latest_news` | Latest curated crypto headlines with sentiment and categories |
| `get_news_recap` | AI-generated summary for a category (hourly for crypto/macro) |
| `search_news` | Search news by keyword |
| `get_categories` | List all 18+ news categories |
| `get_news_item` | Get a specific news item by ID |

### Paid tier (x402)
| Tool | Description |
|------|-------------|
| `get_enriched_news` | Full context, entity analysis, token mentions |
| `get_ticker_summary` | 24h AI summary for any ticker/topic |

Paid tools return payment instructions using the [x402 protocol](https://x402.org) (USDC on Base).

## Quick start

### Claude Desktop / Claude Code (stdio)

```json
{
  "mcpServers": {
    "gloria": {
      "command": "uv",
      "args": ["--directory", "/path/to/gloria-mcp", "run", "gloria-mcp"],
      "env": {
        "GLORIA_API_TOKEN": "your_token",
        "AI_HUB_BASE_URL": "https://ai-hub.cryptobriefing.com"
      }
    }
  }
}
```

### Remote (Streamable HTTP)

```bash
export GLORIA_API_TOKEN=your_token
export AI_HUB_BASE_URL=https://ai-hub.cryptobriefing.com
export MCP_TRANSPORT=streamable-http
export MCP_PORT=8005

uv run gloria-mcp
```

## Development

```bash
# Install
uv pip install -e .

# Test with MCP Inspector
mcp dev src/gloria_mcp/server.py

# Run directly
uv run gloria-mcp
```

## Environment variables

| Variable | Required | Default |
|----------|----------|---------|
| `GLORIA_API_TOKEN` | Yes | (none) |
| `AI_HUB_BASE_URL` | No | `https://ai-hub.cryptobriefing.com` |

### Token requirements

`GLORIA_API_TOKEN` must be an ai-hub token with the `reader` role. The server
calls only these four read-only routes, and a reader token allows nothing else:

| Route | Used by |
|-------|---------|
| `GET /news` | `get_latest_news`, `search_news` |
| `GET /news/{id}` | `get_news_item` |
| `GET /recaps` | `get_news_recap` |
| `GET /available-feed-categories` | `get_categories`, `gloria://categories` |

Do not use a token with broader roles.

The token is sent only in the `Authorization: Bearer` header, never in a URL.
Upstream failures reach the MCP caller as a generic message such as
`Upstream request failed (status 403)`, with no URL, header or upstream body.
Ids, categories and timeframes are validated before any request is sent.

## Tests

```bash
pip install -r requirements.txt pytest
PYTHONPATH=src pytest -q tests
```

"""Offline MCP contract smoke for gloria-mcp.

Starts the real server (src/gloria_mcp/server.py) against a fake ai-hub on
127.0.0.1, then drives it as an MCP client would:

  * streamable-http, the production transport, with the same initialize
    request and Host header as scripts/health-check.sh
  * the transport-security Host allow-list (localhost, mcp.itsgloria.ai,
    and the 421 cases DEPLOY.md documents)
  * initialize, tools/list, every tools/call, resources/list, resources/read
  * stdio, the transport Claude Desktop and Claude Code use

The server runs under guarded_server.py, which refuses and records every
non-loopback socket connect. The token is a fake value. No real ai-hub, x402
payment, model or user is touched.

Usage (from any directory):
  <venv>/bin/python -I mcp_contract_smoke.py <path-to-gloria-mcp-repo>

Exit code 0 means every check passed. The last line is a JSON summary.
"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.abspath(sys.argv[1])
SRC = os.path.join(REPO, "src")
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = "fake-" + secrets.token_hex(6)
SENTINEL = "SENTINEL-" + secrets.token_hex(4)
HUB_LOG = []

NEWS = {
    "id": "n-1", "signal": f"headline {SENTINEL}", "sentiment": "positive",
    "sentiment_value": 0.7, "timestamp": "2026-10-26T00:00:00Z",
    "feed_categories": ["bitcoin"], "sources": ["x"], "author": "a", "tokens": ["BTC"],
    "tweet_url": "https://x.com/i/1",
    "long_context": "PAID-FIELD-MUST-NOT-LEAK", "short_context": "PAID-FIELD-MUST-NOT-LEAK",
}


class Hub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        HUB_LOG.append({"path": u.path, "query": q})
        if q.get("token") != TOKEN:
            return self._send(401, {"error": "bad token"})
        if u.path == "/news":
            return self._send(200, [dict(NEWS, id=f"n-{i}") for i in range(int(q.get("limit", 5)))])
        if u.path == "/news/n-1":
            return self._send(200, NEWS)
        if u.path.startswith("/news/"):
            return self._send(404, {"error": "not found"})
        if u.path == "/recaps":
            if q.get("feed_category") == "missing":
                return self._send(404, {"error": "not found"})
            return self._send(200, {"feed_category": q.get("feed_category"), "timeframe": q.get("timeframe"),
                                    "recap": f"recap {SENTINEL}", "created_at": "2026-10-26T00:00:00Z"})
        if u.path == "/available-feed-categories":
            return self._send(200, [{"code": "bitcoin", "name": "Bitcoin", "recap_timeframe": "8h"},
                                    {"code": "ai", "name": "AI", "recap_timeframe": None}])
        return self._send(404, {"error": "not found"})


hub = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
threading.Thread(target=hub.serve_forever, daemon=True).start()
HUB = f"http://127.0.0.1:{hub.server_address[1]}"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DENIED_LOG = tempfile.mktemp(prefix="mcp-denied-")
open(DENIED_LOG, "w").close()
PORT = free_port()
ENV = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
ENV.update({"GLORIA_API_TOKEN": TOKEN, "AI_HUB_BASE_URL": HUB, "NO_PROXY": "127.0.0.1,localhost"})
WORK = tempfile.mkdtemp(prefix="mcp-smoke-")  # cwd with no .env, so load_dotenv finds nothing

http_env = dict(ENV, MCP_TRANSPORT="streamable-http", MCP_PORT=str(PORT))
server = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "guarded_server.py"), SRC, DENIED_LOG],
                          env=http_env, cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "deploy-health-check", "version": "1"}}}).encode()


def raw_initialize(host):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/mcp", data=INIT, method="POST", headers={
        "Host": host, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return 0, repr(e)


code, body = 0, ""
for _ in range(60):
    code, body = raw_initialize("localhost")
    if code == 200:
        break
    if server.poll() is not None:
        break
    time.sleep(0.5)
check("health-check handshake (Host: localhost) returns 200 with serverInfo and protocolVersion",
      code == 200 and '"serverInfo"' in body and '"protocolVersion"' in body, f"HTTP {code}")
server_info = ""
for line in body.splitlines():
    if line.startswith("data:") and "serverInfo" in line:
        server_info = json.dumps(json.loads(line[5:])["result"]["serverInfo"])
check("serverInfo names Gloria AI", '"Gloria AI"' in server_info, server_info)

c, _ = raw_initialize("mcp.itsgloria.ai")
check("Host: mcp.itsgloria.ai (nginx proxy_set_header Host $host) is accepted", c == 200, f"HTTP {c}")
c, _ = raw_initialize(f"127.0.0.1:{PORT}")
check("Host: 127.0.0.1:<port> is rejected with 421, as DEPLOY.md documents", c == 421, f"HTTP {c}")
c, _ = raw_initialize("evil.example")
check("an unknown Host is rejected with 421 (DNS rebinding protection)", c == 421, f"HTTP {c}")


async def drive(session, label):
    out = {}
    tools = await session.list_tools()
    names = sorted(t.name for t in tools.tools)
    expected = sorted(["get_latest_news", "get_news_recap", "search_news", "get_categories",
                       "get_news_item", "get_enriched_news", "get_ticker_summary"])
    check(f"{label}: tools/list returns the seven tools", names == expected, ",".join(names))

    def payload(res):
        if getattr(res, "structuredContent", None):
            sc = res.structuredContent
            return sc.get("result", sc) if isinstance(sc, dict) else sc
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        vals = []
        for t in texts:
            try:
                vals.append(json.loads(t))
            except Exception:  # noqa: BLE001
                vals.append(t)
        return vals[0] if len(vals) == 1 else vals

    h0 = len(HUB_LOG)
    r = payload(await session.call_tool("get_latest_news", {"category": "bitcoin", "limit": 50}))
    q = HUB_LOG[h0]["query"] if len(HUB_LOG) > h0 else {}
    check(f"{label}: get_latest_news clamps limit to 10 and filters by category",
          isinstance(r, list) and len(r) == 10 and q.get("limit") == "10" and q.get("feed_categories") == "bitcoin"
          and q.get("token") == TOKEN, json.dumps(q))
    check(f"{label}: free tier strips long_context and short_context",
          "PAID-FIELD-MUST-NOT-LEAK" not in json.dumps(r) and SENTINEL in json.dumps(r))

    h0 = len(HUB_LOG)
    r = payload(await session.call_tool("search_news", {"query": "ETF", "limit": 9}))
    q = HUB_LOG[h0]["query"] if len(HUB_LOG) > h0 else {}
    check(f"{label}: search_news clamps limit to 5 and sends keyword", isinstance(r, list) and len(r) == 5
          and q.get("keyword") == "ETF", json.dumps(q))

    r = payload(await session.call_tool("get_news_item", {"id": "n-1"}))
    check(f"{label}: get_news_item returns the free-tier item", isinstance(r, dict) and r.get("id") == "n-1"
          and "long_context" not in r, json.dumps(r)[:120])
    r = payload(await session.call_tool("get_news_item", {"id": "nope"}))
    check(f"{label}: get_news_item on a 404 returns an error object", isinstance(r, dict) and "not found" in r.get("error", ""),
          json.dumps(r))

    h0 = len(HUB_LOG)
    r = payload(await session.call_tool("get_news_recap", {"category": "bitcoin", "timeframe": "8h"}))
    q = HUB_LOG[h0]["query"] if len(HUB_LOG) > h0 else {}
    check(f"{label}: get_news_recap passes category and timeframe", isinstance(r, dict) and SENTINEL in r.get("recap", "")
          and q.get("feed_category") == "bitcoin" and q.get("timeframe") == "8h", json.dumps(q))
    r = payload(await session.call_tool("get_news_recap", {"category": "missing"}))
    check(f"{label}: get_news_recap on a 404 returns an error object", isinstance(r, dict) and "No recap found" in r.get("error", ""))

    r = payload(await session.call_tool("get_categories", {}))
    check(f"{label}: get_categories returns the hub list", isinstance(r, list) and r and r[0].get("code") == "bitcoin")

    r = payload(await session.call_tool("get_enriched_news", {}))
    check(f"{label}: get_enriched_news returns x402 payment instructions only",
          isinstance(r, dict) and r.get("payment_required") is True and r.get("protocol") == "x402")
    r = payload(await session.call_tool("get_ticker_summary", {}))
    check(f"{label}: get_ticker_summary returns x402 payment instructions only",
          isinstance(r, dict) and r.get("payment_required") is True and r.get("endpoint", "").endswith("/news-ticker-summary"))

    res = await session.list_resources()
    uris = sorted(str(x.uri) for x in res.resources)
    check(f"{label}: resources/list returns gloria://about and gloria://categories",
          uris == ["gloria://about", "gloria://categories"], ",".join(uris))
    from pydantic import AnyUrl
    about = await session.read_resource(AnyUrl("gloria://about"))
    check(f"{label}: resources/read gloria://about", "Gloria AI" in about.contents[0].text)
    cats = await session.read_resource(AnyUrl("gloria://categories"))
    check(f"{label}: resources/read gloria://categories renders hub categories",
          "- bitcoin: Bitcoin (recaps every 8h)" in cats.contents[0].text and "- ai: AI (no recaps)" in cats.contents[0].text,
          cats.contents[0].text.replace("\n", " | ")[:160])
    return out


async def http_client():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(f"http://127.0.0.1:{PORT}/mcp", headers={"Host": "localhost"}) as (r, w, sid):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            check("streamable-http: MCP client initialize", init.serverInfo.name == "Gloria AI",
                  f"protocol {init.protocolVersion}, server {init.serverInfo.version}")
            check("streamable-http: the server issued a session id", bool(sid()))
            await drive(session, "streamable-http")


async def stdio_client_run():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable,
                                   args=["-I", os.path.join(HERE, "guarded_server.py"), SRC, DENIED_LOG],
                                   env={k: v for k, v in ENV.items() if k != "MCP_TRANSPORT"}, cwd=WORK)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            check("stdio: MCP client initialize", init.serverInfo.name == "Gloria AI")
            await drive(session, "stdio")


try:
    if code == 200:
        asyncio.run(http_client())
    asyncio.run(stdio_client_run())
except Exception as e:  # noqa: BLE001
    check("client run completed without an exception", False, repr(e)[:400])

check("server process still alive after the HTTP run", server.poll() is None)

# GHSA-84m7-p3x7-pcfv (unbounded Streamable HTTP sessions) is fixed in mcp
# 1.30.0 by an idle timeout and a session cap. Read them from the real server
# object in a clean child, so this reports what production will run.
probe = subprocess.run(
    [sys.executable, "-I", "-c",
     "import sys, json; sys.path.insert(0, sys.argv[1]); import gloria_mcp.server as s; st = s.mcp.settings; "
     "print(json.dumps({k: getattr(st, k, None) for k in ('session_idle_timeout', 'max_sessions')}))", SRC],
    env=ENV, cwd=WORK, capture_output=True, text=True, timeout=60)
try:
    caps = json.loads(probe.stdout.strip().splitlines()[-1])
except Exception:  # noqa: BLE001
    caps = {}
check("streamable-http session idle timeout and session cap are set (GHSA-84m7-p3x7-pcfv)",
      bool(caps.get("session_idle_timeout")) and bool(caps.get("max_sessions")), json.dumps(caps))
server.terminate()
try:
    server.wait(10)
except subprocess.TimeoutExpired:
    server.kill()
srv_out = server.stdout.read() if server.stdout else ""
check("server log has no Traceback", "Traceback" not in srv_out, srv_out[-300:] if "Traceback" in srv_out else "")
denied = [l for l in open(DENIED_LOG).read().splitlines() if l]
check("no non-loopback connection was attempted by the server", not denied, ";".join(denied))
check("every fake ai-hub request carried the token", HUB_LOG and all(h["query"].get("token") == TOKEN for h in HUB_LOG),
      f"{len(HUB_LOG)} requests")

from importlib.metadata import version as _v  # noqa: E402
versions = {}
for p in ["mcp", "starlette", "sse-starlette", "uvicorn", "PyJWT", "cryptography", "httpx", "anyio", "pydantic",
          "python-multipart", "python-dotenv"]:
    try:
        versions[p] = _v(p)
    except Exception:  # noqa: BLE001
        versions[p] = None
failed = [r for r in RESULTS if not r["ok"]]
print(json.dumps({"python": sys.version.split()[0], "passed": len(RESULTS) - len(failed), "failed": len(failed),
                  "denied_connects": denied, "server_info": server_info, "versions": versions}))
sys.exit(0 if not failed else 1)

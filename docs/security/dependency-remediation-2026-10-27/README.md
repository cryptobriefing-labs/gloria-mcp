# gloria-mcp dependency remediation, 2026-10-27

Scope: `requirements.txt`, the `dependencies` list in `pyproject.toml`, and one section of `DEPLOY.md`. No source change under `src/`. No workflow change.

**Merging this to `main` deploys it.** `.github/workflows/prod.yml` runs on every push to `main`. It installs `requirements.txt` because the file changed, and then restarts the service. The restart drops every live MCP session.

## What was wrong

1. **The live server runs old, advisory-listed packages.** The parent reported the active venv with `mcp` 1.26.0, `starlette` 0.52.1, `PyJWT` 2.11.0 and `cryptography` 46.0.5 (39 packages). OSV lists advisories for all four (`receipts/osv-audit.txt`).
2. **One `mcp` advisory hits this server directly.** GHSA-84m7-p3x7-pcfv (CVE-2026-59951): the Streamable HTTP transport keeps sessions without limit, so a client can exhaust memory by opening sessions. Production serves that transport publicly at `https://mcp.itsgloria.ai/mcp`. GitHub's advisory lists `>= 1.8.0, < 1.30.0` as affected, fixed in 1.30.0. The OSV record lists only 2.2.0 as its fix version, but its range data also names the v1.30.0 tag.
3. **A fresh install of the source floors breaks the server.** `requirements.txt` and `pyproject.toml` said `mcp[cli]>=1.26.0` with no upper bound. On 2026-10-27 that resolves `mcp` 2.3.0, which removes `mcp.server.fastmcp`. `import gloria_mcp.server` then fails with `ModuleNotFoundError`. The running box did not hit this because `pip install -r` keeps an already-satisfied `mcp`. A rebuilt venv, the Docker image, or a new PyPI install would get 2.x (`receipts/smoke-unbounded-main-fresh.txt`).

## What changed

- `requirements.txt` is now an exact lock of 37 packages. It was resolved fresh on CPython 3.10 with `mcp[cli]==1.30.0`, `httpx>=0.27,<1` and `python-dotenv>=1.2.2`, then checked on 3.12. `exceptiongroup` carries a `python_version < "3.11"` marker.
- `pyproject.toml`: `mcp[cli]>=1.30.0,<2`, `httpx>=0.27.0,<1`, `python-dotenv>=1.2.2`. The version stays 1.0.0, and nothing publishes until someone cuts a GitHub Release.
- `DEPLOY.md`: the "Why the install is conditional" section now describes the lock instead of the floors.

Key moves against the reported live versions:

| Package | Live (parent report) | After |
|---|---|---|
| mcp | 1.26.0 | 1.30.0 (latest 1.x) |
| starlette | 0.52.1 | 1.7.0 (newest release that supports Python 3.10; 1.8.0 needs 3.11) |
| PyJWT | 2.11.0 | 2.15.1 |
| cryptography | 46.0.5 | 50.0.2 |

### Behaviour changes that come with mcp 1.30.0

From the upstream release notes, checked against the code:

- **Idle Streamable HTTP sessions now expire after 30 minutes. A server holds at most 10,000 sessions, and new sessions beyond that get a 503.** This is the fix for the advisory. A client that sits idle for more than 30 minutes and does not keep the GET stream open gets a 404 and must initialize again. The SDK's own client keeps the stream open. I kept the defaults.
- The other changes are client-side (redirects, OAuth issuer checks) or opt-in. This server uses none of them.

## Which Python the host runs

An internal host file manifest from 2026-08-07 lists the live venv at `.../gloria-mcp/venv/lib/python3.10/`. That is a source value, not re-checked here. A 3.10 venv with the four reported live versions has 39 packages including pip and setuptools, which matches the 39 the parent reported. The `Dockerfile` uses 3.12. So every test runs on 3.10.22, and again on 3.12.13.

## Evidence

All runs were on pepito (macOS arm64) on 2026-10-27 UTC. Receipts are in `receipts/`.

| Check | Result | Receipt |
|---|---|---|
| Fresh 3.10 venv from the lock, `pip check` | ok, 39 packages including tooling | `freeze-py310-after.txt` |
| Fresh 3.12 venv from the lock, `pip check` | ok, 37 packages including tooling | `freeze-py312-after.txt` |
| Live-like 3.10 venv upgraded in place by `pip install -r` (the deploy path) | install ok, `pip check` ok | `inplace-upgrade-diff.txt` |
| OSV audit, live-like before | mcp 4, starlette 5, PyJWT 17, cryptography 6 advisories | `osv-audit.txt` |
| OSV audit after, 3.10 fresh and in-place | only `setuptools` 84.0.0 (Windows-only, venv bootstrap) | `osv-audit.txt` |
| OSV audit after, 3.12 | only `pip` 26.1.2 (venv bootstrap) | `osv-audit.txt` |
| Linux wheel resolution, `--only-binary=:all:` dry run | all pins resolve for cp310 and cp312, x86_64 and aarch64, down to glibc 2.17 | none |
| Offline MCP smoke, live-like before | 40 of 41. The one failure is the session cap check: both settings are absent on 1.26.0 | `smoke-livelike-before.txt` |
| Offline MCP smoke, after on 3.10, 3.12 and in-place 3.10 | 41 of 41 each | `smoke-py310-after.txt`, `smoke-py312-after.txt`, `smoke-py310-inplace-upgrade.txt` |
| Offline MCP smoke, fresh install of the old floors | 1 of 11: the server cannot import on `mcp` 2.3.0 | `smoke-unbounded-main-fresh.txt`, `unbounded-main-fresh-import-error.txt` |

The "live-like" venv uses the four versions the parent reported. Its other packages are fresh, so the real live venv may differ. The Linux dry run proves that a wheel exists for each pin. It does not install or run anything on Linux.

### The MCP smoke

`tools/mcp_contract_smoke.py` starts the real server through `tools/guarded_server.py`. That wrapper refuses and records every socket connect to a non-loopback address. The server talks to a fake ai-hub on 127.0.0.1 with a fake token. The smoke then:

- sends the exact `initialize` request `scripts/health-check.sh` sends, with `Host: localhost`, and expects 200 with `serverInfo` and `protocolVersion`
- checks the transport-security Host list: `mcp.itsgloria.ai` (what nginx forwards) gets 200; `127.0.0.1:<port>` and an unknown host get 421, as `DEPLOY.md` documents
- runs a real MCP client over Streamable HTTP, and again over stdio: `initialize`, `tools/list` (all seven tools), a `tools/call` for every tool including the 404 paths, `resources/list`, and `resources/read` for both resources
- checks the requests the fake ai-hub received: token present, `limit` clamped to 10 and 5, the category and keyword filters, and that `long_context` and `short_context` never reach the client
- reads `session_idle_timeout` and `max_sessions` from the real server object
- checks that the server did not crash, logged no traceback, and attempted no external connection

It does not touch the real ai-hub, x402 payments or any real client. It does not test a 30-minute idle expiry in real time, or 10,000 concurrent sessions.

Rerun:

```
python3.10 -m venv /tmp/mcp && /tmp/mcp/bin/pip install -r requirements.txt && /tmp/mcp/bin/pip check
```

```
/tmp/mcp/bin/python -I docs/security/dependency-remediation-2026-10-27/tools/mcp_contract_smoke.py .
```

## Reachability, as far as I checked

- **mcp, session accumulation (GHSA-84m7-p3x7-pcfv):** reached. The production transport is public Streamable HTTP.
- **mcp, other three:** experimental task handlers (not used), the WebSocket server transport (not used), and unverified principal on HTTP sessions (this server configures no auth). They are low for this server, as far as I can read. They are fixed by 1.30.0 anyway.
- **starlette:** the two Host and path poisoning advisories touch the request path this server serves behind nginx. The `StaticFiles`, `HTTPEndpoint` and form-limit advisories cover features this server does not use.
- **PyJWT, cryptography:** `mcp` depends on them for its auth helpers. This server configures no auth, so I found no call path. Patched anyway.

## Open items for the parent

1. **Staging on Linux before merge.** Build a fresh Python 3.10 venv from this branch on Linux, for example on the existing WP staging host, then run the two commands above. The smoke needs no secrets. Do not depend on the temporary Ubuntu canary.
2. **Confirm host Python and the real inventory** with `venv/bin/python --version` and `venv/bin/pip list` before merging. The 3.10 fact is from August.
3. **Merging deploys.** Merge only when a restart, and dropping the live sessions, is acceptable. The pipeline's health check runs the same `initialize` handshake the smoke runs. After the deploy, `serverInfo.version` should read `1.30.0`. The top-level rsync also copies this new `docs/` folder to the box. It is tracked in git, so `scripts/drift-check.sh` accepts it, and nothing on `PYTHONPATH` loads it.
4. **Venv tooling.** `pip install -r` does not upgrade `pip` or `setuptools` in the host venv. That is a separate host action: `venv/bin/python -m pip install -U pip setuptools`.
5. **PyPI and the registry.** Users who install `gloria-mcp` from PyPI still get the old `>=1.26.0` floor, which now resolves `mcp` 2.x and breaks on import. A new GitHub Release (for example 1.0.2, with `server.json` updated to match) would publish the bounded range. That is a release decision, not part of this PR.
6. **Stale comment.** `.github/workflows/prod.yml` still says `requirements.txt` pins floors (around line 207). It is a comment only, and this PR does not touch the workflow.
7. **Rollback.** Revert the commit on `main` and let the pipeline redeploy. It reinstalls the previous floors. That keeps `mcp` 1.30.0 in place, because a floor is already satisfied. To truly return to 1.26.0, a reviewed commit would have to pin it.

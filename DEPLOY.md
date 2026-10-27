# DEPLOY.md

> **THIS REPOSITORY IS PUBLIC.** Infrastructure identifiers are deliberately not
> written down here: no account number, no role ARN, no security group id, no
> host address, no SSH user or port. Every one of them is referenced by the name
> of a GitHub Actions variable, and the value lives in repo settings where only
> people with access can read it. **Keep it that way when you edit this file.**
> The values themselves are in the deploy handover note held by the repo owner.

```
Owner:        <UNASSIGNED>        # DECISION PENDING D1, Diego's call. Do not fill in without agreement.
Backup owner: <UNASSIGNED>
Tier:         S                   # box service. No staging box, and that is the correct answer.
Runtime:      One EC2 instance, referenced by the SSH_HOST variable.
              Directory:  $APP_DIRECTORY variable
              systemd:    $APP_NAME variable (a .service unit, User=ubuntu)
              A verbatim copy of the live unit is committed at deploy/gloria-mcp.service.
              It runs `venv/bin/python -m gloria_mcp.server` with PYTHONPATH set to
              the src/ directory, MCP_TRANSPORT=streamable-http, MCP_PORT=8005.
Production:   https://mcp.itsgloria.ai/mcp  (nginx proxies to 127.0.0.1:8005; see deploy/nginx-mcp.conf)
Staging:      none, Tier S.
```

**Status: the pipeline in this file is AUTHORED BUT NEVER RUN.** It has not been
exercised once. Read "Before the first deploy" at the bottom before merging
anything that would trigger it.

---

## The one sanctioned deploy path

Production is deployed ONLY by `.github/workflows/prod.yml`, triggered by a push
to `main`, plus a manual `workflow_dispatch` for the case where you need to force
a restart without a code change.

Nothing else deploys this repo. If you deployed it another way, that is an
incident, not a shortcut. Tell the owner.

**The transport is `rsync`, never `git pull` on the box.** The runtime directory
has never been a git clone and must never become one. A `.git` on a runtime host
invites `git pull` or `git checkout` during an incident. That is how a sibling
runtime in this estate lost two commits and five stashes on 2026-07-16.
`scripts/drift-check.sh` fails the deploy if a `.git` appears there.

### What the workflow does, in order

| # | Step | Note |
|---|---|---|
| 1 | OIDC assume-role, get runner public IP | No long-lived AWS keys. The IP lookup is validated so a DNS failure reads as a DNS failure |
| 2 | Open port 22 to the runner's single `/32` | Just-in-time. Revoked in step 11 under `if: always()` |
| 3 | Fingerprint `src/**.py` and `requirements.txt` on the box | Taken BEFORE anything is written |
| 4 | `rsync src/` **with `--delete`** | Bounded blast radius. Removes stale modules |
| 5 | `rsync ./` top level **without `--delete`** | `venv/` and `.env` live here |
| 6 | `scripts/check-env.sh` | Read-only gate. Runs BEFORE any restart |
| 7 | `pip install -r requirements.txt` **only if `requirements.txt` changed** | See "Why the install is conditional" |
| 8 | `systemctl restart` **only if `src/` Python or `requirements.txt` changed**, or `force_restart` | See "Why the restart is conditional" |
| 9 | `scripts/health-check.sh` | **Unconditional.** Runs whether or not step 8 restarted |
| 10 | Parity check, then `scripts/drift-check.sh` | Box equals git, and the box holds nothing extra |
| 11 | Discord notify, revoke the `/32`, wipe `~/.ssh` | Revoke and cleanup are `if: always()` |

### Why the restart is conditional

A restart is not free. This server runs the MCP **streamable-http** transport and
holds client sessions in the server process. A restart drops every live session.
Deploying a README or a `pyproject.toml` version bump should not disconnect
clients, so the workflow restarts only when a file the running process actually
loads has changed.

"A file the running process actually loads" is precisely `src/**/*.py` plus
`requirements.txt`, because the `ExecStart` runs `python -m gloria_mcp.server`
with `PYTHONPATH` pointed at `src/`. Nothing else in this repo is read at
runtime.

**The health check runs either way.** A deploy that restarted nothing still has
to prove the service is alive, or a green run tells you nothing about production.

### Why the install is conditional

`requirements.txt` is an **exact lock** of every runtime package (since
2026-10-27, see `docs/security/dependency-remediation-2026-10-27/`). It used to
pin floors only (`mcp[cli]>=1.26.0`). A fresh install of those floors resolves
`mcp` 2.x, which removes `mcp.server.fastmcp`, and the server then fails to
import. `mcp` is now held on the 1.x line.

The install still runs only when the file changes. A dependency change is a
reviewed edit to `requirements.txt`, and it restarts the service.

The install is deliberately `pip install -r requirements.txt` and **never**
`pip install .` or `pip install -e .`. The unit runs the source tree directly via
`PYTHONPATH`; installing the package would put a second copy of `gloria_mcp` in
`site-packages` and which one wins would become a `sys.path` accident.

### The packaging files are not runtime files

`pyproject.toml`, `server.json`, `glama.json` and `LICENSE` exist because this
repo also **publishes to PyPI** (`.github/workflows/publish.yml`, triggered by a
GitHub Release) and is **listed in the MCP registry**. The version in
`pyproject.toml` is the release version. It is not what runs on the box and
nothing on the box reads it.

Consequence, and it is deliberate: those files are synced to the box as inert
metadata, and the deploy does not gate a restart on them. The repo being ahead of
the box on `pyproject.toml` and `.gitignore` (which it was on 2026-08-07, before
this pipeline existed) is not drift that matters, and the first deploy simply
brings the box level without touching a single line of executed code.

---

## Forbidden

Do NOT do any of the following, ever, for any reason, including urgency:

- `scp` or `rsync` individual files to the runtime directory outside the pipeline
- edit any file on the production host, including "just this one line", including
  as root
- run `git init`, `git pull`, `git checkout`, `git stash`, `git clean` or
  `git reset` inside the runtime directory. It is not a clone and must not become
  one
- delete, truncate, recreate or `echo >>` the host `.env`. It is the source of
  truth for this service's secrets and the pipeline only ever READS it
- delete or rebuild `venv/` as part of a deploy. It is the running interpreter
- `pip install .` or `pip install -e .` into that venv (see above)
- add `--delete` to the top-level rsync. `venv/` and `.env` live there
- create `.bak`, `.pre`, `.old` or dated sidecar copies of a file as a substitute
  for a commit
- add, remove or edit the live systemd unit or the nginx vhost without committing
  the same change to `deploy/` in this repo
- write a secret value into any file that git tracks
- **write an infrastructure identifier into any file that git tracks.** This repo
  is public. Host addresses, account numbers, role ARNs and security group ids
  belong in GitHub Actions variables, referenced by name
- change `allowed_hosts` in `src/gloria_mcp/server.py` without changing
  `MCP_HOST_HEADER` in `scripts/health-check.sh` in the same commit. They are a
  matched pair and the deploy fails on every run the moment they desync

If you need to do one of these to recover an outage, do it, then open a PR the
same day that captures exactly what you did. An uncaptured emergency edit is how
runtimes in this estate drifted from their repos in the first place.

---

## Runtime files that are NOT in git, and why

| Path (relative to `$APP_DIRECTORY`) | Why | Where the real copy lives |
|---|---|---|
| `.env` | Secrets. Loaded by the unit via `EnvironmentFile=`. Two keys: `GLORIA_API_TOKEN`, `AI_HUB_BASE_URL` | The box, mode 0664 today. Key names in `.env.example` |
| `venv/` | The Python virtualenv the `ExecStart` runs. Built on the box | The box. Rebuilt by hand, never by a deploy |
| `__pycache__/` | CPython bytecode cache | Regenerated automatically |

That list is complete as of 2026-08-07 and `scripts/drift-check.sh` enforces it:
anything else appearing at the top level fails the deploy.

Two things that are **not** in this list because they are not this repo's:

- The live systemd unit. This repo carries a copy at `deploy/gloria-mcp.service`
  for the record, byte-identical as of 2026-08-07. It is documentation, not a
  deployed artifact.
- The live nginx vhost. Same, copy at `deploy/nginx-mcp.conf`.

---

## Rollback

```
Anchor:      the previous commit sha on `main`, plus a dated tarball on the host
Last tested: NEVER TESTED
```

Take the anchor before the first deploy. `~/freeze-backups/` does not exist on
the host yet, so create it. `$HOST` below is your ssh alias for the production
box, and `$APP` is the directory name.

```
ssh "$HOST" 'mkdir -p ~/freeze-backups'
```

```
ssh "$HOST" "tar -C \$(dirname \"$APP_DIRECTORY\") --exclude='*/venv' -czf ~/freeze-backups/gloria-mcp-\$(date +%Y%m%d-%H%M%S).tgz \$(basename \"$APP_DIRECTORY\")"
```

Verify it is readable before proceeding. **An untested backup is not a backup.**

```
ssh "$HOST" 'tar -tzf ~/freeze-backups/gloria-mcp-<stamp>.tgz | head'
```

To roll back, revert the commit on `main` and let the pipeline redeploy:

```
git revert <bad-sha>
```

```
git push origin main
```

If the pipeline itself is what is broken, roll back by hand from a clone at the
previous sha, then fix the pipeline in a PR the same day:

```
rsync -rlz --delete --no-times --no-perms --exclude='__pycache__/' --exclude='*.pyc' src/ "$HOST:$APP_DIRECTORY/src/"
```

```
ssh "$HOST" "sudo systemctl restart \"\$APP_NAME\" && cd \"$APP_DIRECTORY\" && ./scripts/health-check.sh"
```

---

## Health check

`scripts/health-check.sh`, run by the pipeline on every deploy and runnable by
hand at any time. It writes nothing, so run it whenever you want the truth:

```
ssh "$HOST" 'bash -s' < scripts/health-check.sh
```

Four assertions, and the specific failure each one catches:

| # | Assertion | What it catches that a weaker check would miss |
|---|---|---|
| 1 | `systemctl is-active` on the unit is `active` | The unit failed to start at all |
| 2 | A real MCP `initialize` handshake on `http://127.0.0.1:8005/mcp` returns 200 with `serverInfo` | An `ImportError` or `SyntaxError` in synced source, a broken dependency, a transport-security change, FastMCP failing to register tools. `GET /` on this server returns **404 by design**, so a naive "does it answer HTTP" check proves nothing |
| 3 | `NRestarts` did not increase during the settle window | **The crash loop.** The unit is `Restart=on-failure` with `RestartSec=5`, so a service that starts, serves one request and dies is `active` again five seconds later and looks perfectly healthy to any point-in-time check |
| 4 | `systemctl is-active` is still `active` after the settle window | A death that starts after assertion 2 passed |

**The `Host` header is load-bearing.** `src/gloria_mcp/server.py` enables DNS
rebinding protection with `allowed_hosts = ["mcp.itsgloria.ai", "localhost",
"127.0.0.1"]`. curl's default `Host` for a URL carrying an explicit port is
`127.0.0.1:8005`, which is **not** in that list. Measured on the production host
2026-08-07:

```
Host: 127.0.0.1:8005  ->  HTTP 421 Misdirected Request
Host: localhost       ->  HTTP 200, serverInfo {"name":"Gloria AI","version":"1.26.0"}
```

The script sends `Host: localhost`. If you change `allowed_hosts`, change
`MCP_HOST_HEADER` in the same commit.

---

## Parity check

Proves the box byte-matches the deployed commit. This is the Tier S substitute
for a staging environment: there is no second box to compare against, so the
check is "does production match the commit".

Run from a clean clone at the deployed sha:

```
rsync -rln --checksum --itemize-changes --no-times --no-perms --exclude='.git/' --exclude='.github/' --exclude='venv/' --exclude='.venv/' --exclude='.env' --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.egg-info/' --exclude='dist/' --exclude='build/' ./ "$HOST:$APP_DIRECTORY/"
```

Expected: **no output at all.** Any line means drift.

**Stated limitation:** `rsync --dry-run` reports only what it would send. It
cannot see receiver-only files, so this proves "everything in git matches the
box", not "the box has nothing extra". The extras half is
`scripts/drift-check.sh`, which the pipeline runs immediately after.

```
Last run: 2026-08-07, read-only, from the tip of `main`.
Result:   src/ byte-identical. Box behind on .gitignore and pyproject.toml,
          and missing LICENSE, glama.json and server.json. All five are
          packaging or registry metadata, none is executed code.
```

---

## Before the first deploy

In order. Nothing here has been done.

1. Assign an owner. Fill in `Owner:` above. DECISION PENDING D1.
2. Create the `Production` GitHub Environment and populate the variables and
   secrets listed below. **The values are not in this file and must not be added
   to it.**
   **Confirm first that the deploy IAM role's trust policy allows
   `repo:cryptobriefing/gloria-mcp:*`.** If it is scoped to the repos that
   already use it, step 1 of every run fails with
   `Not authorized to perform sts:AssumeRoleWithWebIdentity`.
3. Take and verify the tarball rollback anchor (above). Test the rollback once.
4. Set the environment's deployment branch policy to `main` only. **This repo is
   public**, so unlike the private repos in this org, classic branch protection
   on `main` is also available today on the Free plan and should be turned on.
5. Merge this PR, then let it deploy once. The first deploy is a **no-op for the
   running service**: `src/` is already byte-identical, so no restart fires and
   only inert metadata is written. That makes it a safe first exercise.
6. **Then break it deliberately once.** Stop the unit and confirm the health
   check fails. An untested gate is not a gate.

### What the `Production` environment needs

Names only. **Do not write the values into this file or into the workflow.**

| Kind | Name | What it is |
|---|---|---|
| var | `AWS_REGION` | The region the instance is in |
| var | `AWS_ROLE_ARN` | The OIDC deploy role. Same one the org's existing EC2 pipeline already uses |
| var | `AWS_INSTANCE_SG_ID` | The security group the just-in-time port 22 rule is added to and removed from |
| var | `SSH_HOST` | The instance's public DNS name |
| var | `SSH_PORT` | The SSH port |
| var | `SSH_USERNAME` | The deploy user on the box |
| var | `APP_DIRECTORY` | Absolute path to the runtime directory |
| var | `APP_NAME` | The systemd unit name, including `.service` |
| secret | `SSH_PRIVATE_KEY` | A deploy key authorized for the deploy user. **Not created by this PR** |
| secret | `DISCORD_WEBHOOK` | The deploy-notification webhook |

Three facts the owner needs before configuring this, none of which belong in a
public file as values:

- **The security group is shared with a second production box.** Opening port 22
  for the runner opens it on both for the duration of a deploy.
- **`SSH_HOST` is a public DNS name derived from a public IPv4.** If that address
  is not an Elastic IP, a stop/start of the instance silently invalidates the
  variable.
- Every AWS value here is identical to what the org's existing, working EC2
  deploy pipeline already has configured for this same estate. Copy from there
  rather than looking anything up.

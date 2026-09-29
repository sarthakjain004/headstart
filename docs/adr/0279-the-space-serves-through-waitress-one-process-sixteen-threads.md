# ADR-0279: The Space serves through waitress, in one process with sixteen threads

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0262](0262-a-caller-with-no-session-reads-the-public-routes-sixty-times-a-minute.md) (its
Context names Werkzeug's threaded server as the one process) · **Relates to:**
[ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) (the `/mcp` places),
[ADR-0325](0325-the-model-retries-an-edge-failure-and-a-description-scan-runs-alone.md) (the
description-scan place) · **Issue:** #595 (PR #844, recorded after the fact by the #844 review)

## Context

`start.sh` ends in `exec python app.py`. Until #844, `app.py`'s main block called `app.run(...)`,
Werkzeug's development server (`server: Werkzeug/3.1.8` on the live Space's replies). #595, a
read-only security audit, flagged it. That server starts one thread per connection with no bound,
so a client that opens connections and never finishes sending holds a thread for each. Measured
locally on 2026-09-29 with a trivial Flask app and 40 such clients: the development server held
41 extra threads, and waitress with 16 threads held none. A normal GET answered in about 10 ms
on both.

Several things live only in this process's memory: the résumé-read guard (`_PARSING`, #591),
every `RateLimit` (ADR-0262), the `/mcp` places (ADR-0267, ADR-0325), and the Trends and facet
answers kept for the boot.

## Decision

**Waitress, pinned to one version.** `app.py`'s main block calls
`waitress.serve(app, **_WAITRESS_SETTINGS)`. `deploy/hf-space/requirements.txt` pins
`waitress==3.0.2`, and the `dev` extra pins the same version, so CI tests the server the Space
runs. `tests/test_space_requirements.py` fails when the two pins differ.

**One process.** Waitress runs one process with a thread pool. A second worker process would keep
a second copy of each in-memory guard, limit and kept answer above, and each copy would admit its
own share.

**16 threads on the Space's 2 vCPUs.** At most two requests can compute at once, so the other
threads are there to wait. A résumé read waits on the router for up to 120 s (`llm_router`'s
`_TIMEOUT`). Each Saved set or Profile write waits on an HF commit. A `/mcp` request waits up to
`_MCP_PLACE_WAIT_S` (10 s) for one of `_MCP_AT_ONCE` (4) places, or for the one
`_MCP_SCANS_AT_ONCE` description-scan place. With all 5 places taken and four more `/mcp` requests
queued for them, 7 threads are still left for the page. These are the values on 2026-09-29. A
change to either place count, or to the thread count, should redo this sum. Waitress reads each
request in full before a thread takes it, so a client that never finishes sending holds a
connection, not a thread.

**`clear_untrusted_proxy_headers=False`.** Waitress 3 deletes `X-Forwarded-For`,
`X-Forwarded-Host`, `-Proto`, `-Port`, `-By` and `Forwarded` from every request whose peer is
not a named trusted proxy. No proxy is named, so the default would delete them all.
`_client_address` reads the caller from the last `X-Forwarded-For` entry, which is the one Hugging
Face's edge appends (ADR-0262). Without that header, every anonymous caller would count as the
edge's one address and share one 60-a-minute budget. `tests/test_space_app.py` serves the app
through a real waitress server with these settings, and a second address is still admitted after
the first is refused. With the default setting, that test fails.

Keeping these headers exposes nothing new. Flask reads `X-Forwarded-Host` and `-Proto` only
through `ProxyFix`, and this app does not install it. The development server kept every one of
these headers too.

## Options rejected

- **Keep the development server.** It has no bound on threads, and Werkzeug says not to deploy it.
- **Several worker processes (gunicorn `-w N`).** Each process would hold its own guard, limits
  and places, so one caller could get N times its budget, and N Accounts could each start N
  résumé reads at once. Moving that state to a shared store is a bigger change than #595 asked
  for.
- **gunicorn with one worker and a thread pool.** It is the same model as waitress, with a second
  process for the arbiter and a server this repo has never pinned or tested. Waitress is pure
  Python and has one setting to learn here.
- **Name the edge as a trusted proxy** (`trusted_proxy="*"`, `trusted_proxy_headers={"x-forwarded-for"}`,
  `trusted_proxy_count=1`). Waitress would then set `REMOTE_ADDR` from the last entry itself, the
  same answer `_client_address` gives. It would put ADR-0262's rule in a second place, in
  waitress's terms. And waitress answers a forwarded header it cannot parse with a 400 of its own
  (`proxy_headers.py`), so a caller's junk header would change what that caller gets. That was not
  measured through the edge.

## Consequences

- The Space still has no request timeout and does not cancel a request whose client disconnects.
  Waitress does neither, as the development server did not.
- Waitress prints no line per request, as the development server did. `app.py` prints one itself
  (#883), and its path is percent-encoded so a caller cannot start a line of its own.
- `tests/test_space_app.py` stops its waitress server through waitress internals
  (`server.trigger`, `server.task_dispatcher`), since waitress has no public way to stop a
  server running on another thread. The version pin bounds that.

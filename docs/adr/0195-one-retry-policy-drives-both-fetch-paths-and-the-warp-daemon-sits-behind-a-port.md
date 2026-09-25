# ADR-0195: One retry policy drives both fetch paths, and the WARP daemon sits behind a port

**Status:** accepted · **Date:** 2026-09-24 · **Amends:** neither
[ADR-0002](0002-pooled-thread-local-http.md) (the pooled session and its retry ladder),
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress),
[ADR-0065](0065-wait-for-the-fresh-ip-rather-than-riding-the-spent-one.md) (earned attempts),
[ADR-0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) nor
[ADR-0081](0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md). Every rule they decided
stands; this changes only where the rules live.

## Context

Two structural problems sat under the reliable-fetch seam.

**The retry-and-egress loop was written twice.** `http.fetch` and `http.fetch_async` were ~80-line
near-copies, differing only in which calls they awaited. Each drove `spare_egress` through about
nine calls — `proxy_for`/`proxy_for_async`, `generation`, `riding_the_tunnel`, `note_routed`,
`mark_walled`, `note_settled` twice, `rotate` through `_rotate_for`, and `wait_deadline` — in an
order that had to match by hand. They had drifted once already: the async copy resolved its route
through the blocking `proxy_for`, which froze the event loop under every rotation drain (ADR-0063's
2026-09-05 amendment). Two of those calls, `generation` and `riding_the_tunnel`, were not even in
`spare_egress.__all__`, so the module's stated interface was not the one `http` used.

**The WARP daemon had no seam.** `spare_egress` shells out (`warp-cli`, `sudo -n systemctl restart
warp-svc`, `launchctl kickstart`), opens a SOCKS5 socket and reads Cloudflare's trace endpoint from
private functions the policy called directly. A test that forgot to stub any of them reached the
real machine: `tests/test_http.py` had to autouse-stub `rotate`, because unstubbed it restarted the
developer's real WARP daemon (pid 96855 -> 97119 during one test run).

## Options considered

For the loop:

- **(a) A sans-IO step generator.** One generator makes every decision and yields the I/O it needs
  — resolve a route, send the request, rotate, back off — as values; `fetch` and `fetch_async` each
  drive it, with blocking and awaited calls respectively. Order, budget and accounting live once.
- **(b) A policy object with methods** (`on_response`, `on_error`, …) called from each loop. The
  decisions move, but each loop still spells out the control flow — which call comes next, when to
  `continue` — so the order that drifted last time would still be written twice.
- **(c) Make the sync path call the async one** (`asyncio.run` per request). One loop, but it
  would put an event loop on every sync request of every scraper and change the thread-local
  session model ADR-0002 measured.

**Decision: (a)**, the only option that writes the order once without changing how either path
does its I/O.

For the daemon:

- **(d) A four-operation port.** `EgressDaemon`: `dial()`, `restart()`, `reconnect()`,
  `read_trace(proxy)`. The policy — the gate, the drain, the cooldown, the generation, the counters
  and every log line — stays in `spare_egress` and reaches the outside only through it.
- **(e) One method per shell command** (`run_warp_cli(*args)`, `restart_service()`,
  `socks5_ready()`, `get(url)`). Wider, and it leaks the WARP recipe (proxy mode, then port, then
  connect) into the policy, which then cannot be driven by anything but WARP.

**Decision: (d)**, the smaller interface.

## Decision

- `http._retry_policy` is a generator holding the whole loop. It yields `_ResolveRoute`,
  `_SendRequest`, `_RotateEgress` and `_BackOff`; a `RequestsError` from the send is thrown back
  into it, and it ends by yielding `_Settled` with the response, or re-raises. It yields its result
  rather than returning it so no driver catches `StopIteration`: a first draft did, around its
  whole loop, and so turned a `StopIteration` raised by the session into a returned value.
  `fetch` drives it with `proxy_for`, `session().request`, `_rotate_for` and `time.sleep`;
  `fetch_async` with
  `proxy_for_async`, the caller's `AsyncSession`, `asyncio.to_thread(_rotate_for, …,
  wait_deadline())` and `asyncio.sleep`. Neither driver decides anything.
- `generation` and `riding_the_tunnel` join `spare_egress.__all__`, so the module's declared
  interface is the one `http` uses.
- `spare_egress.EgressDaemon` is the port. `WarpDaemon` is the real adapter: its implementation is
  the module's existing private OS functions (`_connect`, `_restart_daemon`, `_reregister`,
  `_await_socks5`, `_run`), which nothing else in the module calls any more. The settle-then-re-arm
  after a restart moved from `rotate` into `WarpDaemon.reconnect`. `InMemoryEgressDaemon` never
  leaves the process; its default is a machine with no WARP (nothing dials, no restart succeeds),
  which is what CI's test job already was. `use_daemon` swaps the adapter.
- `tests/conftest.py` installs an `InMemoryEgressDaemon` for every test. `test_network_http.py`'s autouse
  stub of `rotate` is gone: an unstubbed rotation now fails in memory and returns False, which is
  what the stub returned. The tests that assert the real commands (`test_network_spare_egress.py`'s
  `_stub`, `_rotating` and `_flapping`) install `WarpDaemon` after stubbing `subprocess`, the
  handshake and the trace, exactly as before.

The private OS functions were kept as functions rather than folded into `WarpDaemon`'s methods.
Their tests patch them by module name, and `tests/test_log_levels.py` keys its annotation budget on
`spare_egress.py:<function>`; folding them in would have moved every one of those for no change in
what the port hides.

## Consequences

- **Behaviour is unchanged, and was measured so.** A differential harness drove the pre-refactor
  and post-refactor `fetch` and `fetch_async` through 3,000 randomized scenarios each (outcome and
  error sequences, attempt budgets, `egress_on`/`retry_on` sets, routes, rotation outcomes and
  rotation generations), recording every `spare_egress` call, request and backoff: the traces were
  identical on all 6,000 runs. Lowering `_MAX_EARNED_ATTEMPTS` in the new module alone made the
  harness fail, so it can see a drift. `test_network_http.py` and `test_network_spare_egress.py` pass with their
  assertions unchanged.
- **The two paths can no longer disagree on a decision.** `test_network_http.py`'s
  `test_both_paths_drive_one_policy_to_the_same_egress_decisions` drives the whole ladder through
  both and compares every egress call.
- **No test can reach the real daemon by omission.** `test_network_spare_egress.py`'s
  `test_the_policy_reaches_the_daemon_only_through_the_port` refuses `subprocess`, sockets and the
  trace read, then dials, rotates and observes twice through an in-memory daemon.
- **A second egress provider is an adapter, not an edit of the policy.** Nothing needs one today.

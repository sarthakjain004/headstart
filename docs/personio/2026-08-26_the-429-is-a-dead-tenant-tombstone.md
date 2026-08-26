# Personio's terminal 429 is a dead-tenant tombstone, not a rate limit (2026-08-26)

**Verdict: revert PR #312.** Its premise — that personio meters per origin, so a second egress IP
buys a second budget — is false. The 429 is not personio's rate limiter and is not keyed on the
client IP at all. Fixed upstream instead, in `PersonioScraper.fetch_raw`.

Corrects `docs/personio/vercel-bot-wall-bypass.md`, whose central claim ("Personio's
`{tenant}.jobs.personio.{de|com}` boards sit behind a Vercel Firewall Challenge") attributes the
wall to the wrong host. See §6.

## The mechanism

A tenant that has **left personio** need not 404 — most do (184 of 200 sampled dead ledger rows
return a plain 404), but a departed subdomain that is still routed answers a redirect instead, and
that is the case the scrape never survived. `https://{host}/xml` answers:

```
HTTP/2 307
location: https://personio.com
x-powered-by: Express            <- personio's own app, via CloudFront
```

…and `https://www.personio.com/` — the **marketing site**, a different property on Vercel — is
behind a Vercel firewall challenge:

```
HTTP/2 429
server: Vercel
x-vercel-mitigated: challenge
x-vercel-challenge-token: 2.1787738848.60.MjJjYmM4YmI2YTM1YmI3YjYwMTc0Njkz…
(no Retry-After)
```

The scrape followed that redirect and read the marketing site's bot challenge as the Board's own
rate limit. Every downstream consequence — the wall marking, the rotations, the ProxyErrors, the
narrowed fan-out on healthy Boards — follows from that one misread.

## 1. Every terminal-429 Board is a departed tenant (n = 22 of 22)

The union of personio Boards that failed with `HTTP Error 429` across the two runs carrying #312
(`32936269675`, `32942748996`) is 22 hosts. Probed live 2026-08-26:

| population | n | redirect to `www.personio.com` | serve a real `<workzag-jobs>` feed |
|---|---|---|---|
| terminal-429 Boards, both runs | 22 | **22 (100%)** | 0 |
| random sample of `live` ledger rows | 600 | **8 (1.33%)** | 592 |

**0 of 600** sampled Boards redirect anywhere other than the marketing site, and **0 of 600**
redirect and still serve a feed — so a redirect off the Board host is an unambiguous tombstone,
and not following it cannot cost a live Board.

One of the 8 dead Boards in the random sample, `brugg-rohrsysteme-gmbh.jobs.personio.de`, is
itself a terminal-429 Board in run `32936269675`. The association is total in both directions.

The liveness ledger still calls all 22 `live`, several with `jobs=0` and `checked_at=2026-07-03`
(`ace`, `ciratum`, `croftstone`, `deltia-ai`, `dsr-digital`) — see §5.

Artifacts: `experiment/personio-429-metering/artifacts/2026-08-26_terminal-429-boards_direct.txt`,
`…_survey-600-live-boards_direct.jsonl`.

## 2. A different egress IP does not clear it

The real scraper, with #312 live, driven against those Boards (`experiment/personio-429-metering/repro.py`):

```
spare egress: personio:cantourage-gmbh-1 walled the current IP — rotating
spare egress: rotated to a fresh egress IP (#11)
spare egress: now egressing from 104.28.220.169 via BOM (SAME as before)
…
spare egress: now egressing from 104.28.220.175 via BOM (moved)
  RED   cantourage-gmbh-1.jobs.personio.de   HTTPError: HTTP Error 429:
```

**16 rotations across 3 verified-distinct addresses** — `104.28.220.169`, `104.28.220.175`,
`104.28.252.174`, the module's own `cdn-cgi/trace` confirming `moved` — and **429 from every one**,
on 4 of 4 Boards reached before the run was stopped. This is the measurement ADR-0063's
`egress_fallback_on` docstring requires and #312 never made.

Artifact: `…_repro_before-fix_rotated-3-distinct-ips.txt`.

## 3. The wall is keyed on the header, not the client

Same IP, same second, same TLS fingerprint (`curl_cffi impersonate="chrome"`), one variable — the
`User-Agent`:

| route | Board | User-Agent | result |
|---|---|---|---|
| direct | dead tenant | Chrome | **200** (1,720,839 B of marketing HTML) |
| direct | dead tenant | `headstart/0.1 (job-board reader)` | **429** `x-vercel-mitigated: challenge` |
| direct | live tenant | Chrome | 200, real feed |
| direct | live tenant | `headstart/0.1 (job-board reader)` | 200, real feed |

An IP-keyed budget cannot produce that split. The live tenant is unaffected by either UA, which is
the control: the challenge lives on the marketing site only.

The held fingerprint is part of the claim. Re-measured in a 4-arm matrix (see "Reproducing"), the
`User-Agent` is **necessary but not sufficient**: under this scraper's own TLS a Chrome UA is
still refused. "Keyed on the header, not the client" is exact about the part that matters — the
client IP is not an input — but the request signature the wall reads is wider than one header.

Artifact: `…_ua-fingerprint-ab_direct.txt`.

Note what this rules out as a lever: making the scraper claim to be Chrome would turn the 429 into
a 200 — of 1.7 MB of marketing HTML that still is not a job feed. There is no version of chasing
that redirect that yields a Board.

## 4. Live personio Boards do not rate-limit at shard shape

44 randomly-drawn live Boards at 16 concurrent workers, direct: **44/44 200, 0 redirects, 0 429s,
1.6 s wall-clock**. 4 live Boards through the WARP datacenter egress: 4/4 real feeds. Personio's
own edge showed no rate limiting at or above the concurrency a shard applies.

Sample caveat: both arms ran from a residential IP (`106.51.118.151`) and a Cloudflare WARP address
(`104.28.220.169`); neither is a GitHub Actions runner range. This is evidence that live Boards are
not the 429 source, not proof that no CI-side limit exists.

## 5. Why these Boards never aged out

ADR-0058's quarantine counts only **HTTP 404/410** as *gone*; a 429 is a fetch failure and
deliberately never ages a Board. So a departed tenant reporting 429 sat in the scrape slice failing
every single run, indefinitely. Four of them (`pitch`, `zellerfeld`, `hishab`, `egym`) failed in
**10 of 10** runs examined. The rest rotate through the slice, which is why the per-run count is a
stable ~12–16 rather than a fixed list.

The prober has the same defect one layer up. `check_liveness.py`'s `p_personio` follows the
redirect, lands on the marketing page and counts `<position>` occurrences in it — so a departed
tenant is recorded `live, jobs=0` (on a 200) or left untouched (on a 429), and **never `dead`**.
That is the same shape as the bug already recorded in that function's own comment ("Every one of
the 312 such rows is recorded live with jobs=0"). Not changed here — see §7.

## 6. Correction to `vercel-bot-wall-bypass.md`

That document is right about *what* the challenge is (Vercel Firewall "Challenge" action, not
BotID — this investigation reproduced its exact signature) and wrong about *where* it is. It says
the tenant boards sit behind it. They do not: the boards are served by personio's own
Express/CloudFront stack, and 44/44 live boards answered 200 with real XML at shard concurrency.
Only `www.personio.com` is on Vercel.

Two of its recommendations are therefore aimed at the wrong target, and one is the same error
#312 made:

- *"Running the Personio liveness pass from a non-datacenter egress may clear far more boards"* —
  no. §2 and §3 show the egress is not the variable; the redirect target is.
- *"Building a custom solver is not worth it right now"* — the right conclusion for the wrong
  reason. There is nothing to solve: the challenge only ever guards a marketing page that holds no
  job data.

Its own evidence pointed here already — "a fresh, unthrottled curl from a home network got a clean
200 on the first try against a **live** tenant" — but was read as the wall being intermittent
rather than as the live tenant never having been walled.

## 7. What changed, and what did not

**Changed** (`src/headstart/scrapers/personio.py`):

- `egress_fallback_on = frozenset({429})` **reverted**.
- `fetch_raw` no longer follows a redirect off the Board host, and reports one in the shape
  `board_failures.is_gone` recognises — the way `lever` reports a slug that is on no Lever board.

Consequences: no request reaches the marketing site, so no 429; personio is never marked walled, so
healthy Boards keep their direct route and full fan-out width; no rotations, so none of the
ProxyErrors #312 introduced; and the departed tenants finally age into the ADR-0058 quarantine
instead of failing forever.

Verified live — `python experiment/personio-429-metering/repro.py` exits 0: **22/22 dead tenants
read as gone, 6/6 live Boards still parse**, zero rotations.

**Deliberately not changed, needs a decision:**

1. **`check_liveness.py`'s `p_personio`** (§5) — the same one-line defect, and the reason the
   ledger keeps carrying departed tenants as `live`. `_sr_board_host` in that same file is the
   precedent: fetch with `allow_redirects=False` and read the *target* as the signal. Without it
   the scrape-side fix still works (the quarantine retires them), but the ledger stays wrong.
2. **`check_liveness.py`'s `_SPANNING` gate** for `jobs.personio.de`/`.com`, justified by "personio
   answered 429 on all four passes" (2026-08-14). If those 429s were the marketing site, the gate
   is throttling ~9,400 healthy Boards because of ~1.3% dead ones. Worth re-measuring before
   touching — this investigation did not measure the prober's own traffic.
3. **`http.fetch`'s transport-error path** — see below.

## 8. Concern B: the ProxyError is real, but #312 caused it and the revert removes it

`ProxyError: curl: (97) Failed to receive SOCKS response, proxy closed connection` appeared **0
times across all 8 pre-#312 runs** and **3 and 1 times** in the two runs carrying it. All 4 are
personio. Workday and eightfold — which have rotated for weeks — produced none across the same 10
runs.

The defect in `http.fetch` is genuine: the `except RequestsError` branch **neither rotates nor
re-dials**. It refunds an attempt via `_severed_by_our_rotation` only when
`spare_egress.generation()` changed *during* the request — i.e. when a peer's rotation tore the
socket down. When the proxy is instead simply not carrying traffic, the generation does not move,
no attempt is refunded, and `proxy_for()` hands back the *same cached* SOCKS URL on every remaining
attempt. The board is then lost to our own infrastructure while the direct route sat available.

This is the case ADR-0063 called "a WARP that connects but cannot carry traffic … the one bad
case", and its stated guard is incomplete: `_socks5_ready()`'s RFC-1928 handshake proves the
*listener* is up, not that the *tunnel* is carrying — which is exactly what `curl: (97)` reports.

What made personio uniquely able to trigger it: a wall no IP can clear turns rotation into a tight
loop (`deltia-ai` rotated 4 times in 20 s, `dsr-digital` 3 times in 30 s), so the odds of a request
being in flight across a `systemctl restart warp-svc` rise sharply. Workday's 429s are spread over
real origin work; personio's dead tenants 429 instantly.

**Not fixed here.** The revert removes every observed instance, and the residual gap is latent for
the two ATSes that still rotate. Fixing it means changing shared retry policy — options, cheapest
first: (a) on a `RequestsError` through a proxy, clear the cached proxy so the next attempt
re-dials or degrades to direct (mirrors `rotate`'s own "a failed rotation must not be permanent"
invariant); (b) treat a proxied transport error as a wall and rotate; (c) fall back to the direct
route for that attempt. (a) is the smallest and most consistent with the module's existing
reasoning, but it is cross-cutting and unexercised today, so it is a separate call.

## 9. Why the exit criterion could not have caught this

The shard report said 95.0% and 95.9% recovered — healthy by ADR-0063's stated criterion — while
the outcome #312 was adopted to fix did not move:

| | rescued/walled requests | rate | terminal 429 Boards | ProxyError |
|---|---|---|---|---|
| pre-fix, 8 runs | — | — | **12.1 mean** (sd 2.7, range 8–16) | **0** |
| `32936269675` | 327/341 | 95.9% | **14** | 1 |
| `32942748996` | 268/282 | 95.0% | **14** | 3 |

14 sits inside the pre-fix range and is +0.70 sd *above* the mean. Per 1,000 Boards attempted the
rate went 16.23 (pre) → 18.42 / 18.67 (post) — marginally worse. The tightest control,
`32932727429`, differs by exactly this one commit and had 16.

**The two numbers count different things.** `spare_egress.note_settled` is called for *every*
request the spare egress carries once a group is walled, and buckets any 200 as `rescued`. But by
then the group is walled for the whole shard, so the overwhelming majority of those requests are
**healthy Boards that were never refused**. The rate therefore answers "what fraction of this ATS's
Boards on this shard are healthy" — ~95% before #312 too. The residual is the tell: **unrescued
requests were exactly 14 in both runs, exactly the terminal Board count.**

A rescue rate is pinned high by traffic that never needed rescuing. Only a per-Board outcome can
say whether the fallback bought anything — and any future opt-in judged by this metric inherits the
same blind spot, Workday's own amendment included.

## Reproducing

> **The scripts and artifacts named here are local-only.** `experiment/` is gitignored
> (`.gitignore:34`), so nothing under it ships with the repo and no later reader can open the
> `Artifact:` files cited above. Every number in this document is a live measurement against
> unauthenticated public endpoints, so re-measure rather than trust the citation — the commands
> below are the recipe, not a pointer to stored output.

```bash
python experiment/personio-429-metering/repro.py       # exit 0 = fixed; drives the real scraper
python experiment/personio-429-metering/survey.py 600  # dead-tenant base rate in the ledger
python experiment/personio-429-metering/fingerprint.py # the User-Agent A/B of §3
python experiment/personio-429-metering/funnel.py live 16   # live Boards at shard concurrency
```

**Independently re-measured 2026-08-26** (review of this PR, separate samples, residential IP):
400 random `live` ledger rows — 396 served a 200 feed, 4 answered 307 and **all 4 to
`https://personio.com`**, none anywhere else. 200 random `dead` rows — 184 a plain 404, 15 a 307
to `https://personio.com`, 1 a live feed. The §3 A/B reproduces exactly, including the
1,720,839-byte marketing page, **but only with the TLS fingerprint held**: in a 4-arm × 2-rep
matrix from one IP, `impersonate="chrome"` + Chrome UA is the only arm that gets 200 — default TLS
with a Chrome UA is 429, and Chrome TLS with our UA is 429. So the wall reads the whole request
signature, not the `User-Agent` alone; §3's "keyed on the header" is right that it is **not** the
client IP, which is the load-bearing part.

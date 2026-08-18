# ADR-0063: A shard that spends an origin's budget picks up a spare egress IP

**Status:** accepted · **Date:** 2026-08-18 · **Relates to:**
[ADR-0026](0026-parallelize-nightly-scrape.md),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md),
[ADR-0056](0056-darwinbox-browser-escalation.md) · **Amends:**
[ADR-0047](0047-pace-against-the-origin.md)

## Context

ADR-0047 established how this project treats an ATS that meters **per origin**: parallel Actions
shards get distinct egress IPs, so each shard holds its own budget with that ATS, and the way to
spend those budgets evenly is to cap how many of one ATS's Boards a single shard may take
(`binpack.lpt_pack_capped`). Eightfold was the ATS that motivated it, and 405 was identified there
as the shape its edge returns once a budget is spent.

That cap is **relative** — `ceil(n/m)` for `n` Boards over `m` shards. It distributes load without
bounding it. When #161 restored Workday and Personio priority scoring, the slice changed shape and
`n` for Eightfold grew from ~55 to ~79 per run. The cap dutifully passed the growth through: the
typical shard went from 3–5 Eightfold Boards to 5–6, and the budget went from comfortable to spent.

**Measured across ten runs** (2026-08-17 15:46Z → 2026-08-18 09:55Z, full analysis in
`docs/pipeline/2026-08-18_ten-run-log-review.md`):

| head | Eightfold Boards / run | per-shard spread | fatal Board errors | wall rate |
|---|---|---|---|---|
| `ab23879` (7 runs) | 50–61 | `[5,5,5,5,5,5,4,4,4,4,3,2,2,1,1]` | **0** | 0% |
| `f7415c8`+ (3 runs) | 73–79 | `[6,6,6,6,6,6,6,5,5,5,5,5,4,1,1]` | 18–30 | 25–38% |

The failure is per-shard and scales with that shard's own load, which is what identifies the
mechanism rather than merely correlating with it:

| Eightfold Boards on a shard | shards observed | fatal errors per shard |
|---|---|---|
| 6 | 23 | 2.09 |
| 5 | 15 | 1.73 |
| 4 | 2 | 0.50 |
| 1 | 5 | **0.00** |

Every shard holding a single Eightfold Board finished clean. ADR-0047's model is therefore correct;
what it lacked was any ceiling that survives the slice growing.

Two properties of the wall matter for the decision. **It is about the IP, not the tenant or the
request.** All 18 hosts that failed in run `32124170195` answer 200 from a different, low-volume
client — and the *same host* comes back 403 in one run and 405 in the next (kering 403/403/405,
libertymutual 405/405/403), so neither status can be read as a property of the tenant. **And the
Boards are unrecoverable within the run**: once a shard is walled, every Eightfold Board it has left
fails, because retrying from the same IP is the one thing that cannot work.

## Decision

**Retry is not the last rung. An ATS that walls a shard escalates to a second egress IP — Cloudflare
WARP in proxy mode — for the rest of that shard's run.**

- `http.fetch` gains an opt-in `egress_group`. A response in that group's wall statuses marks the
  group, and this and every later request naming it route through WARP. Callers that name no group
  behave byte-for-byte as before.
- **Keyed on the ATS, not the Board.** The metering is per origin across all of an ATS's tenants,
  so per-Board marking would make each remaining Board spend its own three attempts rediscovering
  what the first one already proved.
- **Marking is independent of retry budget.** A wall on the final attempt still loses that request,
  but it is exactly as informative about the origin as an early one, and recording it is what
  spares every subsequent Board.
- **Opt-in per scraper** via `BaseScraper.egress_fallback_on`. Eightfold declares `{403, 405}`.
  Every other ATS keeps its direct route unconditionally.
- **Lazy connect, eager install.** `headstart.spare_egress` dials only once something has actually walled,
  and caches the outcome — including failure — for the process. `pipeline.yml` installs and
  registers `warp-cli` before the scrape, because an on-demand `apt-get` would land inside the
  first ten minutes of the shard, which is precisely the window Eightfold's Boards occupy and the
  window the fallback exists to save.
- **It degrades, it never raises.** A missing binary, an unregistered client, a tunnel that will not
  come up: all return None and leave the scrape on its direct route — today's behaviour exactly.
  A fallback is only worth having if its absence costs nothing.
- **Proxy mode, never VPN mode.** VPN mode routes the whole runner through Cloudflare, including the
  artifact upload, the HF push and the GitHub API. Proxy mode moves only the clients pointed at the
  SOCKS5 port.

**Routing and marking are separate knobs**, because Eightfold needs exactly one of each:
`egress_group` says *route this with that group once the group is walled*, `egress_on` says *these
statuses, seen here, are what walls it*.

That split exists for the **API-availability probe**. Eightfold's first `/api/pcsx/search` page is
how the scraper asks whether a tenant exposes the API at all, and ~40% answer a steady 403 there
and a healthy 200 on the sitemap immediately after (8 of 18 hosts probed). So the probe must not
*mark* — that would dial the spare egress on nearly every shard, on the normal path. But it must
still *route*: exempting it from routing too would send it over the spent IP on exactly the walled
shard this exists to rescue, `_api_search` would return None, and every remaining Board would fall
through to the per-job sitemap path — thousands of fetches for Boards like nvidia (2,629 postings)
inside a 60-minute budget, plus the loss of `data.count`'s exact ADR-0053 truncation. Every other
surface — the careers page, the sitemap, pagination past the first page — both routes and marks.

**It reports what it did.** `spare_egress` counts requests carried and requests recovered per
group, and the shard report prints one line per walled ATS: `eightfold: walled; spare egress
carried 412 request(s), 398 recovered (97%)`. A walled group that carried *nothing* is reported
too — that is the worst case and the one a bare traffic counter would hide, since it means no
spare egress could be raised and those Boards were lost exactly as before. Recovery rate is the
number to watch: high routed with low recovery means the spare egress is walled as well, which is
the signal to stop trusting it rather than to route more.

Verified before building, per the live-API rule in `CLAUDE.md`: all 8 probed Eightfold hosts —
including the persistently API-disabled ones — serve **200 on both surfaces through a WARP egress**
(`104.28.252.175`), which falsifies the one risk `.claude/skills/ats-gap-search/resilience.md`
flags about WARP's shared ranges. Eightfold is fronted by AWS CloudFront, not Cloudflare, and does
not block the range.

**Reconciling the darwinbox measurement.** `docs/darwinbox/cloudflare-wall.md` already tried WARP
and rejected it: "as the first arm it passed 6/40 (15%)", and "it also re-routes every ATS on the
runner, not just darwinbox". Both objections survive and neither applies here. The pass rate was
measured against *darwinbox's* Cloudflare wall, which that document concludes is a client-fingerprint
problem rather than an egress one — Eightfold's is an origin budget, which is precisely the case an
IP change does address, and the 8/8 probe above measures that separately. The re-routing objection
is answered by construction: `egress_group` moves only the ATS that walled, and WARP runs in proxy
mode, so darwinbox, Workday and every other ATS on the same runner keep their direct route. This
decision therefore does **not** reopen WARP for darwinbox.

## Consequences

**A walled shard keeps its remaining Boards** instead of losing 1–3 of them, and the 18–30 fatal
Board errors per run should go to roughly zero. That also shrinks the ADR-0053 truncation set,
since `HTTP 405 on page N` mid-pagination is the same wall arriving later in the same Board.

**The politeness question is real and is answered narrowly.**
`.claude/skills/ats-gap-search/resilience.md` states that rotating
an IP to dodge a 429 is ineffective and impolite, and ADR-0026 makes per-host politeness binding.
This decision does not weaken either: it applies only to statuses an ATS returns *instead of* a
rate-limit signal, only where the same request demonstrably succeeds from another IP moments later,
and only for an ATS that has opted in by name. The per-host pacing, the detail-pass widths and the
`Retry-After` honouring in ADR-0047 all stand unchanged — this is what happens *after* they have
been respected and the origin has refused anyway. A 429 is deliberately **not** in Eightfold's
`egress_fallback_on`.

**It treats the symptom, not the cause.** The relative cap is still relative, so a further growth in
`n` still raises per-shard load; this buys a second budget rather than bounding the first. An
absolute per-shard ceiling remains the complementary fix and is not taken here — measured against
this window it would defer ~19 Boards per run, and the spare egress recovers them instead.

**A WARP that connects but cannot carry traffic is the one bad case.** The status poll before
handing out the proxy is what guards it; if it slips through anyway, that ATS's requests fail and
the run degrades to exactly the fatal errors we have today — no worse, but no better.

**Scope: the sync path only.** `fetch_async` takes a caller-supplied session, so Eightfold's async
detail pass is not routed. Both symptoms this addresses — the fatal Board error and the
mid-pagination truncation — occur on the sync path, so this is a real limit rather than a gap in
the fix; widening it means giving `fan_out_async` a proxied session.

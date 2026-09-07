# Zoho has a second listing surface — an RSS feed nothing in this repo knew about

**2026-09-07.** Started as review item 8 ("paginate past the zoho ~750 ceiling, or `mark_truncated`").
The pagination half is dead, re-confirmed independently. But looking for it turned up a **second
listing surface** the scraper does not use, and it changes what a small zoho board's job count means.

## How it was found

`zoho.py`'s docstring records a thorough 2026-08-22 investigation: query-string variants, the
front-end JS, every embedded blob. It never checked **`robots.txt`**, which names two endpoints:

```
Allow: /recruit/downloadrssfeed
Allow: /recruit/sitemapfeed
```

Both 302 to an IAM error at the host root. But the *portal-scoped* path works:

```
GET https://{tenant}.zohorecruit.{tld}/jobs/{Portal}/rss    ->  200 application/rss+xml
```

A real RSS 2.0 feed. On `2coms.zohorecruit.in` it is 4.78 MB and carries 606 `<item>`s.

## First: the ceiling half of item 8 is still dead

Re-probed live rather than inherited from the docstring. On `2coms` (at the cap), every
query-string variant returns a **byte-identical id set**:

| variant | jobs | identical id set to plain? |
|---|---|---|
| *(plain)* | 750 | — |
| `?fromIndex=751&toIndex=1500` | 750 | yes |
| `?page=2` / `?start=750` / `?limit=2000` | 750 | yes |
| `?department=IT` / `?jobLocation=Kolkata` / `?industry=Technology` | 750 | yes |

The facet-partition idea (the trick that beats Workday's 2,000 cap) fails too: **the server ignores
the query string entirely.**

## Availability of the feed

Swept 150 Hiring Boards on an even stride by job count, so every size band is represented
(`scripts/validate/zoho_rss_coverage.py --limit 150`; raw output in
`2026-09-07_rss-vs-widget-150-boards.txt`):

| outcome | boards | share |
|---|---|---|
| feed **disabled** — no RSS root, just a one-line sentence | 116 | 77% |
| **working feed** | 29 | 19% |
| feed present but **empty** (RSS root, zero items) | 5 | 3% |
| **total** | **150** | 100% |

**The disabled case is a soft failure and it is localized.** Zoho answers with `200
application/rss+xml` and a bare sentence — English ("Oops! It seems that the joblist has been
removed."), French, or Spanish, depending on tenant. Matching the English wording, as the first
draft of the probe did, silently counted four boards as working-but-empty feeds. The
language-independent signal is the **absence of an `<rss>`/`<channel>` root**, which also keeps a
genuinely empty feed as its own outcome — hence the three-way split above.

## It does **not** beat the 750 ceiling

At-ceiling boards are rare: the ledger holds **10 rows at exactly 750 out of 5,337 Hiring Boards**
(0.2%). The stride sample contains one of them, and that is an artefact of the stride, not a
measurement — a step of 35.6 can only ever admit the top-ranked row, so do not read "1 of 150" as a
rate. The ledger count is the rate.

**All ten were probed directly** (`2026-09-07_rss-on-the-at-ceiling-boards.txt`): **nine have the
feed disabled, and the one that does not — `2coms` — returns 606 items, fewer than its widget.**
Item 8's "paginate past the ceiling" half stays dead by both routes.

**It corroborates truncation on `2coms`, but does not prove it.** The union of widget and feed there
is **768-769** against a widget frozen at 750, and every extra id is absent from the widget's raw
HTML — so the board demonstrably has more jobs than the widget serves. It is tempting to call that
proof of truncation and reopen `mark_truncated`, and an earlier draft of this writeup did.

**That inference does not hold, and the data below refutes it.** Union > 750 would prove truncation
only if the RSS extras belong to the population the widget was drawing from. The next section shows
they need not: `chicagolandhabitat` serves **1** job from its widget and 9 from its feed, nowhere
near any ceiling. So a widget-omission mechanism exists that is independent of the cap, and
`2coms`'s +19 is equally explained by it. The union is a lower bound on the board's *jobs*; it is
not a true total of the widget's own population, which is what `mark_truncated` would need.

Separating the two mechanisms — cap truncation versus whatever omits jobs on a 1-job board — is the
open question, and it has to be answered before the `mark_truncated` half of item 8 can move.

## The real finding: small boards are systematically under-reported

6 of the 29 working feeds carry jobs whose ids appear nowhere in the widget's raw HTML — and they
are overwhelmingly *small* boards:

| board | widget | rss | rss-only | ratio |
|---|---|---|---|---|
| `chicagolandhabitat.zohorecruit.com` | 1 | 9 | +8 | **9.0x** |
| `solveq.zohorecruit.eu` | 1 | 6 | +5 | 6.0x |
| `sec24-7.zohorecruit.com` | 1 | 6 | +5 | 6.0x |
| `taleemabad.zohorecruit.com` | 1 | 2 | +1 | 2.0x |
| `ibygroup.zohorecruit.com` | 2 | 3 | +2 | 1.5x |
| `2coms.zohorecruit.in` | 750 | 606 | +18 | — |

Split by size, the effect is stark:

| working feeds | n | of which RSS adds jobs |
|---|---|---|
| widget **<= 3** jobs | 9 | **5 (56%)** |
| widget **> 3** jobs | 20 | 1 (5%) |

**A majority of small boards with a feed are under-reporting**, by up to 9x. An earlier sample
caught the same shape on `blinknhire.zohorecruit.in`: widget 3 (one `Publish=True`), RSS 16, 14
unseen — a 5.7x under-report nowhere near the ceiling. So the widget's count is not a reliable
measure of board size at the small end, and the liveness ledger's `jobs` inherits the error.

Verified the extras are real openings, not stale feed entries: their detail pages are
indistinguishable from a control page for a widget-listed job — ~1.77 MB, an apply affordance
present, none of the "no longer available / not found / removed" wording (4 sampled + 1 control).
Their `pubDate`s are interleaved through the widget's own date span (2021-08 .. 2026-09), **not
older than its oldest job** — so this is not the widget truncating by recency.

## Three things checked and cleared, recorded so they are not re-raised

- **The scraper's `Publish` / `Is_Locked` filter is not broken.** The blob's values could have been
  the strings `"True"`/`"False"`, in which case `not r.get("Is_Locked")` would invert. Measured:
  they are real JSON booleans. The filter keeps 602 of `2coms`'s 750.
- **The cap bites harder than the raw number suggests.** 750 is the *raw* blob size; only 602
  survive the publish filter. So the excess beyond 750 is lost from an already-filtered population.
- **freshteam's 1000-job ceiling was not investigated here.** `simera-talent`, `abnhire` and `kalam`
  are untouched by any of the above.

## What this is worth, and what it is not

Across all 29 working feeds the aggregate gain is **+39 jobs on 2,283 — 1.7%**, because the boards
that gain are small ones. The value is not volume; it is that **a majority of small boards with a
feed are recorded at the wrong size**, which affects the ledger, the cost model, and any decision
keyed on a board's job count.

Whether that justifies a scraper change depends on how many live zoho boards are both small and
feed-enabled. From this sample: 19% have a feed, and 56% of the small ones among those gain — but
150 boards cannot pin either rate tightly. **The next step is a full sweep of the ~5,300 live zoho
boards, not a scraper change.** This is a measurement, not a recommendation.

**These are point-in-time numbers on live boards.** `2coms` read +16 on a first pass and +18 twenty
minutes later. That is board churn, not instability in the method — and a second confirmation the
gap is real rather than a parsing artefact.

## Probe notes, learned the hard way

Three details in `scripts/validate/zoho_rss_coverage.py` exist because the obvious version was
wrong, and each corrupted a headline number before it was caught:

1. **Parse the widget with `ZohoScraper._records`, not a lookalike regex.** A re-derived pattern
   diverged from the scraper's (it required `type="hidden"` before `value`), so the sweep would
   have measured a parse the pipeline never runs.
2. **Detect a disabled feed by shape, not by an English sentence** — see above.
3. **A job id is the path segment after the portal**, `/jobs/{Portal}/{id}/{title-slug}`, not the
   last long digit-run in the URL. Title slugs carry requisition numbers on some tenants (15 of 640
   links on `talproindia`), so taking the last run read the wrong number and inflated "rss-only".

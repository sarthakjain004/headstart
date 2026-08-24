# Eightfold: the sitemap-index child-following branch — correct, but unexercised

**Date:** 2026-08-24 · **Follow-up to:** `sitemap-primary-evaluation.md` (the #150 proposal to make
the sitemap the *primary* listing surface, not yet built) · **Related:**
`pcsx-replica-instability.md`, `no-client-side-fix-for-replica-instability.md` ·
**Code under review:** `src/headstart/scrapers/eightfold.py::_job_urls` (lines 348-375)

## Why this matters

`sitemap-primary-evaluation.md` proposes promoting `/careers/sitemap.xml` from *fallback* (used
today only when the PCSX API 403s, ~20% of tenants) to *primary* listing surface for every
Eightfold board. That proposal is attractive because it routes around the replica-instability
problem entirely — a batch-generated sitemap can't be reordered by whichever backend replica
answers.

But promoting a surface also promotes every branch inside it. `_job_urls()` has a
**child-following branch** — for when the sitemap is not a flat list of jobs but an *index*
pointing at child sitemaps that must each be fetched separately. That branch is real, shipped
code. This document asks the question that should be asked before any code becomes load-bearing:
**has it ever actually run?**

Short answer: **no live tenant exercises it today — all 102 of them — but the code is
nevertheless correct, proven end-to-end against a real cross-host index.** Those are two separate
findings and this doc keeps them separate, because conflating "we tested it" with "production
tests it" is exactly how an untested branch quietly becomes infrastructure.

## Background: the two sitemap shapes

A sitemap at `/careers/sitemap.xml` can be either of two things:

- a **`<urlset>`** — a flat list of job URLs, ready to use directly; or
- a **`<sitemapindex>`** — a list of *other* sitemap files, each of which must be fetched to get
  the actual jobs. This is the standard way large sites split a sitemap that would otherwise
  exceed the 50,000-URL / 50 MB limits the sitemap protocol imposes.

`_job_urls()` handles both. The relevant code, verbatim:

```python
def _job_urls(self) -> list[str]:
    r = self._get(accept="application/xml")
    r.raise_for_status()
    jobs = _dedupe(_JOB_LOC.findall(r.text))
    if jobs:
        return jobs                       # <- flat urlset: done, branch below never runs
    children = [
        c
        for c in _dedupe(_CHILD_SITEMAP.findall(r.text))
        if "index" not in c.lower()
    ]
    found: list[str] = []
    for child in children[:_MAX_INDEX_CHILDREN]:
        cr = self._get(child, accept="application/xml")
        if cr.status_code == 200:
            found.extend(_JOB_LOC.findall(cr.text))
        else:
            self.mark_truncated(...)      # ADR-0053: a failed child is a partial read
    return _dedupe(found)
```

The `if jobs: return jobs` short-circuit is the crux: **if the first fetch already yields job
URLs, the child-following code below it is never reached.**

## Finding 1: no same-host restriction — cross-host children work

A child sitemap listed in an index is a full absolute URL, and nothing requires it to live on the
same host as the index. Does the code handle that?

Reading the call path: `_CHILD_SITEMAP` captures the whole `<loc>` content (`[^<\s]*sitemap...`),
so an absolute cross-host URL is captured intact. `_get(child, ...)` passes that string straight
to `http.fetch("GET", url or self.url(), ...)`, and `http.fetch` performs no origin check. **There
is no same-host restriction anywhere in the path.** Verified by reading, then confirmed
empirically below.

One caveat found by reading, not by measurement: the `Referer` header on a child fetch is always
`https://{self.slug}/careers` — the *tenant's* host, never the child's own. This was harmless in
testing, but has never been validated against a host that actually checks Referer.

## Finding 2: `ngc.eightfold.ai` has a real cross-host index — but the scraper never sees it

Northrop Grumman's board is the one place a real cross-host index was found. All figures below
independently re-measured live on 2026-08-24 (a prior pass measured 3,685 jobs eight days
earlier; the drift to 3,653 is ordinary posting churn, not a discrepancy):

| resource | status | bytes | root tag | job locs |
|---|---:|---:|---|---:|
| `robots.txt` | 200 | 351 | — | declares `Sitemap: .../careers/sitemap_index.xml?domain=ngc.com` |
| `/careers/sitemap.xml` **(what the scraper fetches)** | 200 | 930,812 | `<urlset>` | **3,653** |
| `/careers/sitemap_index.xml?domain=ngc.com` (robots.txt's canonical entry) | 200 | 333 | `<sitemapindex>` | 2 children |
| ↳ child 1: `jobs.northropgrumman.com/careers/sitemap.xml?domain=ngc.com` | 200 | 930,812 | `<urlset>` | 3,653 |
| ↳ child 2: `jobs.northropgrumman.com/careers/sitemap_cat.xml?domain=ngc.com` | 200 | 35,846 | `<urlset>` | 0 (category listing) |

Two things stand out.

**The tenant's own `robots.txt` points somewhere the scraper doesn't go.** ngc declares
`sitemap_index.xml?domain=ngc.com` as its canonical entry point — a genuinely different resource
from the bare `/careers/sitemap.xml` (different filename, plus a `?domain=` query param) that
`url()` hardcodes. The scraper has never read this tenant's robots.txt and doesn't need to,
because the bare endpoint happens to be complete.

**The bare endpoint is already cross-host anyway.** The 3,653 job URLs inside
`ngc.eightfold.ai/careers/sitemap.xml` are themselves hosted on `jobs.northropgrumman.com` —
Eightfold is proxying another domain's content without any index indirection at all. So even
today, without the child-following branch, this scraper is already consuming cross-host sitemap
content successfully.

**Net effect: the child-following branch is dead code for ngc.** `if jobs: return jobs` fires on
3,653 URLs from the first fetch, and execution never reaches the children.

## Finding 3: exhaustive sampling — 0 of 102 live tenants exercise the branch

Every row marked `live` in `data/validate/liveness/eightfold.csv` — **all 102, not a sample** —
was probed at the scraper's actual fetch target, bare `/careers/sitemap.xml`:

| result | tenants | consequence |
|---|---:|---|
| flat `<urlset>` with job locs on first fetch | **99** | child-following branch never reached |
| bare sitemap **404s** | **3** | see below |
| `<sitemapindex>` at the bare endpoint | **0** | branch is load-bearing for nobody |

Job counts across the 99 span 2 (`bluecrabconsulting`) to 22,140 (`starbucks`) — so this isn't an
artifact of only sampling small boards; even a 22k-posting tenant serves a single flat urlset
rather than splitting into an index.

The three 404s (`bms.eightfold.ai`, `climate.eightfold.ai`, `juniper.eightfold.ai` — independently
re-confirmed 404 during this write-up) are currently harmless: all three have a working PCSX API
(`bms` serves 676 jobs; `climate` and `juniper` return `count=0`, genuinely empty boards), so
`fetch_raw()` never falls through to the sitemap path for them. Worth noting for the
sitemap-primary proposal, though: two of the three (`bms`, `climate`) *also* declare a
`sitemap_index.xml?domain=...` in robots.txt like ngc does — but unlike ngc, **that declared URL
404s too.** A dead robots.txt entry, not a working index.

Combined with an earlier pass's 11 tenants (an overlapping set), this is exhaustive against the
current ledger. **No live Eightfold tenant today needs the child-following branch to be scraped
at all.**

## Finding 4: forced end-to-end test — the code is correct

Since no tenant naturally exercises the branch, it was forced: subclass `EightfoldScraper`
overriding *only* `url()` to point at ngc's real robots.txt-declared cross-host `sitemapindex`,
then call the real, unmodified `_job_urls()`. Reproduced independently for this write-up:

```text
forced (cross-host index) path: 3653 urls in 2.4s
distinct hosts in returned urls: {'jobs.northropgrumman.com'}
real production path:           3653 urls in 1.6s
identical sets: True
forced-only: 0   real-only: 0
```

The real code detected zero job locs on the index page, extracted both children, fetched both
**cross-host**, and aggregated a set byte-identical to the known-good production result. This is
a genuine external resource, not a synthetic mock, exercised through the real class method.

**The cross-host child-following code works.** That uncertainty is closed.

## Finding 5 (code reading only, not observed live): the `"index"` child filter is lexical

```python
if "index" not in c.lower()
```

This drops any child whose *URL string* contains "index", as a proxy for "this child is itself
another index, don't treat it as a leaf." It's a filename heuristic, not a check of the child's
actual root tag. Two theoretical failure modes follow:

- a genuine **leaf** child conventionally named `sitemap_index_1.xml` is wrongly **dropped** — its
  jobs silently missing, with no `mark_truncated` call (that only fires on a non-200), so the
  board reads as complete when it isn't;
- a genuinely **nested index** named e.g. `sitemap2.xml` is wrongly treated as a leaf — fetched,
  yields 0 job locs, contributes nothing, again silently.

**Neither has been observed on any live tenant** — flagged from reading the code, demonstrated
only synthetically. Recorded because it would become load-bearing under the sitemap-primary
proposal, and because both failure modes are *silent* (no truncation signal), which is the
category this repo's own history shows is most expensive to discover late.

## Verdict, and what to do before shipping sitemap-primary

Two separate answers to two separate questions:

- **Does the child-following code work?** **Yes** — proven end-to-end against a real cross-host
  `sitemapindex`, via the real class method, result identical to the known-good path.
- **Does any live tenant exercise it?** **No** — 0 of 102 live tenants, exhaustive against the
  current ledger. It remains unexercised-in-production code.

The original concern ("an untested branch that becomes load-bearing infrastructure is exactly the
kind of gap that bites in production") is **half resolved**: the correctness uncertainty is gone,
but the "no naturally-occurring fixture" half stands, and no amount of further tenant sampling
will close it while Eightfold keeps serving flat urlsets to every live board.

Concrete suggestions if `sitemap-primary-evaluation.md`'s proposal proceeds:

1. **Add the forced cross-host test as a regression fixture.** It's real, reproducible, and cheap
   (2 fetches, ~2.4s). Pin it against a recorded response rather than the live network so it
   doesn't flake, but keep the live version runnable for periodic revalidation.
2. **Replace the lexical `"index"` filter with a root-tag check** (`<sitemapindex` vs `<urlset` in
   the fetched child), or at minimum make the drop non-silent. Finding 5's failure modes both lose
   jobs without any signal, which is the exact shape ADR-0053 exists to prevent.
3. **Consider reading `robots.txt` for the tenant's declared canonical sitemap** rather than
   hardcoding `/careers/sitemap.xml`. ngc demonstrates that a tenant's own stated entry point can
   differ from the hardcoded one; today that costs nothing, but under sitemap-primary the
   hardcoded guess becomes the single source of listing truth.
4. **Re-run the 102-tenant probe before shipping.** Eightfold's sitemap shapes are their
   infrastructure decision, not ours; "0 of 102 today" is a measurement with a shelf life, not a
   permanent property.

## Reproduction

```bash
# Finding 2 — ngc's robots.txt vs the endpoint the scraper actually fetches
curl -s https://ngc.eightfold.ai/robots.txt | grep -i sitemap
curl -s "https://ngc.eightfold.ai/careers/sitemap.xml" | head -c 200          # <urlset ...>
curl -s "https://ngc.eightfold.ai/careers/sitemap_index.xml?domain=ngc.com"   # <sitemapindex ...>

# Finding 4 — force the cross-host index path through the REAL scraper code
.venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from headstart.scrapers.eightfold import EightfoldScraper
class Forced(EightfoldScraper):
    def url(self): return f'https://{self.slug}/careers/sitemap_index.xml?domain=ngc.com'
forced = Forced('ngc.eightfold.ai')._job_urls()
real   = EightfoldScraper('ngc.eightfold.ai')._job_urls()
print(len(forced), len(real), 'identical:', set(forced) == set(real))
print('hosts:', {u.split(\"/\")[2] for u in forced})
"
```

Raw per-tenant data for all 102 sampled boards was captured during the investigation; the summary
counts in Finding 3 are what matter for the decision and are reproducible by re-probing the
`live` rows of `data/validate/liveness/eightfold.csv` at `/careers/sitemap.xml`.

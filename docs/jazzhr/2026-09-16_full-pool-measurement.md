# JazzHR + Jobvite at full pool: what re-enabling them actually costs

**Date:** 2026-09-16 · **Decision:** [ADR-0158](../adr/0158-jazzhr-and-jobvite-are-worth-their-storage.md)
· **Supersedes the counts in:** `docs/jazzhr/2026-09-07_surface-investigation.md`,
`docs/jobvite/LOG.md` (their *method* stands; only the pool they were measured on was partial)

Covers both ATSes because the decision was joint. Every number here is reproducible from the
committed ledgers or the commands quoted.

## 1. The pool was never finished

Both candidate lists came from a **partial** Wayback sweep: jazzhr 40 of 632 CDX pages (an
alphabetically-early slice), jobvite 25 of 699. Running the sweep to completion:

```bash
python scripts/discover/wayback_pages.py jazzhr  --domain applytojob.com   --style sub  --refresh
python scripts/discover/wayback_pages.py jobvite --domain jobs.jobvite.com --style path --refresh
```

| ATS | partial sweep | full sweep | committed ledger before | never probed |
| --- | ---: | ---: | ---: | ---: |
| jazzhr | 782 | **13,950** | 4,647 | 9,969 |
| jobvite | 178 | **4,109** | 517 | 3,598 |

Run them **sequentially**. `wayback_pages.py` documents CDX running ~470x slower at 4 workers than
at 2, so two concurrent sweeps are the same mistake one level up.

**Wayback alone is not the union.** Folding in the Common-Crawl candidates from #463 added **96
tenants Wayback did not find** — 87 jazzhr, 9 jobvite — of which **25 jazzhr and 3 jobvite probe
live**. `data/ats-tenants-merged/README.md` says this outright ("neither source is complete and
they have different blind spots"); it is worth re-reading before trusting any single channel.

## 2. Liveness

18,395 boards probed. Settled:

| ATS | rows | live | dead | unknown | hiring | postings | jobs/hiring board |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| jazzhr | 14,703 | 6,177 | 8,525 | 1 | 4,871 | 99,963 | 20.5 |
| jobvite | 4,124 | 1,079 | 3,044 | 1 | 749 | 49,573 | 66.2 |

## 3. The storage decision

| ATS | postings | page size | storage | tech share | tech Jobs |
| --- | ---: | ---: | ---: | ---: | ---: |
| jazzhr | 99,963 | 112 KB (measured, 2026-09-07) | ~10.7 GB | 5.1% | ~5,098 |
| jobvite | 49,573 | — | ~3–4 GB | 7.0% | ~3,470 |

**jobvite is 2.1x the 23,461 postings assumed** — never measured at full pool. **jazzhr lands on
its old ~10.7 GB by coincidence**: more Boards (3,684 → 4,871), fewer jobs each (27.5 → 20.5), and
the two cancel. Reading its unchanged total as "the estimate held" is wrong on both inputs.

## 4. The gate: `applytojob.com` refuses under whole-pool load

Every jazzhr tenant is `{slug}.applytojob.com` on one Cloudflare zone, and neither host had a
`_GATES` entry. Measured from both directions:

| load | refusals |
| --- | --- |
| 18,299-board pass @ **432** workers | **12, across 6 distinct tenants** — and **2,740 `dead` rows written** |
| 18,299-board pass @ **16** workers | **zero** |
| 350 distinct tenants @ concurrency 200 | 0 (350/350 → 200) |
| 60 requests concentrated on one tenant @ 12 | 0 (60/60 → 200) |

Six *distinct* tenants refusing under one-probe-per-tenant load is a shared meter — the case
`_SPANNING` exists for, and the opposite of the per-datacenter Workday case that list warns about.
`applytojob.com` is seeded at 16 in-flight / no spacing.

`jobs.jobvite.com` is **deliberately ungated**: one fixed netloc that the auto-gate already keys
exactly, and it drew zero refusals even at 432. A gate for it would be configuration with no
measurement behind it.

The blast radius is real and accepted: one tenant's Cloudflare challenge now trips the gate for all
14,703 jazzhr rows, which short-circuits them to UNKNOWN. That is the right trade — UNKNOWN
re-probes next run, `dead` settles for 90 days — and it is why the 432-worker pass was the
dangerous one: it wrote `dead`.

### Why not `egress_fallback_on = {429}` on the scrapers

Asked for, and declined on measurement. `base.py` puts the burden on the opt-in: *"Set it only for
an ATS measured to meter **per origin**, where a wall is about the shard's IP"* — and CLAUDE.md
records freshteam and personio being opted in on a 429 count alone, both reverted.

The discriminating test is whether a refused tenant serves 200 from a **second IP**. That was never
run, so the flag must not be set. What was run — 350 tenants at concurrency 200, and 60 hits on one
tenant — came back clean, with no `Retry-After` ever sent, and the refusals appear only at a
sustained width the prober produces and a scrape shard does not.

**Residual risk, stated rather than hidden:** a scrape shard's peak in-flight is roughly
`workers + workers*8`, and 15 shards share this origin. If real-scrape 429s show up in a shard
report, that is the measurement that reverses this call — run the two-IP test then.

## 5. Field audit — no scraper changes needed

Both scrapers were driven live against real boards before enabling.

**jazzhr** (45 boards, 252 jobs): location 100%, employment_type 100%, `experience` **100%**
native (`"Mid Level"` — ADR-0018 Tier 1), description 100%, posted_at 71%, department 48%, salary
28% native and already formatted (`"80000-95000 USD YEAR"`). Company resolves to a real name on
**88%** of boards from the listing's JSON-LD `Organization` ("Boston Center for the Arts", "Luxfer
MEL Technologies"); its `<title>` is vendor branding on 30/30 boards, so the scraper's comment
declining `board_page()` is correct.

**jobvite**: its JSON-LD carries 10–11 keys and **no `jobLocationType` or `experienceRequirements`
on any of 66 postings across 6 boards**, so the absent `experience` and the location-derived
`remote` are correct rather than gaps. Company comes from `hiringOrganization` plus its existing
`board_page()`/`PATTERNS` entry.

One caveat worth recording because the first pass got it wrong: jobvite is **not** in `salary.py`'s
`_FIELD_PARSERS` (jazzhr is). A 120-posting probe across 15 large boards found 8 populated
`baseSalary` blocks, all with `unitText` of `"Annually"`/`"Hourly"` — phrase-shaped, which
`_field_generic`'s `_period_multiplier` reads correctly. So no parser is needed, but the original
evidence for that conclusion was 6 postings with **zero** populated salaries: the right answer,
unmeasured.

## 6. Reproduction

```bash
# the gate A/B — the second returns zero refusals
LIVENESS_WORKERS=432 python scripts/validate/check_liveness.py jazzhr jobvite   # DO NOT: writes false `dead`
LIVENESS_WORKERS=16  python scripts/validate/check_liveness.py jazzhr jobvite

# the counts in §2 and §3, straight off the committed ledgers
python - <<'EOF'
import csv
for a, kb, tech in (("jazzhr", 112, .051), ("jobvite", None, .070)):
    rows = list(csv.DictReader(open(f"data/validate/liveness/{a}.csv")))
    live = [r for r in rows if r["status"] == "live"]
    hire = [r for r in live if int(r["jobs"] or 0) >= 1]
    jobs = sum(int(r["jobs"] or 0) for r in live)
    gb = f" {jobs*kb/1048576:.1f} GB" if kb else ""
    print(a, len(rows), len(live), len(hire), jobs, round(jobs/len(hire), 1), gb, round(jobs*tech))
EOF
```

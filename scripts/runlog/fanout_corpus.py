#!/usr/bin/env python3
"""What the run did to the corpus: scrape volume, the tech gate, and the description store.

Three stages that only make sense read together, because each one's denominator is the previous
one's output:

**`scrape_join`** — `workday.jsonl: 541801 lines from 15 shard(s)` per ATS, then the total. This
is raw scrape volume. Watch it per-ATS across runs: a single ATS collapsing while the total holds
is the signature of a scraper or egress problem, and the total alone hides it.

**`filter_tech`** — `workday 76219 541801 14.1%` per ATS. The authoritative tech gate (ADR-0017) is
recall-biased on purpose: no tech job dropped, some non-tech creep tolerated. So a *rising* kept%
is not automatically good news — it can mean the gate is admitting more non-tech, which is what
issue #186 tracks. Compare kept% per ATS against its own history, not against other ATSes, whose
board mixes differ wildly.

**`update_descriptions`** — `filled N from the store, learned N, queued N to re-derive` per ATS
(ADR-0050; the `settled N as having none` clause this once also read was removed by ADR-0089). `filled` is the store paying off: descriptions recovered without
refetching. `learned` is new text banked this run. `queued` is the re-derive backlog. A large
`queued` against a small `learned` means the store is losing ground.

**The one number that is not what it looks like.** `filled` is dominated by whichever ATSes keep
their descriptions on the listing endpoint (eightfold, workday) — measured at ~22,000 of ~24,000
across both sampled runs. Every other ATS shows `filled 0` not because the store failed them but
because their scrape never had descriptions to bank. Do not read a per-ATS `filled 0` as a defect
without checking whether that scraper carries descriptions at all.

Run: python scripts/runlog/fanout_corpus.py 32272854468
     python scripts/runlog/fanout_corpus.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re

from run_logs import Run, common_args, runs_from, warn_if_unparsed

ATS_LINES = re.compile(r"\[scrape_join\] (\w+)\.jsonl: (\d+) lines from (\d+) shard")
JOIN_TOTAL = re.compile(r"\[scrape_join\] wrote (\d+) lines across (\d+) ATS files")
TECH = re.compile(r"\[filter_tech\] (\w+)\s+(\d+)\s+(\d+)\s+([\d.]+)%")
TECH_TOTAL = re.compile(r"\[filter_tech\] TOTAL\s+(\d+)\s+(\d+)\s+([\d.]+)%")
# No `settled N as having none` clause: ADR-0089 removed that fact (the `detail_fetched` flag it
# was gated on is wrong on live data), and this pattern kept requiring it — so it matched nothing
# and every ATS printed filled/learned/queued as 0 while the log said `workday: filled 6,012`.
# Silent zeros, for every run built after that ADR. `run_logs.warn_if_unparsed` is the guard
# against a repeat.
DESC = re.compile(
    r"\[update_descriptions\] (\w+): filled ([\d,]+) from the store, learned ([\d,]+), "
    r"queued ([\d,]+) to re-derive"
)
STORE = re.compile(
    r"\[update_descriptions\] prior store: ([\d,]+) already-embedded ids"
)
SKIP = re.compile(r"\[update_descriptions\] skip-list: ([\d,]+) Jobs held")
# filter_tech's two zero-output warnings — an ATS scraped this run but empty, and the corpus-wide
# case where nothing at all reached the filter. Both used to be near-silent: the per-ATS table
# simply omitted the row, and a zero-total run printed its header and stopped.
TECH_EMPTY = re.compile(
    r"(\d+) ATS\(es\) were in this run's slice but contributed zero rows: ([^—]+)—(.+)"
)
TECH_ZERO_TOTAL = re.compile(
    r"no rows at all reached the tech filter -> (\S+) is empty"
)

# Note the boundary: this covers JOB-LEVEL digest lines (one per stage, in the join job's
# log). Per-Board scraper warnings (trakstar's card cap, darwinbox's browser escalation,
# zoho's/freshteam's widget ceilings, workday's per-page failure classes) are NOT parsed by
# any runlog tool — they live in the per-shard scrape log, keyed by Board, and reading them
# means grepping that shard's text directly rather than a structured report.

num = lambda s: int(s.replace(",", ""))


def report(run: Run) -> None:
    jobs = run.stage_jobs("join")
    if not jobs:
        print("  no join job in this run", flush=True)
        return
    text = run.log(jobs[0])

    scraped = {m[0]: int(m[1]) for m in ATS_LINES.findall(text)}
    # `TOTAL` shares the per-ATS line shape, so it matches TECH too; drop it here and print the
    # real total once, from TECH_TOTAL, rather than emitting a bogus half-empty row.
    tech = {
        m[0]: (int(m[1]), int(m[2]), float(m[3]))
        for m in TECH.findall(text)
        if m[0] != "TOTAL"
    }
    desc = {m[0]: tuple(num(x) for x in m[1:]) for m in DESC.findall(text)}

    te = TECH_EMPTY.search(text)
    if te:
        n, atses, why = te.groups()
        print(
            f"  {n} ATS(es) scraped but contributed zero tech rows: {atses.strip()}— "
            f"{why.strip()}",
            flush=True,
        )
    tz = TECH_ZERO_TOTAL.search(text)
    if tz:
        print(
            f"  CORPUS-WIDE: nothing reached the tech filter -> {tz.group(1)} is empty",
            flush=True,
        )

    names = sorted(
        set(scraped) | set(tech) | set(desc), key=lambda a: -scraped.get(a, 0)
    )
    print(
        f"{'ats':18}{'scraped':>10}{'tech':>9}{'kept%':>7}"
        f"{'filled':>10}{'learned':>9}{'queued':>8}",
        flush=True,
    )
    for a in names:
        kept, _total, pct = tech.get(a, (0, 0, 0.0))
        filled, learned, queued = desc.get(a, (0, 0, 0))
        print(
            f"{a:18}{scraped.get(a, 0):>10,}{kept:>9,}{pct:>7.1f}"
            f"{filled:>10,}{learned:>9,}{queued:>8,}",
            flush=True,
        )

    warn_if_unparsed(
        text, "[update_descriptions] ", desc, "update_descriptions per-ATS"
    )

    jt = JOIN_TOTAL.search(text)
    tt = TECH_TOTAL.search(text)
    if jt and tt:
        print(
            f"{'TOTAL':18}{int(jt.group(1)):>10,}{int(tt.group(1)):>9,}{float(tt.group(3)):>7.1f}"
            f"{sum(d[0] for d in desc.values()):>10,}{sum(d[1] for d in desc.values()):>9,}"
            f"{sum(d[2] for d in desc.values()):>8,}",
            flush=True,
        )
    store, skip = STORE.search(text), SKIP.search(text)
    if store:
        print(f"  description store: {store.group(1)} already-embedded ids", flush=True)
    if skip:
        print(f"  skip-list: {skip.group(1)} Jobs held", flush=True)
    if desc:
        top = max(desc.items(), key=lambda kv: kv[1][0])
        total_filled = sum(d[0] for d in desc.values())
        if total_filled and top[1][0] / total_filled > 0.5:
            print(
                f"  NB: {top[0]} is {100 * top[1][0] / total_filled:.0f}% of all `filled` — "
                "other ATSes read 0 because their scrape carries no descriptions, not because "
                "the store failed.",
                flush=True,
            )


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    runs = runs_from(args)
    for run in runs:
        print(f"\n===== run {run.id} head={run.head} — corpus =====", flush=True)
        report(run)
    if len(runs) > 1:
        print(
            "\n  NB: per-ATS deltas between two runs reflect the slice each run drew "
            "(scrape_plan splits priority/exploration differently every run), not only scraper "
            "health. Check the slice line before calling a drop a regression.",
            flush=True,
        )


if __name__ == "__main__":
    main()

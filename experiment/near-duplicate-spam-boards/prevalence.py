"""How common is the near-duplicate-spam shape among the highest-priority Boards?

Bounded by handing every scraper a ``have_details`` container that claims we already hold every
detail, so ``BaseScraper.needs_detail`` returns False for every posting (ADR-0048). Most scrapers
then make no per-Job request at all; ``successfactors`` is the exception — its ``fetch_raw`` fans a
detail fetch over its gated list without consulting ``needs_detail``, which is why its Boards are
the slow ones here. Titles and locations are read off the *listing*, so no ratio depends on detail
data, and a Board whose listing states no title is reported as `titles_missing` rather than
silently scored.

**A non-None ``have_details`` also re-enables the ADR-0017 detail tech-gate.** For 19 of the 23
Boards measured that is inert on counts (their ``parse`` walks the whole listing regardless); for
the four ``successfactors`` Boards it is not, because ``fetch_raw`` returns only the gated subset.
LOG.md §3 has the per-call-site check and which way it biases the conclusion.

One subprocess per Board with a hard wall-clock cap, a small worker pool, results streamed to
JSONL as they land.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKER = Path(__file__).with_name("prevalence_worker.py")
TOP_N = int(sys.argv[1])
CAP_S = int(sys.argv[2])
OUT = Path(sys.argv[3])


def top_boards(n: int) -> list[tuple[str, str, int]]:
    """The top n priority-ledger Boards as (ats, raw scraper slug, last_tech_jobs).

    Joined back to the liveness ledger rather than split out of the priority key: that key is
    ``board_key()``'s own *output*, and Workday's is the shorthand ``{co}/{site}`` its parser
    rejects (its real slug is a whole careers URL). Splitting the key alone made every Workday
    Board in the top 30 fail to construct.
    """
    from headstart.board_identity import board_identity, lower_key
    from headstart.config import load_active_companies

    by_key = {
        lower_key(board_identity(c)): c
        for c in load_active_companies("data/validate/liveness", min_jobs=0)
    }
    with open("data/state/board_priority.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: float(r["score"]), reverse=True)
    out: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows[:n]:
        company = by_key.get(r["board"].lower())
        if company is None:  # dead, parked or excluded since it was last scored
            out.append(("?", r["board"], int(r["last_tech_jobs"])))
            continue
        # The top 30 priority keys hold duplicate spellings of one Board (nvidia's and micron's
        # Workday sites each appear twice), which dedupe to one CompanyRef — measure it once.
        if (company.ats, company.slug) in seen:
            continue
        seen.add((company.ats, company.slug))
        out.append((company.ats, company.slug, int(r["last_tech_jobs"])))
    return out


def run_one(ats: str, slug: str, tech: int) -> dict:
    if ats == "?":
        return {"board": slug, "last_tech_jobs": tech, "skipped": "not in the live set"}
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(WORKER), ats, slug],
            capture_output=True,
            text=True,
            timeout=CAP_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"board": f"{ats}:{slug}", "last_tech_jobs": tech, "skipped": "timeout"}
    tail = (proc.stdout or "").strip().splitlines()
    for line in reversed(tail):
        if line.startswith("{"):
            res = json.loads(line)
            res["last_tech_jobs"] = tech
            return res
    return {
        "board": f"{ats}:{slug}",
        "last_tech_jobs": tech,
        "skipped": "error",
        "stderr": (proc.stderr or "")[-400:],
    }


def main() -> None:
    boards = top_boards(TOP_N)
    done: list[dict] = []
    with OUT.open("w", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(run_one, *b): b for b in boards}
        for fut in as_completed(futures):
            res = fut.result()
            done.append(res)
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
            fh.flush()
            print(
                f"{len(done):>3}/{len(boards)} {res['board']}: "
                + (
                    res["skipped"]
                    if res.get("skipped")
                    else f"{res['postings']} postings, {res['distinct_titles']} titles, "
                    f"max/title {res['max_postings_one_title']}, "
                    f"{res['distinct_stems']} stems, max/stem {res['max_postings_one_stem']}, "
                    f"{res['distinct_locations']} locations"
                ),
                flush=True,
            )
    # Ranked by *share*, not by the absolute counts above. An absolute "max postings per stem"
    # threshold flags every large Board — 21 of 23 at max/stem >= 100 — which says nothing. The
    # share is what separates: see LOG.md §3.
    print("\n=== ranked by top-stem share ===", flush=True)
    ok = [r for r in done if r.get("postings") and not r.get("skipped")]
    ok.sort(key=lambda r: -r["max_postings_one_stem"] / r["postings"])
    for r in ok:
        share = 100 * r["max_postings_one_stem"] / r["postings"]
        print(
            f"{share:>6.1f}%  {r['board']:<58} "
            f"{r['postings']:>6} postings, {r['distinct_locations']:>5} locations, "
            f"max/title {r['max_postings_one_title']}",
            flush=True,
        )
    for r in done:
        if r.get("skipped"):
            print(f"  SKIPPED {r['board']}: {r['skipped']}", flush=True)


if __name__ == "__main__":
    main()

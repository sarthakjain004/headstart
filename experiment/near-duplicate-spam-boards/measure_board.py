"""Measure one Board's true shape live: postings, distinct titles, distinct locations, top repeats.

Deliberately duplicates part of ``prevalence_worker.py``'s census rather than sharing it. This one
leaves ``have_details`` as ``None``, which is the whole point — the ADR-0017 detail tech-gate is off
and the Board's **entire** set is read, at the cost of a detail fetch per posting. The worker takes
the cheap gated path so a 23-Board sweep is affordable. Folding them together would mean one script
with a flag that changes what the number *means*, which is worse than two short scripts.
"""

import collections
import json
import sys
from datetime import UTC, datetime

from headstart.scrapers import registry

ats, slug = sys.argv[1], sys.argv[2]
out = sys.argv[3]

s = registry.get_scraper(ats, slug, slug)
print(
    f"{ats}:{slug} have_details={s.have_details} board_key={s.board_key()}", flush=True
)
raw = s.fetch_raw()
now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
jobs = s.parse(raw, now)

titles = collections.Counter(j.title for j in jobs)
locs = collections.Counter(j.location for j in jobs)
res = {
    "board": f"{ats}:{slug}",
    "measured_at": now,
    "postings": len(jobs),
    "distinct_ids": len({j.id for j in jobs}),
    "distinct_titles": len(titles),
    "distinct_locations": len(locs),
    "distinct_title_location_pairs": len({(j.title, j.location) for j in jobs}),
    "max_postings_one_title": titles.most_common(1)[0][1] if titles else 0,
    "top_titles": titles.most_common(15),
    "top_locations": locs.most_common(10),
    "sample_titles": [j.title for j in jobs[:10]],
}
print(json.dumps(res, indent=2, ensure_ascii=False), flush=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump(res, fh, indent=2, ensure_ascii=False)

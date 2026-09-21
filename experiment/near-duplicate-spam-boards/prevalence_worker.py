"""One Board's listing-only title/location/title-stem census. Prints one JSON line on stdout.

The cheap counterpart to ``measure_board.py`` — see that module's docstring for why the overlap is
deliberate rather than duplication to fold away.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from datetime import UTC, datetime

from headstart.scrapers import registry

#: The first separator a templated title puts before its per-city / per-country tail. Reboot
#: Monkey's shape defeats a plain distinct-title ratio ("Data Center Technician - Nigeria - Lagos
#: - On-site" is genuinely a distinct string), so the stem is what makes it visible.
_TAIL = re.compile(r"\s+[-–—|/]\s+|\s*[(,\[]|\s+in\s+|\s+at\s+", re.IGNORECASE)


def stem(title: str) -> str:
    return _TAIL.split(title, 1)[0].strip().lower()


class HaveEverything:
    """A ``have_details`` container that claims every detail is already held, so
    ``needs_detail`` is False for every posting and no detail request is made (ADR-0048)."""

    def __contains__(self, _key: object) -> bool:
        return True


ats, slug = sys.argv[1], sys.argv[2]
s = registry.get_scraper(ats, slug, slug, have_details=HaveEverything())
raw = s.fetch_raw()
now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
jobs = s.parse(raw, now)

titles = collections.Counter(j.title for j in jobs if j.title)
locs = collections.Counter(j.location for j in jobs if j.location)
stems = collections.Counter(stem(j.title) for j in jobs if j.title)
res: dict[str, object] = {
    "board": f"{ats}:{slug}",
    "measured_at": now,
    "postings": len(jobs),
    "titled": sum(titles.values()),
    "distinct_titles": len(titles),
    "distinct_locations": len(locs),
    "max_postings_one_title": titles.most_common(1)[0][1] if titles else 0,
    "distinct_stems": len(stems),
    "max_postings_one_stem": stems.most_common(1)[0][1] if stems else 0,
    "top_titles": titles.most_common(5),
    "top_stems": stems.most_common(5),
}
if len(jobs) and res["titled"] < 0.8 * len(jobs):
    # The listing does not state titles on this ATS (they come from the detail pass we skipped),
    # so no ratio computed here would mean anything. Say so rather than score it.
    res = {k: res[k] for k in ("board", "measured_at", "postings", "titled")}
    res["skipped"] = "titles_missing"
print(json.dumps(res, ensure_ascii=False), flush=True)

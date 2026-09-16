"""Every loss cause is reported with its count — no `+N more causes` tail.

`base.loss_breakdown` dropped this same `[:4]` cap in #457, on the grounds that the cause
vocabulary is closed (status codes plus a handful of parse-shape labels) so no line grows
unreadable. The run-level copy in `observability` survived that change, and it is the one that
hurts most: it aggregates across every Board, so the tail it hid was the long tail.

Measured over the five runs of 2026-09-16: **27** lines hit the cap — 12 in the join roll-up and
15 more in the shard logs — and the widest carried 8 distinct causes. The cap was discarding counts
to save four entries.
"""

from collections import Counter

from headstart.ingest.observability import ScrapeHealth


def test_every_loss_cause_is_reported_with_its_count():
    causes = Counter(
        {
            ("detail", "workday", f"HTTP {code}"): 10 - i
            for i, code in enumerate(range(500, 507))
        }
    )
    health = ScrapeHealth(
        {"workday": Counter({"successful": 100, "failed": 0, "partial": 0})},
        {"workday": Counter({"detail_missing": 7, "detail_attempted": 100})},
        causes,
        {key: {"workday:a"} for key in causes},
        15,
        15,
        0,
    )
    line = next(line for line in health.loss_lines() if "loss causes" in line)
    assert "more causes" not in line
    for code in range(500, 507):
        assert f"HTTP {code}" in line, f"HTTP {code} was dropped from the tail"

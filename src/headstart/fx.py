#!/usr/bin/env python3
"""Cross-currency comparison for the salary bracket — one dated table, no live lookup (ADR-0117).

ADR-0082 period-normalises salaries and deliberately does **not** FX-convert them, and until now
the bracket honoured that by pinning one currency: picking a USD range silently dropped every INR
job. For an index whose India coverage is a strength, that is a trap rather than a caveat — the
user gets fewer results and no indication why.

So the bracket converts. Three things keep that honest:

**The rates are committed and dated, never fetched.** `config/fx_rates.json` carries `as_of`
beside the numbers, and the UI prints it next to the filter. A live lookup would put a third-party
call on the search path, fail invisibly when it 500s, and give two users different results for the
same query on the same data.

**A missing rate excludes rather than assumes.** A currency absent from the table cannot be
compared, so its Jobs stay out of a cross-currency bracket. The alternative — treating it as 1:1 —
is the silent wrong answer this module exists to avoid.

**No rates at all means no conversion, not a broken filter.** :func:`table` returns ``None`` when
the file is missing or malformed, and the caller falls back to the single-currency behaviour that
predates this. Degrading to the old, narrower answer is recoverable; degrading to a wrong one is
not.

What this is NOT: a claim that converted salaries are comparable *offers*. Market FX is not
purchasing power — ₹40,00,000 in Bengaluru and the ~$48,000 it converts to are not the same job.
The UI says so beside the control; this module only makes the arithmetic available.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

# `logging.getLogger` rather than `headstart.log.get`, which is the same call: `deploy-space.yml`
# copies this module into the Space image as a flat `fx.py` with no `headstart` package beside it,
# so the seam cannot be imported there (the same constraint `_candidates` documents for paths, and
# the one `search.py` and `alerts/store.py` document for this). In the repo the name still resolves
# under the `headstart` root, so a stage's `log.setup()` reaches it.
_log = logging.getLogger(__name__)


def _candidates() -> tuple[Path, ...]:
    """Where the rate table might be, nearest first.

    Two layouts, one module: a flat `fx_rates.json` beside `app.py` in the Space image, and
    `config/fx_rates.json` in the repo. Walked rather than indexed, and computed lazily rather
    than at import: this module lives at `/app/fx.py` in the Space, whose path has only two
    ancestors, so a hardcoded `parents[2]` raised `IndexError` **at import time** — before the
    guarded read below could fall back — and took the whole Space down with it. Nothing here
    may raise on a path shallower than it expects.
    """
    here = Path(__file__).resolve()
    return (
        here.parent / "fx_rates.json",
        *(ancestor / "config" / "fx_rates.json" for ancestor in here.parents),
    )


#: Parsed once. ``False`` distinguishes "tried and failed" from "not tried yet", so a malformed
#: file is not re-read on every request.
_CACHE: dict[str, Any] | None | bool = False


def _no_conversion(reason: str, exc_info: bool = False) -> None:
    """One WARNING for every way the table ends up unusable, naming ``reason`` and the cost.

    Shared by both branches because they have one consequence between them, and named by that
    consequence rather than by its symptom. :func:`table` caches its result for the life of the
    process, so neither branch is one failed read: every salary bracket for the rest of this
    Space's uptime compares within a single currency and drops every Job priced in another — the
    exact trap the module docstring says converting exists to avoid — with nothing in the UI to
    say so. Swallowed silently, the feature simply never worked and no record anywhere said why.

    WARNING, not ERROR: the fallback is the narrower answer that predates this module, which is
    degraded rather than broken. And not INFO, because nothing calls `log.setup()` in the Space —
    `logging.lastResort` carries WARNING and above to stderr with no handler configured, and
    anything below it is discarded there.
    """
    _log.warning(
        f"fx_rates.json {reason} - no conversion for the rest of this process: the salary "
        "bracket falls back to one currency and every Job priced in another silently drops "
        "out of a cross-currency range",
        exc_info=exc_info,
    )


def table(path: Path | None = None) -> dict[str, Any] | None:
    """The rate table, or ``None`` when it cannot be used.

    ``None`` is a supported state, not an error: the salary bracket falls back to comparing
    within a single currency, which is what it did before this module existed.
    """
    global _CACHE
    if path is None and _CACHE is not False:
        return _CACHE  # type: ignore[return-value]
    result: dict[str, Any] | None
    try:
        found = path or next((c for c in _candidates() if c.exists()), None)
        if found is None:
            raise FileNotFoundError("no fx_rates.json on either known path")
        raw = json.loads(found.read_text())
        stated = {str(k).upper(): v for k, v in (raw.get("rates") or {}).items()}
        rates = {
            k: float(v)
            for k, v in stated.items()
            # A non-positive rate would divide by zero or invert the comparison; drop the
            # entry rather than the whole table, so one bad row cannot disable the feature.
            if isinstance(v, (int, float)) and float(v) > 0
        }
        if dropped := sorted(stated.keys() - rates.keys()):
            # Not `_no_conversion`: dropping the row is what keeps the feature up, so the cost is
            # partial rather than total. But it is still a currency whose Jobs now vanish from
            # every cross-currency range, and until now the drop named neither the currency nor
            # the fact. Bounded to one line per process by the cache below, like the rest.
            _log.warning(
                f"fx_rates.json states {len(dropped)} unusable rate(s), dropped: "
                f"{', '.join(dropped)} - Jobs priced in them stay out of every cross-currency "
                "salary range"
            )
        base = str(raw.get("base") or "").upper()
        as_of = str(raw.get("as_of") or "")
        # Refusing the table whole is a product decision (see the module docstring) — saying
        # nothing about it was not. This is arguably the likelier dark path of the two: a rate
        # refresh that writes valid JSON under a renamed `base` lands here, not in the `except`.
        # The failing conditions are named individually because the operator's next move differs
        # per shape — a `base` absent from its own `rates` is a one-word edit to the file, an
        # empty `rates` is a broken refresh job — and a bare "unusable" would leave them guessing
        # at a file they can see. All three are reported together rather than short-circuiting,
        # for the same reason, and still cost exactly one annotation.
        broken = [
            reason
            for reason, ok in (
                ("no usable rates", bool(rates)),
                (f"base {base or '(unset)'} has no rate of its own", base in rates),
                ("no as_of date", bool(as_of)),
            )
            if not ok
        ]
        if broken:
            _no_conversion(f"unusable ({'; '.join(broken)})")
            result = None
        else:
            result = {"base": base, "as_of": as_of, "rates": rates}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        _no_conversion(f"unreadable ({type(exc).__name__}: {exc})", exc_info=True)
        result = None
    if path is None:
        _CACHE = result
    return result


def convert(amount: float, frm: str, to: str, rates: dict[str, float]) -> float | None:
    """``amount`` expressed in ``frm``, restated in ``to``. ``None`` if either has no rate.

    Rates are units-per-base, so the base cancels: dividing out of ``frm`` and multiplying into
    ``to`` needs no special case for whichever currency the table happens to be keyed on.
    """
    a, b = rates.get((frm or "").upper()), rates.get((to or "").upper())
    if not a or not b:
        return None
    return amount / a * b

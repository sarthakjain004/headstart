#!/usr/bin/env python3
"""Cross-currency comparison for the salary bracket — one dated table, no live lookup (ADR-0116).

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
from pathlib import Path
from typing import Any

# Two layouts, one module: `config/fx_rates.json` in the repo, and a flat `fx_rates.json`
# beside app.py in the Space image (deploy-space.yml copies it there). Checked in that order
# rather than branching on an environment guess.
_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "config" / "fx_rates.json",
    Path(__file__).resolve().parent / "fx_rates.json",
)

#: Parsed once. ``False`` distinguishes "tried and failed" from "not tried yet", so a malformed
#: file is not re-read on every request.
_CACHE: dict[str, Any] | None | bool = False


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
        found = path or next((c for c in _CANDIDATES if c.exists()), None)
        if found is None:
            raise FileNotFoundError("no fx_rates.json on either known path")
        raw = json.loads(found.read_text())
        rates = {
            str(k).upper(): float(v)
            for k, v in (raw.get("rates") or {}).items()
            # A non-positive rate would divide by zero or invert the comparison; drop the
            # entry rather than the whole table, so one bad row cannot disable the feature.
            if isinstance(v, (int, float)) and float(v) > 0
        }
        base = str(raw.get("base") or "").upper()
        as_of = str(raw.get("as_of") or "")
        result = (
            {"base": base, "as_of": as_of, "rates": rates}
            if rates and base in rates and as_of
            else None
        )
    except (OSError, ValueError, TypeError, AttributeError):
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

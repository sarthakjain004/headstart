#!/usr/bin/env python3
"""Audit: does by-provider/ capture every slug-bearing source file?

Re-uses consolidate_harvested_lists' OWN extraction logic per file, so the audit
reflects exactly what the consolidation did. Flags data files that yielded zero
slugs (candidates for missed data) and providers named in source filenames that
have no by-provider output. Read-only; prints a report.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import consolidate_harvested_lists as C  # noqa: E402

SRC = C.SRC
BYP = C.OUT


def classify(p: Path) -> str:
    if p.suffix.lower() == ".md":
        return "doc"
    if p.name.startswith("_"):
        return "meta"
    if p.name.lower() in C.SKIP_NAMES:
        return "skip"
    return "data"


_KEYED_SUF = (".csv", ".txt", ".json", ".py", ".ts", ".js", ".yaml", ".yml")


def contribution(p: Path) -> tuple[int, int, int]:
    """(url_scan, columnar_scan, filename-keyed) slug counts — mirrors main()."""
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0, 0, 0
    url = len(list(C.url_scan(text)))
    col = len(list(C.columnar_scan(p, text)))
    ats = C.provider_from_name(p.stem)
    fk = 0
    if ats and p.suffix.lower() in _KEYED_SUF:
        fk = sum(1 for s in C.filename_keyed_slugs(ats, p) if C.valid_slug(s))
        if fk == 0 and p.suffix.lower() == ".csv":  # phenom-style url+name fallback
            for row in C.csv.DictReader(C.io.StringIO(text)):
                low = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
                nm = low.get("company_code") or low.get("code") or C._slugify(
                    next((low[c] for c in C.NAME_COLS if low.get(c)), ""))
                fk += 1 if C.valid_slug(nm) else 0
    return url, col, fk


def first_line(p: Path) -> str:
    try:
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.strip():
                return ln.strip()[:95]
    except Exception:
        pass
    return ""


def main() -> int:
    byp = {f.stem for f in BYP.glob("*.csv") if not f.stem.startswith("_")}
    byp_norm = {C.norm_ats(b) for b in byp}
    files = [p for p in SRC.rglob("*") if p.is_file()]
    buckets: dict[str, list[Path]] = {"data": [], "doc": [], "meta": [], "skip": []}
    for p in files:
        buckets[classify(p)].append(p)

    zero: list[tuple[Path, str]] = []
    contributing = 0
    seen_known: set[str] = set()
    for p in buckets["data"]:
        url, col, fk = contribution(p)
        prov = C.provider_from_name(p.stem)
        if prov:
            seen_known.add(prov)
        if url + col + fk > 0:
            contributing += 1
        else:
            zero.append((p, f"url={url} col={col} fk={fk}"))

    print(f"source files: {len(files)}  |  data {len(buckets['data'])}  "
          f"doc {len(buckets['doc'])}  meta {len(buckets['meta'])}  skip {len(buckets['skip'])}")
    print(f"data files contributing >=1 slug: {contributing}")
    print(f"data files with ZERO slugs: {len(zero)}")

    print("\n=== ZERO-yield data files (biggest first — inspect for missed slug data) ===")
    for p, why in sorted(zero, key=lambda t: -t[0].stat().st_size):
        print(f"  {p.stat().st_size:>8}  {p.relative_to(SRC)}  [{why}]")
        print(f"            first: {first_line(p)}")

    print("\n=== provider coverage ===")
    print(f"by-provider files: {len(byp)}")
    missing = sorted(p for p in seen_known if C.norm_ats(p) not in byp_norm)
    print(f"KNOWN providers named in source files but ABSENT from by-provider: {missing or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

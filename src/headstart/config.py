"""Loading the configured list of companies to scrape."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompanyRef:
    ats: str
    slug: str
    name: str | None = None


def load_companies(path: str | Path) -> list[CompanyRef]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [
        CompanyRef(ats=entry["ats"], slug=entry["slug"], name=entry.get("name"))
        for entry in data.get("company", [])
    ]

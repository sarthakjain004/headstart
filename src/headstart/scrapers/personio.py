"""Personio job-board scraper ({slug}.jobs.personio.{de|com}/xml feed).

Personio publishes a public XML feed of openings. The careers host's TLD varies per tenant
(`.de`, `.com`, ...), and the pool already stores each tenant's full host, so the slug here is
that host and the feed is ``https://{host}/xml``. Each ``<position>`` carries the title, office
(location), department, employment type, seniority, salary, and one or more ``<jobDescription>``
sections (CDATA HTML) that we concatenate into the description.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper


def _text(pos: ET.Element, tag: str) -> str | None:
    e = pos.find(tag)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def _description(pos: ET.Element) -> str | None:
    """Concatenate the <jobDescription> sections (name + CDATA HTML value) into clean text."""
    block = pos.find("jobDescriptions")
    if block is None:
        return None
    parts = []
    for d in block.findall("jobDescription"):
        name = (d.findtext("name") or "").strip()
        value = (d.findtext("value") or "").strip()
        if value:
            parts.append(f"{name}\n{value}" if name else value)
    return html_to_text("\n\n".join(parts)) if parts else None


class PersonioScraper(BaseScraper):
    ats = "personio"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        host = (url or "").split("://", 1)[-1].rstrip("/")
        return host if "personio" in host else f"{tenant}.jobs.personio.de"

    def url(self) -> str:
        # no ?language= — that returns the listing but empties descriptions for non-English
        # tenants; the bare feed gives each posting in the company's own language.
        return f"https://{self.slug}/xml"

    def fetch_raw(self) -> Any:
        # personio serves XML; encode back to bytes so ElementTree accepts the encoding decl.
        return ET.fromstring(self._get().encode("utf-8"))

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        tenant = self.slug.split(".")[0]
        jobs: list[Job] = []
        for pos in raw.findall("position"):
            jid = _text(pos, "id")
            if not jid:
                continue
            office = _text(pos, "office")
            etype, sched = _text(pos, "employmentType"), _text(pos, "schedule")
            jobs.append(
                Job(
                    id=f"{self.ats}:{tenant}:{jid}",
                    ats=self.ats,
                    company=_text(pos, "subcompany") or self.company,
                    title=_text(pos, "name") or "",
                    location=office,
                    remote=is_remote(office),
                    department=_text(pos, "department"),
                    url=f"https://{self.slug}/job/{jid}",
                    posted_at=_text(pos, "createdAt"),
                    scraped_at=scraped_at,
                    description=_description(pos),
                    experience=_text(pos, "seniority")
                    or _text(pos, "yearsOfExperience"),
                    employment_type=" / ".join(x for x in (etype, sched) if x) or None,
                    salary=_text(pos, "salaryInformation"),
                )
            )
        return jobs

#!/usr/bin/env python3
"""Extract historical recruiting role inboxes from every archived HTML page of ATS boards.

Input is the Wayback-derived board roster (`ats,tenant,url`).  For each board this walks the
archived root and every URL beneath it, keeping one capture per unique response digest.  Output
is an evidence ledger, not proof that an inbox is current: every row carries the exact archived
snapshot URL and timestamp.  It deliberately keeps only public, role-based recruiting or
application contacts; named addresses and EEO/accommodation/legal contacts stay out.

Run a complete roster slowly (Wayback is deliberately capped at two workers):

    python scripts/discover/wayback_hr_emails.py \
      --input data/wayback-ats \
      --out data/discover/wayback-hr-emails/2026-09-13_all-ats.csv

Interrupted Boards are recorded as `partial` and retried on the next run.  Result rows are
append-only; consumers deduplicate `(board_key, snapshot_url, email)`.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from wayback_feeder import FetchError, fetch

_EMAIL = re.compile(
    r"(?<![\w.+-])([a-z0-9][a-z0-9.!#$%&'*+/=?^_`{|}~-]*@[a-z0-9-]+(?:\.[a-z0-9-]+)+)",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]*>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_ROLE = re.compile(
    r"career|jobs?|recruit|talent|hiring|(?:^|[._+-])hr(?:$|[._+-])|people|candidate|apply",
    re.IGNORECASE,
)
_APPLICATION = re.compile(
    r"apply|application|resume|curriculum vitae|\bcv\b|candidate|job opening|vacancy",
    re.IGNORECASE,
)
_EXCLUDED = re.compile(
    r"accommodation|disabilit|\beeo\b|equal opportunity|privacy|legal|data protection",
    re.IGNORECASE,
)
_GENERIC = {"contact", "info", "help", "support"}
_FREE_MAIL = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
}
_EVIDENCE_FIELDS = [
    "board_key",
    "ats",
    "tenant",
    "board_url",
    "email",
    "contact_kind",
    "snapshot_at",
    "original_url",
    "snapshot_url",
    "page_title",
    "source_quote",
]
_CHECKED_FIELDS = ["board_key", "status", "pages", "contacts", "checked_at", "error"]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", value))).strip()


def classify(email: str, context: str) -> str | None:
    """Return a narrowly-scoped contact class, or reject this archived address."""
    local, domain = email.lower().rsplit("@", 1)
    if domain in _FREE_MAIL or _EXCLUDED.search(context):
        return None
    if _ROLE.search(local):
        return "recruiting_role_inbox"
    if local in _GENERIC and _APPLICATION.search(context):
        return "application_contact"
    return None


def contacts(page: str) -> list[tuple[str, str, str]]:
    """Extract `(email, contact_kind, source_quote)` from one archived HTML response."""
    found: dict[str, tuple[str, str]] = {}
    for match in _EMAIL.finditer(html.unescape(page)):
        email = match.group(1).rstrip('.,;:)>]}"').lower()
        raw_context = page[max(0, match.start() - 180) : match.end() + 180]
        quote = clean(raw_context)
        kind = classify(email, quote)
        if kind and email not in found:
            found[email] = (kind, quote[:360])
    return [(email, kind, quote) for email, (kind, quote) in found.items()]


def title(page: str) -> str:
    match = _TITLE.search(page)
    return clean(match.group(1))[:240] if match else ""


def cdx_rows(text: str) -> tuple[list[tuple[str, str]], str | None]:
    """Read CDX `timestamp original digest` rows and its optional resume key."""
    lines = [line for line in text.splitlines()]
    resume = None
    if len(lines) >= 2 and not lines[-2] and lines[-1]:
        resume = lines.pop()
        lines.pop()
    rows = []
    for line in lines:
        parts = line.split(" ", 2)
        if (
            len(parts) == 3
            and parts[0].isdigit()
            and parts[1].startswith(("http://", "https://"))
        ):
            rows.append((parts[0], parts[1]))
    return rows, resume


def cdx_url(board_url: str, prefix: bool, resume: str | None = None) -> str:
    target = board_url.rstrip("/") + ("/" if prefix else "")
    query = {
        "url": target,
        "matchType": "prefix" if prefix else "exact",
        "fl": "timestamp,original,digest",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "collapse": "digest",
        "limit": "1000",
        "showResumeKey": "true",
    }
    if resume:
        query["resumeKey"] = resume
    return "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
        query, doseq=True
    )


def snapshot_url(timestamp: str, original: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def belongs_to_board(board_url: str, original: str) -> bool:
    """Whether an archive prefix result is the Board itself or one of its pages.

    CDX's prefix matching is lexical, so `/stripe/` can contain `/stripes/...` as well.  The
    request remains usefully broad (it also finds http/https variants), while this check makes
    the archived-page walk exact at the board-path boundary.
    """
    board = urllib.parse.urlsplit(board_url)
    page = urllib.parse.urlsplit(original)
    if board.netloc.lower() != page.netloc.lower():
        return False
    base = board.path.rstrip("/")
    return page.path.rstrip("/") == base or page.path.startswith(base + "/")


class Sink:
    def __init__(self, out: Path):
        self.out = out
        self.checked = out.with_suffix(".checked.csv")
        self.lock = threading.Lock()
        out.parent.mkdir(parents=True, exist_ok=True)
        self._ensure(out, _EVIDENCE_FIELDS)
        self._ensure(self.checked, _CHECKED_FIELDS)

    @staticmethod
    def _ensure(path: Path, fields: list[str]) -> None:
        if not path.exists() or not path.stat().st_size:
            with path.open("a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=fields).writeheader()

    def completed(self) -> set[str]:
        with self.checked.open(newline="", encoding="utf-8") as fh:
            return {
                row["board_key"]
                for row in csv.DictReader(fh)
                if row["status"] == "complete"
            }

    def evidence(self, row: dict[str, str]) -> None:
        with self.lock, self.out.open("a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=_EVIDENCE_FIELDS).writerow(row)
            fh.flush()

    def finish(
        self, board_key: str, status: str, pages: int, count: int, error: str = ""
    ) -> None:
        with self.lock, self.checked.open("a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=_CHECKED_FIELDS).writerow(
                {
                    "board_key": board_key,
                    "status": status,
                    "pages": pages,
                    "contacts": count,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "error": error[:240],
                }
            )
            fh.flush()


def scan_board(row: dict[str, str], sink: Sink) -> None:
    ats, tenant, board_url = row["ats"], row["tenant"], row["url"].rstrip("/")
    key = f"{ats}:{tenant}"
    snapshots: set[str] = set()
    pages = count = 0
    try:
        for prefix in (False, True):
            resume = None
            while True:
                rows, resume = cdx_rows(fetch(cdx_url(board_url, prefix, resume)))
                for timestamp, original in rows:
                    if not belongs_to_board(board_url, original):
                        continue
                    source = snapshot_url(timestamp, original)
                    if source in snapshots:
                        continue
                    snapshots.add(source)
                    page = fetch(source)
                    pages += 1
                    page_title = title(page)
                    for email, kind, quote in contacts(page):
                        sink.evidence(
                            {
                                "board_key": key,
                                "ats": ats,
                                "tenant": tenant,
                                "board_url": board_url,
                                "email": email,
                                "contact_kind": kind,
                                "snapshot_at": timestamp,
                                "original_url": original,
                                "snapshot_url": source,
                                "page_title": page_title,
                                "source_quote": quote,
                            }
                        )
                        count += 1
                        print(f"{key}: {email} ({kind})", flush=True)
                if not resume:
                    break
        sink.finish(key, "complete", pages, count)
        print(
            f"{key}: complete; {pages} archived page(s), {count} contact occurrence(s)",
            flush=True,
        )
    except FetchError as error:
        sink.finish(key, "partial", pages, count, str(error))
        print(f"{key}: PARTIAL after {pages} page(s): {error}", flush=True)


def roster(path: Path) -> list[dict[str, str]]:
    files = sorted(path.glob("*.csv")) if path.is_dir() else [path]
    rows = []
    for file in files:
        with file.open(newline="", encoding="utf-8") as fh:
            rows.extend(
                row
                for row in csv.DictReader(fh)
                if row.get("ats") and row.get("tenant") and row.get("url")
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/wayback-ats"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--ats", help="scan one ATS from a directory input")
    parser.add_argument(
        "--tenant", help="scan one exact tenant (optionally with --ats)"
    )
    parser.add_argument(
        "--limit", type=int, help="test on the first N unfinished boards"
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        raise SystemExit("Wayback is measured safe only at 1-2 workers")
    sink = Sink(args.out)
    todo = [
        row
        for row in roster(args.input)
        if f"{row['ats']}:{row['tenant']}" not in sink.completed()
    ]
    if args.ats:
        todo = [row for row in todo if row["ats"] == args.ats]
    if args.tenant:
        todo = [row for row in todo if row["tenant"] == args.tenant]
        if not todo:
            raise SystemExit(f"no unfinished Wayback board named {args.tenant!r}")
    if args.limit is not None:
        todo = todo[: args.limit]
    print(
        f"Wayback HR-email scan: {len(todo)} board(s), workers={args.workers}",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed(
            [pool.submit(scan_board, row, sink) for row in todo]
        ):
            future.result()


if __name__ == "__main__":
    main()

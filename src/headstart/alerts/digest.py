"""Render one Digest — the email a Subscription gets after a run (ADR-0035).

`render` is pure and returns text plus HTML; `to_xlsx` builds the attachment separately.
They are split because the attachment needs `xlsxwriter` and the body does not: CI's
quality job installs no extras, so keeping the body renderer dependency-free is what lets
it be tested there at all.

The body carries the links because that is the surface people actually use — a link is one
tap on a phone, where an attachment is download-open-scroll. The spreadsheet is for working
the list at a desk.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from .store import Subscription

COLUMNS = ("company", "title", "location", "score", "url")


@dataclass
class Digest:
    subject: str
    text: str
    html: str


def _line(job: dict[str, Any]) -> str:
    bits = [str(job.get("company") or "?"), str(job.get("title") or "Role")]
    if job.get("location"):
        bits.append(str(job["location"]))
    return " — ".join(bits)


def subject_for(sub: Subscription, jobs: list[dict[str, Any]]) -> str:
    n = len(jobs)
    return f"{n} new {'match' if n == 1 else 'matches'} for “{sub.query}”"


def render(
    sub: Subscription, jobs: list[dict[str, Any]], unsubscribe_url: str
) -> Digest:
    """Subject, plain text and HTML for `jobs`. Callers only render when `jobs` is non-empty —
    a Digest with nothing in it is not sent at all."""
    text_rows, html_rows = [], []
    for job in jobs:
        score = job.get("score")
        score_text = f"{float(score):.3f}" if isinstance(score, (int, float)) else "—"
        url = str(job.get("url") or "")
        text_rows.append(f"- {_line(job)}  [{score_text}]\n  {url}")
        html_rows.append(
            f'<li style="margin:0 0 14px 0">'
            f'<a href="{html.escape(url, quote=True)}" style="font-weight:600">'
            f"{html.escape(_line(job))}</a>"
            f'<span style="color:#666"> · score {score_text}</span></li>'
        )

    body = "\n".join(text_rows)
    text = (
        f"{len(jobs)} new job(s) matching: {sub.query}\n\n{body}\n\n"
        f"The spreadsheet attached has the same rows.\n"
        f"Unsubscribe: {unsubscribe_url}\n"
    )
    markup = (
        f'<div style="font-family:system-ui,sans-serif;max-width:640px">'
        f"<p>{len(jobs)} new job(s) matching "
        f"<strong>{html.escape(sub.query)}</strong>:</p>"
        f'<ul style="padding-left:18px">{"".join(html_rows)}</ul>'
        f'<p style="color:#666;font-size:13px">The attached spreadsheet has the same rows.'
        f' · <a href="{html.escape(unsubscribe_url, quote=True)}">Unsubscribe</a></p></div>'
    )
    return Digest(subject=subject_for(sub, jobs), text=text, html=markup)


PER_MESSAGE = 10


def to_telegram(
    sub: Subscription, jobs: list[dict[str, Any]], per_message: int = PER_MESSAGE
) -> list[str]:
    """The same Digest as consecutive Telegram messages, newest-first order preserved.

    Chunked because Telegram caps one message at 4096 characters and a Digest carries up to
    30 roles — well past it. Ten per message keeps each comfortably inside the cap with room
    for long titles, and matches the batch size the old keyword bot already used.

    There is no unsubscribe link: a DM is stopped by blocking the bot or by the owner
    striking the entry from the allowlist, so a URL carrying a token would be a second,
    weaker path to the same thing. Telegram's HTML subset is `<b>` and `<a>`, hence no
    inline styling — and every interpolated value is escaped, since a job title containing
    a stray `<` would otherwise make Telegram reject the whole message.
    """
    batches = [jobs[i : i + per_message] for i in range(0, len(jobs), per_message)] or [
        []
    ]
    total = len(jobs)
    out: list[str] = []
    for index, batch in enumerate(batches):
        lines = []
        if index == 0:
            lines.append(
                f"<b>{total} new job(s)</b> matching: {html.escape(sub.query)}"
            )
        else:
            lines.append(
                f"<b>…continued ({index * per_message + 1}–"
                f"{index * per_message + len(batch)} of {total})</b>"
            )
        for job in batch:
            score = job.get("score")
            score_text = (
                f"{float(score):.3f}" if isinstance(score, (int, float)) else "—"
            )
            url = html.escape(str(job.get("url") or ""), quote=True)
            lines.append(
                f'• <a href="{url}">{html.escape(_line(job))}</a> · {score_text}'
            )
        out.append("\n".join(lines))
    return out


def to_xlsx(jobs: list[dict[str, Any]]) -> bytes:
    """The same rows as a spreadsheet, apply links clickable."""
    import io

    import xlsxwriter

    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True})
    sheet = book.add_worksheet("new jobs")
    header = book.add_format({"bold": True})
    link = book.add_format({"font_color": "blue", "underline": 1})

    for column, name in enumerate(COLUMNS):
        sheet.write(0, column, name, header)
    for row, job in enumerate(jobs, start=1):
        for column, name in enumerate(COLUMNS):
            value = job.get(name)
            if name == "url" and value:
                sheet.write_url(row, column, str(value), link, "apply")
            else:
                sheet.write(row, column, "" if value is None else value)
    sheet.set_column(0, 2, 34)
    sheet.set_column(4, 4, 12)
    book.close()
    return buffer.getvalue()

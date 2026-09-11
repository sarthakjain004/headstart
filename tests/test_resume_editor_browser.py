"""The Résumé tab's editor, driven in a real browser — the checks `node --test` cannot make.

`tests/js/resume_editor.test.js` says so in its own header: it runs the shipped editor over a
shallow DOM stub with no geometry, and "what needs real geometry (page breaks, drag targets,
pixels per inch) is measured in a real browser instead". Until this file there was no such
browser, so nothing measured any of it and six review findings survived three rounds unseen.
These are those measurements: a real pointer drag against real drop rectangles, and a bullet
measured against the line boxes Chromium actually lays out.

**A GREEN CI IS NOT EVIDENCE THESE PASSED.** CI installs base deps only and has no Chromium, so
`playwright` is missing and every test here SKIPS — the same arrangement as
`tests/test_readme_schema.py`. Run them locally before shipping anything in
`src/headstart/ui/static/resume/`:

    pip install playwright flask && playwright install chromium
    python -m pytest tests/test_resume_editor_browser.py -v

The page under test is the real one: `scripts/ui/serve_resume_stub.py` renders the shipped
`base.html` and the shipped `static/resume/*.js`, with the index and the model stubbed out.
The editor is driven through `window.ResumeEditor`, the public surface its own source says the
browser tests use.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip(
    "playwright.sync_api", reason="browser tests need playwright + chromium"
)
pytest.importorskip("flask")

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "scripts" / "ui" / "serve_resume_stub.py"

# The worked example the first visit opens — its name, as the plain-text export prints it.
CANDIDATE = "LEE KORELITZ"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    """The stub server, up for the module and torn down after it."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(_SERVER), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if proc.poll() is not None:
                pytest.skip("the stub UI server would not start")
            try:
                urllib.request.urlopen(url, timeout=0.5).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            pytest.skip("the stub UI server never answered")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except PlaywrightError as exc:  # no browser binary on this machine
            pytest.skip(f"chromium is not installed: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    """The Résumé tab, booted, on its Preview segment — where the sheet is."""
    pg = browser.new_page(viewport={"width": 1500, "height": 1100})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{base_url}/#resume")
    pg.wait_for_function("() => window.ResumeEditor && window.ResumeEditor.current()")
    pg.evaluate(
        "() => document.querySelector('#rb-seg [data-seg=\"preview\"]').click()"
    )
    pg.wait_for_timeout(300)
    assert not errors, f"the page threw before the test began: {errors}"
    yield pg
    pg.close()


def _first_bullet(pg) -> str:
    return pg.evaluate("""() => {
      let bullet = null;
      window.ResumeDocument.walk(window.ResumeEditor.current().root,
        n => { if (!bullet && n.type === 'bullet') bullet = n; });
      return bullet.id;
    }""")


def test_a_bullet_dragged_to_the_top_of_the_page_is_refused(page):
    """Dragging a bullet above the header must not put it at the document root.

    Measured before the fix: it did, root became ['bullet','header','section','section'], the
    sheet rendered a stray <li> outside any <ul>, and — the reason this is a P0 — the plain-text
    export began "- Operated our Point of Sale…" with the candidate's name on the line BELOW it.
    That export is what people paste into an application form's "paste your résumé" box, so one
    stray drag silently corrupts the file every application is made with.
    """
    bullet = _first_bullet(page)
    grab = page.evaluate(
        """(id) => {
          const paper = document.getElementById('rb-paper');
          const handle = paper.querySelector('[data-node="' + id + '"] .rb-h[data-handle="move"]');
          const header = paper.querySelector(
            '[data-node="' + window.ResumeEditor.current().root.children[0].id + '"]');
          const h = handle.getBoundingClientRect(), t = header.getBoundingClientRect();
          return {fromX: h.left + h.width / 2, fromY: h.top + h.height / 2,
                  toX: t.left + t.width / 2, toY: t.top};
        }""",
        bullet,
    )

    page.mouse.move(grab["fromX"], grab["fromY"])
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(
            grab["fromX"] + (grab["toX"] - grab["fromX"]) * step / 10,
            grab["fromY"] + (grab["toY"] - grab["fromY"]) * step / 10,
        )
    page.mouse.up()
    page.wait_for_timeout(200)

    after = page.evaluate("""() => {
      const d = window.ResumeEditor.current();
      return {root: d.root.children.map(c => c.type),
              text: window.ResumeExport.plainText(d)};
    }""")

    assert after["text"].startswith(CANDIDATE), (
        "the plain-text export — the copy that goes into application forms — must open with the "
        f"candidate's name, not {after['text'][:60]!r}"
    )
    assert "bullet" not in after["root"], (
        f"a bullet reached the document root: {after['root']}"
    )


# 281 characters — measured in Chromium as the longest bullet that still lays out in three line
# boxes on a US Letter sheet at the default 10.5pt body and 0.3in indent. `charsPerLine`'s
# estimate (an average advance of half the point size) puts the ceiling at 255, so everything
# between the two is told to split a bullet that already fits.
LONG_BULLET = (
    "Rebuilt the nightly billing reconciliation so a failed batch retries from the last good "
    "checkpoint instead of starting over, which cut the on-call pages it raised from eleven a "
    "week down to one and saved the finance team about six hours of manual re-keying a month"
)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="the fix is in resume_layouts.js's charsPerLine, which another agent holds; this "
    "records the measurement so the estimate cannot be called correct by default. `raises` is "
    "narrow on purpose — a broken drive of the page throws something else and fails loudly, "
    "rather than being swallowed as the expected failure",
)
def test_a_bullet_that_fits_on_three_lines_is_not_called_four(page):
    """The "no bullet over three lines" finding must agree with the sheet it is measuring.

    Node cannot check this: `charsPerLine` is an ESTIMATE, and only a browser knows how many
    line boxes the text really occupies. Measured here — 281 characters lay out in 3 lines and
    the panel calls them "About 4 lines long", so the advice is wrong for every bullet between
    256 and ~290 characters, which is exactly the length a good bullet is.
    """
    assert len(LONG_BULLET) == 281
    bullet = _first_bullet(page)
    result = page.evaluate(
        """([id, text]) => {
          const ed = window.ResumeEditor;
          ed.current().content[id].text = text;
          /* Repaint through the segment strip, the way a user's own edit reaches the sheet. */
          document.querySelector('#rb-seg [data-seg="edit"]').click();
          document.querySelector('#rb-seg [data-seg="preview"]').click();
          const el = document.querySelector('#rb-paper [data-node="' + id + '"]');
          /* One client rect per LINE BOX — the only honest line count, and unaffected by the
             zoom transform the sheet is drawn under. */
          const range = document.createRange();
          range.selectNodeContents(el);
          return {lines: range.getClientRects().length,
                  findings: ed.findings().filter(f => f.nodeId === id && f.ruleId === 'three-lines')
                    .map(f => f.message)};
        }""",
        [bullet, LONG_BULLET],
    )
    assert result["lines"] == 3, f"the sheet laid this out in {result['lines']} lines"
    assert result["findings"] == [], (
        f"a bullet that renders in 3 lines was flagged: {result['findings']}"
    )

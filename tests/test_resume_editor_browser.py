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

import re
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
    reason="STILL FAILS after ADR-0130, and the reason is worth keeping: that change fixed the "
    "width `three-lines` is handed (it measured the page where the text has a column, so the "
    "three column layouts were 24-39% over and stayed silent on real four-line bullets) and it "
    "does not touch this case, because `headless-headhunter` has ONE column and its width was "
    "already right. This is a different defect — estimator variance. Measured in Chromium: this "
    "281-character bullet lays out in 3 line boxes at 94 characters a line, and a different "
    "281-character bullet lays out in 4 at 70, same layout, same width, same font. A character "
    "count cannot see which text is which, so no width fix separates them and raising the cap "
    "would re-break the column layouts. `raises` is narrow on purpose — a broken drive of the "
    "page throws something else and fails loudly, rather than being swallowed as expected",
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


def test_no_page_break_is_drawn_through_a_block_the_layout_refuses_to_split(page):
    """A drawn page break must never fall inside a block whose stylesheet says `break-inside: avoid`.

    That is the whole job of the unbreakable-block sweep: the printer pushes such a block whole
    onto the next sheet, so a marker drawn through one is telling the user the page ends somewhere
    it does not.

    The sweep used to find those blocks by naming four CSS classes — `.hh-entry, .tc-entry,
    .cv-entry, .rb-entry` — which four of the seven then-registered layouts do not use. It found
    ZERO unbreakable blocks on `jakes-resume`, `harvard-classic`, `modern-sidebar` and `europass`,
    and drew its cuts by raw height alone: 14 wrong page counts across 63 layout/length
    combinations, and `europass` previewing two pages for a document that printed on three.

    `node --test` cannot make this check at all — `break-inside` is a computed style and the DOM
    stub has none — which is how a wrong class list sat here while the suite stayed green.

    This asserts the EDITOR's own output, not a selector restated: it reads the `.rb-break`
    elements the editor drew, against the elements Chromium says are unbreakable.
    """
    long_text = LONG_BULLET * 3
    page.evaluate(
        """(text) => {
          /* Fatten every bullet so the document runs past one sheet on every layout. */
          const doc = window.ResumeEditor.current();
          window.ResumeDocument.walk(doc.root, n => {
            if (n.type === 'bullet' && doc.content[n.id]) doc.content[n.id].text = text;
          });
        }""",
        long_text,
    )

    through = []
    for layout_id in page.evaluate("() => window.ResumeLayouts.all().map(l => l.id)"):
        page.evaluate("(id) => window.ResumeEditor.changeLayout(id)", layout_id)
        page.wait_for_timeout(200)
        result = page.evaluate("""() => {
          const paper = document.getElementById('rb-paper');
          const cuts = [...paper.querySelectorAll('.rb-break')]
            .map(el => el.getBoundingClientRect().top);
          const avoid = [...paper.querySelectorAll('*')]
            .filter(el => getComputedStyle(el).breakInside === 'avoid')
            .map(el => el.getBoundingClientRect());
          /* Strictly inside: a cut level with a block's own edge is the printer agreeing. */
          const bad = cuts.filter(y => avoid.some(r => y > r.top + 1 && y < r.bottom - 1));
          return {cuts: cuts.length, avoid: avoid.length, bad: bad.length};
        }""")
        if result["avoid"] and result["bad"]:
            through.append(f"{layout_id}: {result}")

    assert through == [], (
        "a page-break marker is drawn through a block the printer will move whole, so the "
        f"preview is claiming a page ends where it does not: {through}"
    )


def test_check_coverage_marks_the_page_the_moment_it_is_clicked(page):
    """The keyword check's highlights must land on the click that asked for them.

    Measured before the fix: straight after clicking **Check coverage** the summary read
    "2 of 2 present · 100% in the top half of page one" with ZERO `<mark>` elements on the sheet.
    Typing one space into the résumé's name box then produced five. The check set the terms and
    returned, under a comment saying "Repaint so the page marks them", so the marks arrived on
    whatever unrelated change happened next — which is worse than not marking at all, because by
    then the reader has stopped looking for them.
    """
    summary = page.evaluate("""() => {
      document.getElementById('rb-kw-toggle').click();
      document.getElementById('rb-kw').value = 'Point of Sale\\nCustomer Service';
      document.getElementById('rb-kw-run').click();
      return document.getElementById('rb-kw-summary').textContent;
    }""")
    marks = page.evaluate(
        "() => document.querySelectorAll('#rb-paper mark.rb-kw-hit').length"
    )
    assert "present" in summary, f"the check did not run at all: {summary!r}"
    assert marks > 0, (
        f"the panel reported {summary!r} and marked nothing on the page — the highlights are "
        "waiting for some later repaint"
    )


def test_the_sheet_is_refitted_when_the_stage_narrows(page):
    """A window narrowed after load must not leave the page hanging out of its wrapper.

    `fitToWidth` ran once behind a `fitted` flag and nothing listened for a resize. Measured:
    load at 1440 so the sheet fits, narrow to 420, and the paper overflowed its wrapper by 211px
    while the zoom readout still said 100%; clicking "Fit" dropped it to 45%, which is the answer
    the editor could have given itself.

    `node --test` cannot make this check — the DOM stub has no layout and no ResizeObserver.
    """
    before = page.evaluate("""() => {
      const p = document.getElementById('rb-paper');
      return Math.round(p.getBoundingClientRect().width - p.parentElement.clientWidth);
    }""")
    assert before <= 0, (
        f"the sheet already overflowed at the fixture's width by {before}px"
    )

    page.set_viewport_size({"width": 420, "height": 900})
    # The refit is debounced, and the repaint behind it is a full re-render of the sheet.
    page.wait_for_timeout(800)

    after = page.evaluate("""() => {
      const p = document.getElementById('rb-paper');
      return {over: Math.round(p.getBoundingClientRect().width - p.parentElement.clientWidth),
              zoom: document.getElementById('rb-zoom-read').textContent};
    }""")
    assert after["over"] <= 0, (
        f"the page hangs {after['over']}px out of its wrapper at 420px, and the zoom reads "
        f"{after['zoom']}"
    )


def test_a_drag_inside_one_column_costs_one_undo_press(page):
    """Dropping a top-level block where it already was column-wise is ONE command, not two.

    `Cmd.setSlot` fired on every top-level drop, changed or not, and `Cmd.moveNode` followed it.
    So a drag within a single column pushed two undo entries — the second a visible no-op — and
    the label on the whole move read "Move to column". `UNDO_LIMIT` is 60, so a drag-heavy
    session spends half of it undoing nothing.

    Asserted through the Undo button's own `disabled`, because that is the only place the stack's
    depth is visible from outside: a freshly opened document has an empty stack, so after one
    drag and one undo the button must be dead again.
    """
    state = page.evaluate("""() => ({
      undo: document.getElementById('rb-undo').disabled,
      root: window.ResumeEditor.current().root.children.map(c => c.id),
    })""")
    assert state["undo"], (
        "the document arrived with something already on the undo stack"
    )
    assert len(state["root"]) >= 3, (
        "the worked example no longer has three top-level blocks"
    )

    grab = page.evaluate(
        """([moved, above]) => {
          const paper = document.getElementById('rb-paper');
          const block = paper.querySelector('[data-node="' + moved + '"]');
          /* The block's OWN handle. A descendant selector finds the first handle anywhere
             inside it, which for a section is one of its entries' — so the drag under test
             would have been a different block's. */
          const handle = [...block.querySelectorAll('.rb-h[data-handle="move"]')]
            .find(h => h.closest('[data-node]') === block);
          const onto = paper.querySelector('[data-node="' + above + '"]');
          const h = handle.getBoundingClientRect(), t = onto.getBoundingClientRect();
          return {fromX: h.left + h.width / 2, fromY: h.top + h.height / 2,
                  toX: t.left + t.width / 2, toY: t.top + 2};
        }""",
        [state["root"][2], state["root"][1]],
    )
    page.mouse.move(grab["fromX"], grab["fromY"])
    page.mouse.down()
    for step in range(1, 21):
        page.mouse.move(
            grab["fromX"] + (grab["toX"] - grab["fromX"]) * step / 20,
            grab["fromY"] + (grab["toY"] - grab["fromY"]) * step / 20,
        )
    page.mouse.up()
    page.wait_for_timeout(250)

    after = page.evaluate(
        "() => window.ResumeEditor.current().root.children.map(c => c.id)"
    )
    assert after != state["root"], (
        "the drag moved nothing, so there is no undo to count"
    )

    page.evaluate("() => document.getElementById('rb-undo').click()")
    page.wait_for_timeout(250)
    back = page.evaluate("""() => ({
      undo: document.getElementById('rb-undo').disabled,
      root: window.ResumeEditor.current().root.children.map(c => c.id),
    })""")
    assert back["root"] == state["root"], "one undo did not put the block back"
    assert back["undo"], (
        "one drag left a second undo entry behind it — the no-op column change, which the user "
        "has to press Undo again to clear"
    )


def test_every_template_card_says_how_many_pages_it_runs_to(page):
    """The gallery must answer the question its own copy promises to answer.

    The heading said "the dashed page-break line tells you if the new one runs onto a second
    sheet" and `templatesPaint` never called the break painter: nine cards, zero lines. At a
    190px card a hairline is near-invisible anyway, so the card states the count in words — the
    same answer the miniature beside the form already gives — and the copy says so.
    """
    page.evaluate("() => document.querySelector('[data-act=\"templates\"]').click()")
    page.wait_for_timeout(400)
    seen = page.evaluate("""() => ({
      cards: document.querySelectorAll('.rb-tcard').length,
      pages: [...document.querySelectorAll('.rb-tpages')].map(e => e.textContent),
      copy: document.querySelector('.rb-templates-head .note').textContent,
    })""")
    assert seen["cards"] >= 9, f"only {seen['cards']} templates are registered"
    assert len(seen["pages"]) == seen["cards"], "some cards carry no page count at all"
    bad = [t for t in seen["pages"] if not re.fullmatch(r"\d+ pages?", t.strip())]
    assert bad == [], f"these cards say nothing useful about their length: {bad}"
    assert "dashed" not in seen["copy"], (
        "the gallery still promises a dashed page-break line: " + seen["copy"]
    )


def test_below_a_thousand_pixels_checks_is_still_near_the_top(browser, base_url):
    """ADR-0128 says the aside is sticky so Checks stays reachable. Below 1000px it was not.

    `.rb-aside` is `position: static` under that breakpoint, where `.rb-work` is a single column
    and the aside stacks under the form — so `sticky` has nothing to stick inside even when it is
    left on. Measured at 820px: the Checks card landed 1,107px down an 1,855px page, which is
    past the end of the form rather than beside it.
    """
    pg = browser.new_page(viewport={"width": 820, "height": 900})
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    try:
        pg.goto(f"{base_url}/#resume")
        pg.wait_for_function(
            "() => window.ResumeEditor && window.ResumeEditor.current()"
        )
        pg.wait_for_timeout(400)
        assert not errors, f"the page threw: {errors}"
        where = pg.evaluate("""() => {
          const top = el => Math.round(el.getBoundingClientRect().top + window.scrollY);
          return {checks: top(document.getElementById('rb-pane-checks').closest('.rb-card')),
                  form: top(document.getElementById('rb-pane-document')),
                  page: Math.round(document.documentElement.scrollHeight)};
        }""")
        assert where["checks"] < where["form"], (
            f"Checks sits {where['checks']}px down a {where['page']}px page, below the "
            f"{where['form']}px form it is supposed to be advising on"
        )
    finally:
        pg.close()


def test_a_free_canvas_drag_is_bounded_by_the_document_s_own_paper(page):
    """A block dragged off the right edge must stop where the *document's* sheet ends.

    #423 gave `ResumeLayouts` a `boundsFor(layout, doc)` deriving the free canvas's maxima from
    the paper the document chose — 6.9 x 9.4in on Letter, 6.67 x 10.09 on A4. The editor read
    the Layout's own raw `bounds` at three sites, so on an A4 document the drag offered the
    Letter maximum while the renderer clamped to A4: the UI invited a position it then took
    away, and the new horizontal-overflow rule warned about it afterwards.

    This is a real pointer drag against real bounds, which `node --test` has no geometry for.
    """
    setup = page.evaluate("""() => {
      const ed = window.ResumeEditor;
      ed.changeLayout('free-canvas');
      const paper = document.getElementById('rb-paper-size');
      paper.value = 'a4';
      paper.dispatchEvent(new Event('change', {bubbles: true}));
      const lay = window.ResumeLayouts.get('free-canvas');
      return {letter: window.ResumeLayouts.boundsFor(lay, null).x[1],
              a4: window.ResumeLayouts.boundsFor(lay, ed.current()).x[1],
              paper: ed.current().paper,
              block: ed.current().root.children[0].id};
    }""")
    assert setup["paper"] == "a4", f"the paper did not change: {setup['paper']!r}"
    assert setup["a4"] < setup["letter"], (
        "A4 and Letter bound the canvas identically, so this test proves nothing"
    )

    page.wait_for_timeout(200)
    grab = page.evaluate(
        """(id) => {
          const paper = document.getElementById('rb-paper');
          const block = paper.querySelector('[data-node="' + id + '"]');
          const handle = [...block.querySelectorAll('.rb-h[data-handle="move"]')]
            .find(h => h.closest('[data-node]') === block);
          const h = handle.getBoundingClientRect();
          return {fromX: h.left + h.width / 2, fromY: h.top + h.height / 2};
        }""",
        setup["block"],
    )
    page.mouse.move(grab["fromX"], grab["fromY"])
    page.mouse.down()
    # Far past the right edge of any sheet, so only the bound decides where it stops.
    for step in range(1, 21):
        page.mouse.move(grab["fromX"] + 40 * step, grab["fromY"])
    page.mouse.up()
    page.wait_for_timeout(250)

    landed = page.evaluate(
        "(id) => (window.ResumeDocument.find(window.ResumeEditor.current(), id).geometry || {}).x",
        setup["block"],
    )
    assert landed == setup["a4"], (
        f"the drag stopped at {landed}in — this document is A4, whose canvas ends at "
        f"{setup['a4']}in, and Letter's is {setup['letter']}in"
    )


def test_the_saved_job_picker_scrolls_and_leaves_the_menu_usable(page):
    """Forty stars must not push "Create version" out of the menu, and a click on a row's
    second line must still pick that row.

    Neither is answerable in `tests/js/resume_editor.test.js`. The DOM stub measures nothing, so
    "the footer is still on screen" is not a question it can be asked; and its `closest()` answers
    about the element it was called on and nothing above it, so a click that really lands on the
    `<span class="note">` inside the row button — which is most of the row's height — is a climb
    the stub never has to make.

    `saved_on=False` in the stub server, so the page's own `window.savedJobs` reports no list
    (the signed-out shape). The fixture replaces it, which is exactly the seam the editor reads.
    """
    page.evaluate("""() => {
      window.savedJobs = () => Array.from({length: 40}, (_, i) => ({
        job_id: 'greenhouse:acme:' + i, title: 'Backend Engineer ' + i, company: 'Company ' + i,
        url: 'https://example.test/' + i, location: 'Bengaluru', salary: '₹40L–₹60L',
        starred_at: '2026-09-' + String(40 - i).padStart(2, '0') + 'T09:00:00+00:00', open: true,
      }));
    }""")
    page.evaluate("() => document.getElementById('rb-version-new').click()")
    page.wait_for_timeout(150)

    box = page.evaluate("""() => {
      const pop = document.getElementById('rb-pop-version');
      const pick = document.getElementById('rb-version-jobs');
      const create = document.getElementById('rb-version-create');
      const r = el => { const b = el.getBoundingClientRect();
                        return {top: b.top, bottom: b.bottom, height: b.height}; };
      return {pop: r(pop), pick: r(pick), create: r(create),
              rows: pick.querySelectorAll('[data-saved]').length,
              scrolls: pick.scrollHeight > pick.clientHeight + 1,
              viewport: window.innerHeight};
    }""")

    assert box["rows"] == 40, (
        f"the picker drew {box['rows']} rows, not the 40 it was handed"
    )
    assert box["scrolls"], (
        "the picker is not scrolling its own rows, so forty stars grow the menu instead"
    )
    assert box["create"]["bottom"] <= box["pop"]["bottom"] + 1, (
        f"Create version ends {box['create']['bottom']}px down and the menu ends at "
        f"{box['pop']['bottom']}px — the button the whole menu exists for is outside it"
    )
    assert box["create"]["bottom"] <= box["viewport"], (
        f"Create version is {box['create']['bottom'] - box['viewport']}px below the fold"
    )

    # And on a phone, where the menu's own cap is 60vh rather than 520px and there is far less
    # room for the list to give back. 390x844 is the viewport the popover's max-height comment
    # already cites as the one it fits whole on.
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(150)
    phone = page.evaluate("""() => {
      const r = id => document.getElementById(id).getBoundingClientRect();
      return {pop: r('rb-pop-version').bottom, create: r('rb-version-create').bottom};
    }""")
    assert phone["create"] <= phone["pop"] + 1, (
        f"on a 390x844 phone Create version ends {phone['create']}px down and the menu at "
        f"{phone['pop']}px"
    )
    page.set_viewport_size({"width": 1500, "height": 1100})
    page.wait_for_timeout(150)

    # The click a real pointer makes: on the row's own second line, not on the button's padding.
    picked = page.evaluate("""() => {
      const note = document.querySelector('#rb-version-jobs [data-saved] .note');
      note.click();
      return {name: document.getElementById('rb-version-name').value,
              marked: document.querySelectorAll('#rb-version-jobs [aria-pressed="true"]').length};
    }""")
    assert picked["name"] == "Company 0 · Backend Engineer 0", (
        f"clicking inside a row named the version {picked['name']!r}"
    )
    assert picked["marked"] == 1, "the clicked row is not the one marked as chosen"

"""Solve DataDome's "slide right to secure your access" challenge with a humanized drag.

The challenge renders in an out-of-process iframe (geo.captcha-delivery.com), so we attach to
that OOPIF target to locate the slider handle, then drag it to the track end at PAGE-level
coordinates — CDP input dispatched at the browser reaches the OOPIF even cross-origin. The
drag is non-linear (ease-in/out) with jitter and realistic timing so DataDome's trajectory
check accepts it. This is the simple slide-to-end variant (no jigsaw offset to compute), so
we overshoot rightward and let the handle clamp at the end.

Honest limit: solving the slider does NOT fix IP trust. A distrusted WARP IP may immediately
re-challenge or mint a low-trust cookie. Treat success as best-effort.

See experiment/wellfound-datadome/LOG.md.
"""

import asyncio
import json
import random
import re
import urllib.request
from datetime import datetime
from pathlib import Path

from pydoll.browser.tab import Tab
from pydoll.commands.input_commands import InputCommands
from pydoll.protocol.input.types import MouseButton, MouseEventType, PointerType

try:  # audio fallback dep — offline STT; optional so the module loads without it
    from faster_whisper import WhisperModel
except Exception:  # noqa: BLE001
    WhisperModel = None

_NUMWORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def _clean_transcript(text: str) -> str:
    """Whisper output -> the digit string DataDome expects ("3 4 4 9 4 6" -> "344946").

    The clip reads its own instruction aloud before the digits, so Whisper returns something
    like "Please type the numbers you hear. 3 4 4 9 4 6". Mapping number-words to digits and
    concatenating every token submitted `pleasetypethenumbersyouhear344946` and failed the
    challenge. The answer is only ever digits, so drop everything else — which also discards
    any stray filler Whisper hallucinates between them.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    mapped = "".join(_NUMWORDS.get(w, w) for w in words)
    return re.sub(r"\D", "", mapped)


def audio_ready() -> tuple[bool, str]:
    """(ready, human-readable status) for the audio-challenge fallback.

    Reported at scrape startup rather than discovered mid-challenge: without it the escalation
    drops straight to the slider drag, and when that misses the run stalls on a 300s wait for a
    human. Also reports whether the Whisper weights are already cached — an uncached model
    downloads on first use, i.e. exactly while a challenge is on screen and timing matters.
    """
    if WhisperModel is None:
        return False, "NOT set up — run: pip install faster-whisper"
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    cached = any(cache.glob("models--Systran--faster-whisper-tiny*")) or any(
        cache.glob("models--guillaumekln--faster-whisper-tiny*")
    )
    if not cached:
        return True, "ready (model not cached yet — first solve downloads ~75MB)"
    return True, "ready (model cached)"


_WHISPER = None  # cache the model so we load (and download) it at most once


def _transcribe(mp3_path: str) -> str:
    """Blocking: load (cached) Whisper-tiny and transcribe. Call via asyncio.to_thread so the
    model download/inference can't freeze the event loop (the bug that wedged the first sweep)."""
    global _WHISPER
    if _WHISPER is None:
        _WHISPER = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = _WHISPER.transcribe(mp3_path)
    return _clean_transcript("".join(s.text for s in segments))


# The DataDome slider widget is injected dynamically (often into a nested, same-origin iframe),
# so static selectors miss it (the old code fell through to the wrong button). This JS runs in
# the captcha OOPIF, descends into any same-origin nested iframe, finds the draggable handle and
# its track, and returns their geometry in the OOPIF's top-viewport coordinates.
_SLIDER_JS = r"""
// Handle selectors in PRIORITY order, most-specific first. This must not collapse into one
// querySelector(): that returns the first match in DOCUMENT order, and DataDome lays the
// container out as sliderbg -> sliderMask -> sliderTarget -> slider, so the grouped selector
// resolved to `.sliderMask` — which is `display:none` until the drag starts, measures 0x0, and
// failed the size check while the real handle (`.slider`, 63x40, cursor:grab) sat right after it
// and was never reached. Deterministic miss, every single time.
const HANDLES=['.slider','.geetest_slider_button','.geetest_slice','[class*="handle"]','[class*="control"]','[class*="btn"]','.sliderMask'];
function pickHandle(c){
  for(const sel of HANDLES){
    for(const h of c.querySelectorAll(sel)){
      const hr=h.getBoundingClientRect();
      if(hr.width>=10&&hr.height>=10) return hr;   // first VISIBLE candidate wins
    }
  }
  return null;
}
function find(doc){
  const conts=doc.querySelectorAll('.sliderContainer,#ddv1-captcha-container,.geetest_slider,.geetest_slider_box,.slideTrack,.slider');
  for(const c of conts){
    const cr=c.getBoundingClientRect();
    if(cr.width<40||cr.height<10) continue;
    const hr=pickHandle(c);
    if(hr)
      return {hx:hr.left,hy:hr.top,hw:hr.width,hh:hr.height,trackLeft:cr.left,trackRight:cr.right,tw:cr.width};
  }
  return null;
}
function search(){
  let r=find(document); if(r) return r;
  for(const f of document.querySelectorAll('iframe')){
    try{const fd=f.contentDocument; if(!fd) continue;
      const fr=f.getBoundingClientRect(); const rr=find(fd);
      if(rr){rr.hx+=fr.left;rr.hy+=fr.top;rr.trackLeft+=fr.left;rr.trackRight+=fr.left;return rr;}
    }catch(e){}
  }
  return null;
}
return search();
"""

# Widget state, sampled either side of a drag. The decisive field is the handle's `left` and the
# mask's width: if they are unchanged after a drag, the input never reached the widget (our
# coordinates or dispatch are wrong). If they DID move and the challenge still stands, the drag
# landed and DataDome scored it and rejected it — two very different bugs that the logs could not
# previously tell apart.
_STATE_JS = r"""
return (function(){
  const q = s => document.querySelector(s);
  const r = e => { if(!e) return null; const b=e.getBoundingClientRect();
                   return {left:Math.round(b.left), top:Math.round(b.top),
                           width:Math.round(b.width), height:Math.round(b.height)}; };
  const el = s => { const e=q(s); return e ? {rect:r(e), cls:e.className,
                    style:e.getAttribute('style')||''} : null; };
  return {
    url: location.href,
    container: el('.sliderContainer'),
    handle:    el('.slider'),
    mask:      el('.sliderMask'),
    target:    el('.sliderTarget'),
    sliderText: (q('.sliderText')||{}).innerText || null,
    toast: (q('.toast')||{}).innerText || null,
    panels: {puzzle:(q('#captcha__puzzle')||{}).className||null,
             bottom:(q('#captcha__frame__bottom')||{}).className||null,
             audio:(q('#captcha__audio')||{}).className||null},
    canvases: [...document.querySelectorAll('canvas')].map(c=>({w:c.width,h:c.height})),
    bodyText: (document.body ? document.body.innerText : '').slice(0,1500)
  };
})();
"""


async def _capture(tab, oopif, outdir: Path, tag: str) -> dict | None:
    """Snapshot widget state + DOM + screenshot into `outdir` under `tag`. Never raises."""
    state = await _eval(oopif, _STATE_JS)
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        (outdir / f"{tag}-state.json").write_text(
            json.dumps(state, indent=1), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001, S110
        pass
    for obj, name in (
        (oopif, f"{tag}-captcha-dom.html"),
        (tab, f"{tag}-page-dom.html"),
    ):
        try:
            (outdir / name).write_text(await obj.page_source, encoding="utf-8")
        except Exception:  # noqa: BLE001, S110
            pass
    try:
        await tab.take_screenshot(path=str(outdir / f"{tag}.png"))
    except Exception:  # noqa: BLE001, S110
        pass
    return state


def _moved(before: dict | None, after: dict | None) -> str:
    """Did the drag actually shift the widget? The single most diagnostic line in the capture."""
    if not before or not after:
        return "unknown (state capture failed)"
    bh = (before.get("handle") or {}).get("rect") or {}
    ah = (after.get("handle") or {}).get("rect") or {}
    bm = (before.get("mask") or {}).get("rect") or {}
    am = (after.get("mask") or {}).get("rect") or {}
    dx = (ah.get("left", 0) or 0) - (bh.get("left", 0) or 0)
    dw = (am.get("width", 0) or 0) - (bm.get("width", 0) or 0)
    verdict = (
        "INPUT NEVER REACHED THE WIDGET (coords/dispatch wrong)"
        if dx == 0 and dw == 0
        else "drag landed — widget moved, so DataDome scored and rejected it"
    )
    return f"handle moved {dx}px, mask grew {dw}px -> {verdict}"


# Side-effect JS: switch BACK to the image/slider tab after the audio attempt.
# Raw string: the JS regex below contains \s, which Python would otherwise read as an escape.
_IMAGE_TAB_JS = r"""
// Symmetric to _AUDIO_TAB_JS: click the real toggle by id rather than "first element that
// doesn't look audio-ish", which resolved by document order and was only ever correct by luck.
// This became load-bearing the moment the audio path started genuinely switching panels — until
// then the widget never left the visual tab, so a no-op here was invisible.
// Only click when audio is actually the active panel: these toggles are stateful, so clicking
// the already-active one risks toggling the slider away rather than to it.
const audio = document.querySelector('#captcha__audio');
const audioActive = audio && /(^|\s)toggled(\s|$)/.test(audio.className || '');
if (audioActive) {
  const b = document.querySelector('#captcha__puzzle__button')
        || [...document.querySelectorAll('button.captcha-toggle')].find(
             e => /visual|image|puzzle/i.test((e.getAttribute('title')||'')+' '+(e.getAttribute('aria-label')||'')));
  if (b) b.click();
}
"""


async def _eval(frame, js):
    """Run JS in a frame, return its value (or None). Mirrors pydoll's response nesting.

    Failures are printed, not swallowed: a thrown JS error, a detached target and a genuine
    "not found" all used to collapse into the same silent None, so the caller could only ever
    report "handle not locatable" — which is what hid the selector bug above.
    """
    try:
        r = await frame.execute_script(js, return_by_value=True)
    except Exception as e:  # noqa: BLE001
        print(f"    [eval] call failed: {type(e).__name__}: {e}", flush=True)
        return None
    details = r.get("result", {}).get("exceptionDetails")
    if details:
        print(f"    [eval] JS threw: {details.get('text')}", flush=True)
        return None
    try:
        return r["result"]["result"]["value"]
    except (KeyError, TypeError):
        print(f"    [eval] unexpected response shape: {str(r)[:160]}", flush=True)
        return None


def challenged(src: str) -> bool:
    return "captcha-delivery.com" in src


async def _safe_source(tab) -> str:
    """tab.page_source intermittently raises (KeyError mid-navigation); retry, '' if all fail.
    A single flaky read must never crash a long sweep."""
    for _ in range(5):
        try:
            return await tab.page_source
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.5)
    return ""


async def _attach_captcha_frame(tab, browser):
    """Attach a Tab to the DataDome OOPIF target so we can read its DOM."""
    for t in await browser.get_targets():
        if "captcha-delivery" in str(t.get("url", "")):
            return Tab(
                browser, target_id=t["targetId"], connection_port=tab._connection_port
            )
    return None


async def _oopif_offset(tab) -> tuple[float, float]:
    """Page offset of the captcha OOPIF iframe (≈0,0 when it fills the viewport).

    OOPIF-relative coordinates have to be shifted by this to become main-page coordinates that
    `tab`'s mouse dispatch will land on.
    """
    try:
        frames = await tab.find(tag_name="iframe", find_all=True, raise_exc=False) or []
        for el in frames if isinstance(frames, list) else [frames]:
            if "captcha-delivery" in (el.get_attribute("src") or ""):
                b = await el.get_bounds_using_js()
                return b.get("x", 0) or 0, b.get("y", 0) or 0
    except Exception:  # noqa: BLE001, S110
        pass
    return 0.0, 0.0


_RECT_JS = """
return (function(){
  const e = document.querySelector(%r);
  if(!e) return null;
  const b = e.getBoundingClientRect();
  if(b.width < 2 || b.height < 2) return null;
  return {x:b.left + b.width/2, y:b.top + b.height/2};
})();
"""


async def _click_trusted(tab, oopif, selector: str) -> bool:
    """Click an element in the captcha frame with REAL mouse input, not JS `.click()`.

    A scripted `.click()` dispatches an event with `isTrusted: false`. DataDome's own tag checks
    `isTrusted` (it is why _human_pause uses CDP input, and why the pressure artifact mattered),
    so its UI handlers ignore synthetic clicks — which is why toggling to the audio panel silently
    did nothing and the answer boxes stayed `display:none`. Resolve the element's centre, shift it
    by the OOPIF offset, and dispatch a genuine press/release there.
    """
    rect = await _eval(oopif, _RECT_JS % selector)
    if not rect:
        return False
    ox, oy = await _oopif_offset(tab)
    x, y = ox + rect["x"], oy + rect["y"]
    await _mouse(tab, MouseEventType.MOUSE_MOVED, x, y)
    await asyncio.sleep(0.05 + random.random() * 0.10)
    await _mouse(
        tab,
        MouseEventType.MOUSE_PRESSED,
        x,
        y,
        force=_PRESSED,
        button=MouseButton.LEFT,
        clicks=1,
    )
    await asyncio.sleep(0.04 + random.random() * 0.08)
    await _mouse(
        tab, MouseEventType.MOUSE_RELEASED, x, y, button=MouseButton.LEFT, clicks=1
    )
    return True


async def _slider_geometry(tab, oopif):
    """Locate the dynamically-rendered slider; return page-coord (sx, sy, ex) or None.

    The JS returns handle+track geometry in the OOPIF's top-viewport coords (folding in any
    nested same-origin iframe offset); we then add the captcha OOPIF iframe's own page offset
    (≈0 when it fills the viewport) to get true main-page coordinates for tab.mouse.
    """
    geo = await _eval(oopif, _SLIDER_JS)
    if not geo:
        return None
    ox, oy = await _oopif_offset(tab)
    sx = ox + geo["hx"] + geo["hw"] / 2
    sy = oy + geo["hy"] + geo["hh"] / 2
    ex = (
        ox + geo["trackRight"] - geo["hw"] / 2 - 2
    )  # drag handle centre to the track's end
    return sx, sy, ex


_PRESSED = (
    0.5  # Pointer Events: a mouse with a button down reports pressure exactly 0.5
)


async def _mouse(tab, event, x, y, *, force=0.0, button=None, clicks=0):
    """Raw CDP mouse dispatch, so we can set `force` — pydoll's Mouse never does.

    DataDome hooks `pointerdown` capture-phase and stores the pressure as `m_pp` (see
    artifacts/datadome-tags.beautified.js):

        "mouse" === n.pointerType && 0 < n.buttons && t("m_pp", n.pressure)

    CDP defaults `force` to 0, and pydoll's `_dispatch_button` doesn't pass it, so every drag we
    made reported pressure 0 while claiming buttons=1. The Pointer Events spec reserves 0 for
    "no buttons down" and requires 0.5 for a pressed mouse — a combination real hardware cannot
    produce, so it identifies the input as synthetic no matter how good the trajectory is.
    Measured both ways by experiment/wellfound-datadome/probe_cdp_mouse_artifacts.py.
    """
    await tab._execute_command(
        InputCommands.dispatch_mouse_event(
            type=event,
            x=round(x),
            y=round(y),
            button=button,
            click_count=clicks,
            force=force,
            pointer_type=PointerType.MOUSE,
        )
    )


async def _humanized_drag(tab, sx, sy, ex, ey):
    """Press at (sx,sy), slide to (ex,ey) with ease-in/out + jitter, release.

    Every event goes through _mouse() rather than tab.mouse so the whole drag carries a
    spec-correct pressure: 0.5 while the button is held (press + each move), 0 on release.
    """
    await _mouse(tab, MouseEventType.MOUSE_MOVED, sx, sy)
    await asyncio.sleep(0.10 + random.random() * 0.20)
    await _mouse(
        tab,
        MouseEventType.MOUSE_PRESSED,
        sx,
        sy,
        force=_PRESSED,
        button=MouseButton.LEFT,
        clicks=1,
    )
    await asyncio.sleep(0.05 + random.random() * 0.12)
    steps = 35 + int(random.random() * 18)
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)  # smoothstep: accelerate then decelerate
        x = sx + (ex - sx) * ease + random.uniform(-1.5, 1.5)
        y = sy + (ey - sy) * ease + random.uniform(-2.0, 2.0)
        # buttons stays down through the slide, so pressure must stay 0.5 here too
        await _mouse(
            tab,
            MouseEventType.MOUSE_MOVED,
            x,
            y,
            force=_PRESSED,
            button=MouseButton.LEFT,
        )
        await asyncio.sleep(0.006 + random.random() * 0.020)
    fx, fy = ex + random.uniform(-2, 2), ey + random.uniform(-1, 1)
    await _mouse(
        tab, MouseEventType.MOUSE_MOVED, fx, fy, force=_PRESSED, button=MouseButton.LEFT
    )
    await asyncio.sleep(0.05 + random.random() * 0.12)
    # release: buttons returns to 0, so pressure returns to 0 (spec)
    await _mouse(
        tab, MouseEventType.MOUSE_RELEASED, fx, fy, button=MouseButton.LEFT, clicks=1
    )


async def _wait_for_manual(tab, secs: float) -> bool:
    """Pause for a human to solve the challenge in the visible browser; poll until cleared."""
    print(
        f"    >>> auto-solve failed — please solve the challenge in the browser window. "
        f"Waiting up to {int(secs)}s...",
        flush=True,
    )
    waited = 0.0
    while waited < secs:
        await asyncio.sleep(3)
        waited += 3
        if not challenged(await _safe_source(tab)):
            print(
                f"    manual solve detected after {int(waited)}s — continuing",
                flush=True,
            )
            return True
    print(
        "    no manual solve within the window — giving up on this challenge",
        flush=True,
    )
    return False


async def _try_slider(tab, browser, artifacts_dir, attempts: int) -> bool:
    """Dynamic humanized slider drag: switch to the image/slider tab, poll for the live-injected
    handle (across nested frames), and drag it to the track end at real page coords. Best-effort
    — if the widget can't be located, returns False so manual-wait takes over."""
    oopif = await _attach_captcha_frame(tab, browser)
    if oopif is None:
        return False
    if artifacts_dir:
        try:
            (Path(artifacts_dir) / "captcha-iframe-dom.html").write_text(
                await oopif.page_source, encoding="utf-8"
            )
        except Exception:  # noqa: BLE001, S110
            pass
    # audio-first may have left the audio tab active; switch back to the slider. Same reasoning
    # as the audio toggle: a real click, because the handler ignores untrusted ones.
    try:
        state = await _eval(oopif, _STATE_JS) or {}
        if "toggled" in ((state.get("panels") or {}).get("audio") or ""):
            if not await _click_trusted(tab, oopif, "#captcha__puzzle__button"):
                await oopif.execute_script(_IMAGE_TAB_JS)
            await asyncio.sleep(1.0)
    except Exception:  # noqa: BLE001, S110
        pass
    # One directory per solve, so a failure's evidence can't be overwritten by the next challenge.
    run_dir = (
        Path(artifacts_dir)
        / "failures"
        # local wall clock on purpose: this names a directory a human reads back
        / datetime.now().strftime("%Y-%m-%d_%H%M%S")  # noqa: DTZ005
        if artifacts_dir
        else None
    )
    for attempt in range(attempts):
        if not challenged(await _safe_source(tab)):
            return True
        coords = None
        for _ in range(8):  # the widget renders dynamically — poll for it to appear
            coords = await _slider_geometry(tab, oopif)
            if coords:
                break
            await asyncio.sleep(1.0)
        if not coords:
            print(
                f"    slider attempt {attempt + 1}: handle not locatable (dynamic widget)",
                flush=True,
            )
            if run_dir:
                await _capture(tab, oopif, run_dir, f"attempt{attempt + 1}-nohandle")
                print(f"    [capture] {run_dir}", flush=True)
            return False
        sx, sy, ex = coords
        print(
            f"    slider attempt {attempt + 1}: drag ({sx:.0f},{sy:.0f})->({ex:.0f}) [dynamic]",
            flush=True,
        )
        before = (
            await _capture(tab, oopif, run_dir, f"attempt{attempt + 1}-before")
            if run_dir
            else None
        )
        await _humanized_drag(
            tab, sx, sy, ex + random.uniform(-2, 2), sy + random.uniform(-2, 2)
        )
        await asyncio.sleep(2.5)
        if not challenged(await _safe_source(tab)):
            print(f"    slider cleared on attempt {attempt + 1}", flush=True)
            return True
        # Failed: record the post-drag state while it is still on screen, and say in one line
        # whether the widget even moved — the difference between "we missed" and "we were judged".
        if run_dir:
            after = await _capture(tab, oopif, run_dir, f"attempt{attempt + 1}-after")
            print(f"    [capture] {_moved(before, after)}", flush=True)
            try:
                (run_dir / f"attempt{attempt + 1}-drag.json").write_text(
                    json.dumps(
                        {"sx": sx, "sy": sy, "ex": ex, "attempt": attempt + 1}, indent=1
                    ),
                    encoding="utf-8",
                )
            except Exception:  # noqa: BLE001, S110
                pass
            print(f"    [capture] evidence -> {run_dir}", flush=True)
    return not challenged(await _safe_source(tab))


# The humanized slider drag is the default solver: it is 4-for-4 live since the pressure fix.
# The audio path is OFF by default because it has never once cleared a challenge — it still
# cannot open its own panel — so running it first only spends a Whisper transcription and a few
# seconds before falling through to the drag anyway. --audio-first re-enables it.
AUDIO_FIRST = False


async def solve_slider(
    tab,
    browser,
    artifacts_dir=None,
    try_audio: bool | None = None,
    drag_attempts: int | None = None,
    manual_wait_secs: float = 300,
) -> bool:
    """Clear a DataDome challenge.

    Default: humanized slider drag, with a manual wait in the visible browser as the backstop.
    With --audio-first: try audio + Whisper before the drag. `try_audio`/`drag_attempts`
    override the mode explicitly when passed.
    """
    if try_audio is None:
        try_audio = AUDIO_FIRST
    if drag_attempts is None:
        drag_attempts = 2
    if not challenged(await _safe_source(tab)):
        return True
    if not try_audio:
        print("    challenge hit — sliding (audio off)", flush=True)
    if try_audio:
        print("    challenge hit — trying audio + Whisper first...", flush=True)
        try:
            if await _solve_audio(tab, browser, artifacts_dir):
                print("    audio challenge cleared", flush=True)
                return True
        except Exception as e:  # noqa: BLE001
            # Print the message, not just the class: "audio path errored: AttributeError" gave
            # no way to tell which attribute, and hid the async-property bug above for two runs.
            print(f"    audio path errored: {type(e).__name__}: {e}", flush=True)
    if drag_attempts and challenged(await _safe_source(tab)):  # noqa: SIM102
        if await _try_slider(tab, browser, artifacts_dir, drag_attempts):
            return True
    if manual_wait_secs and challenged(await _safe_source(tab)):
        return await _wait_for_manual(tab, manual_wait_secs)
    return not challenged(await _safe_source(tab))


# Side-effect JS: switch to the audio tab (clicking the captcha's own UI doesn't need a
# trusted event — only the answer is validated). Returns nothing; we read state via find().
_AUDIO_TAB_JS = """
// Click the real toggle BUTTON. The old version scanned buttons AND divs and matched on
// innerHTML, so any ancestor container whose markup merely *contains* the audio panel matched
// first — it clicked a plain <div>, which does nothing. The panel stayed hidden and every
// subsequent element lookup failed with ElementNotVisible.
const b = document.querySelector('#captcha__audio__button')
      || [...document.querySelectorAll('button.captcha-toggle')].find(
           e => /audio/i.test((e.getAttribute('title')||'')+' '+(e.getAttribute('aria-label')||'')));
if (b) b.click();
"""

# Are the answer boxes actually on screen yet? The panel animates in after the toggle click.
_AUDIO_VISIBLE_JS = """
return (function(){
  const i=[...document.querySelectorAll('.audio-captcha-inputs')];
  return {total:i.length, visible:i.filter(e=>e.getBoundingClientRect().width>0).length};
})();
"""


async def _solve_audio(tab, browser, artifacts_dir=None) -> bool:
    """Switch to the audio challenge, transcribe with Whisper, submit the answer."""
    if WhisperModel is None:
        print(
            "    audio fallback unavailable — `pip install faster-whisper`", flush=True
        )
        return False
    frame = await _attach_captcha_frame(tab, browser)
    if frame is None:
        return False
    # Real mouse click first — DataDome's toggle handler ignores untrusted synthetic clicks.
    # The JS click stays as a fallback for markup variants without that id.
    if not await _click_trusted(tab, frame, "#captcha__audio__button"):
        try:
            await frame.execute_script(_AUDIO_TAB_JS)
        except Exception:  # noqa: BLE001, S110
            pass
    # Wait for the panel to actually be on screen. Proceeding blind is what produced
    # `ElementNotVisible` on every challenge: the answer boxes exist in the DOM from the start,
    # but sit inside #captcha__audio, which is display:none until the toggle marks it `toggled`.
    for _ in range(10):
        await asyncio.sleep(0.5)
        vis = await _eval(frame, _AUDIO_VISIBLE_JS) or {}
        if vis.get("visible"):
            break
    else:
        print("    audio: panel never became visible — falling through", flush=True)
        return False
    if (
        artifacts_dir
    ):  # dump audio-challenge DOM so selectors can be refined from real markup
        try:
            (Path(artifacts_dir) / "captcha-audio-dom.html").write_text(
                await frame.page_source, encoding="utf-8"
            )
        except Exception:  # noqa: BLE001, S110
            pass
    src = None
    for sel in ({"tag_name": "audio"}, {"tag_name": "source"}):
        el = await frame.find(**sel, raise_exc=False)
        if el and (src := el.get_attribute("src")):
            break
    if not src:
        print("    audio: no <audio> src found (DOM dumped for refinement)", flush=True)
        return False
    mp3 = Path(artifacts_dir or ".") / "captcha-audio.mp3"
    try:
        req = urllib.request.Request(
            src,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://geo.captcha-delivery.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: ASYNC210
            mp3.write_bytes(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"    audio download failed: {type(e).__name__}", flush=True)
        return False
    try:  # run blocking Whisper off the loop, bounded so a slow/hung model load can't freeze us
        answer = await asyncio.wait_for(
            asyncio.to_thread(_transcribe, str(mp3)), timeout=90
        )
    except Exception as e:  # noqa: BLE001
        print(f"    whisper failed/timed out: {type(e).__name__}", flush=True)
        return False
    print(f"    audio transcript -> {answer!r}", flush=True)
    if not answer:
        return False
    # The answer is NOT one text field. DataDome renders one box per digit —
    # `.audio-captcha-inputs`, maxlength="1", data-index 0..N — so the old
    # `find(tag_name="input")` + `insert_text(answer)` pushed a 6-char string at a 1-char box
    # (and `find` returned whichever input came first in the document, including the hidden
    # contact-form ones). Type one digit per box instead.
    boxes = await frame.find(
        class_name="audio-captcha-inputs", find_all=True, raise_exc=False
    )
    boxes = boxes if isinstance(boxes, list) else ([boxes] if boxes else [])
    if not boxes:
        print("    audio: no answer boxes found", flush=True)
        return False
    if len(answer) != len(boxes):
        # Guard rather than guess: a transcript that doesn't fill the boxes exactly is a
        # mis-transcription, and a partial fill leaves submit disabled anyway.
        print(
            f"    audio: transcript has {len(answer)} digits but {len(boxes)} boxes — skipping",
            flush=True,
        )
        return False
    for box, digit in zip(boxes, answer):
        await box.click()  # focus the box; these auto-advance on input
        await box.type_text(digit)
        await asyncio.sleep(0.06 + random.random() * 0.12)
    await asyncio.sleep(0.3 + random.random() * 0.4)
    # The submit button ships `disabled` and only enables once every box is filled, so it must
    # be clicked after the loop above, never before.
    btn = await frame.find(class_name="audio-captcha-submit-button", raise_exc=False)
    if btn is None:
        buttons = await frame.find(tag_name="button", raise_exc=False, find_all=True)
        buttons = (
            buttons if isinstance(buttons, list) else ([buttons] if buttons else [])
        )
        for b in buttons:
            # pydoll declares `text` as an ASYNC property, so `b.text` is a coroutine, not a
            # str; without the await this raised AttributeError and killed the loop before any
            # click, leaving a correct answer typed but never submitted.
            label = ((await b.text) or "").lower()
            if any(k in label for k in ("submit", "verify", "check", "confirm")):
                btn = b
                break
    if btn is None:
        print("    audio: no submit button found", flush=True)
        return False
    await btn.click()
    await asyncio.sleep(3)
    return not challenged(await _safe_source(tab))

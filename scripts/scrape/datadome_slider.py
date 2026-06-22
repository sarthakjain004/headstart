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
import random
import re
import urllib.request
from pathlib import Path

from pydoll.browser.tab import Tab

try:  # audio fallback dep — offline STT; optional so the module loads without it
    from faster_whisper import WhisperModel
except Exception:
    WhisperModel = None

_NUMWORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
             "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _clean_transcript(text: str) -> str:
    """DataDome audio is a short digit/char string; normalise Whisper output to that."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "".join(_NUMWORDS.get(w, w) for w in words)


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
function find(doc){
  const conts=doc.querySelectorAll('.sliderContainer,#ddv1-captcha-container,.geetest_slider,.geetest_slider_box,.slideTrack,.slider');
  for(const c of conts){
    const cr=c.getBoundingClientRect();
    if(cr.width<40||cr.height<10) continue;
    const h=c.querySelector('.slider,.sliderMask,.geetest_slider_button,.geetest_slice,[class*="handle"],[class*="control"],[class*="btn"]');
    if(h){const hr=h.getBoundingClientRect();
      if(hr.width>=10&&hr.height>=10)
        return {hx:hr.left,hy:hr.top,hw:hr.width,hh:hr.height,trackLeft:cr.left,trackRight:cr.right,tw:cr.width};}
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

# Side-effect JS: switch to the image/slider tab (needed if audio-first switched away).
_IMAGE_TAB_JS = """
const tg=[...document.querySelectorAll('.captcha-toggle,button,[role=button],[role=tab]')];
const img=tg.find(e=>!/audio|sound|volume/i.test(
  ((e.getAttribute&&(e.getAttribute('aria-label')||e.getAttribute('title')))||'')+' '+(e.textContent||'')))||tg[0];
if(img) img.click();
"""


async def _eval(frame, js):
    """Run JS in a frame, return its value (or None). Mirrors pydoll's response nesting."""
    try:
        r = await frame.execute_script(js, return_by_value=True)
        return r["result"]["result"]["value"]
    except Exception:
        return None


def challenged(src: str) -> bool:
    return "captcha-delivery.com" in src


async def _safe_source(tab) -> str:
    """tab.page_source intermittently raises (KeyError mid-navigation); retry, '' if all fail.
    A single flaky read must never crash a long sweep."""
    for _ in range(5):
        try:
            return await tab.page_source
        except Exception:
            await asyncio.sleep(0.5)
    return ""


async def _attach_captcha_frame(tab, browser):
    """Attach a Tab to the DataDome OOPIF target so we can read its DOM."""
    for t in await browser.get_targets():
        if "captcha-delivery" in str(t.get("url", "")):
            return Tab(browser, target_id=t["targetId"],
                       connection_port=tab._connection_port)
    return None


async def _slider_geometry(tab, oopif):
    """Locate the dynamically-rendered slider; return page-coord (sx, sy, ex) or None.

    The JS returns handle+track geometry in the OOPIF's top-viewport coords (folding in any
    nested same-origin iframe offset); we then add the captcha OOPIF iframe's own page offset
    (≈0 when it fills the viewport) to get true main-page coordinates for tab.mouse.
    """
    geo = await _eval(oopif, _SLIDER_JS)
    if not geo:
        return None
    ox = oy = 0.0
    try:
        frames = await tab.find(tag_name="iframe", find_all=True, raise_exc=False) or []
        for el in (frames if isinstance(frames, list) else [frames]):
            if "captcha-delivery" in (el.get_attribute("src") or ""):
                b = await el.get_bounds_using_js()
                ox, oy = b.get("x", 0) or 0, b.get("y", 0) or 0
                break
    except Exception:
        pass
    sx = ox + geo["hx"] + geo["hw"] / 2
    sy = oy + geo["hy"] + geo["hh"] / 2
    ex = ox + geo["trackRight"] - geo["hw"] / 2 - 2  # drag handle centre to the track's end
    return sx, sy, ex


async def _humanized_drag(tab, sx, sy, ex, ey):
    """Press at (sx,sy), slide to (ex,ey) with ease-in/out + jitter, release."""
    await tab.mouse.move(sx, sy)
    await asyncio.sleep(0.10 + random.random() * 0.20)
    await tab.mouse.down()
    await asyncio.sleep(0.05 + random.random() * 0.12)
    steps = 35 + int(random.random() * 18)
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)  # smoothstep: accelerate then decelerate
        x = sx + (ex - sx) * ease + random.uniform(-1.5, 1.5)
        y = sy + (ey - sy) * ease + random.uniform(-2.0, 2.0)
        await tab.mouse.move(x, y)
        await asyncio.sleep(0.006 + random.random() * 0.020)
    await tab.mouse.move(ex + random.uniform(-2, 2), ey + random.uniform(-1, 1))
    await asyncio.sleep(0.05 + random.random() * 0.12)
    await tab.mouse.up()


async def _wait_for_manual(tab, secs: float) -> bool:
    """Pause for a human to solve the challenge in the visible browser; poll until cleared."""
    print(f"    >>> auto-solve failed — please solve the challenge in the browser window. "
          f"Waiting up to {int(secs)}s...", flush=True)
    waited = 0.0
    while waited < secs:
        await asyncio.sleep(3)
        waited += 3
        if not challenged(await _safe_source(tab)):
            print(f"    manual solve detected after {int(waited)}s — continuing", flush=True)
            return True
    print("    no manual solve within the window — giving up on this challenge", flush=True)
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
                await oopif.page_source, encoding="utf-8")
        except Exception:
            pass
    try:  # audio-first may have left the audio tab active; switch back to the slider
        await oopif.execute_script(_IMAGE_TAB_JS)
        await asyncio.sleep(1.0)
    except Exception:
        pass
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
            print(f"    slider attempt {attempt + 1}: handle not locatable (dynamic widget)",
                  flush=True)
            return False
        sx, sy, ex = coords
        print(f"    slider attempt {attempt + 1}: drag ({sx:.0f},{sy:.0f})->({ex:.0f}) [dynamic]",
              flush=True)
        await _humanized_drag(tab, sx, sy, ex + random.uniform(-2, 2), sy + random.uniform(-2, 2))
        await asyncio.sleep(2.5)
        if not challenged(await _safe_source(tab)):
            print(f"    slider cleared on attempt {attempt + 1}", flush=True)
            return True
    return not challenged(await _safe_source(tab))


async def solve_slider(tab, browser, artifacts_dir=None, try_audio: bool = True,
                       drag_attempts: int = 2, manual_wait_secs: float = 300) -> bool:
    """Clear a DataDome challenge. Escalation, audio-first (the audio markup is in the DOM;
    the slider widget is dynamic): audio + Whisper -> dynamic slider drag (frame-walking,
    real page coords) -> wait for a manual solve in the browser. Name kept for callers' imports.
    """
    if not challenged(await _safe_source(tab)):
        return True
    if try_audio:
        print("    challenge hit — trying audio + Whisper first...", flush=True)
        try:
            if await _solve_audio(tab, browser, artifacts_dir):
                print("    audio challenge cleared", flush=True)
                return True
        except Exception as e:
            print(f"    audio path errored: {type(e).__name__}", flush=True)
    if drag_attempts and challenged(await _safe_source(tab)):
        if await _try_slider(tab, browser, artifacts_dir, drag_attempts):
            return True
    if manual_wait_secs and challenged(await _safe_source(tab)):
        return await _wait_for_manual(tab, manual_wait_secs)
    return not challenged(await _safe_source(tab))


# Side-effect JS: switch to the audio tab (clicking the captcha's own UI doesn't need a
# trusted event — only the answer is validated). Returns nothing; we read state via find().
_AUDIO_TAB_JS = """
const els=[...document.querySelectorAll('button,[role=button],[role=tab],a,div')];
const t=els.find(e=>/audio|sound|volume/i.test(
    (e.getAttribute&&(e.getAttribute('aria-label')||e.getAttribute('title')||''))
    + ' ' + (e.textContent||'') + ' ' + (e.innerHTML||'')));
if(t) t.click();
"""


async def _solve_audio(tab, browser, artifacts_dir=None) -> bool:
    """Switch to the audio challenge, transcribe with Whisper, submit the answer."""
    if WhisperModel is None:
        print("    audio fallback unavailable — `pip install faster-whisper`", flush=True)
        return False
    frame = await _attach_captcha_frame(tab, browser)
    if frame is None:
        return False
    try:
        await frame.execute_script(_AUDIO_TAB_JS)
    except Exception:
        pass
    await asyncio.sleep(2)
    if artifacts_dir:  # dump audio-challenge DOM so selectors can be refined from real markup
        try:
            (Path(artifacts_dir) / "captcha-audio-dom.html").write_text(
                await frame.page_source, encoding="utf-8")
        except Exception:
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
        req = urllib.request.Request(src, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://geo.captcha-delivery.com/"})
        with urllib.request.urlopen(req, timeout=20) as r:
            mp3.write_bytes(r.read())
    except Exception as e:
        print(f"    audio download failed: {type(e).__name__}", flush=True)
        return False
    try:  # run blocking Whisper off the loop, bounded so a slow/hung model load can't freeze us
        answer = await asyncio.wait_for(asyncio.to_thread(_transcribe, str(mp3)), timeout=90)
    except Exception as e:
        print(f"    whisper failed/timed out: {type(e).__name__}", flush=True)
        return False
    print(f"    audio transcript -> {answer!r}", flush=True)
    if not answer:
        return False
    inp = await frame.find(tag_name="input", raise_exc=False)
    if not inp:
        print("    audio: no input field found", flush=True)
        return False
    await inp.insert_text(answer)
    await asyncio.sleep(0.4 + random.random() * 0.4)
    buttons = await frame.find(tag_name="button", raise_exc=False, find_all=True)
    buttons = buttons if isinstance(buttons, list) else ([buttons] if buttons else [])
    for b in buttons:
        if any(k in (b.text or "").lower() for k in ("submit", "verify", "check", "confirm")):
            await b.click()
            break
    await asyncio.sleep(3)
    return not challenged(await _safe_source(tab))

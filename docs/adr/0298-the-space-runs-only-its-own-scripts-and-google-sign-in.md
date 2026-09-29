# ADR-0298: The Space runs only its own scripts and Google's sign-in

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0042](0042-signed-in-ui-saved-sets.md) (the sign-in wall and Google Sign-In),
[ADR-0112](0112-the-door-earns-the-sign-in-before-it-asks.md) (the door, which inlines its own styles),
[ADR-0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md) (the
preload script in the page's head), [ADR-0262](0262-a-caller-with-no-session-reads-the-public-routes-sixty-times-a-minute.md)
(the one-process Space)

## Context

Issue #595, from a read-only security audit, found that the Space set no security headers. #785
added `X-Content-Type-Options: nosniff`, a referrer policy and `frame-ancestors 'self'
https://huggingface.co`, and left the rest of a Content-Security-Policy open. That part needed an
inventory of what the page loads and runs first.

The inventory, read from the templates and static scripts on 2026-09-29:

- **Inline scripts:** four. Three are in `base.html` (the ADR-0251 preload, the sidebar's folded
  state before first paint, `window.CFG`) and one is the door's script in `signin.html`.
- **Inline event handlers:** nineteen. Thirteen are in templates: nine `onclick` attributes and
  the `onload`/`onerror` pair on each of the two Google Sign-In script tags. Six are in HTML that
  `app.js` builds: the filter pills, the pager, and the empty result's advice.
- **Third-party:** Google Sign-In (`accounts.google.com/gsi/client`), on the door and, when
  alerts are configured, on the page. Nothing else: no fonts, images or fetches leave the origin.
- **Styles:** the door's `<style>` block, style attributes in templates and in HTML built by
  `app.js`, and the résumé builder's styles. The builder writes `<style>` elements for its preview
  and its template gallery, style attributes in three layouts, and a `<style>` into the
  `about:blank` frame it prints from. That frame inherits the page's policy. Google Sign-In also
  injects a `<style id="googleidentityservice_button_styles">` into the page (measured below).

Google's sign-in guide ("Content Security Policy", last updated 2026-04-06, read 2026-09-29)
names `script-src https://accounts.google.com/gsi/client`, `frame-src
https://accounts.google.com/gsi/`, `connect-src https://accounts.google.com/gsi/` and `style-src
https://accounts.google.com/gsi/style`. It says nothing about nonces or `'strict-dynamic'`.

## Decision

Every response carries this policy. Only an HTML page's response has a nonce, a new one each time:

```
default-src 'self';
script-src 'self' https://accounts.google.com/gsi/client 'nonce-<per response>';
style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style;
connect-src 'self' https://accounts.google.com/gsi/;
frame-src https://accounts.google.com/gsi/;
object-src 'none'; base-uri 'none'; form-action 'self';
frame-ancestors 'self' https://huggingface.co
```

- **Scripts.** A script may come from the Space's own files, from Google's sign-in library, or
  from an inline `<script>` that carries the response's nonce. The four inline scripts carry
  `nonce="{{ csp_nonce }}"`, and a context processor makes one nonce per response
  (`secrets.token_urlsafe(16)`). No inline handler runs. Every control is now wired by
  `addEventListener`. HTML that `app.js` builds carries `data-drop-filter`, `data-goto-page` or
  `data-match-by-meaning`, and one document listener reads them. Google's script tag has an id
  and no attributes. Its `load` and `error` events do not bubble, so a capturing listener on the
  document hears them. The listener is registered before the tag is parsed: in `app.js` for the
  page, and in the door's own script for the door.
- **`'self'` in `script-src` is safe here.** The origin serves no script a caller controls. Its
  other answers are JSON or HTML sent with `nosniff`, and the browser refuses to run them as
  scripts. Measured: a `<script src="/me">` injected under this policy was refused because its
  MIME type (`application/json`) is not executable.
- **Styles keep `'unsafe-inline'`.** The door's block, every style attribute, the résumé
  builder's `<style>` elements, its print frame and Google's injected button styles all depend on
  it. A nonce in `style-src` would switch `'unsafe-inline'` off. Google's script writes its
  `<style>` itself, so that style would then apply only if Google copied our nonce onto it, which
  was not tested. An injected style cannot run code, so the XSS containment rests on
  `script-src`.
- **Google's origins as its guide lists them.** The host-and-path sources in `script-src` and
  `style-src`, and the `/gsi/` prefix in `connect-src` and `frame-src`.
- **The page's own guard.** A test renders the door and the signed-in page and checks that every
  inline script carries that response's nonce, that no two responses share one, and that no
  element has an inline handler. A second test scans every template and every static script,
  including templates only some deployments render, for an inline handler or a script without
  the nonce.

### Measured, 2026-09-29

The real `app.py` (heavy dependencies stubbed, as its tests do), with the live Trends history
and Hiring now ranking pulled from the dataset, was served through waitress and driven in
headless Chromium with the policy enforced. The page recorded every `securitypolicyviolation`
event.

| | violations | page errors |
|---|---|---|
| The door, signed out: Google's script loaded and `initSignin` rendered its button | 0 | 0 |
| Signed in, every tab opened: Home, Search, Matches, Saved, Hiring now, Trends, Résumé, Profile | 0 | 0 |

Inside Search, an example chip, the Search button, the theme button, Save this search, dropping
a Company pill, the pager's Next (it asked for page 2) and the empty result's "remove it" all
did what their inline handlers did. The Résumé tab's PDF export wrote its print frame, and that
frame's `<style>` applied (body margin `0px`, the sheet white). As a control, an injected
`<button onclick>` was refused (`script-src-attr`) and did not run. `scripts/eval/ui_smoke.py`
passed at 1280 px and 390 px.

## Alternatives considered

- **`'strict-dynamic'` with a nonce on every script tag** (Google's generic strict-CSP
  recipe). It stops trusting host allowlists, so all 21 external script tags (20 on the page, 1
  on the door) would need the nonce, and so would anything they insert. It would protect against a script
  gadget on an allowlisted host. Here that host is our own origin, which serves no
  caller-controlled script, plus a single path on accounts.google.com. Google's sign-in guide
  documents the allowlist, not this recipe. Rejected as more surface for no measured gain.
- **Styles by nonce or hash, or moved into stylesheets.** Google's injected `<style>` would
  need our nonce, as above. Doing this for our own styles alone
  would mean threading a nonce through the résumé builder's renderer and into the print frame's
  document. That buys little, because style injection does not run code. Rejected.
- **`style-src-elem` with a nonce, and `style-src-attr 'unsafe-inline'`.** This splits the same
  problem in two, and Google's `<style>` still needs the nonce.
- **Ship it as `Content-Security-Policy-Report-Only` first.** The Space has no report endpoint,
  and agents cannot read its logs, so nothing would ever be read. The browser run above does
  the job a report period would, and the change can be reverted in one PR.

## Consequences

- A new inline `<script>` must carry `nonce="{{ csp_nonce }}"`, and a new control must be wired
  with `addEventListener`, never `on…=`. The tests above fail otherwise.
- A new third-party script, font, image or fetch needs its own source in this policy. Without
  one, the browser blocks it.
- `scripts/ui/serve.py`, the local renderer, sends no policy. Its templates render
  `nonce=""`, which is harmless without a policy, but a violation shows only on the Space. The
  tests above are the guard for local work.
- Found on the way, and not changed here: with alerts on and Saved sets on, `base.html` loads
  Google's script, but `search.html` renders no `#gsignin`. `initAlerts` then logs "Failed to
  render button because there is no parent". This happened before this change too.

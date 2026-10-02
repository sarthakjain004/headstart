# Visitor experience and presentation

HeadStart's useful journey is: understand the offering, find relevant Jobs, inspect the source,
keep a shortlist or search, prepare a résumé, then apply with the employer. The design should
make this path discoverable and predictable. The research, per-screen placement decisions,
expected marketing benefits and their limits are in
[the dated research report](../../../docs/product/2026-10-02_website-journey-design-research.md).
No HeadStart preference or conversion uplift has been measured.

## Placement contract

| Surface | Placement and purpose |
| --- | --- |
| Sign-in | After a visitor chooses to retain work, the sign-in action and email disclosure precede the longer source proof. Shared theme tokens and pre-paint appearance state keep it consistent with the app; authentication is unchanged. |
| Masthead | Linked brand left, session/theme utilities right, source promise below. Account entry is secondary to discovery. |
| Desktop navigation | Home; Find jobs: Search, Hiring now, Trends; Your workspace: Saved jobs, Saved searches, Résumé, Profile. Group headings explain how destinations relate. Sidebar folding remains available. |
| Compact navigation | Home, Search, Saved jobs and Résumé stay visible; More discloses Hiring now, Trends, Saved searches and Profile. One registry renders both surfaces, with capability gates. Bottom placement is a tested implementation choice, not a proven preference. |
| Home | Promise and labelled role search first; clickable examples and browse link; live-count/source proof; explanatory workflow; secondary tools; source/scope explanation and optional tour; limitations; final Find jobs action. |
| Search | Heading/query/matching mode; promoted Country, experience and Remote fields on compact screens; advanced disclosure or visible grouped desktop rail; save/sort controls, status and removable applied values before jobs; pager after jobs. |
| Results | Title opens the source; company/location and factual tags provide context; salary and labelled Search match align beside them; visible Save/Saved occupies a stable column. Company hiding/trends remain secondary. |
| Hiring now | Question/lenses, staffing option and dated window before ranking; each row offers See roles, then Follow, then See trend. |
| Trends | Company subject and open-role handoff first; selected question and controls before chart at every width; chart with legend, optional exact dates, table and explanation. Preserve counting/provenance behavior. |
| Saved searches | Select a search, then manage only the active search; sort/results are the main work, date refinement is optional. Rename/delete use a visible, named decision dialog. Successful saving links directly here. |
| Saved jobs | Shortlist and closed-status explanation; source/save actions on each Job; Find jobs is available when empty. |
| Profile | Manual preferences first; optional résumé read with disclosure before transfer; Save and Search after the fields; deletion in a separate management disclosure. |
| Résumé | Orientation, document/version utilities and persistent Download; Edit/Preview workspaces. Compact Edit keeps writing checks in a disclosure with its count visible; compact Preview offers Change template above the paper. Keep document and print geometry independently owned. |

The tutorial starts only when requested and remains available on Home and in navigation. It
opens the filter disclosure when demonstrating filters. Results do not stagger into view;
brief panel/state feedback never delays focus or requests. Reduced motion changes states
immediately. Shapes carry purpose: rounded rectangles for actions, pills for selected filters,
restrained surfaces for content, named danger actions at confirmation.

## Deep modules and their interfaces

The app remains Flask/Jinja and plain scripts. No framework or layout configuration language
is introduced.

- `navigation.html` owns the destination registry, task grouping, feature gates and two
  navigation surfaces. Hashes and panel IDs remain the existing ones. Only one surface is
  visible at a time; both derive labels and destinations from the same registry.
- `navigation.js` exposes `create({document, window, location, storage, onEnter})`, returning
  `current()`, `navigate(hash, {focus}?)`, and `start()`. Its implementation owns panel
  visibility, history events, menu/fold state, selected destinations, scroll and focus. It
  makes no transport requests. The browser and Node harness supply the same dependencies.
- `app.js` owns data/state and one `enterScreen` callback. Matches reruns, Saved/Profile
  reload, Trends reconciles its URL/history, and Résumé repaints when measurable. Cross-screen
  intents use the navigation interface; a profile handoff chooses meaning and clears an old
  company scope. Do not replace real freshness rules with a generic cache flag.
- `decisions.js` exposes `request({title, body, label?, value?, confirm, danger?})`, returning
  `{confirmed, value}` after the user's decision. It owns the native dialog, labels, focus,
  Escape and cancellation. Callers own writes; cancellation performs none.
- `search_layout.js` owns responsive placement of the actual common filter fields. It moves
  native nodes rather than cloning inputs, retaining values, labels and listeners through
  resize. `app.js` listens to both filter surfaces through the same behavior.
- `resume_workspace.js` keeps optional checks compact without hiding their summary/count,
  and restores focus to that summary when a resize closes it.
- `trends_layout.js` keeps the newly merged coverage summary before the desktop plot and
  after the compact plot, moving the native region so reading and focus order stay coherent.
- `home.js` only fills/submits the existing form for examples. Home has no second request or
  result-rendering path. `jobCard` remains the shared rendering implementation for all Jobs.

Authentication/access policy is owned by the Space renderer and its separately developed
change. These modules neither relax account-route protection nor implement authentication.

## Reskin without moving the layout

`base.html` loads the owning stylesheets in parallel through `url_for`, so each carries the
Space renderer’s boot version and benefits from its cache. `theme-tokens.css` and the
shared pre-paint template cover both the application and voluntary sign-in page. `style.css` and `resume.css` remain
compatibility entrypoints for standalone consumers; do not append overrides to either.

| Owner | Properties it owns |
| --- | --- |
| `app-layout.css`, `resume-layout.css`, `home-layout.css`, `signin-layout.css` | Structure, reading order, display/hidden state, dimensions, spacing, text metrics, structural transforms, border width/style, overflow and responsive behavior. |
| `theme-tokens.css`, `app-skin.css`, `resume-skin.css`, `home-theme.css`, `signin-skin.css` | Semantic colours, backgrounds, border colours, corner shapes, shadows, outlines and decorative appearance. |
| `app-motion.css`, `resume-motion.css`, `signin-motion.css` | Feedback timing and animations. Focus, requests and state do not wait for completion. |
| Résumé layout registry/export | The document's physical paper, typography, scaling, content and printing. Application skins do not own these. |

Mixed border shorthands were expanded before extraction; selector specificity, conditional
nesting and declaration order are retained. Font family/size, copy and border thickness can
change wrapping and are layout changes. Skin replacement is safe only within this ownership
contract; no arbitrary stylesheet can be guaranteed to preserve placement.

Existing `go`/`btn-primary`, `ghost` and `linkish` classes are the primary, secondary and tertiary
action vocabulary. New callers use these instead of inventing per-screen button treatments.
Principal controls use at least 44px touch targets; this is the design choice, not a claim
that WCAG AA requires 44px. Semantic state (`aria-current`, `aria-pressed`, expanded/hidden,
loading and disabled) stays in behavior/markup, so a reskin cannot redefine what a control does.

## Verification

Run Node behavior tests, Space renderer tests, résumé browser checks and the full journey tests:

```sh
node --test tests/js/*.test.js
PYTHONPATH=src python -m pytest tests/test_space_app.py tests/test_space_deploy_sync.py tests/test_resume_editor_browser.py tests/test_ui_experience_browser.py
PYTHONPATH=src python scripts/eval/ui_smoke.py
```

The journey tests exercise discovery, saving, cancellation, rename/delete, profile mode reset,
resize with filter values retained, transport recovery, company handoff, Trends and résumé
preview/download access. They also remove all application/editor skins and compare visible
HTML geometry on all eight screens at 375/768/1440px in light/dark. Existing résumé tests retain
physical-page and print checks. Fixtures establish interface behavior, not live service or
conversion performance. Additional user research is needed to choose top versus bottom
navigation and the exact promoted-filter priorities.

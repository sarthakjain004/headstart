# What makes users stick — the research, and what it means for HeadStart

**Date:** 2026-09-28 · **For:** GitHub issue #755, item 13 · **Code read at:** `19b8984b`
(`src/headstart/ui/templates/*.html`, `static/app.js`, `static/style.css`) · **No code changed.**

Issue #755 asked what makes people come back to a website and enjoy using it — one-click
interactions, where buttons and tabs go, how a UI feels seamless, and what Apple does. This
document answers that from primary sources (Apple's Human Interface Guidelines, Nielsen Norman
Group, Baymard Institute, Google's Material guidance, W3C and peer-reviewed HCI papers), then
walks the real app and turns the findings into prioritised, HeadStart-specific recommendations.
Other #755 work is already in flight, so every recommendation is marked with whether that work
covers it.

---

## 1. The short answer

People stay when three things happen quickly, and keep happening:

1. **They see value before they are asked for anything.** Visitors decide within about ten seconds
   whether a page is worth their time, and they form a visual impression in a twentieth of a second.
   Asking for a sign-in before showing a single result is the most expensive thing a first visit can
   do.
2. **Every action is cheap, visible and reversible.** The goal is not "one click" as such — click
   counts on their own predict nothing — but low total *interaction cost*: few decisions, controls
   where the eye already is, an immediate visible response, and an undo instead of a scare.
3. **There is a reason to return.** Job searching is episodic. People come back when something new
   is waiting for them and the product tells them so.

Apple's guidance says the same things in its own words. Its current principles (reintroduced in
June 2026) lead with **Purpose** ("create value", "keep focused") and **Agency** ("stay out of the
way", "help people recover from mistakes"). The older, more famous iOS themes — **clarity,
deference, depth** — are a visual expression of the same idea: the content is the product, and the
interface should step back.

**The five changes this research most supports that no current #755 batch covers** (section 6
has all of them, with evidence and effort):

| # | Change | Main principle |
| --- | --- | --- |
| A1 | Let a signed-out visitor see real results before the Google sign-in | Delay sign-in; value in the first 10 seconds |
| A2 | Preview a job inside HeadStart (an expandable description with "why it matched") before sending people to the employer's site | Progressive disclosure; cut round trips |
| A3 | Replace the silent "hide company" with an Undo toast | Forgiveness beats confirmation; visible system status |
| A4 | Recent searches and suggestions under the search box, and the example chips on phones | Recognition over recall; search suggestions |
| A5 | A return trigger: "N new since your last visit" on saved searches, plus open email alerts | Triggers drive return behaviour |

---

## 2. How this was researched

**Sources.** Apple's Human Interface Guidelines (HIG) were read from Apple's own documentation
data (the live pages, checked 2026-09-28), and the 2021 HIG from the Internet Archive for the
older themes. Nielsen Norman Group (NN/g), Baymard Institute, Laws of UX, Google's Material and
Android guidance, and W3C's WCAG 2.2 were read directly. Peer-reviewed papers were checked against
their DOI records. Sources are listed in section 9; claims below are paraphrased, not quoted at
length.

**Guideline search.** The `ui-ux-pro-max` UX-guideline database was searched for progressive
disclosure, loading feedback, empty states, navigation, filter chips, undo, onboarding, search
suggestions and touch targets. Its matches agree with the primary sources and are not cited
separately.

**The live walkthrough.** The app was run from this checkout with the local renderer
(`scripts/ui/serve.py`) and driven in headless Chromium at desktop (1440×900) and phone (390×844,
touch, coarse pointer) sizes. Every tab the local renderer serves was screenshotted, and one core
task was walked while counting clicks and watching which actions actually sent a search request.
The signed-out door of the live Space (`imposeidon-headstart-search.hf.space`) was screenshotted
at both sizes too.

Limits, stated so the numbers are read correctly:

- The local renderer serves **Search, Résumé and Data** only. Saved, Matches, Profile, Hiring now
  and Trends need Google sign-in on the Space, which a headless browser cannot complete, so they were
  read from source rather than walked. Saving a job (the star) could not be exercised for the same
  reason.
- The local index was a snapshot of **514,163 jobs dated 2026-09-23** (the live door reported
  533,799 on 2026-09-28). The data is stale; the UI code is current. No finding below depends on
  the data being fresh.
- Response times were measured on a local machine and **do not represent the Space**.

---

## 3. What the research says

### 3.1 The first ten seconds decide the visit

- A large log study of 2 billion page visits found that the chance of leaving a page is highest in
  the first seconds and falls after that: pages either lose people almost immediately or keep them
  for minutes ([Liu, White & Dumais, SIGIR 2010](https://doi.org/10.1145/1835449.1835513);
  summarised by [NN/g](https://www.nngroup.com/articles/how-long-do-users-stay-on-web-pages/)).
  NN/g's conclusion: the value proposition has to land in about ten seconds.
- People rate a page's visual appeal after seeing it for 50 milliseconds, and those snap ratings
  match what they say after looking longer
  ([Lindgaard et al., 2006](https://doi.org/10.1080/01449290500330448)).
- Apple: *delay sign-in for as long as possible* — people often abandon an app when forced to sign
  in before they can do anything useful; let them get a sense of what it does first, and ask for an
  account only if the core function needs one
  ([HIG: Managing accounts](https://developer.apple.com/design/human-interface-guidelines/managing-accounts)).
- NN/g's study of login walls found the same: when the benefit is not yet evident, a login wall
  stops people cold, and letting them browse first (a job marketplace is the article's own example)
  converts more of them later ([NN/g: Login walls](https://www.nngroup.com/articles/login-walls/)).

### 3.2 "One click" really means low interaction cost

- NN/g defines *interaction cost* as the total mental and physical effort — reading, scrolling,
  finding, clicking, typing, waiting, remembering — needed to reach a goal, and warns that fewer
  clicks is not automatically better: typing a few characters can cost less than scanning a long
  list ([NN/g: Interaction cost](https://www.nngroup.com/articles/interaction-cost-definition/)).
- The "three-click rule" has no supporting data; what predicts success is clear labels ("information
  scent") and each step being obvious ([NN/g: The 3-click rule](https://www.nngroup.com/articles/3-click-rule/)).
- **Hick's law:** decision time grows with the number and complexity of choices
  ([Laws of UX](https://lawsofux.com/hicks-law/)). **Fitts's law:** the time to hit a target depends
  on its distance and size ([Laws of UX](https://lawsofux.com/fittss-law/)). Put frequent controls
  close to where attention already is, and make them big enough.
- Drop-down menus hide their options behind a click and are easy to overlook. NN/g's advice is to
  use visible radio buttons or checkboxes instead for small sets — five or fewer choices — because
  all the options stay on screen to compare
  ([NN/g: Radio buttons vs. dropdowns](https://www.nngroup.com/articles/listbox-dropdown/);
  [NN/g: Checkboxes vs. radio buttons](https://www.nngroup.com/articles/checkboxes-vs-radio-buttons/)).
  Google's Material guidance
  makes a segmented button (options side by side) the control for choosing among two to five items
  and for sorting ([Android: Segmented button](https://developer.android.com/develop/ui/compose/components/segmented-button);
  [Material 3: Segmented buttons](https://m3.material.io/components/segmented-buttons/guidelines)).
- Baymard's e-commerce testing found that people's first move on a results list is to look for a
  suitable filter, and that promoting the most important filters as visible chips above the list,
  with counts, lets them narrow quickly; 61% of sites do not
  ([Baymard: Promoting filters](https://baymard.com/blog/promoting-product-filters)). Applied filters
  should also be listed as removable chips with a "clear all"
  ([Baymard: Applied filters](https://baymard.com/blog/how-to-design-applied-filters)).

### 3.3 Where navigation and buttons go

- **Jakob's law:** people spend most of their time on other sites and expect yours to work the same
  way ([Laws of UX](https://lawsofux.com/jakobs-law/)). Apple's current principles say the same
  under *Familiarity*: use concepts people know, keep visuals and interactions consistent
  ([HIG: Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles)).
- A left-hand vertical navigation scales to more sections, is quicker to scan than a horizontal
  strip, and should use text labels rather than icons alone
  ([NN/g: Left-side vertical navigation](https://www.nngroup.com/articles/vertical-nav/)). Apple's
  sidebar guidance adds: no more than two levels, let people hide it but do not hide it by default
  ([HIG: Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars)).
- Apple's tab-bar guidance: use tabs to navigate, not to trigger actions; keep the bar visible;
  **don't disable or hide a tab when its content is unavailable — explain why instead**; use
  single-word labels; reserve badges for information that deserves attention
  ([HIG: Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars)). NN/g's
  tab guidance agrees on short labels, a clearly marked current tab and the most-used content first
  ([NN/g: Tabs, used right](https://www.nngroup.com/articles/tabs-used-right/)).
- Search: Apple advises giving search a primary position, showing its current scope, offering
  recent searches before typing and suggestions while typing, starting the search as people type
  where possible, and letting people clear their history
  ([HIG: Searching](https://developer.apple.com/design/human-interface-guidelines/searching);
  [HIG: Search fields](https://developer.apple.com/design/human-interface-guidelines/search-fields)).

### 3.4 Progressive disclosure

- Show the few options most people need; put the specialised ones one step away. Done well it
  improves learnability, speed and error rates at once; more than two levels and people get lost
  ([NN/g: Progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/)).
- Apple: put the controls people use most at the top of the disclosure hierarchy, always visible,
  with advanced ones hidden by default and a label that says what is hidden
  ([HIG: Disclosure controls](https://developer.apple.com/design/human-interface-guidelines/disclosure-controls)).
  On iPhone, Apple's advice is to limit the controls on screen and make secondary actions
  discoverable with minimal interaction
  ([HIG: Designing for iOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-ios)).

### 3.5 Feedback and speed

- The classic limits: about **0.1 s** feels instant, about **1 s** keeps the flow of thought
  unbroken, about **10 s** is as long as attention holds without a progress indicator
  ([Miller, 1968](https://doi.org/10.1145/1476589.1476628);
  [Card, Robertson & Mackinlay, CHI 1991](https://doi.org/10.1145/108844.108874);
  [NN/g: Response times](https://www.nngroup.com/articles/response-times-3-important-limits/)).
  The Doherty threshold puts the "neither side waits" pace at under 400 ms
  ([Laws of UX](https://lawsofux.com/doherty-threshold/)).
- In web search specifically, a controlled study found delays under about 500 ms mostly go
  unnoticed, delays over about 1,000 ms are noticed with high likelihood, and users of a fast
  engine are more sensitive to added delay
  ([Arapakis, Bai & Cambazoglu, SIGIR 2014](https://doi.org/10.1145/2600428.2609627)).
- Apple: show something as soon as possible — placeholders beat a blank screen, which reads as a
  fault ([HIG: Loading](https://developer.apple.com/design/human-interface-guidelines/loading)).
  NN/g: skeleton screens for full-page loads of 2–10 s, progress bars beyond 10 s, nothing under 1 s
  ([NN/g: Skeleton screens](https://www.nngroup.com/articles/skeleton-screens/)).
- Apple: put status next to the thing it describes, confirm only significant completions, and when
  something cannot be done, say why ([HIG: Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback)).
- When results change somewhere the user is not looking, they often do not notice at all
  (*change blindness*). For filters, NN/g recommends keeping the page where it is, dimming the
  results and showing progress while they update, and showing counts so people avoid dead ends. It
  also describes when to apply filters immediately and when to batch them behind an Apply button
  ([NN/g: Applying filters](https://www.nngroup.com/articles/applying-filters/);
  [NN/g: Change blindness](https://www.nngroup.com/articles/change-blindness/)).

### 3.6 Forgiveness: undo beats "are you sure?"

- *User control and freedom* — a clearly marked way out and undo — is one of Nielsen's ten
  heuristics ([NN/g: 10 heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)).
- Apple's *Agency* principle: when people know they can reverse an action they explore more
  freely; build forgiveness in ([HIG: Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles)).
  Apple also says not to warn when losing something is the expected result of the action
  ([HIG: Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback)).
- Shneiderman's *direct manipulation* rests on rapid, incremental, **reversible** actions whose
  effect is immediately visible ([Shneiderman, 1983](https://doi.org/10.1109/MC.1983.1654471);
  [NN/g summary](https://www.nngroup.com/articles/direct-manipulation/)).
- Google's pattern for this is the snackbar: a brief message at the bottom of the screen that
  carries one action such as Undo and then goes away
  ([Android: Snackbar](https://developer.android.com/develop/ui/compose/components/snackbar)).

### 3.7 Onboarding and tours

- Apple: ideally people understand the product by using it; if onboarding is needed it should be
  fast, fun and **optional**; teach through interactivity; prefer tips in context over one long flow;
  if someone skips a tutorial, never show it again automatically but keep it easy to find later;
  postpone non-essential setup ([HIG: Onboarding](https://developer.apple.com/design/human-interface-guidelines/onboarding)).
- Apple's tips guidance: a tip is one or two sentences; a feature needing more than three actions
  is too complicated for a tip; show a tip only to people who have not used the feature, and space
  tips out ([HIG: Offering help](https://developer.apple.com/design/human-interface-guidelines/offering-help)).
- NN/g: front-loaded "deck of cards" tutorials do not improve task performance and are widely
  skipped, because people want to get on with their task (the *paradox of the active user*);
  contextual help that appears when it is relevant works better
  ([NN/g: Onboarding tutorials vs. contextual help](https://www.nngroup.com/articles/onboarding-tutorials/)).
  Empty states are a good place for that help and for a direct path to the next action
  ([NN/g: Empty states](https://www.nngroup.com/articles/empty-state-interface-design/)).

### 3.8 Trust

- NN/g's four credibility factors: design quality; up-front disclosure; content that is
  comprehensive, correct and current; and being connected to the rest of the web
  ([NN/g: Trustworthy design](https://www.nngroup.com/articles/trustworthy-design/)).
- In a study of 2,684 people judging real sites, the most common thing they mentioned when rating
  credibility was the site's look (46.1% of comments)
  ([Fogg et al., DUX 2003](https://doi.org/10.1145/997078.997097)). People also rate attractive
  interfaces as easier to use, before and after using them
  ([Kurosu & Kashimura, CHI 1995](https://doi.org/10.1145/223355.223680);
  [Tractinsky, Katz & Ikar, 2000](https://doi.org/10.1016/s0953-5438(00)00031-x);
  [Laws of UX: Aesthetic-usability effect](https://lawsofux.com/aesthetic-usability-effect/)).
  Polish is not decoration here; it is read as evidence.
- Apple's *Responsibility* principle: be transparent about what the product does and why, from the
  first interaction ([HIG: Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles)).

### 3.9 Coming back

- Fogg's behaviour model: a behaviour happens when motivation, ability and a **trigger** meet at
  the same moment ([Fogg, Persuasive 2009](https://doi.org/10.1145/1541948.1541999)). For a job
  seeker the motivation is there; the trigger is "new jobs that match you".
- The *goal-gradient* effect: people speed up as they see themselves nearing a goal
  ([Kivetz, Urminsky & Zheng, 2006](https://doi.org/10.1509/jmkr.43.1.39)) — visible progress keeps
  people going.
- The *peak-end rule*: people remember an experience by its most intense moment and its end, not
  its average ([Kahneman et al., 1993](https://doi.org/10.1111/j.1467-9280.1993.tb00589.x);
  [Laws of UX](https://lawsofux.com/peak-end-rule/)).
- Apple: ask for commitments (ratings, purchases — and by extension sign-ups and alerts) after
  people have had a chance to engage, not before
  ([HIG: Onboarding](https://developer.apple.com/design/human-interface-guidelines/onboarding)).

---

## 4. What Apple actually does

Apple's strategy has two layers, and the issue named the older one.

**The iOS themes and principles (HIG as archived in 2021).** Three *themes*: **clarity** (legible
text, precise icons, restrained adornment, a focus on function), **deference** (fluid motion and a
crisp interface that help people understand content without competing with it) and **depth**
(visual layers and realistic motion that convey hierarchy). Six *principles*: **aesthetic
integrity** (appearance matches purpose — a serious task gets subtle graphics and predictable
behaviour), **consistency** (standard controls, familiar icons, uniform terms), **direct
manipulation** (act on the content itself and see the result immediately), **feedback**
(acknowledge every action, show progress), **metaphors** (familiar real-world actions) and **user
control** (people decide; the app suggests, warns, confirms destructive actions and makes it easy
to cancel) ([HIG, iOS Themes, 2021 archive](https://web.archive.org/web/20210602062900/https://developer.apple.com/design/human-interface-guidelines/ios/overview/themes/)).

**The current principles (HIG, reintroduced June 8, 2026).** Eight groups: **Purpose** (create
value, keep focused, find new ways to solve the problem), **Agency** (stay out of the way, freedom
to explore, recover from mistakes), **Responsibility** (be transparent, keep data safe),
**Familiarity** (known concepts, consistency, clear feedback), **Flexibility** (design for
everyone, preserve context, many input methods), **Simplicity** ("simplicity isn't minimalism";
be concise; establish hierarchy), **Craft** (quality sets the tone; iterate) and **Delight**
(create defining moments, but don't mistake delight for decoration)
([HIG: Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles)).
The 2025 *Liquid Glass* visual language is the current look of those ideas — translucent controls
that recede so content comes forward
([Apple Newsroom, June 2025](https://www.apple.com/newsroom/2025/06/apple-introduces-a-delightful-and-elegant-new-software-design/)).

**What transfers to a web app, and what does not.** The principles transfer; the materials do not.
Glass, blur and parallax are platform effects that cost contrast and performance on the web.
HeadStart's own palette decision (ADR-0116) — calm surfaces, colour reserved for meaning, no glow —
is already *deference* and *aesthetic integrity* applied to a long, tiring task. What Apple's
guidance adds for HeadStart is mostly behavioural:

| Apple idea | What it means on HeadStart |
| --- | --- |
| Deference / Stay out of the way | Jobs are the content; explanations shrink to one line or move behind a disclosure |
| Clarity / Be concise | Plain words in labels and tips; no internal terms (ADR numbers, "ATS", "first seen") in the main flow |
| Direct manipulation / Feedback | A filter change visibly changes the list, where the user is looking |
| User control / Agency | Every removal (hide, clear) has an Undo |
| Depth | A job preview opens in place (a drawer or expanding row) instead of losing context to a new tab |
| Delay sign-in / Responsibility | Show real results first; say exactly what signing in stores (the door already does the second) |
| Consistency / Familiarity | Filters look like the chips and segments people use on other job and shopping sites |

---

## 5. What the live app does today

Measured on 2026-09-28 in the walkthrough described in section 2.

**The signed-out door (live Space).** It loads in about 2 s and carries 327 words — a headline,
three live counts (533,799 jobs; 170,954 added in 7 days; 47 ATS providers), four trust points and
the sign-in terms — but **no job listing at all**. The sign-in block starts at 692 px on a 900 px
desktop screen and at **1,364 px on a phone, about 1.6 screens down**. Its lede says "no reposts,
no agencies", while the app's own footer says some boards belong to staffing firms and recruiters,
and the Hiring-now tab has a switch to show them. (The door answered in 2 s because the Space was
awake. A free Space sleeps after 48 hours without visits, and the project's deployment notes say
the first visitor after that waits about a minute. The pipeline's scheduled alert run calls the
Space, which probably keeps it awake; worth confirming before spending effort on it.)

**The first screen after sign-in (Search, no query).** Desktop: the first job card starts at
407 px, with 56 words of controls and explanation above it in the Search panel, and 4 cards are
fully visible. Phone: the
first card starts at 453 px and only 1–2 cards are fully visible, because a card is 187–207 px
tall. The unranked list is "most recently added first", so the very first card in the walkthrough
was a *Maintenance Area Manager, Reliability Maintenance Engineering* role — not what a tech job
seeker expects to see first. On a phone the "Try:" example queries are hidden (`display:none`) and
the search box is 242 px wide, so the placeholder is cut to "Describe the role you want –".

**The core task.** "Find a remote, full-time backend Python role for someone with 3 years'
experience, newest first, and open one." It took **10 clicks and 2 typed fields** on both desktop
and phone:

| Step | Clicks | What happened |
| --- | --- | --- |
| Type the query, press Enter | 0 (+1 field) | Results in ~0.2 s locally |
| Open Filters | 1 | The panel is 670 px tall on desktop, **1,759 px on a phone**; results are pushed below it (first card at 1,074 px desktop, 2,209 px phone) |
| Tick "Remote only" | 1 | **No search sent** |
| Type 3 in experience, press Enter | 1 (+1 field) | **No search sent** — Enter only works in the two text boxes |
| Choose Full-time from a drop-down | 2 | **No search sent** |
| Scroll back up and press Search | 1 | Search sent. The list updated **below the fold** (first card at 1,108 px on desktop), so nothing on screen visibly changed except "Filters (3)" |
| Choose "Newest posted" in the Sort drop-down | 2 | Search sent |
| Close Filters | 1 | Results come back into view |
| Open the first result | 1 | Opens the employer's board in a new tab; there is no preview inside HeadStart |

A plausible target for the same task, with visible chips, auto-apply and an inline sort, is
**about 5 clicks and 2 typed fields, with no scrolling**, and every change visible where it was
made.

**Hiding a company.** The "hide" link on a card removed Amazon from the list immediately (the count
fell from 514,163 to 504,512 — about 9,700 jobs). The only way back, "1 company hidden… show them
again", is written inside the Filters panel, which was closed, so **nothing visible said what had
happened or how to undo it**.

**Keyboard.** From the search box, 10 Tab presses reach the first job on desktop (7 on the phone
layout), because the toolbar sits between them. The skip link ("Skip the filters, go to results")
is the second stop and shortens this when used.

**Touch targets on a phone.** Under a coarse pointer the card's "hide" link grows to 44×44 px,
matching Apple's 44 pt default (the stylesheet gives the "trend" link the same rule). The row's × (hide this job) stays 22×22 px — under WCAG 2.2's
24×24 px minimum unless its spacing exception applies, and half Apple's size
([WCAG 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html);
[HIG: Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)).
The Filters button is 38 px tall.

**Zero results.** Handled well: "Nothing matched — your Company filter is the one ruling everything
out — remove it", with a one-click removal.

**Résumé tab.** A clear "Start here" card, undo/redo and a live one-page check are good. The
worked example it opens with is a café cashier's résumé, on a site for software jobs.

**Data tab.** A long page of explanations, provider lists and ADR links. It earns trust with the
few who read it; it is not where a first visit should land.

### Already done well — keep these

These match the research and should survive any redesign: skeleton rows while results load
(HIG Loading; NN/g), the facet counts that arrive separately so they never delay the jobs, the
optimistic star that fills instantly and rolls back on failure, the applied-filter chips each with
their own ×, the "which filter is ruling everything out" message on zero results (HIG Feedback:
say why), deep links that restore a search from the URL, 44 px coarse-pointer targets for the
card links, the whole card being clickable without covering text, reduced-motion support and the
contrast work, and the door's plain statement of what signing in stores (HIG Responsibility).

---

## 6. Recommendations, in priority order

Work already in flight under #755:

- **Filters/sort/results batch** — fewer clicks in filters, inline sort (items 5, 7, 8, 12; the
  result-card items 4, 6 and 14 may land with it).
- **Navigation/Home/tour/logo batch** — left sidebar, a new Home tab, a guided tour, a logo
  (items 1, 3, 10, 16).
- **Trends copy batch** — plain-language Trends text (items 11, 15).

### A. Not covered by any batch

**A1. Show value before the sign-in wall.** *Principle:* delay sign-in; value in ten seconds
(HIG Managing accounts; NN/g Login walls; Liu et al. 2010). *Evidence:* the door shows no job, and
on a phone the sign-in button is 1.6 screens down. ADR-0042 itself calls public search "the
decision most worth revisiting if sign-ups stall". This is the owner's call, so three options:

1. *Recommended:* **a live sample on the door.** Three to five real cards for one example query
   (read-only, links working), above the sign-in button, plus a sign-in button in the first screen
   on phones. Keeps the wall exactly as ADR-0042 set it; the door shows the product instead of
   describing it. Effort: small–medium (one server-side query, cached).
2. **Public search, sign in to save.** Search and Trends open; the wall moves to starring, saved
   searches, alerts and the profile — the "browse, then check out" shape both Apple and NN/g
   describe. Largest effect on first visits; costs the anonymous-traffic protection the wall gives
   today (search load, scraping of the index) and would need a rate limit. Effort: medium.
3. **A few free searches, then sign in.** A middle ground, but a counter is easy to reset and
   feels like a trick when it runs out. Not recommended.

**A2. Preview a job in place.** *Principle:* progressive disclosure; depth (context kept);
interaction cost (every "is this right for me?" currently costs a new tab and an ATS page load,
often a slow Workday one). *Evidence:* the title is the only way to learn more, and it leaves the
site. *Change:* clicking a card expands it (or opens a side drawer on desktop) to show the first
few paragraphs of the description, the extracted facts (pay, years, remote, posted date) and a
"why it matched" line — the query terms or skills found in the text, in the spirit of Baymard's
*contextual list item information* ([Baymard](https://baymard.com/blog/contextual-list-item-information)).
The employer link stays one click away and stays the only way to apply. The served table already
stores a `description` column (the Keyword filter searches it), but the search API leaves it out
of its response today, so this is a UI change plus an endpoint that returns one job's text on
demand — fetched when a card is opened, not with every result page. Effort: medium.

**A3. Undo for "hide company", and a visible confirmation.** *Principle:* forgiveness beats
confirmation (HIG Agency; heuristic 3; Shneiderman's reversible actions); status next to the action
(HIG Feedback). *Evidence:* one click silently removed about 9,700 jobs, and the undo is in a closed
panel. *Change:* a snackbar at the bottom — "Hidden: Amazon (9,651 jobs) · Undo" — for about eight
seconds, and the "N companies hidden" line shown above the results while any are hidden, not only
inside Filters. The same toast pattern suits unstarring and deleting a saved search. Effort: small.
(If the results batch removes the company "hide" link along with the per-job ×, this item goes
with it.)

**A4. Recent searches and suggestions; examples on phones.** *Principle:* recognition rather than
recall (heuristic 6); Apple's search guidance (recent searches before typing, suggestions while
typing, clear scope). *Evidence:* no search history is offered; on phones the example chips are
hidden and the placeholder is truncated. *Change:* on focus, list the last five searches (stored
locally, with "clear"), then the user's saved searches; show the example chips on phones as one
horizontally scrolling row; shorten the phone placeholder to "e.g. backend engineer, climate
startup". Live search-as-you-type is **not** recommended: every keystroke would be an embedding
call on the Space's two shared vCPUs ([Hugging Face: Space hardware](https://huggingface.co/docs/hub/spaces-gpus)),
and the ranked list would shuffle under the reader's eyes. Effort: small.

**A5. A reason to come back.** *Principle:* a trigger at the moment motivation exists (Fogg 2009);
ask for commitment after value (HIG Onboarding). *Evidence:* today the "new" tag means "added to
the index in the last 24–48 hours", not "new since *you* last looked"; email alerts are invite-only
and only one saved search can email. *Change:* record each saved search's last-viewed time and show
"12 new since Tuesday" on the saved search and as a badge on the Matches navigation item (a badge is
justified here: it is information the user asked to be told about — HIG Tab bars). Then, as an
owner decision on cost, open email alerts to every signed-in user. Effort: medium (the watermark
machinery from ADR-0043 already exists).

**A6. Make every trust claim consistent and checkable.** *Principle:* content must be correct
(NN/g credibility factors); be transparent (HIG Responsibility). *Evidence:* the door says "no
agencies"; the app's footer and Hiring-now tab say some boards are staffing firms. *Change:* reword
the door to what is true — e.g. "straight from each company's own hiring system; staffing firms
are labelled" — and keep one wording for each claim across door, footer and Home. A single
contradiction noticed on the door undoes the other three trust points. Effort: tiny.

**A7. Lead with relevant jobs, not the newest rows.** *Principle:* create value, keep focused
(HIG Purpose); first impressions (Lindgaard 2006). *Evidence:* the unranked landing list opened
with a maintenance-manager role. *Change:* if the user has a profile or a saved search, open Search
already ranked for it (the "Search with this profile" action exists; make it the default). If not,
make the landing a ranked example ("Showing: software engineer — change it") rather than an
arbitrary recency list. If the Home tab becomes the landing page, this still applies to Search's
empty state. Effort: small.

**A8. A software-engineer résumé as the worked example.** *Principle:* familiarity and relevance
(Jakob's law; HIG Keep focused). *Evidence:* the builder opens on a café cashier's résumé. *Change:*
ship a software-engineer example (a new grad and a mid-level one, perhaps), keeping the current
guide's structure. Issue item 9 (an easier data-entry flow) is waiting on the owner and is separate.
Effort: small.

**A9. Make the phone results screen denser.** *Principle:* limit on-screen controls on iPhone (HIG
Designing for iOS); Fitts's law. *Evidence:* first card at 453 px, 1–2 cards per screen. *Change:*
fold the "what this list is" sentence into a one-line label with an info disclosure; put the count
on the same line as Filters and Sort; tighten card padding on phones. Removing Compact (issue item
8) and the sidebar (item 1) will change this screen, so re-measure after those land. Effort: small.

**A10. Keyboard shortcuts for heavy users.** *Principle:* flexibility and efficiency of use
(heuristic 7); many input methods (HIG Flexibility). *Evidence:* 10 Tab presses from the query to
the first job. *Change:* "/" focuses search; J/K (or arrow keys) move between results; S stars;
Enter or O opens. Announce them once in a tip, not in the tour. Effort: small. Lowest priority.

### B. Covered by a batch in flight — acceptance checks from the research

These are not new work. They are what the research says the in-flight work should meet, so it can
be checked when it lands.

**Filters/sort/results batch.**

- Every filter change either re-runs the search immediately or sits behind one Apply button that
  shows the live count ("Show 1,240 jobs"); never a control that silently waits for a Search button
  somewhere else (NN/g Applying filters). Today three of the four controls tried did nothing.
- While results update, dim them and keep the page where it is; if the list is off screen, bring it
  into view or show the new count next to the control that changed (NN/g Change blindness).
- Options that fit — employment type (4 values), remote, sort (4 values) — become chips or
  segments, not drop-downs (NN/g: visible options for five or fewer; Material segmented buttons for
  2–5 options). The most used filters sit above the list as promoted chips with counts; the full
  set stays in the panel (Baymard Promoting filters).
- On phones, the filter panel opens as a sheet over the results with Apply and Clear pinned at the
  bottom, instead of pushing the first result 2,209 px down.
- The per-job × is 22×22 px today; if any small control remains, it meets 24×24 px (WCAG 2.5.8)
  and ideally 44 px on touch.
- On the card, drop the "–" for missing pay and the "via {ATS}" label (issue items 4 and 14):
  both spend attention on things the job seeker does not act on (heuristic 8, minimalist design).

**Navigation/Home/tour/logo batch.**

- Sidebar: text labels (icons optional), current page clearly marked, at most two levels, collapsible
  but open by default (NN/g Vertical nav; HIG Sidebars). Actions (Sign out, theme) are not
  navigation items (HIG Tab bars).
- "Soon" items: Apple advises against disabled navigation items. Either leave a section out until
  it ships, or keep it clickable and explain on arrival what is coming (HIG Tab bars).
- Home: states the value in the first screen and puts the search box (or a sample of live results)
  there, not only prose and a video. A video supports the page; it must not be the page (NN/g
  10-second finding). Keep it muted by default and never autoplay with sound.
- Tour: optional, three to five steps at most, each pointing at a real control the user can try;
  skippable from every step; never shown again once skipped or finished, but re-openable from a
  "Take the tour" link; nothing in it that explains standard controls (HIG Onboarding and Offering
  help; NN/g Onboarding). Prefer one contextual tip at the moment a feature is first relevant (for
  example, on first opening Filters) to a tour that runs on arrival.
- Logo: it is the first 50 ms of the impression (Lindgaard 2006; Fogg 2003). Simple, legible at
  16 px as a favicon, consistent with the calm palette of ADR-0116.

**Trends copy batch.**

- Plain words, no internal references in the reading path — no "ADR-0057", "comparable coverage",
  or "index 120" without saying what it means (HIG Writing: simple, plain language, no jargon;
  heuristic 2). The caveats can stay one disclosure away (progressive disclosure).
- Each control's help fits in one tooltip of about 60–75 characters (HIG Offering help). If a
  control cannot be explained that briefly, the control is the problem.
- Keep the answer as a sentence above the chart — the page already writes one per line as its
  verdict — and make that sentence the plainest text on the page ("Backend openings rose about 8%
  this month"); the chart and its caveats then support it (NN/g 10-second finding).

---

## 7. What not to copy

- **Engagement tricks.** Streaks, fake urgency ("3 people are viewing this job"), and notification
  spam raise short-term visits and cost trust — the thing HeadStart's positioning depends on. Apple
  warns against mistaking delight for decoration; the equivalent here is mistaking visits for value.
- **Apple's materials.** Glass, blur and heavy motion. Copy the principles in section 4, not the
  look.
- **Click-count targets on their own.** The goal is lower total effort and fewer surprises. A
  change that saves one click but moves a control away from where people look is a regression
  (NN/g Interaction cost; The 3-click rule).

---

## 8. How to tell whether it worked

Suggested measures. Each can be counted on the server — from request logs and the account store —
with no third-party analytics; some need a timestamp the app does not record yet (for example, an
account's last visit):

- **Door conversion:** sign-ins per unique door visit, before and after A1.
- **First-search time:** seconds from session start to the first search with results.
- **Return rate:** share of accounts that come back within 7 days, before and after A5.
- **Filter completion:** searches with at least one filter, and zero-result rate after filtering.
- **Latency:** p50 and p95 time to the first painted result on the Space, against a 1 s budget
  (Arapakis et al. 2014; NN/g response times). The page's own code notes cold facet counts taking
  3–16 s; the jobs already paint first, so the budget is for the rows.

---

## 9. Sources

Apple (Human Interface Guidelines, read 2026-09-28 unless marked)

- [Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles) (change log: reintroduced June 8, 2026)
- [iOS Themes and Design Principles, 2021 archive](https://web.archive.org/web/20210602062900/https://developer.apple.com/design/human-interface-guidelines/ios/overview/themes/)
- [Onboarding](https://developer.apple.com/design/human-interface-guidelines/onboarding) ·
  [Managing accounts](https://developer.apple.com/design/human-interface-guidelines/managing-accounts) ·
  [Loading](https://developer.apple.com/design/human-interface-guidelines/loading) ·
  [Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback) ·
  [Searching](https://developer.apple.com/design/human-interface-guidelines/searching) ·
  [Search fields](https://developer.apple.com/design/human-interface-guidelines/search-fields) ·
  [Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars) ·
  [Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars) ·
  [Disclosure controls](https://developer.apple.com/design/human-interface-guidelines/disclosure-controls) ·
  [Offering help](https://developer.apple.com/design/human-interface-guidelines/offering-help) ·
  [Designing for iOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-ios) ·
  [Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) ·
  [Writing](https://developer.apple.com/design/human-interface-guidelines/writing)
- [Apple Newsroom: a new software design (Liquid Glass), June 2025](https://www.apple.com/newsroom/2025/06/apple-introduces-a-delightful-and-elegant-new-software-design/)

Nielsen Norman Group

- [10 usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) (Nielsen, updated 2024)
- [Response times: the 3 important limits](https://www.nngroup.com/articles/response-times-3-important-limits/) (Nielsen, 1993/2014)
- [How long do users stay on web pages?](https://www.nngroup.com/articles/how-long-do-users-stay-on-web-pages/) (Nielsen, 2011)
- [Progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) (Nielsen, 2006)
- [Applying filters: batch vs. interactive](https://www.nngroup.com/articles/applying-filters/) (Sherwin, 2016)
- [Change blindness](https://www.nngroup.com/articles/change-blindness/)
- [Login walls stop users in their tracks](https://www.nngroup.com/articles/login-walls/) (Budiu, 2014)
- [Trustworthiness in web design: 4 credibility factors](https://www.nngroup.com/articles/trustworthy-design/) (Harley, 2016)
- [Onboarding tutorials vs. contextual help](https://www.nngroup.com/articles/onboarding-tutorials/) (Laubheimer, 2023)
- [Skeleton screens 101](https://www.nngroup.com/articles/skeleton-screens/) (Tankala, 2023)
- [Listboxes vs. dropdown lists](https://www.nngroup.com/articles/listbox-dropdown/) (Kaley, 2020)
- [Checkboxes vs. radio buttons](https://www.nngroup.com/articles/checkboxes-vs-radio-buttons/) (Nielsen, 2004)
- [Tabs, used right](https://www.nngroup.com/articles/tabs-used-right/) (Sunwall, 2024)
- [Left-side vertical navigation on desktop](https://www.nngroup.com/articles/vertical-nav/) (Laubheimer, 2021)
- [Direct manipulation](https://www.nngroup.com/articles/direct-manipulation/) (Sherugar & Budiu, 2016)
- [Designing empty states](https://www.nngroup.com/articles/empty-state-interface-design/) (Kaplan, 2021)
- [Interaction cost](https://www.nngroup.com/articles/interaction-cost-definition/) (Budiu, 2013)
- [The 3-click rule](https://www.nngroup.com/articles/3-click-rule/) (Laubheimer, 2019)

Baymard Institute

- [Promoting important filters](https://baymard.com/blog/promoting-product-filters) (2023)
- [Display applied filters in an overview](https://baymard.com/blog/how-to-design-applied-filters) (2020, updated 2026)
- [Contextual list item information](https://baymard.com/blog/contextual-list-item-information) (2015)

Google, W3C, Laws of UX

- [Material 3: Segmented buttons](https://m3.material.io/components/segmented-buttons/guidelines) ·
  [Android: Segmented button](https://developer.android.com/develop/ui/compose/components/segmented-button) ·
  [Android: Chips](https://developer.android.com/develop/ui/compose/components/chip) ·
  [Android: Snackbar](https://developer.android.com/develop/ui/compose/components/snackbar)
- [WCAG 2.2, 2.5.8 Target size (minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- [Hugging Face: Space hardware and sleep time](https://huggingface.co/docs/hub/spaces-gpus)
- Laws of UX: [Doherty threshold](https://lawsofux.com/doherty-threshold/) ·
  [Hick's law](https://lawsofux.com/hicks-law/) · [Fitts's law](https://lawsofux.com/fittss-law/) ·
  [Jakob's law](https://lawsofux.com/jakobs-law/) · [Peak-end rule](https://lawsofux.com/peak-end-rule/) ·
  [Aesthetic-usability effect](https://lawsofux.com/aesthetic-usability-effect/)

Peer-reviewed

- Miller, R. B. (1968). Response time in man-computer conversational transactions. AFIPS Fall Joint
  Computer Conference. [doi:10.1145/1476589.1476628](https://doi.org/10.1145/1476589.1476628)
- Shneiderman, B. (1983). Direct manipulation: a step beyond programming languages. *IEEE Computer*
  16(8). [doi:10.1109/MC.1983.1654471](https://doi.org/10.1109/MC.1983.1654471)
- Card, S. K., Robertson, G. G. & Mackinlay, J. D. (1991). The information visualizer, an
  information workspace. CHI '91. [doi:10.1145/108844.108874](https://doi.org/10.1145/108844.108874)
- Kahneman, D. et al. (1993). When more pain is preferred to less: adding a better end.
  *Psychological Science* 4(6). [doi:10.1111/j.1467-9280.1993.tb00589.x](https://doi.org/10.1111/j.1467-9280.1993.tb00589.x)
- Kurosu, M. & Kashimura, K. (1995). Apparent usability vs. inherent usability. CHI '95 companion.
  [doi:10.1145/223355.223680](https://doi.org/10.1145/223355.223680)
- Tractinsky, N., Katz, A. S. & Ikar, D. (2000). What is beautiful is usable. *Interacting with
  Computers* 13(2). [doi:10.1016/S0953-5438(00)00031-X](https://doi.org/10.1016/s0953-5438(00)00031-x)
- Fogg, B. J. et al. (2003). How do users evaluate the credibility of Web sites? DUX '03.
  [doi:10.1145/997078.997097](https://doi.org/10.1145/997078.997097)
- Lindgaard, G., Fernandes, G., Dudek, C. & Brown, J. (2006). Attention web designers: you have 50
  milliseconds to make a good first impression! *Behaviour & Information Technology* 25(2).
  [doi:10.1080/01449290500330448](https://doi.org/10.1080/01449290500330448)
- Kivetz, R., Urminsky, O. & Zheng, Y. (2006). The goal-gradient hypothesis resurrected. *Journal of
  Marketing Research* 43(1). [doi:10.1509/jmkr.43.1.39](https://doi.org/10.1509/jmkr.43.1.39)
- Fogg, B. J. (2009). A behavior model for persuasive design. Persuasive '09.
  [doi:10.1145/1541948.1541999](https://doi.org/10.1145/1541948.1541999)
- Liu, C., White, R. W. & Dumais, S. (2010). Understanding web browsing behaviors through Weibull
  analysis of dwell time. SIGIR '10. [doi:10.1145/1835449.1835513](https://doi.org/10.1145/1835449.1835513)
- Arapakis, I., Bai, X. & Cambazoglu, B. B. (2014). Impact of response latency on user behavior in
  web search. SIGIR '14. [doi:10.1145/2600428.2609627](https://doi.org/10.1145/2600428.2609627)

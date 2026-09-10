# Resume builder editor UX: Reactive Resume, FlowCV, Teal, Standard Resume

Scope note: Resume.io, Kickresume, and Novoresume are covered elsewhere and are out of scope here.
Reactive Resume is researched from its actual source (AmruthPillai/Reactive-Resume, `main` branch,
read 2026-09-10 — this is an actively developed monorepo, so line numbers and even file paths will
drift; all citations below are to the file, not a pinned commit). FlowCV, Teal, and Standard Resume
are researched from official docs, official marketing pages, and — where the actual in-app editor
isn't publicly documented — official product screenshots served from the vendor's own asset CDN.
Every claim below is either sourced to a URL or explicitly marked as unverified; nothing is invented.

## Summary table

| | Left/content rail & entry editing | Design controls location | **Template picker** | Top chrome | First-run empty state | Preview: interactive? sync mechanism | Multi-version / tailoring |
|---|---|---|---|---|---|---|---|
| **Reactive Resume** | Left sidebar: one accordion per section (Basics, Summary, Experience, …); each item is a drag-reorderable row, click opens an **edit dialog** (not inline expansion) | Right sidebar, same accordion pattern, **same panel as content** (opposite rail) — Template / Layout / Typography / Design / Styles / Page / … all stacked | **Full-screen modal gallery**, responsive grid (2→5 cols), **static per-template JPGs** (not user content), name + tag badges under each card, one-sentence description + tags in the right-rail mini-panel too; **15 templates**, scales via scroll + grid, no categories | 1 band (h-14 header): left-toggle \| home/name/lock/save-status/AI/version-history/options \| download+right-toggle. A separate floating bottom-center "dock" (undo/redo, zoom, page-layout, AI, copy-link) | Not directly verified (out of scope for this pass) | **Read-only** paper; **same in-memory reactive store** (immer/Zustand) feeds both the sidebar forms and the preview, so any field edit re-renders the preview immediately — no click-to-edit on the paper itself | **Duplicate the whole resume** (dashboard-level, taggable) for a new application; separate auto-saved **version history** (dropdown, restore-only) for undo within one document |
| **FlowCV** | No primary source found for the in-app panel layout (no public help center located) | No primary source found for in-app location | Public marketing gallery: **100+ templates in 7 categories** (Popular/Simple/Modern/Creative/Photo/Compact/First Job), **static thumbnails**, category-level "Ideal for: …" audience text; per-template ATS callouts not found. In-app picker itself: no primary source found | No primary source found | No primary source found | No primary source found (a "real-time preview" claim exists only in third-party/review text, not an official page — excluded as evidence) | No primary source found |
| **Teal** | Editing happens in a **"Builder" mode**; entries (company/position/bullet) are added via a **pencil-icon → edit form** (fields + Cancel/Save); form is not shown embedded in page context in the KB screenshots, so inline-panel vs. modal isn't fully confirmable from the images alone | **Separate "Designer" mode/tab**, reached via a tab "near the top of the page" — i.e. NOT the same rail as content; Designer itself has 4 sub-tabs: Presentation (template+font+color+dates), Sections (drag-reorder), Settings (date/location format), Advanced (Teal+ only: bullets/spacing/titles) | **In-panel "Template Library"** opened via "Add Template"; each card **previews with the user's own saved experience data**, not a static mock; "Save Template" applies it. Public marketing site separately claims "100+" templates but that's a different, SEO landing page — in-app count not confirmed by an official source (third-party review cites ~10 free / more on Teal+, flagged as such) | Not fully confirmed; at least one band holds the Builder/Designer mode-switch | **Confirmed**: "If you do not have any work experience saved … click the 'Add Work Experience' button to get started" | Template selection **live-updates the preview** ("your resume preview will automatically change"); click-to-edit-on-paper not confirmed | **"Resume Syncing"**: one master content pool, **multiple tailored resumes** pull from it; bullets are toggled on/off per version; edits can "Save to all resumes" or stay local, with "Update Available" flags when they don't sync; paired with a Job Matcher "Match Score" |
| **Standard Resume** | No primary source found for the content-entry form itself (behind login; product screenshot shows the Preview+Design view, not Write) | **Separate "Preview" mode's left rail** carries "Design options" (Change template, Add profile photo) + granular **named color swatches** (Titles, Primary text, Subtitles, Captions, List-item background, List-item text, Background) — confirmed via official screenshot | "**Change template**" menu item (grid icon) in the Design rail — icon implies a thumbnail grid, exact modal/dropdown mechanics not directly captured; **12 templates**, 4 style categories (Simple/Modern/Creative/Professional); marketing collage shows the *same* sample content rendered across multiple templates, consistent with content-driven (not purely static) previews | **1 band, 3 tabs: "Write \| Preview \| Share"** — confirmed via official screenshot | No primary source found | Screenshot shows **Write and Preview as separate modes** (tabs), not simultaneous panels — implies you leave the content form to see/style the rendered page, unlike the other three products' split-view. Not personally used live, so inferred from the screenshot + tab labels, not confirmed interaction-by-interaction | **Duplicate-based**: "Duplicate your resume in one click and customize the new resume for a specific application" |

## Reactive Resume

Source: `AmruthPillai/Reactive-Resume`, `main` branch, read via raw.githubusercontent.com 2026-09-10.
The repo has moved past the `apps/client/src/pages/builder/` layout named in the research brief; the
live structure is `apps/web/src/routes/builder/$resumeId/` (TanStack Router route with `-sidebar`,
`-components`, `-store` folders — the `-` prefix marks route-local, non-routed folders).

- **Left sidebar = content.** `BuilderSidebarLeft` renders one `SectionBase` accordion per section
  (Basics, Summary, Profiles, Experience, Education, Projects, Skills, Languages, Interests, Awards,
  Certifications, Publications, Volunteer, References, Custom) inside a single scrollable column,
  plus a narrow icon "rail" (`SidebarEdge`) for jump-navigation.
  [`-sidebar/left/index.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/left/index.tsx),
  [`-sidebar/left/shared/section-base.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/left/shared/section-base.tsx)
  (this is a real `<Accordion>` component, collapsible per section, state in a `useSectionStore`).
- **Entries inside a section are a drag-reorderable list, edited via a dialog, not inline expansion.**
  `SectionItem` renders each entry as a `Reorder.Item` row with a drag handle; clicking the row body
  calls `openDialog(resume.sections.${type}.update, ...)` — a modal/dialog, confirmed by the dispatch
  key naming (`.create` / `.update` dialogs) and by there being no expanded-field markup in the row
  component itself. Each row also has a `⋮` menu: Hide/Show, Update, Duplicate, "Move to" (another
  section/page/new page), Delete.
  [`-sidebar/left/shared/section-item.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/left/shared/section-item.tsx)
- **Sections themselves (not entries) are reordered in the right sidebar's Layout panel**, via
  `dnd-kit` sortable "cards" that can be dragged between a page's `main`/`sidebar` columns and across
  multiple pages — a small drag-and-drop board, not a simple list.
  [`-sidebar/right/sections/layout/pages.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/right/sections/layout/pages.tsx)
  A sidebar-width slider lives in the same Layout accordion.
  [`-sidebar/right/sections/layout/index.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/right/sections/layout/index.tsx)
- **Right sidebar = design/meta, same panel shape as the left (opposite rail, not a separate mode).**
  `BuilderSidebarRight` stacks: Template, Layout, Typography, Design, (Custom) Styles, Page, Notes,
  Sharing, Statistics, ATS check, Export, Information — same accordion-in-a-scroll-area pattern as
  the left sidebar, with its own icon rail.
  [`-sidebar/right/index.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/right/index.tsx)
  Design section = color picker (primary/etc.) + skill-level display style, same in-rail accordion.
  [`-sidebar/right/sections/design.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/right/sections/design.tsx)
- **Template picker: the one deliberately different control — a full-screen modal, not an accordion.**
  The right sidebar's Template section shows only the *current* template (static thumbnail image,
  name, one-sentence description, tag badges) with a "swap" button that opens
  `resume.template.gallery`.
  [`-sidebar/right/sections/template.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-sidebar/right/sections/template.tsx)
  The gallery dialog renders all templates in a responsive CSS grid (`grid-cols-2` → `md:3` →
  `lg:4` → `xl:5`) inside a scrollable, wide (`lg:max-w-6xl xl:max-w-7xl`) dialog; each card is a
  **static JPG** (`/templates/jpg/{name}.jpg`), not a render of the user's own content; clicking
  applies immediately and closes, with an "Undo" action in the confirmation toast.
  [`dialogs/resume/template/gallery.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/dialogs/resume/template/gallery.tsx)
  There are **15 templates**, all Pokémon-named (azurill, bronzor, chikorita, ditgar, ditto, gengar,
  glalie, kakuna, lapras, leafish, meowth, onyx, pikachu, rhyhorn, scizor), each with a one-sentence
  description, an array of free-text tags, and a `sidebarPosition` (`left`/`right`/`none`). "ATS
  friendly" appears as a tag on the single-column, low-decoration templates (ditto, kakuna, lapras,
  meowth, onyx, rhyhorn, scizor — 7 of 15); the two-column, more decorated ones don't carry that tag
  — a positive-framing signal rather than an explicit warning on the rest.
  [`dialogs/resume/template/data.ts`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/dialogs/resume/template/data.ts)
  Corroborated by the official user guide, which additionally documents a "Test with real content"
  tip ("What looks great with sample data might not work as well with your specific information")
  and a settings table (Colors/Typography/Layout/Spacing) matching the right-sidebar sections found
  in code.
  [`docs/guides/choosing-a-template.mdx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/docs/guides/choosing-a-template.mdx)
- **Top chrome: one 56px header band with three flex zones**, not several stacked bands: left
  (toggle-left-sidebar) — center (home icon → dashboard, breadcrumb resume name, lock icon, save
  status, AI-assistant button, version-history dropdown, a `⋮` options dropdown for Edit
  details/Duplicate/Lock/Delete) — right (Download button, toggle-right-sidebar).
  [`-components/header.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-components/header.tsx)
  Separately, a floating pill ("dock") is pinned bottom-center over the canvas — undo/redo, zoom
  out/level/in, page-stacking toggle, "Open AI agent", copy public link — not part of the header.
  [`-components/dock.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-components/dock.tsx)
- **Preview is read-only; sync is via a shared reactive store, not click-to-edit.** `ResumePreview`
  reads the same `useResumeData()` hook the sidebar writes through
  ([`preview.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/features/resume/preview/preview.tsx)); the
  rendering component (`preview.browser.tsx`) has no `onClick`/`cursor-pointer` handlers on content
  blocks — confirmed by grepping the fetched source for click affordances (none found). The canvas
  itself is pan/zoomable (`react-zoom-pan-pinch`) but the paper content is not directly editable.
  [`-components/preview-page.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-components/preview-page.tsx)
- **Multi-version / tailoring**: two distinct mechanisms, neither of which is a "tailor for this job"
  feature. (1) Auto-saved **version history** — a dropdown of past snapshots with restore-only
  semantics ("Earlier versions are kept; the builder's undo history is reset").
  [`-components/version-history.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-components/version-history.tsx)
  (2) **Duplicate** the whole resume as an independent, taggable document from the header's options
  dropdown — this is how a differently-tailored copy for another application gets made; it's a
  dashboard-level action, not an in-builder tailoring UI.
  [`-components/header.tsx`](https://github.com/AmruthPillai/Reactive-Resume/blob/main/apps/web/src/routes/builder/%24resumeId/-components/header.tsx)

## FlowCV

**No official help center was located.** `help.flowcv.com`, `support.flowcv.com`, `flowcv.com/help`,
`flowcv.com/faq`, `flowcv.com/blog`, and `flowcv.com/features` all failed to resolve or 404'd when
probed directly (checked 2026-09-10). A web search for an Intercom/Zendesk-style help portal under
FlowCV's own domain returned nothing. `app.flowcv.com` (the live editor) requires login and renders
client-side, so its DOM wasn't inspectable without an authenticated session.

What IS confirmed from FlowCV's own marketing pages:
- **Template gallery (public/marketing page)**: "100+ Free Resume Templates" across **7 categories**
  — Popular, Simple, Modern, Creative, Photo, Compact, First Job — each with one line of "Ideal
  for: …" audience guidance (e.g. Simple: "job seekers in traditional industries who prefer
  timeless, straightforward, and easy-to-read resume"). Thumbnails are static sample layouts with
  placeholder text, not personalized. No per-template ATS callout was found.
  [flowcv.com/resume-templates](https://flowcv.com/resume-templates)
- **Homepage** frames the flow as four steps (choose template → add experience → customize layout
  → download) and states, in the maker's own copy: "Fill your resume with content. We'll guide you
  along the way," then separately, "Adjust layout and design until your resume feels like you.
  FlowCV gives you full control while keeping things easy" — implying content-entry and
  layout/design are treated as distinct steps, but the page shows no actual editor screenshot, so
  the left/right panel geometry, whether the template picker is a modal vs. inline, and whether the
  preview is click-to-edit are **not confirmed**.
  [flowcv.com](https://flowcv.com/)
- The maker's own Product Hunt listing describes "customizable templates," "step-by-step guidance,"
  and (in user-submitted reviews on that same page, not the maker's copy) a "drag-and-drop interface"
  for rearranging sections and a "real-time preview" — flagged here as **partly user-review text on
  a semi-official page**, not FlowCV's own documentation, and not corroborated elsewhere.
  [producthunt.com/products/flowcv](https://www.producthunt.com/products/flowcv)

**No primary source found** for: the in-app panel layout, whether the template picker is a dropdown/
gallery/full-screen mode inside the actual editor, whether in-app thumbnails render the user's own
content, per-template ATS warnings inside the app, the top-chrome structure, the first-run empty
state, and how (or whether) multiple tailored versions are surfaced. Third-party review sites
(resumearena.com, resumehog.com, hireflow.net, saasworthy.com — all either SEO/affiliate content or
competitor-adjacent "best resume builder" roundups) turned up in search but are excluded as evidence
per the research brief; none were fetched or cited.

## Teal

Primary source: `help.tealhq.com` (Teal's Intercom-hosted Knowledge Base — direct `curl`/WebFetch
hit Cloudflare's bot-block, so pages were read via a reader proxy that renders the same HTML the
browser would show; content quoted below is the article text as published by Teal, dated
2026-07-27 on each article).

- **Two top-level modes, not two rails of one screen.** Content editing lives in the **Resume
  Builder**; design/template/layout controls live in a separate **Resume Designer**, reached by
  "navigat[ing] to the Designer tab located near the top of the page."
  [Getting Started: Resume Designer](https://help.tealhq.com/en/articles/9508951-getting-started-resume-designer)
- **Entries are edited via a pencil-icon → form**, not evidently inline-expanded in the flowing list
  (the KB's own screenshots are cropped tightly to the form — "Edit Position" / "Edit Company" boxes
  with Cancel/Save buttons — so whether the form floats as a modal or expands in place isn't
  fully verifiable from the images alone). Adding a first entry is a single "Add Work Experience"
  button when the section is empty; subsequent entries use a "+" that offers "new company and
  position" or "new position at an existing company." Bullets are added per-position via an "Add
  Bullet" button, with AI assist tools (Bullet Coach, Bullets AI, Bullet Assistant) attached.
  [Work Experience](https://help.tealhq.com/en/articles/10620365-work-experience)
- **Design mode has 4 sub-tabs**: **Presentation** (templates, font, line height, list line height,
  accent color via presets or hex/hue picker, date format), **Sections** (drag-and-drop reorder;
  rename via a pencil icon; Teal does not support adding custom sections — only renaming existing
  ones), **Settings** (date/location display and alignment), **Advanced** (Teal+ only: bullet
  formatting, text, line spacing, section titles).
  [Presentation Tab](https://help.tealhq.com/en/articles/9510029-presentation-tab),
  [Sections Tab](https://help.tealhq.com/en/articles/10644765-sections-tab),
  [Getting Started: Resume Designer](https://help.tealhq.com/en/articles/9508951-getting-started-resume-designer)
- **Template picker: an in-panel "Template Library," previewed with the user's own content.**
  "In the 'My Templates' section, click 'Add Template' to open our Template Library. Here, you can
  browse the resume templates Teal offers and click on each one **to see a preview of how it would
  look with your experiences**. Once you select your desired template, click 'Save Template' to
  apply it." This is the one clear case among the four products where the picker itself is
  confirmed (by the vendor's own words) to render live off the user's saved data, not a static mock.
  [Presentation Tab](https://help.tealhq.com/en/articles/9510029-presentation-tab)
  A separate public marketing/SEO landing page claims "100+ Free Resume Templates and Formats," but
  that's a different surface (downloadable static templates for non-users), not evidence about the
  in-app Template Library's size.
  [tealhq.com/resume-templates](https://www.tealhq.com/resume-templates)
  Page setup (Letter/A4, margins) is also under Presentation, not a separate mode.
- **ATS-friendliness is a blanket FAQ claim covering all templates, not per-template picker text.**
  "All of Teal's resume templates adhere to ATS-friendly guidelines (i.e., simple layouts without
  excessive columns/graphics/tables, minimal design elements, standard headings for sections, and
  text-based export formats like .pdf)."
  [FAQ](https://help.tealhq.com/en/articles/9592114-frequently-asked-questions-faq)
- **Tailoring for jobs is a named, documented workflow — Teal's most distinctive pattern.** The
  philosophy: build one exhaustive "wardrobe" resume (15–20 bullets per position; "Every job you've
  held," not "Your final, send-to-employers resume"), then create tailored resumes that pull from
  it. **"Resume Syncing"**: new content added anywhere is instantly available in every resume as a
  toggle; editing a bullet offers "Save to all resumes" (opt-in propagation) or stays local to that
  version; un-synced edits leave an "Update Available" flag on the other resumes. Tailoring a
  specific application = open the Job Tracker entry, check the "Match Score" (via the Job Matcher),
  then toggle bullets on/off for that version and export — "You're not editing your main resume.
  You're creating a view of it, customized for this specific job."
  [How to Build Your Resume in Teal](https://help.tealhq.com/en/articles/14435724-how-to-build-your-resume-in-teal)

## Standard Resume

**No dedicated help center was located** (`standardresume.co/help`, `/faq`, and `help.standardresume.co`
all 404 or failed to resolve, checked 2026-09-10) — the findings below come from the product's own
marketing pages and, notably, an **actual screenshot of the live editor** that the marketing site
itself serves from its asset CDN (`assets.standardresume.co`), which is a stronger source than page
copy since it shows real UI chrome and labels rather than a paraphrase.

- **Top chrome: one band, three tabs — "Write | Preview | Share."** Confirmed directly from the
  screenshot below; "Preview" is the active tab in the captured state.
- **Design controls live in a dedicated rail inside "Preview" mode, not beside the content form.**
  The left rail (dark background) is headed "Design options" and holds: "Change template" (grid
  icon), "Add a profile photo" ("Not all templates support profile photos"), then "Template colors"
  as **seven independently-named swatches** — Titles, Primary text, Subtitles, Captions, List item
  background, List item text, Background — each opening a picker with a gradient pane, hue slider,
  and hex field (shown open on "List item background," value `#6D7A7E`). This is more granular,
  element-level color control than any of the other three products document.
  Screenshot: [assets.standardresume.co/.../resume-designer-app](https://assets.standardresume.co/q_auto,f_auto,c_fill,w_1872,h_1480/v1/landing-pages/resume-designer-app_zth4pv.png),
  served from [standardresume.co](https://standardresume.co/) (homepage).
- **The screenshot implies Write and Preview are separate modes, not a simultaneous split view** —
  the captured "Preview" state shows only the rendered page + Design rail, no visible content-entry
  form; presumably switching to "Write" shows the form and hides this rail. This is inferred from
  the tab labels and the single-mode screenshot, not confirmed step-by-step (the product is behind
  a signup gate and wasn't used live), so it's flagged as the weakest-evidence claim in this
  section, worth re-checking if this pattern is being seriously considered.
- **Template picker**: "Change template" is a menu item with a grid glyph inside the Design rail;
  the exact resulting UI (dropdown vs. modal gallery) isn't captured in the available screenshot.
  **12 templates total** — "Choose from 12 professionally designed and hiring manager approved
  resume templates" — in **4 style categories**: Simple, Modern, Creative, Professional.
  [standardresume.co/resume-builder](https://standardresume.co/resume-builder),
  [standardresume.co](https://standardresume.co/) (homepage, "Choose from 12 uniquely designed
  resume templates").
  The public template-examples page shows only one template with an explicit ATS callout — Georgia:
  "as eye-catching as an infographic resume and as easy to read as a traditional resume... without
  ... breaking applicant tracking systems" — the other templates carry no per-template ATS text,
  only the blanket "Hiring manager approved" framing.
  [standardresume.co/resume-templates](https://standardresume.co/resume-templates)
  A marketing collage shows the *same* sample profile ("Dana Andrews, Aerospace Engineer") rendered
  across several different templates side by side, consistent with templates being driven by real
  content rather than being purely static mockups — though this is a marketing asset, not a capture
  of the in-app gallery itself.
  [assets.standardresume.co/.../resume-templates-grid-narrow](https://assets.standardresume.co/q_auto,f_auto,c_fill,w_1648,h_718/v1/resume-examples/resume-templates-grid-narrow)
- **Tailoring for jobs is duplicate-based, like Reactive Resume — no per-version toggle system.**
  "Duplicate your resume in one click and customize the new resume for a specific application... We
  recommend that you duplicate your existing resume for different jobs and customize the contents
  and design [of the copy]." No content-syncing between duplicates is mentioned (contrast with
  Teal's Resume Syncing).
  [standardresume.co/resume-builder](https://standardresume.co/resume-builder)
- **No primary source found** for the content-entry form's own layout (inline vs. modal per entry),
  the first-run empty state, or exact template-picker interaction mechanics beyond the icon shown.

## Patterns worth adopting

- **Reactive Resume — template gallery as a full-screen modal with instant apply + Undo toast.**
  Switching is a single click, reversible in one more click via the toast action, so browsing
  templates never feels risky. (`dialogs/resume/template/gallery.tsx`)
- **Reactive Resume — per-template one-line description + tag badges, shown in both the compact
  right-rail summary and the full gallery.** A tag vocabulary ("ATS friendly," "Two-column,"
  "Executive," …) lets users filter mentally without the app needing real filtering. (`dialogs/
  resume/template/data.ts`)
- **Teal — template previews rendered from the user's own saved content, not a generic mock.**
  "click on each one to see a preview of how it would look with your experiences" answers the
  exact question a template picker exists to answer — will *my* resume look good in this — instead
  of making the user extrapolate from a stranger's placeholder text.
  (help.tealhq.com/en/articles/9510029-presentation-tab)
- **Teal — Resume Syncing for tailoring: one content pool, toggle-per-version, explicit
  opt-in propagation with "Update Available" flags.** This solves the exact failure mode plain
  duplication creates (the same fix has to be manually re-applied to every past tailored copy) while
  still letting a version diverge intentionally. It's the most sophisticated answer among the four
  to "how do multiple tailored resumes stay usable over time."
  (help.tealhq.com/en/articles/14435724-how-to-build-your-resume-in-teal)
- **Standard Resume — element-scoped color naming (Titles / Primary text / Subtitles / Captions /
  List item background / List item text / Background) instead of one "accent color."** More
  controllable without needing a full CSS-level styles escape hatch. (assets.standardresume.co
  resume-designer-app screenshot)
- **Reactive Resume — section-level drag-and-drop lives in a small dedicated Layout board (per page,
  per column), separate from the always-present per-entry reorder list**, so "move this whole
  section to page 2's sidebar column" and "move this one bullet up" don't compete for the same drag
  affordance. (`-sidebar/right/sections/layout/pages.tsx`)

## Patterns to reject

- **Standard Resume — Write and Preview as separate tabs/modes (inferred), rather than a
  simultaneous split view.** If confirmed, this forces a mode-switch to see the effect of a content
  edit, which is exactly the friction a live preview exists to remove; Reactive Resume and Teal both
  keep content and preview visible together. Flagged as inferred, not confirmed — worth a quick
  live check before treating this as settled, but not worth copying speculatively either way.
- **Reactive Resume — entry editing via a full dialog for every field, including single-line ones
  (e.g. renaming a company).** A dialog for a one-line edit is more modal friction than the same
  edit inline would need; Teal's pencil-icon-to-form pattern for entries has the same shape, so
  this isn't unique to Reactive Resume, but it's still worth naming: for small fields (job title, a
  URL), inline editing beats a modal round-trip. (`-sidebar/left/shared/section-item.tsx`)
- **FlowCV / marketing-only "100+ templates" style counts (also echoed by Teal's separate SEO
  landing page).** A large template count sourced from a public marketing/SEO gallery is not
  evidence about what the in-app picker actually offers signed-in users — the two surfaces are
  demonstrably different products (Teal's own in-app "Template Library" is a distinct, smaller
  surface from its `/resume-templates` landing page). Don't let a marketing-page count stand in for
  the real in-app picker size in any design decision.
- **Teal — Advanced design controls (line spacing, bullet style, section titles) gated behind a paid
  tier and hidden in a 4th sub-tab.** However reasonable as a business model, as a UX pattern it
  means the deepest customization is both hardest to find (tab 4 of 4, inside a whole separate mode)
  and the one users are most likely to want right before finishing — worth avoiding that specific
  combination of "hardest to reach" and "highest intent" for any paywalled control.
  (help.tealhq.com/en/articles/9508951-getting-started-resume-designer)

# Website journey review

Fixed point: merge-base `10e45e04fbfbec1f421db354020e3e80a20afc0b`.
Reviewed implementation: `416f3913` and `8185c31a`.
Command: `git diff 10e45e04fbfbec1f421db354020e3e80a20afc0b...HEAD`.
Two independent agents reviewed Standards and Spec under the required code-review skill.
The sources were CLAUDE.md, the UI README/ADR ownership contract, and the user's full-site
customer/marketing request recorded in the dated design-research report.

## Standards

- **P2 — Keep product controls out of native résumé printing.** The new résumé `.page-head`
  and `.rb-template-open` were omitted from the print hide list. At 390px with print media,
  Chromium showed orientation and template controls before the document. This violated the
  documented separation of application furniture and physical résumé output.
- **P3 — Center the percentage inside its ring.** Adding the label expanded `.match` to 68px,
  while its absolute percentage occupied the full wrapper. Chromium measured an 8px offset
  between percentage and ring centers. The layout owner must retain ring alignment.

No other hard standards violations were found in module seams, CSS ownership, navigation
capability gates or commit attribution. Priority is a judgment call; the defects were
browser-reproduced rather than inferred from endpoint behavior.

## Spec

- **P2 — Reveal the result of moved résumé import.** The manual-first layout moved import
  beneath the editable fields, but copy still said "fields below" and status appeared in the
  offscreen page header. On a 390px phone, neither the populated fields nor acknowledgment
  was visible. This missed immediate/local feedback and coherent manual-first placement.
- **P2 — Retain refused rename input.** The dialog closed before POST and a 503 discarded
  its attempted name; reopening restored the stored name with no Retry. This missed the
  research contract requiring retained answers and retry after transport failures.

A follow-up P2 finding caught Retry still targeting the prior search after selection changed. Retry now derives from the active search and drafts are pruned when their search is deleted.

No unasked scope creep or other material spec regression was found. Browser spot checks
covered all panels at 320/390/768/1024/1440px. Marketing claims distinguish source findings,
HeadStart hypotheses and fixture verification. The compact tour's unavailable/hidden
Trends target was noted as an observation, not a blocking finding.

Total findings: Standards 2 (worst: native printing); Spec 3 (worst: retry targeting an unselected search).

## Resolution and verification

All five findings were fixed. The percentage is constrained to the ring's 52px box. Print
hides both product additions and the compact template rule is screen-only. Import feedback
lives beside Read, and success reveals/focuses the editable profile heading. Refused rename
names are retained, reopening uses the draft, and Retry resends it without retyping.

Browser regressions assert alignment, print exclusions, visible import context and retained
rename/retry behavior. CI installs Chromium so these checks run on the runner. No change to
production endpoint or credential behavior was needed for these findings.

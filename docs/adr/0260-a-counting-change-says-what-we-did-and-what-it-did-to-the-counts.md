# ADR-0260: A counting change says what we did and what it did to the counts

**Status:** accepted · **Date:** 2026-09-28 · **Extends:**
[ADR-0248](0248-the-trends-tab-speaks-to-a-job-seeker.md) and
[ADR-0255](0255-every-tab-speaks-to-a-job-seeker.md) (plain words) · **Relates to:**
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (counting changes),
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the reading)
· changes no figure, reading field, API field or stored data

## Context

The Trends tab lists every counting change in the window under "Marked changes", beside each
chart marker and under a company's "Not hiring". It named them as the pipeline's steps: "tech-job
filter updated", "experience and salary reading updated", "duplicate postings detection updated",
"job categories re-sorted", "job category list edited". The owner read them as a job seeker would
and could not tell what "tech-job filter updated" meant for the jobs, or why a line jumped there
(issue #756, item 1).

## Decision

**Each change says what HeadStart did, then what that did to the jobs counted**, in one line:

| Field | What we did | So some jobs … |
|---|---|---|
| tech filter | got better at spotting tech jobs | were added to or dropped from the counts |
| duplicate removal | got better at spotting the same job posted twice | were added to or dropped from the counts |
| family classifier | sorted jobs into categories more accurately | moved to a different category |
| family list | changed our list of job categories | moved to a different category |
| taxonomy refit | redrew our job categories | moved to a different category |
| derivations | read experience and salary from job posts more accurately | moved to a different experience level |

"Sep 17 15:26 tech-job filter updated" reads "Sep 17 15:26 we got better at spotting tech jobs, so
some jobs were added to or dropped from the counts". A change of several fields is **one
sentence**, not one sentence a field: "we changed our list of job categories, got better at spotting
the same job posted twice and sorted jobs into categories more accurately, so some jobs moved to a
different category or were added to or dropped from the counts". Each effect is said once.

`netting.METHODOLOGY_WORDS` stays the one home of these words, now as `(what we did, what some
jobs did)`, and `netting.change_label(fields)` and `netting.echo_label(fields, day)` build every
label from them. The week-later echo under New reads "a week after we got better at spotting tech
jobs on Sep 11, the jobs that change moved stopped being new". The change of New to the week's
openings reads "we started counting “New this week” as the jobs opened that week, not the jobs
first seen, so this line jumps once here". The payload's per-field `epochs[].changed` carries each
field's own sentence.

The effect is neutral ("added to or dropped from"), never a direction: a filter change moved
Microsoft +133 on Sep 17 and −2 on Sep 24, and the size beside the label already says which way.

## Consequences

- Labels are longer: about 18 words for one field, 35 for the three-field Sep 25 change. They sit
  in a folded list and a folded "Not hiring" note, where a reader has asked for them, and the
  chart marker's tooltip carries the same text.
- Labels start in lower case after the date, as "duplicate postings removed" and "3 more job sites
  found" already did.
- Golden readings changed in labels only (checked by stripping `label` and `changed` from every
  golden before and after: identical).

## Options not taken

- **A short name plus a separate explanation field.** Clearer to lay out, but it adds a reading
  field and a page change for one sentence the label can carry.
- **Joining each field's own sentence with commas.** The first cut did this on the index view and
  read "…, so some jobs were added to or dropped from the counts, we sorted jobs …, so some jobs
  moved …". The fields the note names are now kept on the note, so every view builds one sentence.

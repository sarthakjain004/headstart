# ADR-0252: A Workday department is read off the family slice that listed it

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:** [ADR-0017](0017-tech-role-filter.md) (the post-hoc tech gate whose department rule this feeds), [ADR-0073](0073-narrow-six-retail-workday-boards-at-the-source.md) (the six fixed-facet Boards)

## Context

`workday.py` served `department` from `item["jobFamilyGroup"]` on the listing item, and the tech
detail gate read the same key. No listing item carries it: 0 of 20 items on each of cmu,
blackline, genmills, accenture and nvidia (2026-09-28), and the detail's `jobPostingInfo` has no
category key either. So `department` was None on every Workday Job (genmills 0/337), and the tech
filter's "vague title under a technical department" rule could never fire for Workday.

A posting's family is knowable only from the `jobFamilyGroup` facet: a query filtered to one family
lists that family's postings, and every listing page's own facet list names each family id.

## Decision

**A posting read inside a query pinned to exactly one `jobFamilyGroup` value takes that family's
name as its `department`.** That covers every capped Board (the crawl already subdivides it on
`jobFamilyGroup` first) and the four single-family ADR-0073 Boards. It costs no request: the name
comes off the slice's own facet list. A posting first read on a capped Board's unfiltered root page
takes the family its slice later names.

Every other Board still serves `department` None.

## Alternatives

- **Slice every Board by family.** This would name the department on every Board. It costs one
  listing query per family on every Board, where one unfiltered pass reads the whole Board today.
  genmills would go from 17 pages to about 25, and most Boards are a single page that would become
  one page per family. Workday is the largest share of a run's wall-clock, and the gain is one
  gate rule. Rejected.
- **Drop the field and the gate input as dead.** Honest, but it throws away a signal the crawl
  already holds on the Boards where the gate matters most (the largest ones). Rejected.

## Consequences

Measured live 2026-09-28, old code against new, same session: nvidia (capped) went from 0 to 2,644
of 2,651 Jobs with a department, and walmart (fixed facets) from 0 to 863 of 863. genmills
(uncapped) is unchanged at 0 of 337. On a capped Board, a vague title under a technical family now
passes the detail gate, so it gets a description where it had none before.

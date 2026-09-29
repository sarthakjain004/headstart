# ADR-0280: Ashby declares how a link writes its slug

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0271](0271-a-scraper-declares-whether-discovery-keeps-its-slugs-casing.md) (the same
one-declaration pattern, for casing) · **Issue:** #864 (a code-review follow-up to #843 and #868)

## Context

Six discovery scripts read an Ashby slug out of a link, and each kept its own regex:
`resolve/fingerprint.py`, `discover/fingerprint_careers.py`, `mine_ashby.py`, `cc_miner.py`,
`consolidate_harvested_lists.py` and `investigate.py`. An Ashby slug is a path segment, and two
kinds of Live row hold characters most of those regexes did not accept.

- **A dot.** 130 Live rows are a Company's domain used as its slug (`ambient.ai`,
  `careers.azx.io`). `resolve/fingerprint.py` cut each at its first dot, and
  `fingerprint_careers.py`'s trailing lookahead dropped it. The cut names another Board or none.
  Of the 128 prefixes long enough to keep, 109 answer 404 from the posting API and 19 answer 200
  as some other Board: `primer` lists 26 postings where `primer.io` lists 28, and `affinity` lists
  none where `affinity.co` lists 8 (2026-09-29). `resolve/fingerprint.py` writes its page hits
  without asking the API, so a cut became a hit for the wrong company.
- **A space.** 30 Live rows hold one, and a link writes it `%20` (`Blackpoint%20Cyber`), as do
  Wayback's and Common Crawl's captures. #868 taught the two fingerprinters to read it.
  `mine_ashby.py`, `cc_miner.py` and `consolidate_harvested_lists.py` still cut the slug there
  (`Flock%20Safety` read as `flock`).

#868 also read a `+` as a space and kept a trailing `%20` on the slug. Neither holds on Ashby.
It reads a `+` in the path as itself: the posting API answers 404 for `Blackpoint+Cyber`,
`Flock+Safety` and `elveo+`, and 404 for `elveo%20` where `elveo` lists 21 (2026-09-29). No `+`
appears in any Ashby link across the local harvests, and `%20` appears in dozens.

## Decision

**`AshbyScraper` declares how a link writes one slug, and every script that reads an Ashby link
uses it.** `slug_in_link` is the regex a capture group wraps. It accepts letters, digits, `_`,
`-`, `.` and `%20`. `slug_from_link` reads a capture back: each `%20` becomes a space, and a space
or dot at either end is dropped, since a trailing `%20` or a sentence's full stop is not part of
the slug. Both fingerprinters, `mine_ashby.py`, `cc_miner.py` and
`consolidate_harvested_lists.py` build their Ashby patterns from the first and decode with the
second.

- **A `+` is not decoded.** The regex's lookahead will not end a capture before a slug character,
  a `+` or a `%`. So a link it cannot read whole, such as `Blackpoint+Cyber` or `acme%2Fjobs`,
  yields nothing, never a prefix that names another Board.
- **`resolve/fingerprint.py` no longer screens a dotted Ashby slug by its leading label.** That
  screen exists for host-shaped slugs (`www.eightfold.ai`). `careers.azx.io` lists 8 postings,
  and its leading `careers` is in the script's `BLOCK`.
- **It is declared on `AshbyScraper`, not on `BaseScraper`.** ADR-0271's `keeps_slug_case` is on
  `BaseScraper` because both fingerprinters ask every ATS the same question. Here no other
  supported ATS's slug holds a character a link encodes, so a declaration every scraper carries
  would be speculative.

`investigate.py` is unchanged. Its page scan records only the matched host
(`jobs.ashbyhq.com`), so a cut slug never reached its output.

## Alternatives

- **Fix each script's own regex.** That is how the scripts drifted: #868 fixed two of the six, and
  the other three kept cutting at the space.
- **Decode `+` as a space, as #868 did.** It would recover the Board a broken link was meant for.
  But Ashby itself answers 404 to such a link, and no captured link uses one. Reading it as a space
  claims something the ATS does not do.
- **A `slug_in_link` on `BaseScraper` that every discovery pattern is built from.** It would
  retire every hand-written slug regex, but every ATS's patterns would change for a problem only
  Ashby has today.

## Not covered

Two paths still drop an Ashby slug with a space, rather than cutting it:
`wayback_feeder.extract`, whose path-slug check rejects `%`, and `consolidate_harvested_lists.py`'s
slug-column and filename-keyed strategies, whose `valid_slug` rejects a space for every ATS but
the URL scan's Ashby matches. Neither can mint a wrong Board. Each misses one.

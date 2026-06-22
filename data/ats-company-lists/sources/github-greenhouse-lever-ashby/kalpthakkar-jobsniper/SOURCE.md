# Source: kalpthakkar/JobSniper

- **Source URL:** https://github.com/kalpthakkar/JobSniper
- **File pulled:** https://raw.githubusercontent.com/kalpthakkar/JobSniper/main/parser/data.json (~69 MB, 194,916 scraped job records)
- **Author/repo:** kalpthakkar (GitHub)
- **ATS provider(s) covered:** Greenhouse, Lever, Ashby (the raw file also contains Workday, Oracle Cloud HCM, GoHire job URLs — not extracted here)
- **Approx entry count (this lane, after dedupe + cleanup):**
  - Greenhouse: **1,711** unique slugs
  - Lever: **819** unique slugs
  - Ashby: **1,407** unique slugs
  - (3,937 total greenhouse+lever+ashby)
- **How accessed:** `curl` of the raw `parser/data.json`; slugs extracted from each record's `applyUrl` field by regex on the live board host:
  - Greenhouse: `(?:boards|job-boards).greenhouse.io/{slug}`
  - Lever: `jobs.lever.co/{slug}`
  - Ashby: `jobs.ashbyhq.com/{slug}`
  URL-decoded and lowercased. 41 Ashby slugs contain spaces (display-name URLs, e.g. `tools for humanity`) — these are real Ashby boards and kept as-is.
- **Date accessed:** 2026-06-23
- **License:** None declared in repo.
- **Description:** `JobSniper` is a multi-ATS job aggregator. `parser/data.json` is its scraped job-listing dump with real `applyUrl`s pointing at live company boards. These slugs are REAL (extracted from actual posting URLs that were live when scraped), not name-derivation guesses. Repo last updated 2026-05-10.
- **Note:** Also contains `data/depreciated_tokens.txt` = tokens that FAILED liveness (expired/dead boards) — logged as a lead, not saved here as a live list. The repo's `parser/generate_board_tokens.py` is the script that classifies applyUrl hosts into ATS buckets.

## Files saved
- `greenhouse_slugs.txt` — 1,711 slugs, one per line
- `lever_slugs.txt` — 819 slugs, one per line
- `ashby_slugs.txt` — 1,407 slugs, one per line

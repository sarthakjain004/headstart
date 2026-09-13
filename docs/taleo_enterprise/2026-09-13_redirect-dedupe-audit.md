# Taleo Enterprise redirect duplicate audit

Run after the 2026-09-13 liveness cut, against all 556 `live` rows:

```console
$ PYTHONPATH=src python scripts/validate/dedupe_boards.py --ats taleo_enterprise
taleo_enterprise: probing 556 of 556 live Board(s)
  [250/556] ...
  [500/556] ...

0 duplicate cluster(s), 0 Board(s) to bury

summary: {} | duplicates 0
```

This is intentionally a dry run: there is no alias ledger to write when the resolved full Career
Section identities produce no clusters. The same frozen candidate pool has 7,442 rows, 7,442
unique canonical URLs, and 7,442 unique `(host, Career Section)` identities. Re-run the command
after discovery adds rows; `TaleoEnterpriseScraper.alias_key()` compares the final canonical Career
Section URL, never just the shared `*.taleo.net` host.

# SOURCE: Remotive "900+ Startups Hiring Remotely" public Google Sheet

- **Source URL:** https://docs.google.com/spreadsheets/d/1TLJSlNxCbwRNxy14Toe1PYwbCTY7h0CNHeer9J0VRzE (read via gviz CSV export: .../gviz/tq?tqx=out:csv). A sibling sheet "150+ Remote Startups Hiring (April 2026)" exists at /d/18bljq3y5YTxPLmA4xj10EaKxs1KWhIdLTr8bF1K5XKI but rendered empty over fetch.
- **Author / community:** Remotive.com (remote-work job community). Publicly shared community job-hunt spreadsheet.
- **ATS provider(s) confirmed in rows:** Greenhouse, Lever, Recruitee, Breezy, Workable (from the careers-URL column on the A–M rows). Most other rows link to company-owned /careers or AngelList/RemoteOK.
- **Approx count:** ~900 company names total in the sheet; full name roster captured (companies-roster.md). ~15 rows have a directly confirmed ATS provider+slug (companies-with-ats-slugs.md).
- **Access:** web, free (public Google Sheet; data pulled via gviz CSV — the htmlview renders empty without JS).
- **License / terms:** Community-shared sheet; no explicit license. Attribution: Remotive.com.
- **Description:** A large community list of remote-first startups with careers links. Best used as a NAME seed; the URL column directly yields ATS slugs for a subset, the rest need resolution. Skews remote/global tech.

## Limitation
gviz returned the careers-URL column only for the A–M block; the N–Z slice came
back with platform/status/name columns but no URL, so ATS confirmation there is
pending (LEAD: re-pull with an explicit column/gid selection to recover N–Z URLs).

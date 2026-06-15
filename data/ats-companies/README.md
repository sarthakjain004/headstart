# ATS company lists (coverage universe)

These CSVs are the full set of known company boards per ATS — the universe HeadStart can
draw from. The live scrape uses a curated subset in `config/companies.toml`; these files
are the source pool for growing that subset.

## Source & attribution

Copied from the open-source **jobhive** project
([kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers), MIT-licensed) on
2026-06-15, which builds these lists largely from Common Crawl. Only the ATSs HeadStart
currently supports are included: Greenhouse, Lever, Ashby. Columns: `name,slug,url`.

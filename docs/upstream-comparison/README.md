# Upstream scraper comparison — `kalil0321/ats-scrapers`

A 1:1 comparison of this repo's scrapers against the open-source
[`kalil0321/ats-scrapers`](https://github.com/kalil0321/ats-scrapers), run **2026-09-22** with
upstream at `6b44a1b` and this repo at `8efb9a85`.

31 of our 38 scrapers overlap with theirs. They were compared in five groups, each reading **both**
implementations in full and probing real hosts wherever a claim rested on live behaviour. Every
finding carries its sample size; findings resting only on a code read say so.

| Report | Scrapers |
| --- | --- |
| [group1-core.md](group1-core.md) | workday, greenhouse, lever, ashby, smartrecruiters, workable |
| [group2-enterprise.md](group2-enterprise.md) | icims, oracle, phenom, taleo_enterprise, successfactors, eightfold |
| [group3-midmarket.md](group3-midmarket.md) | bamboohr, gem, jazzhr, jobvite, personio, teamtailor |
| [group4-smb.md](group4-smb.md) | darwinbox, keka, join, recruitee, rippling |
| [group5-bigtech.md](group5-bigtech.md) | amazon, apple, google, meta, tesla, tiktok, uber, bytedance |

Trakstar was compared separately in the parent session: upstream files it under its pre-rebrand
name `recruiterbox`, so it did not name-match and no group covered it.

**Findings are tracked as GitHub issues #530–#558, indexed by #559.**

## What the comparison concluded

Upstream has no listing surface we lack on 30 of the 31 overlapping scrapers, and nothing upstream
has any counterpart to our truncation and eviction accounting (ADR-0053, ADR-0083, ADR-0121) — to
them a short list and a complete one are the same object.

The value ran the other way. Reading a second independent implementation of the same endpoints
exposed defects in **our** code that reading our own code had not: a `department` parser keyed on a
field its API never ships, three scrapers silently serving one location for a multi-location
posting, a date format that cannot parse what the live page returns, and a hardcoded `None` that had
been running an entire ATS's tech gate on title alone.

## Reading these reports later

Each is a snapshot of two codebases on one day. Line numbers drift, and every live measurement has a
date attached for a reason — **re-measure before acting on a figure**, rather than citing one of
these numbers as current.

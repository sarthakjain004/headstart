# Zwayam tenant discovery — the working plan

_Measured 2026-08-27. Supersedes the "discovery is the cost / boards live on customer domains, so
there is nothing to enumerate" note in `CLAUDE.md` and
`experiment/ats-provider-expansion/artifacts/research_zwayam.md`._

**Result: 9 known Boards → 757 live (224 hiring), 22,456 postings.** Ledger:
`data/validate/liveness/zwayam.csv`. Working notes and raw captures:
`experiment/india-ats-priority/` — **untracked**, since `.gitignore` excludes `experiment/`, so
this file is the durable record and the working notes are local to whoever ran the sweep.

## Zwayam has two Board namespaces, not one

The prior research saw only the first and concluded the provider was unenumerable.

**1. Customer domains** — `careers.persistent.com`, `jobs.happiestminds.com`, `career.crisil.com`.
The enterprise tier, and where essentially all the volume is. No namespace to walk — but see below,
because the TLS certificate hands you the roster.

**2. `{slug}.openings.co`** — a shared tier for tenants with no vanity domain. `CLAUDE.md` records
these hosts as dead; that is **true of `{slug}.zwayam.com`** (every label, real or not, answers with
the wildcard `13.71.116.55` and no CNAME) and **false of `openings.co`**: `impetus.openings.co`
serves 27 jobs and `trask.openings.co` 232 through the same public API. It is a separate tenant
pool, not a mirror — `persistent.openings.co` does not exist.

**The two namespaces are disjoint, and that measures cleanly: 0 of 109** slugs taken from the
customer-domain roster resolve to a live `{slug}.openings.co` Board. This matters because it is a
trap. A sweep that takes its slugs from the customer-domain tier — the obvious thing to do, since
that is the roster you get first — returns `data: null` for *every* host and is indistinguishable
from a bogus control, so it reads as "openings.co is dead" no matter how carefully it is run. One
agent reached exactly that conclusion this way. Right method, wrong slug source: openings.co slugs
are often the tenant's full registered legal name
(`icicilombardgeneralinsurancecompanylimited`), which no brand name derives.

## Step 1 — read the certificate (this is the whole plan)

One TLS handshake against any known Board returns the customer roster, because Info Edge terminates
TLS for its customers and batches their hostnames onto shared certs:

```bash
echo | openssl s_client -connect careers.persistent.com:443 -servername careers.persistent.com \
  2>/dev/null | openssl x509 -noout -subject -ext subjectAltName
# subject=... O=INFO EDGE (INDIA) LIMITED, CN=www.zwayam.com
# SAN: careers.coforge.com, careers.cyient.com, careers.microland.com, careers.cult.fit, ... (74)
```

**Handshake several Boards, not one** — there are at least three certs, partitioned by Akamai edge,
carrying 74 / 48 / 14 SANs for 123 distinct hostnames. One Board sees roughly half the roster.

Full technique, including where else to apply it:
[`shared-cert-tenant-rosters.md`](./shared-cert-tenant-rosters.md).

## Step 2 — verify every candidate against the API

A SAN proves provisioning, never that the Board is live. **One unauthenticated POST per hostname**:

```bash
curl -s --compressed -X POST "https://public.zwayam.com/jobs/search" \
  -H "accept: application/json, text/plain, */*" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ... Chrome/124.0" \
  -H "Origin: https://careers.persistent.com" -H "Referer: https://careers.persistent.com/" \
  -F 'filterCri={"paginationStartNo":0,"selectedCall":"sort","sortCriteria":{"name":"modifiedDate","isAscending":false},"anyOfTheseWords":""}' \
  -F "domain=careers.persistent.com" -F "companyId=MQ=="
```

Three corrections to the captured protocol in `research_zwayam.md`, each verified this session:

- **`companyId` is ignored.** On 4 of 4 Boards, `base64("1")` returns the correct count, and passing
  *another tenant's* real id with Persistent's `domain` returns Persistent's 722. `domain` plus a
  `domain` alone is the entire key (`Origin`/`Referer` are ignored — see below) — so the documented
  config→id→search flow is **one call, not two**, and discovery needs no config call at all.
- **A non-stock `User-Agent` is required, and a missing one hangs.** `curl` and `python-requests`
  defaults both time out rather than answering — not the documented 403, and not an empty body.
- **Live vs not** is read from the body, never the status: a non-Board answers `HTTP 200` with
  `"data": null`; a Board carries `data.totalCount`.

**There is no reproducible rate limit — an earlier claim here overstated it.** A deliberate load
test on 2026-08-27 could not make the endpoint refuse: ~2,160 requests across 150 sequential,
32-wide concurrency (~94 req/s), 60 distinct `domain` values, and 1,500 sustained at 34 req/s, all
200. One `403 Access Denied` from the Akamai front was seen during 2026-08 discovery and did
survive a 150s backoff, so it is real — but it is rare and transient, **not a threshold to design
around**, and this page previously described it as one. If it does appear, rotating the spare
egress clears it (`headstart.spare_egress`; note `warp-cli disconnect`/`connect` is a no-op — only
a daemon restart moves the address).

**What actually breaks the request is the User-Agent.** `curl/8.7.1` and `python-requests`'s own
default both **hang** rather than answering — no status, just a read timeout — so a caller that
reads a timeout as a transient fault retries forever. Any non-stock agent works, including this
repo's own `headstart/0.1 (job-board reader)`.

**`Origin`/`Referer` are ignored.** Omitting them returns the right Board, and sending another
tenant's Origin still returns the one named in `domain`. Earlier notes here called them part of the
key; they are not.

## Step 2b — the tenant directory (the other channel, and a permission question)

Zwayam's career-site Angular bundle calls an **unauthenticated tenant-directory endpoint** keyed by
a sequential integer id:

```bash
curl -s "https://public.zwayam.com/data-service/v2/company/16344/careersite-configurations" \
  -H "Origin: https://careers.persistent.com" -H "Referer: https://careers.persistent.com/" \
  -H "User-Agent: Mozilla/5.0 ... Chrome/124.0"
# -> {"companyName":"Persistent Systems","careerSiteUrl":"careers.persistent.com", ...}
```

Verified 2026-08-27 on ids 16344 (Persistent) and 15173 (Coforge): id → hostname + company name.
Note this is a **different endpoint** from `/jobs/search`, where `companyId` is ignored — the
"companyId is decorative" correction above does **not** generalise, and reading it that way nearly
cost us this channel.

A bounded 1,600-id sample returned 878 tenants and, after API verification, 128 serving Boards.
Extrapolated tenant population: **1,600–2,000**, well above the 350+ Zwayam claims publicly.

**Deliberately not built: a general id-enumeration scanner.** Walking the full id space would
almost certainly yield the complete tenant list, but sweeping a vendor's internal id range is a
different act from resolving hostnames we already have names for, and the permission layer refused
it. That refusal is recorded here rather than worked around — treat building it as a decision for a
human, not a default.

## Step 3 — sweep `openings.co` (cheap, low yield, do it once)

`openings.co` is enumerable two ways. Wayback CDX gives the historical roster in one query:

```bash
curl -s "http://web.archive.org/cdx/search/cdx?url=openings.co&matchType=domain&fl=original&collapse=urlkey&output=text" \
  | sed -E 's#^https?://##; s#/.*##' | sort -u
```

There is also a **DNS sieve** (`scripts/discover/mine_zwayam.py dns`): the zone has a wildcard, so
an unknown label answers `A 13.71.116.55` while a real tenant has an explicit `CNAME` to
`{slug}.ocean.edgekey.net` or `{slug}.cluster.edgekey.net`. A CNAME proves a tenant at one UDP
query — the same shape `mine_keka.py` exploits on `*.keka.com`.

**Temper expectations.** 1,232 Wayback hosts verified to **16 live Boards (1.3%)**, and a DNS sieve
over 11,446 India slugs from the Keka wordlist found exactly one. The namespace is mostly churned
2016–2019 self-serve signups. Worth one pass for Trask (232), Hyundai Mobis (69), Samsung E&A (59)
and Impetus (27); not worth building a recurring sweep around.

## Step 4 — exclude the vendor's test tenants

`fliptest.openings.co` serves **1,856 postings** and is a Flipkart test tenant. `persistentstaging`,
and anything under `.impl.` / `.preprod1.` / `.dev1.` / `.dev3.`, are non-production too. These are
non-jobs that would be served to users — the same defect class as the `labs-axisqa` / `labs-mph`
rows currently live in `ripplehire`.

**Two mechanisms, and only one of them is code.** The pattern filter above ran in the one-off script
that built the ledger, so it kept those hosts out of `zwayam.csv` in the first place — it is not a
standing guard, and an earlier version of this page wrongly implied it was. What survives a re-run
is `config.EXCLUDED_BOARDS`, which is where the two demo tenants that *did* reach the ledger
(`testcompany.cluster3.openings.co`, 77 postings titled "TEDT"/"dsf"/"14 dec";
`zhirematetest.openings.co`, "Test Job"/"Hi") are now listed. Anything found later belongs there
too — confirmed by reading the board's content, never by the slug alone.

## What it is worth (measured, not extrapolated)

Sampling 90 postings from each of the 22 largest Boards (1,980 postings) and filtering with the
repo's own `tech_filter.is_tech`:

| | share | of 22,456 |
|---|---|---|
| India | 85% | ~19,100 |
| **India + tech** | **31%** | **~7,000** |

Two samples were taken as the roster grew, and the second supersedes the first. Sample 1 (1,980
postings, the 22 largest Boards known at the time) gave 81% / 20%; sample 2 (1,277 postings, the 10
largest plus 14 drawn at random from the tail, seed 7) gave **85% / 31%**. The tail is more
tech-heavy than the head — the head carries the big non-tech Boards (`career.axismaxlife.com` 7,638
postings at ~0% tech, `careers.frankfinn.com` 437 at 0%), while the `.openings.co` tail is largely
IT staffing and services SMBs. Sample 2's stratification is the more honest of the two.

**Use the sample-2 figures, not the 55% the first pass produced.** That earlier figure was measured
on nine IT-services Boards; the full roster is far broader — life insurance, aviation training,
hospitals, ship classification, logistics, dairy — and several large Boards are ~0% tech
(`career.axismaxlife.com` 7,638 postings, `careers.frankfinn.com` 437, `careers.cult.fit` 132).

~7,000 India tech jobs is a **+14% lift** on the 50,013 India rows in the served index.

## Cost per Board, by channel

| Channel | Candidates | Live Boards | Verdict |
|---|---|---|---|
| **Tenant-directory endpoint** | 1,790 | **608** | Highest yield by far — 80% of the ledger. See the permission note in step 2b. |
| **Shared-cert SAN roster** | 145 | **98** | Do this first: 3 handshakes, no enumeration, and it owns the custom-domain tier. |
| Wayback CDX over `openings.co` | 1,232 | 16 | One pass, then stop. |
| Guess `careers.{domain}` over a company list | 832 | 9 | Only for names the cert missed. |
| Repo's own unresolved-ATS lists | 1,272 | 7 (2 new) | Free by-product; keep for other ATSes. |
| Vendor marketing / press / tech-lookup DBs | ~185 names | (names only) | Good for *names*, useless as Boards without step 2. TheirStack 404s, Enlyft and 6sense are empty or walled. |


## Two tenant classes, and why the scraper should weight them

Custom-domain hosts verify **91% live**; `.openings.co` hosts only **18%** (trials, QA, staffing
agencies). A scrape that treats the two pools alike will spend most of its budget on dead hosts.

## Hostname derivation does not work — don't build it

Measured against 119 real tenants, only **65%** are reachable as
`<careers|jobs|career|hiring>.<company-website>`. The other 35% have no derivable form:
`careers98765.sansera.in`, `www.careers-lactalisindia.com` (company site: tirumalamilk.com),
`india-jobs.wsp.com`, `isspljobs.irclass.org`. On `.openings.co` the slug is often the full
registered legal name (`icicilombardgeneralinsurancecompanylimited`), so a brand name derives the
wrong slug. This is why the cert roster and the tenant directory beat any guessing scheme — they
return the hostname rather than predicting it.

## Correction to CLAUDE.md's ATS TODO

`careers.practo.com` is a **Zwayam** Board (29 jobs, verified twice by independent channels).
CLAUDE.md's single-company-unlocks list files Practo under **Param.ai** — that entry is wrong or
stale, and Practo also appears as `practo.openings.co`.


## Fingerprinting a Zwayam Board from one no-JS GET

The first pass of this work concluded that Zwayam's tell is a DNS CNAME rather than an HTML
string, which is why its 110-company careers-page sweep read Persistent and Coforge as OPAQUE.
**That was wrong** — there is a server-rendered tell; the sweep looked for the wrong one.

Measured on 4 Boards, 2026-08-27:

| Signal | Boards | Note |
|---|---|---|
| `<app-root>` | 4/4 | Angular shell — necessary, nowhere near sufficient on its own |
| `/` → `/{slug}/` redirect, then `<base href="/{slug}/">` | 3/4 | The slug is readable straight out of the tag |
| inline `impl.openings.co` / `preprod1.openings.co` noindex guard | 1/4 | The only *Zwayam-specific* string, but not universal |

So the practical rule is the **combination** — an Angular shell whose root redirects to a
single-segment path and declares `<base href="/{slug}/">` — treated as a *candidate*, then settled
by the `/jobs/search` call in step 2. `<app-root>` alone matches half the web, and the
`openings.co` guard string is decisive but present on a minority of Boards, so neither works alone.

Note `configurationSeo.metaTags` is **not** usable as a dork: it is byte-identical across tenants,
but it is API-served and injected client-side, so the served HTML carries
`<meta name="keywords" content="">` and no engine ever indexes it.

Worth adding to `scripts/resolve/fingerprint.py`, which has the same blind spot today.

## Where DNS earns its place

`ocean.edgekey.net` is Zwayam-specific and has **no wildcard** — an authoritative NXDOMAIN for an
unknown label, NOERROR for a real tenant, with a measured **0/34 false-positive rate**. But it does
not pay as a *discovery* channel: a hit names an Akamai edge label rather than a Board hostname, DNS
cannot be walked backwards, and 30% of edge labels are underivable from the host
(`careers.practo.com` → `careerspracto`). Measured: **58,354 wordlist labels → 18 tenants → 1 new
live Board.**

Where it does pay is as a **filter in front of the API** on the `openings.co` zone, whose wildcard
has explicit-record exceptions: precision 12/17, recall 12/16 over 1,232 labels — a **72x cut** in
API traffic for a sweep of that namespace.

Also confirmed: **`https://openings.co/` 301s to `https://www.zwayam.com/`** — it is Zwayam's own
product domain, not a third party, which is the cleanest proof that the namespace is the vendor's
to enumerate.


## Final shape of the roster

**757 live Boards, 224 currently hiring, 22,456 postings.** The two tiers are very different sizes
and very different value:

- **114 custom-domain Boards** — the enterprise tier, and where the large Boards are. Found almost
  entirely by the shared-cert roster.
- **643 `{slug}.openings.co` Boards** — the shared tier, found by the tenant directory. Individually
  small, collectively substantial, and heavily IT-staffing, which is why the tail lifted the tech
  share from 20% to 31%.

Most live Boards are not hiring right now (224 of 757 carry >0 postings) — normal, and the reason
`load_active_companies` filters on `jobs >= 1` rather than on `status == live`.

# Zoho Recruit and Keka: every public URL surface a Board can be served on

_Researched 2026-09-26 for Board discovery. Primary sources only: the vendors' help centres and
API docs, the JS the vendors serve to career pages, and live responses. A "live" citation is a
measurement taken on 2026-09-26 (light probing, a handful of hosts per claim). Anything that is
documented but was not confirmed live is marked **unconfirmed**._

## What matters for discovery

1. **Zoho vanity hosts come in batches on shared TLS certificates.** A Zoho custom domain is a
   CNAME to `recruit.cs.zohohost.{dc}` ([Zoho: Setting up your domain][zdomain]; live: two vanity
   hosts on `.in` CNAME to `recruit.cs.zohohost.in`), and Zoho puts about 90 customer hostnames on
   each Let's Encrypt certificate. One handshake against `careers.yellow.ai` returned **90 SANs**.
   77 of them answered Zoho's public jobs API. 26 named a `*.zohorecruit.in` host, and **15 of
   those 26 are not in `data/validate/liveness/zoho.csv`** (live, 2026-09-26). This is the
   `shared-cert-tenant-rosters.md` technique, with one difference: the cert's subject is a
   customer host (`CN=recruit.blueneem.com`), not Zoho. The `.com`/`.eu`/`.com.au` fronts are
   different: they are shared by all Zoho products, and 0 of 33 sampled SANs there were Recruit
   (they were Backstage, Forms and People). **That sample was too small.** The discovery run the
   same day found Recruit bundles on the `.com` and `.com.au` fronts in bulk. Vanity hosts found
   by reverse-IP on `recruit.cs.zohohost.{dc}`, by CT (certspotter) and by SAN handshakes
   resolved to 1,867 new live canonical Boards that no other technique found; the run landed
   1,396 new live Boards on `.com` in all (`scripts/discover/mine_zoho_vanity_tls_sans.py`,
   `mine_zoho_custom_domains_ct.py`, `zoho_resolve_vanity_hosts.py`). Only `.eu` stayed empty:
   reverse-IP returned 0 hosts there.
2. **`recruit.zoho.{dc}/recruit/Portal.na?digest=…` resolves an archived digest to its tenant
   host.** The legacy iframe/job links put the tenant in an opaque `digest` query parameter on
   Zoho's shared host. A request without following redirects returns a 302 to
   `{tenant}.zohorecruit.{dc}`, or to `recruit.zohopublic.com` when the portal is gone. Wayback
   holds **1,792 distinct digests** under `recruit.zoho.com/recruit/`, 50 under `.eu`, 18 under
   `.in` and 4 under `.com.au`. In a 14-digest `.com` sample, 6 resolved to a tenant host (1 not in
   the ledger), 7 went to `zohopublic` (dead) and 1 returned an empty Location. Four `.eu` and four
   `.in` digests all resolved (live, 2026-09-26).
3. **Keka career portals are not all named `default`, and `keka.py` reads only `default`.** The
   embed script takes an optional `khConfig.portalName` ([Keka embed JS][kembedjs]), and per-job
   URLs carry it as `/careers/{portal}/jobdetails/{id}`. Live checks found jobs that the
   `default` read misses: `fenix` 0 on `default` vs **44** on `skillupmena`; `almirqab` 1 vs
   **19** on `amfm`; `supertechgroup` 11 vs **7 more** on `supertech`, with disjoint ids;
   `universaled` +1 on `ACIS`. Both ledger rows for `fenix` and `almirqab` say `live` with 0
   jobs. This confirms `docs/upstream-comparison/group4-smb.md` §4, which was marked
   NEEDS-LIVE-CHECK.
4. **Keka's embed snippet names the tenant in plain text.** The snippet is
   `window.khConfig = {identifier: '{org-uuid}', domain: 'https://{slug}.keka.com/careers/', …}`
   plus `<script src='https://{slug}.keka.com/careers/api/embedjobs/js/{org-uuid}'>`
   ([Keka: How to change Container ID][kcontainer]; live on achiralabs.com, landsterling.com,
   jobs.credentiai.com and badili.africa). Mining page text or embedded-resource hosts on company
   sites (urlscan: `domain:cdn.kekastatic.net AND NOT page.domain:keka.com` returned 41 results)
   yields Slugs directly. The older iframe form `{slug}.kekahire.com/api/embedjobs/{uuid}` also
   still appears (live on interviewkickstart.com).
5. **Zoho has an 11th data centre, `zohorecruit.ae`, that nothing mines.** A made-up label
   resolves and returns Zoho's `cl-error-block` soft-404, the same as the other ten
   (live, 2026-09-26). `mine_zoho.py` lists ten domains without `.ae`, and the ledger has 0 `.ae`
   rows. Wayback so far archives only `insights.zohorecruit.ae`, so the cohort is either young or
   not yet crawled.

## Zoho Recruit

### Data centres

Zoho's API docs list six DCs: US `.com`, AU `.com.au`, EU `.eu`, IN `.in`, CN `.com.cn` and JP
`.jp` ([Zoho Recruit API: multi-DC][zmultidc]). Live, 2026-09-26, `zzqqnotreal.zohorecruit.{tld}`
resolves and serves the `cl-error-block` soft-404 on **com, in, eu, com.au, ca, jp, sa, com.cn,
uk, sg and ae**. `.com.br` is NXDOMAIN. The API doc is out of date: CA, SA, UK, SG and AE are real
and undocumented there. Canada's shared host is `recruit.zohocloud.ca`, not `recruit.zoho.ca`,
which does not resolve. `recruit.zohocloud.ca` has the same IP as the `.ca` wildcard. Ledger rows
by DC (2026-09-26): com 5,661; eu 1,118; in 1,062; com.au 148; ca 90; sa 18; jp 9; com.cn 9;
uk/sg/ae 0.

### Surfaces

| URL pattern | Where the tenant id sits | `zoho.py` as-is? | Source |
|---|---|---|---|
| `https://{t}.zohorecruit.{dc}/jobs/Careers` (default hosted site; `{t}` defaults to the company's web domain label) | subdomain + DC TLD (the full host is the Slug) | **yes** | [zdomain]; `zoho.py` `url()` |
| `https://{t}.zohorecruit.{dc}/careers` | host | yes: same `#jobs` blob, same count (13 = 13 on increff) | live, increff.zohorecruit.com |
| `https://{t}.zohorecruit.{dc}/jobs/{Portal}/{jobId}/{title-slug}?source={CareerSite\|RSS\|…}` (per job) | host; `jobId` is a Zoho record id whose leading digits are the org's id prefix | yes, via the host | live RSS `<link>` on openings.2coms.com; `zoho.py` `job_url()` |
| `https://{t}.zohorecruit.{dc}/jobs/{Portal}/rss` (RSS 2.0; disabled on most Boards) | host | host yes (the scraper does not read RSS) | `docs/zoho/2026-09-07_the-rss-second-listing-surface.md` |
| `https://{host}/recruit/v2/public/Job_Openings?pagename=Careers&source=CareerSite` (public JSON, no auth) | host; each record's `$url` gives the host Zoho considers primary | host yes; returns the same 750 cap (2coms.zohorecruit.in: 750; `page=2&per_page=200` and `fromIndex=751` both returned 0) | [Zoho embed JS][zembedjs]; live on 4 hosts |
| **Vanity host** `https://careers.acme.com/jobs/Careers`: CNAME to `recruit.cs.zohohost.{dc}`, Enterprise plan, Zoho installs a multi-domain SSL cert | the customer's hostname. Fingerprints: `server: ZGS`, `static.zohocdn.com/recruit/css/career-website-*` stylesheets, `#jobs`/`org_info` hidden inputs. The string `zohorecruit` is usually absent | **scrapable as-is, but a duplicate risk.** The vanity and the canonical host serve the same Board (same `org_info.id`: 25955000000110005 on both openings.2coms.com and 2coms.zohorecruit.in; 157454000000312523 on both careers.yellow.ai and yellow.zohorecruit.in). Resolve before landing: the public API's `$url` names `{t}.zohorecruit.{dc}` on 26/77 vanity hosts; the rest echo the vanity host, and `org_info.id` is the stable key there | [zdomain]; live DNS, TLS and API, 2026-09-26 |
| JS embed on a company site: `rec_embed_js.load({… site:"https://{host}", page_name:"Careers", source:"CareerSite"})` loaded from `https://static.zohocdn.com/recruit/embed_careers_site/javascript/v1.1/embed_jobs.js` (v1.0 also archived) | the `site:` value (a zohorecruit host or a vanity host) | yes once `site` is taken as the Slug (vanity caveat above) | [Zoho: Embed jobs on your website][zembed]; [embed JS][zembedjs] |
| Legacy iframe/links: `https://recruit.zoho.{dc}/recruit/Portal.na?iframe={true\|false}&digest={d}`, plus `ViewJob.na?digest=…&embedsource={CareerSite\|Embed}`, `Apply.na?digest=…`, `PortalDetail.na` | the opaque `digest` query value | **no: resolve first.** GET without following redirects; `Location` host = tenant; `recruit.zohopublic.com` = dead ("This page is currently unavailable") | live (see finding 2); robots.txt `Allow` list on increff.zohorecruit.com |
| `https://{t}.zohorecruit.{dc}/recruit/Portal.na?digest=…`, `/ats/Portal.na`, `/ats/ViewJob.na` (the same legacy pages on the tenant host) | host | yes, via the host | robots.txt on increff.zohorecruit.com (updated 22/07/2025) |
| `https://recruit.zoho.{dc}/recruit/downloadrssfeed?digest=…` (legacy digest RSS) | digest | resolve; the one archived sample now answers "the joblist has been removed" | live, recruit.zoho.com.au |
| `/recruit/sitemapfeed`, `/recruit/downloadrssfeed` (no params) on a tenant host | host | not a listing: 302 to `IAMSecurityError.do` | live, increff and finclude |
| `/candidateportal`, `/clientportal`, `/customportal` | host | not a job listing | robots.txt |
| `recruit.zoho.com/recruit/org{N}/…` | org number | no: authenticated app pages (302 to login) | live |
| Job boards (Indeed, SimplyHired, LinkedIn via a "Zoho" XML feed) | the per-job URL carries `?source=`; the feed URL itself is not published | only if the back-link is a tenant URL (**unconfirmed**) | [Zoho: LinkedIn Basic Job Posting][zlinkedin]; [Job publishing][zpublish] |

## Keka

### Hosts

- `{slug}.keka.com/careers` is the only live careers namespace. Every label resolves: a
  wildcard-synthesised label CNAMEs to `cin02.hr.keka.com` and a provisioned tenant to its pod
  (`mine_keka.py`; live: 10decoders and achiralabs → `cin01.hr.keka.com`). `robots.txt` allows
  only `/careers` (live, kpgroup.keka.com). The careers origin reports
  `x-rate-limit-limit: 1m` with ~1,000 remaining (live).
- `{slug}.kekahire.com` is legacy. It serves at the root path, with no `/careers` prefix, and
  302s path-preserving to `{slug}.keka.com/careers/…`: `/api/embedjobs/{uuid}` →
  `/careers/api/embedjobs/{uuid}`, and `/jobdetails/123` → `/careers/jobdetails/123`. Its
  `*.kekahire.com` certificate expired 2025-03-31, so a strict-TLS client fails before the
  redirect (live, 2026-09-26). Pod records look like `cin0N.career.kekahire.com`.
- **No custom-domain feature is documented.** The Keka help centre's career-site articles cover
  only the site builder, published jobs, embed and SEO ([Managing your Career site][kmanage]).
  The careers SPA does handle being served off `/careers`
  (`fromKekaDomain = "careers" == location.pathname.split("/")[1]`, [app.min.js][kapp]), but
  that path matches the `kekahire.com` root layout. `careerportalinfo.careersPortalDomain` equals
  `{slug}.keka.com` on 60 of 60 sampled Hiring Boards (one differed only in case). Treat Keka
  vanity hosts as **not observed**.

### Surfaces

| URL pattern | Where the tenant id sits | `keka.py` as-is? | Source |
|---|---|---|---|
| `https://{slug}.keka.com/careers/` (portal page; the new builder loads the embed JS itself) | subdomain | **yes** | live, csdemo.keka.com |
| `https://{slug}.keka.com/careers/api/jobs/{portal}/active` (the listing the scraper reads) | subdomain + `{portal}` | only for `portal = default` | [app.min.js][kapp]; `keka.py` |
| `https://{slug}.keka.com/careers/{portal}/jobdetails/{id}` and `/careers/jobdetails/{id}` (case variants `JobDetails`, `jobDetails`), `/careers/applyjob/{id}`, `/careers/success/{id}`, `/careers/{id}/2/applicationform` | subdomain; `{portal}` when not default (`<meta name="portalName" content=…>` on the page) | host yes; **non-default portal no** (finding 3) | Wayback CDX over `keka.com` (9,106 `/careers/` rows); live, almirqab |
| JS embed: `<script>window.khConfig={identifier:'{org-uuid}', domain:'https://{slug}.keka.com/careers/', targetContainer:'#…', [portalName:'…']}</script><script src='https://{slug}.keka.com/careers/api/embedjobs/js/{org-uuid}'>` | `domain` host (Slug); `identifier` = org UUID; optional `portalName` | yes for the Slug; read `portalName` if present | [kcontainer]; [kembedjs]; live on 4 company sites |
| Embed JS back-end calls: `/careers/api/embedjobs/grouplinkstatus/{uuid}`, `/api/embedjobs/{portal}/active/{uuid}`, `/api/embedjobs/departments/{uuid}`, `/api/organization/{portal}/careerportalinfo` | subdomain + UUID | not needed: `/api/jobs/{portal}/active` covers the list | [kembedjs] |
| Legacy iframe: `<iframe src="https://{slug}.kekahire.com/api/embedjobs/{uuid}">`, or `https://{slug}.keka.com/careers/api/embedjobs/{uuid}` (an HTML job list) | subdomain | yes, after taking the label (the label is the same on both domains) | live, interviewkickstart.com; urlscan `domain:kekahire.com` (teaxpress) |
| `https://{slug}.keka.com/careers/api/organization/{portal}/careerportalinfo` | subdomain | company name only; `careersPortalDomain` field | live, fenix |
| Sitemap/RSS | none: `/careers/sitemap.xml` 302s to `/careers/` | n/a | live, 42gears.keka.com |
| Indeed/Glassdoor (API integration, "Indeed Apply"; jobs published to the portal auto-sync), LinkedIn (company-id integration), Monster | job-board-side; candidates can also apply "through your Career Site page" | only if a back-link is harvested (**unconfirmed**; no feed URL is published) | [Keka: Indeed][kindeed]; [Keka: LinkedIn][klinkedin] |

Search-engine caveat: job pages carry `<meta name="robots" content="noindex">` as a per-tenant
SEO setting (present on kpgroup and almirqab, absent on 10decoders; live). Dorks under-count
Keka Boards, and archive and embed mining do not have this problem.

## Search patterns to mine

**Zoho**
- CDX/CC host sweep over all 11 DCs, adding **`zohorecruit.ae`** to `mine_zoho.py` and `cc_miner`:
  `url=*.zohorecruit.{com,in,eu,com.au,ca,jp,sa,com.cn,uk,sg,ae}`.
- CDX digests: `url=recruit.zoho.{com,in,eu,com.au,jp,sa,com.cn,uk,sg,ae}/recruit/&matchType=prefix`,
  then `recruit.zohocloud.ca` (whether it redirects is **unconfirmed**). Keep only
  `Portal.na|ViewJob.na|Apply.na|PortalDetail.na|downloadrssfeed` URLs with a `digest=`, and
  resolve each digest once through a 302 with redirects not followed.
- The same digest query string on company sites (CC WARC or urlscan page content):
  `Portal.na?iframe=true&digest=`, `embedsource=Embed`.
- Embeds: page text `rec_embed_js.load` / `embed_careers_site/javascript` → take `site:"…"`.
  urlscan: `filename:"embed_jobs.js"` or `domain:static.zohocdn.com AND NOT page.domain:zohorecruit.com`.
- Vanity rosters: a TLS handshake (SNI = the known vanity) on every Zoho vanity host, snowballing
  through the SANs. Filter each SAN with `GET /recruit/v2/public/Job_Openings?pagename=Careers`
  (`code: success`). Land the `$url` host when it is `*.zohorecruit.*`. Otherwise land the vanity
  host after checking `org_info.id` against held Boards. Seed vanity hosts from DNS: CNAME to
  `recruit.cs.zohohost.{com,in,eu,com.au,…}`.
- Dorks: `inurl:"/jobs/Careers" -site:zohorecruit.com -site:zohorecruit.in -site:zohorecruit.eu`
  (vanity hosts keep the `/jobs/Careers` path).

**Keka**
- Company-site embeds: urlscan `domain:cdn.kekastatic.net AND NOT page.domain:keka.com` (41),
  `domain:cdn.keka.com AND NOT page.domain:keka.com` (19), `domain:kekahire.com` (12). Page text
  or CC WARC: `khConfig`, `/careers/api/embedjobs/js/`, `kekahire.com/api/embedjobs/`. Read
  `domain:` for the Slug and `portalName:` for the portal.
- CDX `url=*.kekahire.com` (root paths) → same label on keka.com.
- Portal discovery: in CDX over `*.keka.com/careers/`, take any path segment that is followed by
  `/jobdetails/` or stands alone and is not a known route (`jobdetails`, `applyjob`, `api`,
  `Content`, `Scripts`, `default`, `success`, …). Candidates from 2026-09-26: `almirqab/amfm`,
  `almirqab/amre`, `fenix/skillupmena`, `fenix/ticketsouq`, `supertechgroup/supertech` and
  `universaled/ACIS`. The six `…/careers` portal names seen (attentiveos, meragi, rnc, scrut,
  squadrun, unboxrobotics) now return 0 and look stale.
- DNS sieve (`mine_keka.py`) is unchanged: `cin02` is the wildcard pod.

## Open questions / not confirmed

- **How many Zoho vanity certificates exist.** crt.sh returned 502 all session, so the roster was
  measured on one certificate only. Whether Recruit-only batching holds beyond `.in` is unknown.
- Zoho's `Job_Openings` `$url` echoes the vanity host on ~2/3 of vanity Boards. `org_info.id`
  matching is the proposed dedup key, measured on 2 pairs only.
- Zoho portal names other than `Careers`: not tested. The multi-career-site feature was not
  checked in the docs, and the CDX sample reached only 19 `.in` hosts.
- Whether Indeed/LinkedIn listings link back to a tenant URL (`?source=Indeed` etc.) for either
  vendor: not measured. Neither vendor publishes its feed URL.
- Keka custom domains: none documented, none observed. A Keka pod answering for an arbitrary
  `Host` was not tested.
- How many Keka tenants run non-default portals: 4 found from archive paths. There is no
  endpoint that lists a tenant's portals (`careerportalinfo` does not enumerate them).
- Keka `grouplinkstatus` (group-linked orgs) changes the embed's fetch set. Its effect on
  `/api/jobs/default/active` was not measured.

[zdomain]: https://help.zoho.com/portal/en/kb/recruit/self-service-portal/setting-up-domain/articles/setting-up-your-domain
[zembed]: https://help.zoho.com/portal/en/kb/recruit/talent-sourcing/career-site/articles/embed-jobs-on-your-website
[zembedjs]: https://static.zohocdn.com/recruit/embed_careers_site/javascript/v1.1/embed_jobs.js
[zmultidc]: https://www.zoho.com/recruit/developer-guide/apiv2/multi-dc.html
[zlinkedin]: https://help.zoho.com/portal/en/kb/recruit/talent-sourcing/job-boards/linkedin-limited-listings/articles/linkedin
[zpublish]: https://help.zoho.com/portal/en/kb/recruit/talent-sourcing/job-boards/overview/articles/job-publishing-in-zoho-recruit
[kcontainer]: https://help.keka.com/hc/en-us/articles/39946668392593-How-to-change-Container-ID-for-Embedded-Jobs
[kmanage]: https://help.keka.com/hc/en-us/articles/39946611528081-Managing-your-Career-site
[kindeed]: https://help.keka.com/hc/en-us/articles/39946792320401-Keka-Hire-Integration-with-Indeed
[klinkedin]: https://help.keka.com/hc/en-us/articles/39946772037649-Keka-Hire-integration-with-Linkedin
[kembedjs]: https://csdemo.keka.com/careers/api/embedjobs/js/59cb9b0e-0222-4124-83f0-2282215119c5
[kapp]: https://cdn.keka.com/careers/v/2026/scripts/app/app.min.js

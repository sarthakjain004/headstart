# ADR-0303: A Zwayam Board is read from the API cluster that holds it

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0203](0203-a-row-becomes-a-board-only-through-its-scraper.md) (the probe asks what the
scraper asks; Lever's `API_HOSTS` is the same shape)

## Context

The Zwayam scraper and `check_liveness.p_zwayam` sent every request to `public.zwayam.com`. The
search selects a Board by the career-site host in its `domain` field. A host the API does not
hold answers HTTP 200 with `data: null`, and the probe reads that as DEAD.

`careers.utthunga.com` read DEAD this way, but its site lists jobs. Its Angular bundle
(`main.f1d9d671456bbe96.js`) sets its API host in a per-tenant constant:

```js
const Ga={COMPANYID:"MTUyNTY=",DOMAIN:"careers.utthunga.com",
  APIENDPOINT:"https://apic2.zwayam.com/",APIENDPOINTNEW:"https://apic2.zwayam.com/",
  JOBSHAREURL:"https://public.zwayam.com",TENANTAPIURL:"https://public.zwayam.com"};
```

The search is `POST {APIENDPOINTNEW}jobs/search`, a multipart form of `filterCri`, `domain` and
`companyId`. That is the request the scraper already sends. The bundles of
`careers.microland.com`, `careers.intimetec.in`, `careers.torryharris.com`,
`careers.persistent.com`, `careers.mirchi.in` and `anikdairy.careers-lactalisindia.com` name
`public.zwayam.com` in the same slot.

**Measured 2026-09-29.** The same request bytes were sent to both hosts, with only the URL's host
changed:

| Board | `public.zwayam.com` | `apic2.zwayam.com` |
| --- | ---: | ---: |
| careers.utthunga.com | `data: null` | 47 |
| bvrpc.cluster2.openings.co | `data: null` | 1,146 |
| careers.microland.com | 1,141 | `data: null` |
| careers.torryharris.com | 27 | `data: null` |
| careers.persistent.com | 623 | `data: null` |
| impetus.openings.co | 30 | `data: null` |

So there are two clusters, and each holds only its own tenants. `api.zwayam.com` answers as
`public` does, and `cluster2.zwayam.com` as `apic2` does. Other `*.zwayam.com` labels resolve to
the zone's wildcard address, and `api2.zwayam.com` times out. Every `*.cluster2.openings.co` row
in the ledger was dead, and 8 of its 12 hosts answer with `data` on `apic2`.

The other two calls also depend on the cluster. The config call (`data-service/v2/
public-configurations`) answers HTTP 403 on the wrong cluster, 3 of 3 Boards tried, and 200 with
the tenant's id and name on the right one. The detail call (`jobs-service/v1/jobs/careersite`)
needs that id, and it answered Utthunga's first posting on `apic2` with 3,047 characters of
description.

The two clusters share one per-IP quota. From one address, both answered 200 before a drive of 700
requests to `apic2` (74 of 100 refused at 600, 100 of 100 at 700). `public`, which the drive never
asked, then refused 5 of 5. A liveness sweep of the dead rows with one gate per cluster left 775 of
2,481 rows UNKNOWN, because each gate rotated the address the other was on. Re-probed with one gate,
those 775 left 4 UNKNOWN.

## Decision

`zwayam.API_HOSTS` is `("public.zwayam.com", "apic2.zwayam.com")`, in the order they are asked.

- **The scraper** asks each cluster for page 0 until one answers with `data`. Later pages, the
  config call and the detail calls all go to that cluster. A host neither cluster holds keeps the
  last `data: null` and reads as unregistered, as before.
- **The probe** asks each cluster in the same order. It reads LIVE from the first that holds the
  Board and UNKNOWN on the first failure. It reads DEAD only when every cluster answers
  `data: null`.
- **`check_liveness` gates both clusters as one host.** `zwayam.com` is in `_SPANNING`, so both
  API hosts share one pace and one egress rotation, and `_QUOTA_403` holds that one gate key. The
  scraper needs no change for this: its egress group is already the ATS.

A Board on `public` costs nothing extra. A Board on `apic2` costs one more request each run, and a
dead host costs one more request each time it is probed.

## Alternatives

- **Read the cluster from the site's bundle.** This is what the site itself does. It costs a
  homepage GET and a bundle of 2.5-3.5 MB for every Board on every run, and the old Angular 1
  shell (`ss.cluster2.openings.co`) keeps the setting somewhere else. Asking both clusters costs
  one small POST.
- **Store the cluster on the ledger row.** This would save the extra request for `apic2` Boards.
  It adds a column to every ledger reader for 20 of 876 live Zwayam rows, and a Board that moves
  cluster would read as dead until someone re-probed it. The extra request is cheaper.
- **Pick the cluster from the hostname.** `*.cluster2.openings.co` hosts are on `apic2`, but
  `careers.utthunga.com` is too, and no hostname rule covers custom domains.

## Consequences

- 20 rows the old probe read as dead are live on `apic2`, 18 of them hiring, with 3,804
  postings (2026-09-29). All 2,481 dead rows were probed before and after the fix, and so were
  24 other rows. No row that read live before reads otherwise after.
- Discovery that verified candidates on `public` alone missed every `apic2` tenant.
  `docs/discovery/zwayam-tenant-discovery.md` now says to verify on both clusters.
- A third cluster would read as dead in the same way. The sign would be a live career site whose
  bundle names a host that is not in `API_HOSTS`.

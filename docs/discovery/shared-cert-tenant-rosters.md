# Reading a vendor's shared TLS certificate to enumerate its customer-domain boards

_Found 2026-08-27 while opening Zwayam. Tracked deliberately —
[`technique-ranking.md`](./technique-ranking.md) is gitignored, and this finding needs to outlive it._

## The problem this solves

Some ATSes put every Board on a hostname the customer owns — `careers.persistent.com`,
`jobs.happiestminds.com`, `career.crisil.com`. There is no `{slug}.vendor.com` namespace to walk,
so the usual subdomain sweep (`wayback_feeder.py`, `mine_keka.py`'s DNS sieve) has nothing to point
at. `wayback_feeder.py` files these providers under *"no enumerable host namespace"* and skips
them; Zwayam, Phenom, PyjamaHR and Oracle are all listed there today.

The fallback is guess-and-check: build a company list, try `careers.{domain}` / `jobs.{domain}`,
verify each. Measured on Zwayam that runs at a **4.3% hit rate** and falls off fast as the list
broadens — 7 boards from the first 74 companies, 2 from the next 134.

## The technique

**If the vendor terminates TLS for its customers' career hostnames, one TLS handshake against any
single known Board returns the customer roster.** Those hostnames must appear as SANs on a cert the
vendor controls, and vendors batch them onto a handful of shared certs rather than buying one per
customer.

The tell is the **subject naming the vendor, not the customer**:

```
$ echo | openssl s_client -connect careers.persistent.com:443 \
      -servername careers.persistent.com 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName

subject=C=IN, ST=Uttar Pradesh, L=Noida, O=INFO EDGE (INDIA) LIMITED, CN=www.zwayam.com
X509v3 Subject Alternative Name:
    DNS:www.zwayam.com, DNS:bpscareers.coforge.com, DNS:career.axismaxlife.com,
    DNS:career.crisil.com, DNS:careers.cult.fit, DNS:careers.cyient.com,
    DNS:careers.livspace.com, DNS:careers.microland.com, DNS:careers.persistent.com,
    DNS:jobs.happiestminds.com, DNS:jobs.itcinfotech.com, DNS:www.flipkartcareers.com, ...
```

Persistent Systems' own certificate is issued to Info Edge and lists 73 of its competitors'
career sites. That is the whole customer roster, free, in one round trip.

## The recipe

1. **Handshake one known Board** and read `subject` + `subjectAltName`. If the subject `O=` is the
   *vendor*, you have a shared cert and every other SAN is a tenant.
2. **Repeat across several known Boards** — a vendor uses more than one cert, partitioned by CDN
   edge, and each carries a different slice. On Zwayam three certs appeared (74 / 48 / 14 SANs,
   123 distinct hostnames); a run that handshakes only one Board sees about half the roster.
3. **Verify every SAN against the ATS's own API.** A SAN proves the hostname was provisioned, never
   that the Board is live. Zwayam: 145 candidates → 105 live.
4. **Drop the vendor's own infrastructure** from the roster (`www.zwayam.com`, `zwayam.com`).

**Do not snowball CT from tenant apex domains.** The instinct once you hold a roster is to feed
each tenant's own domain back into CT and widen. Measured on Zwayam: seeding CertSpotter with
`axismaxlife.com`, `eaplworld.com`, `culko.in` and kin returned **0 new Boards from 228
candidates** — a tenant's apex cert is the *tenant's* footprint (`elink.*` mailers, `devapi.*`,
internal UAT hosts), not the vendor's roster. The vendor's name is what sits on the shared cert, so
**one query keyed on the vendor is the whole angle**; widening it only adds noise.

Prefer the **live handshake** over CT-log search. On Zwayam, Certspotter was useful for historical
depth, but **crt.sh is actively misleading**: `?O=INFO EDGE (INDIA) LIMITED` returns HTTP 200 with
776 rows that collapse to 3 distinct values, because it puts the *organisation* name in
`name_value` rather than the hostnames. It reads like a jackpot and carries no hostnames at all.

## Why this does not contradict "CT logs: ruled out"

[`technique-ranking.md`](./technique-ranking.md) records *"#2 CT logs (mass discovery). Ruled out
2026-06-15. Do not retry."* That verdict stands and this is not it. Mass CT sweeping — trawling the
logs hoping ATS Boards fall out — is still a bad trade. This is the narrow inverse: **you already
know one Board, and you read its certificate to get its siblings.** One handshake, no search, no
rate limit, no corpus.

Treat it as a named exception, scoped to **customer-domain boards with vendor-terminated TLS**.

## Where to try it next

A five-second test per provider, worth running against every ATS whose Boards sit on customer
domains. From CLAUDE.md's TODO that means **Phenom** (Mastercard/Adobe India GCCs), **PeopleStrong**
(`larsentoubrocareers.peoplestrong.com`), **Eightfold**'s vanity tail (`careers.qualcomm.com`,
`jobs.nvidia.com` — the half the `{slug}.eightfold.ai` sweep structurally misses), and
**SuccessFactors**' CSB tenants.

## The blind spot — measured, and it is not small

**A shared-cert sweep silently misses every tenant the vendor put on a *dedicated* certificate.**
Of 9 Zwayam Boards known before this work, **4 — `careers.manipalhospitals.com`,
`careers.rsystems.com`, `careers.tavant.com`, `careers.tigeranalytics.com` — appear on no shared
cert at all**, and no web-corpus source found them either. They sit on dedicated Akamai properties.

That is the failure mode to plan around: the miss is **silent**. The roster you get back looks
complete, arrives with high precision, and gives no signal that a whole class is absent. On Zwayam
the class was ~44% of the previously-known Boards.

So treat the cert roster as a high-precision *seed*, never as the census. Pair it with a channel
that enumerates rather than samples — for Zwayam that is the tenant-directory endpoint, which found
608 of the ledger's 757 live Boards against the cert roster's 98. The cert channel's value is that
it is cheap, needs no enumeration, and owns the enterprise tier; its recall is not its strength.

It fails where the customer fronts its own CDN — 4 Zwayam Boards sit behind Cloudflare, Imperva or
Azure Front Door and present the customer's own cert. Those are exactly the Boards a
`*.ocean.edgekey.net` CNAME sweep also misses, so cert-reading and CNAME-mining have correlated
blind spots; the API verification in step 3 is what catches them.

## Measured yield on Zwayam

| Channel | Candidates | Live Boards | Cost |
|---|---|---|---|
| **Shared-cert SAN roster** | 145 | **105** | 3 handshakes + 145 API calls |
| Guess `careers.{domain}` over a curated company list | 832 | 9 | 832 API calls |
| Repo's own unresolved-ATS lists (`fingerprint_misses_raw.tsv`, `fp_all.txt`) | 1,272 | 7 (2 new) | 1,272 API calls |
| Wayback CDX over the shared `openings.co` namespace | 1,232 | 16 | 1 CDX query + 1,232 API calls |

The cert channel is **an order of magnitude better per request** than any guessing or corpus
channel, and on a cross-check against an independent DNS+fingerprint sweep it dominated: 0 Boards
found there that the cert roster missed, 6 the cert roster caught that it missed.

But per-request efficiency is not recall. The final Zwayam ledger reached **757 live Boards**, and
the cert roster accounts for **98** of them — the tenant-directory endpoint found 608. Read the
cert technique as the cheapest way to *start* on a customer-domain provider, and as the right tool
when no directory endpoint exists; do not read it as a complete roster. See the blind spot above.

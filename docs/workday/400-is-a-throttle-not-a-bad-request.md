# Workday's detail 400: what it is, and what it is not

**Measured 2026-09-02, live against the real API.** Backs [ADR-0102](../adr/0102-a-400-walls-the-origin-too-not-just-a-429.md);
tracked here because `experiment/` is gitignored and an ADR must not rest on evidence nobody else
can read.

## The size of it

Across the ten runs `33548262185`..`33590621111`, Workday's detail pass lost 10.0–17.9% of its
fetches and **85–95% of that loss was a settled HTTP 400** — 256,934 of them. ADR-0098's retry
spent **479,165 attempts against a 2S ceiling of 513,868, a ratio of 0.93**, with every individual
run between 0.92 and 0.94. It recovers essentially nothing.

In run `33590621111` the 400s land on 81 boards and are highly concentrated: the top ten are 51%
of the total, at loss rates of 83–97% (`ghr/us-emplsv` 3,029 of 3,196; `td/TD_Bank_Careers` 1,621
of 1,667; `prismahealth` 1,569 of 1,630).

## What it is not

Each of these was tested against the live endpoint, not reasoned about.

| hypothesis | result |
|---|---|
| the URL is malformed | **No** — the exact URLs production builds return 200 with full job JSON from a laptop, unretried |
| `externalPath` needs encoding | **No** — 4,000 paths across the two worst boards, **zero** characters outside `[A-Za-z0-9/_.-]` |
| wrong data centre | **No** — the *detail* endpoint answers **422** from every non-serving instance (7/7), as ADR-0098 measured for listings |
| the tenant migrated | **No** — `thermofisher` (83% 400) is migrated; `aah/External` (**0%** 400) is also migrated |
| board is simply too big | **No** — the five largest boards have **0%** 400s (`aah` 5,192 details, `pwc/nonpublic_postings` 4,463, `citi/2` 4,186) |
| instantaneous concurrency | **No** — 60 details at widths 1, 5, 10, 25, 50: zero non-200 at every width |
| our proxy/retry layer invents it | **No** — `http.fetch` returns what `session().request()` settles on; a SOCKS failure raises |

## What it is

**A per-tenant request-rate limit.** Walking one board's real paths at production's own width of
25 streams, on one session, with the retry ladder off:

| board | trips at | steady state after |
|---|---|---|
| `ghr/us-emplsv`, direct | ~430 requests | 364 of 800 rejected |
| `ghr/us-emplsv`, via WARP | ~492 requests | 404 of 900 rejected |
| `thermofisher` | ~1,100 requests | ~75% rejected through request 3,200 |

The rejection carries a Cloudflare HTML block page; these tenants are Cloudflare-fronted
(`server: cloudflare`, `cf-ray`, and a `PLAY_SESSION` cookie pinned to a backend instance). The
**steady-state rejection rate of 70–90% is the same magnitude as the 83–95% per-board loss
production records**, and thermofisher explains why short probes read healthy: it is clean for 700
straight requests and only trips at ~1,100.

## The one thing still unexplained

**From this vantage the throttle answers 429; production settles 400.** Run `33590621111`
recorded 36,550 × 400 against 907 × 429. Both statuses appear on the same boards, and walling is
not the discriminator either — `aah` walled the IP 70 times with **zero** 400s while `ghr` walled
52 times with 3,029. The untested variable is the egress: CI runs from GitHub Actions ranges
through WARP exit IPs that have been hitting these tenants hourly for weeks. Settling it needs a
probe from inside Actions, which CLAUDE.md makes a deliberate ask rather than a default.

That gap does not block ADR-0102: every "malformed request" reading is falsified above, the
throttle is reproduced, and the fix is about which statuses may open the spare egress.

## Reproducing

`experiment/workday-400-root-cause/` (untracked) holds `probe_concurrency.py` (width ramp) and
`probe_volume.py` (sustained walk, reporting the request index of the first non-200 and the status
mix per block of 100). Both drive the real scraper's URL construction and `http` seam.

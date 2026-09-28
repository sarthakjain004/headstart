# Avature full ledger (2026-09-28)

The first ledger (#753) probed 839 of the 1,010 labels in the pool. This pass probed the rest,
with `p_avature` as merged in #753 plus one change to its non-production rule.

**Result.** 1,010 rows: 311 live, 683 dead, 16 unknown. Of the live rows, 195 list no public job
page; the 116 that do carry 102 distinct job counts, so the count is not a fallthrough.

**The non-production rule was too narrow.** #753's rule was `(?:sandbox|uat)` applied with
`re.match`, so it caught only a label that *starts* with `sandbox` or `uat`. Ten labels in the pool
mark a test environment another way: `ibmsandbox1`, `vfcsandbox`, `newsandboxjusticejobs`,
`mckinseyuat`, `stagingikea`, `stagingdeloitteus`, `ciscostageats`, `dbgrouptest`, `iatstest` and
`portalstest`. The rule now matches `sandbox` anywhere, `uat` at the start or (with optional
digits) the end, `staging` at the start, `stageats` at the end and `test` (with optional digits)
at the end. `uat` is anchored because it sits inside "graduate". Over the 1,010 labels the rule
matches 80, and every one reads as a test environment; `graduatecareers`, `latestart`, `backstage`
and `bloomberg` are probed normally (tested). A future label ending in an English word such as
"latest" would match `test$` and die unprobed; none in the pool does. Three rows the #753 ledger
held `live,0` (`ciscostageats`, `mckinseyuat`, `newsandboxjusticejobs`) are rewritten `dead` by
the rule, which answers before any request.

**Why a name, not a response, decides these.** A test instance answers like a production one:
`sandboxtql` serves 434 real-looking ids, 329 of them `tql`'s own, so no response tells it apart
from a live Board, and probing it would record it `live`. The rule keeps #753's design (a
name-decided `dead` for an unambiguous marker) and is tested against production look-alikes. A
test instance whose label carries no such marker goes in `EXCLUDED_BOARDS` once its postings are
read: `devwoolworths1` serves one "Store Manager - 74225" on three portals, and req 74225 is
not among the 2,097 rows production `woolworths` lists.

**Radancy's 32 new rows.** No duplicate keys across the 252 rows. The three new live fronts not
already checked (`careers.bupa.com.au`, `careers.bat.com`, `jobs.heraeus.com`) each serve a sitemap
whose job URLs name themselves. `careers.chipotle.com` (301 to the held `jobs.chipotle.com`),
`careers.harris.com` and `careers.heraeus.com` are vanity or legacy hosts and are recorded `dead`,
as the landing rule expects, so they serve nothing. Re-probing 5 new live and 5 new dead rows
agreed with the ledger on all 10.

**Duplicate Boards, three mechanisms.** Casing: none. Key spelling: four URL-encoding debris labels
(`2fcareers`, `2fdeloittebe`, `2fdeloittecm`, `2fkellyds`), all `dead`. Redirect or second name:
`p_avature` kills a label whose CNAME or robots.txt redirect names another tenant. The remaining
pairs that look alike, `cisco`/`cisco2` and `tesco`/`tescoce`, both list 0 jobs, so neither
serves a posting twice.

**cbreglobal is real.** Its `careers` portal lists 4,629 distinct `JobDetail` ids when its child
sitemaps are read on the tenant host. The children name the vanity host `careers.cbre.com`, which
answered 0 bytes from here; `p_avature` rewrites each child to the tenant host, and a check that
does not rewrite reads 0. A job page on the tenant host answers 301 to the same path on the
vanity host.

**Spot check.** Re-probing 5 random dead and 5 random live rows (3 hiring, 2 empty) agreed with
the ledger on all 10; one count moved from 7 to 9 in the hours between.

**Throttle signature.** Under shared load from this machine, portal sitemaps answered `202` with
an empty body rather than 406; the same URLs answered 200 over the spare egress. The probe
records a 202 as unknown, not dead (2 of the 16 unknowns).

"""The Boards a run never scrapes although their rows are Live: vendor test Boards
(`EXCLUDED_BOARDS`) and real Boards withheld for now (`PARKED_BOARDS`)."""

from __future__ import annotations

# Vendor test and sandbox Boards. They are live, they look like they are hiring, and their
# postings are fabricated — RippleHire's own QA/UAT tenants, a SmartRecruiters demo board,
# greenhouse boards whose company name is literally "Test". They reach users as real results
# (a "Software Engineer" at "prodtest"), so `scrapable_boards.load` drops them, which both stops
# the scrape and makes `index prune` evict the rows already indexed — the keep-set is built from
# that same list.
#
# Every entry was confirmed by READING that Board's own postings (2026-08-12), never from the
# shape of its slug. That distinction is the whole point: a slug-pattern rule would also have
# dropped `greenhouse:stage`, which is KKR's real board of 128 jobs, and `recruitee:test1234`,
# which belongs to a real Austrian education agency. Keys are lowercased ``{ats}:{slug}``, so
# one entry covers a Board that appears under several casings (smartrecruiters Dev2/dev2).
EXCLUDED_BOARDS: frozenset[str] = frozenset(
    {
        # Read 2026-10-03: Starbucks' staging front serves 53 postings, including
        # "barista CAN ext test", "barista US ext test" and "test_testing lead".
        # Its PCSX group is starbucks-staging.com; these are test requisitions.
        "eightfold:starbucks-staging.eightfold.ai",
        # Ashby's turn, found late (ADR-0114) by reading board titles rather than slugs:
        # `krakensandbox` titles itself "Kraken Sandbox Jobs" and serves 3 postings,
        # content-confirmed as templates ("Basic Job Template", "Admin Assistant Testing").
        # Its siblings `ashby:bento` and `ripplehire:tenant1` name themselves just as plainly
        # but serve 0 postings, so there is no content to confirm and nothing to remove —
        # `company_name._PLACEHOLDER` refuses their names instead.
        "ashby:krakensandbox",
        # Woolworths' Avature development instance, read 2026-09-28: `devwoolworths1` lists one
        # posting, "Store Manager - 74225", on three portals (`apply`, `apply2` twice), and req
        # 74225 is not among the 2,097 rows production `avature:woolworths` lists: a req that
        # exists only on the dev instance. One label is too few to justify a `dev` arm in
        # `_AVATURE_NONPROD`; its posting, not its name, is the evidence.
        "avature:devwoolworths1",
        # Emirates Group's Avature SIT copy, read 2026-09-29: `emiratesgroupcareers` lists 2,313
        # sitemap ids that include "TEST OUTSTATION ROLE" and "ONB UAT ..." rows, and its
        # `careersmarketplace/SearchJobs` redirects to `emiratesgroupcareers.sit.emirates.dev`, an
        # environment host behind a login. Its held sibling `avature:emiratesjobs` redirects, via
        # `external.emiratesgroupcareers.com`, to the production site `www.emiratesgroupcareers.com`.
        # The label carries no marker for `_AVATURE_NONPROD`; the redirect chain is the evidence.
        "avature:emiratesgroupcareers",
        # Jobvite's own automation tenant, found by reading its board rather than its slug:
        # `jobs.jobvite.com/jvauto` titles itself "Jobvite Automation Careers" and serves exactly
        # 10,000 postings whose titles are generated ids ("0000AAABBB_0Ja700iin3"). The round
        # number is the tell — it is the same shape as Oracle's 78,431-posting load-test instance
        # below. Left in, it would have been **20.2% of the postings** the ADR-0158 re-enable
        # decision was priced on (49,573 -> 39,573, and jobvite's "2.1x the assumed 23,461" is
        # really 1.69x), plus 10,000 synthetic detail fetches a run. It also sits exactly on
        # `jobvite._MAX_PAGES` (10,000 at 50 a page = 200), whose comment calls the cap "not
        # a cap anyone is expected to reach".
        "jobvite:jvauto",
        # MyNextHire's own test tenant: its client record names it "Consultant test1" with the
        # vendor's site, and its 27 postings (2026-09-30) are "Test Req 90", "2146 test" and
        # "Java Developer" whose description is "this is test jd", at places "aassrr11" and
        # "abb10".
        "mynexthire:consultant",
        # Four more non-customer tenants, read 2026-09-30. `staging` is client 999, "Staging",
        # site "www.staging_on_production.com": 328 postings dated 2020-2022, one described
        # "Additional Comment: this statement should appear in JD". `try` is the vendor's
        # demo, client name "MyNextHire Trial Instance": 164 template postings ("Role
        # Overview: We are seeking a talented SDE2 Mobile Developer…") placed from Atlanta to
        # Bengaluru. `mars` names
        # itself "Mars" but is a trial: 7 of its 8 postings are "test mars2", "mdl test" or a
        # "Software Tester" whose description is "Test JD", "ok" or nothing, with ids 3-47 over
        # two years. `prodindefault` is the vendor's default tenant (client name
        # "prodindefault", site "prodindefault.com").
        "mynexthire:staging",
        "mynexthire:try",
        "mynexthire:mars",
        "mynexthire:prodindefault",
        # Jibe clients that are not a board of openings (ADR-0189), each read 2026-09-24.
        # `fedex` lists 136,186 rows from `ats_code: fedex-prod-historical-jobs-feed` — page 1 is
        # 98 postings dated 2024 and 2 dated 2025 — and its board page redirects to an Okta SSO
        # login; its live openings are on FedEx's Workday Boards, which the workday ledger holds.
        # `mortonfinancial` is the vendor's test data (`ats_code: test-bank` on 29 of 38 rows,
        # `jobs-notacustomereutest.icims.com` apply links, dates from 2018). `testaxa` and
        # `axatest` are AXA's UAT site (`testaxa-uat-taleo-external`, "2026-01-22 external job -
        # Fred"; the real Board is `jibe:axa`). `discovery1` serves one posting, "TEST REQ APRIL-
        # DO NOT APPLY". `icims` and `template` are the vendor's own labels, live with nothing
        # listed. `hexdigital` is Jibe's own demo client: 906 rows, page 1 all "Software Engineer"
        # with `hiring_organization: Jibe` or none and no `apply_url` on 98 of 100 — left in, it
        # would serve 906 fake tech postings. `launch` is another: 25 stock titles ("Corporate
        # Lawyer", "Security Officer") with no dates, each applying to its own Jibe host.
        "jibe:fedex",
        "jibe:mortonfinancial",
        "jibe:testaxa",
        "jibe:axatest",
        "jibe:discovery1",
        "jibe:icims",
        "jibe:template",
        "jibe:hexdigital",
        "jibe:launch",
        # Pinpoint's test and demo tenants, each confirmed live with postings on 2026-09-23 by
        # reading its board title and posting titles rather than its slug. `hooli` (the sitcom
        # company) is the vendor's own: "Elvin new test", "Anca's test", "SUP-7257 Canadian
        # account number". `acme` titles itself "ACME candidate 1" and posts "Test Job 1..3";
        # `developers-test` is "Developer Acme"; `integration-testing` is "Integration Testing";
        # `joe-testing` is "Joe's Test Platform" ("Test Job - do not apply"); `myinterviewdemo`
        # is an integration partner's demo board ("test create job", "Test public workflow",
        # "Simon Test 03.09.2026"). The `*-sandbox` tenants need no entry: `is_nonprod` already
        # settles them dead.
        "pinpoint:acme",
        "pinpoint:developers-test",
        "pinpoint:hooli",
        "pinpoint:integration-testing",
        "pinpoint:joe-testing",
        "pinpoint:myinterviewdemo",
        # PeopleStrong's own demo tenant, read 2026-09-25: `candidate.peoplestrong.com` titles
        # itself "Candidate portal" and serves 314 postings (315 at the ledger run), every job
        # code `BOS/…`, titled "Test Job 1909", "sdfghj sdfg", "Excel Job patch 17sep" under org
        # units "Company test" and "Company Y". Left in, 26 of them pass the tech filter.
        "peoplestrong:candidate",
        # Zwayam's own demo/QA tenants, confirmed by reading their board content on 2026-08-27
        # rather than inferred from the slug — the same bar the darwinbox entries below were held
        # to. `testcompany.cluster3` is the worst of them and the reason this entry exists: it is
        # `live` with 77 postings titled "TEDT", "tesdt", "xyz_zbc", "dsf", "14 dec", whose
        # locations are "fd", "sd", "c", "dfs", "H". `zhirematetest` posts "Test Job", "Hi",
        # "Hello Test", "Testing job".
        #
        # Deliberately NOT excluded, though their slugs invite it: `ssttest` ("Software Engineer",
        # "Implementation Engineer") and `hirematetest1` ("Senior Java Developer") carry ordinary
        # titles and real Indian locations, so the content does not confirm the slug's hint. Two
        # further vendor-shaped hosts, `ratestcompany` (0 postings) and `wisseninfotechhiremate`
        # (1), are left for the same reason.
        "zwayam:testcompany.cluster3.openings.co",
        "zwayam:zhirematetest.openings.co",
        # Zwayam's sales demo, read 2026-09-30 (MCP critique round 5, R5-P2-7): its career page
        # titles itself "TechCorp Careers… Powered by Zwayam", and among its 593 postings are
        # 10595 "Quia omnis in laboris nulla cum ea fugit non" at "Officia unde est es",
        # Zwayam's own 10487 "SaaS Solutioning Sales Specialist — Zwayam + DoSelect
        # Assessments", a "Senior" asking 1-3 years and five identical "Senior SAP Abap
        # Consultant" postings. It led role samples and similar_to results as "HireFast".
        "zwayam:hirefast.openings.co",
        # Oracle's own load-test instance, and by far the largest "board" the oracle ledger
        # holds: 78,431 claimed postings, 20% of that ledger's entire volume. Confirmed by
        # content on 2026-09-08, not by the slug (which is an opaque four-letter pod label and
        # hints at nothing): its titles are "HasProspect", "TEST Manager", "Test Director",
        # "ZBEN QA Manager_031219", "volume testng req using template" and
        # "Auto_Engineer-1_User3", and one site is named "Candidate Experience Site_031219".
        # Left in the ledger as `live` because it genuinely is; it just is not an employer.
        "oracle:eubt.fa.us6.oraclecloud.com",
        # Cornerstone's own demo and partner-services sandbox corps, confirmed by reading their
        # listings on 2026-09-23 rather than from the slug: `awavedemo` and `demohk` both open on
        # "Template 1" / "Sales Associate" / "Training Manager, Italy" and date to 2018-2020;
        # `pservsmartdreamers` posts "job title Bogdan trei" and "job title Bogdan unu-trei";
        # `pservsqeptech` posts "This is a VIC Job"; `demokcmo` dates to 2017-2018; `demojk` opens
        # the same generic postings ("L&D Director", "Sales Director") in all of CN, JP, KR, US,
        # MX, BR, ES, FR, GB, NL, SE, DE and IT at once, naming no employer; `maestrademo` posts
        # template copies ("Analista de Sistemas - copy", "Analista financeiro - Orçamentos - 4")
        # dated 2013-2026. All seven are `live` in the ledger, 379 postings between them, none an
        # employer's.
        "cornerstone:awavedemo",  # 56 postings
        "cornerstone:demohk",  # 55 postings
        "cornerstone:demojk",  # 47 postings
        "cornerstone:demokcmo",  # 47 postings
        "cornerstone:maestrademo",  # 45 postings
        "cornerstone:pservsmartdreamers",  # 60 postings
        "cornerstone:pservsqeptech",  # 69 postings
        # More Cornerstone demo and template tenants, found by the csod.com DNS sweep of 2026-09-29
        # and confirmed by READING each Board's listing the same day, not from its slug (every slug
        # below looks like an employer). Each was landed `live` with postings by `check_liveness.py
        # cornerstone`, and none is an employer's: `empower` (55 postings), `tsg` (55) and
        # `medbridge` (53) are the awavedemo/demohk template, "Coordinator Call Center- Madrid",
        # "Sales Associate" in Milan and Rome, "Training Manager, Italy", dated 2018-2020, with
        # 49-55 of their requisitionId+title pairs equal to `demohk`'s and `pservsmartdreamers`'s;
        # `appiphony`, `extend` and `levelaccess` (47 each) are `demojk` again: the same 47
        # requisitions, "Customer Service Representative" opening in AU and NZ, "Registered Nurse",
        # "Cashier" in Miami, dated 2021-2025, and name no employer. `explore` (6) is NOT here: it
        # shares two requisitions with `demojk` by id, title and date, but its other four ("Sales
        # Representative" in Switzerland dated 2026-06-01, two "Customer Service Director" in Santa
        # Monica) do not match any demo tenant, so the read does not confirm it.
        "cornerstone:empower",  # 55 postings
        "cornerstone:tsg",  # 55 postings
        "cornerstone:medbridge",  # 53 postings
        "cornerstone:appiphony",  # 47 postings
        "cornerstone:extend",  # 47 postings
        "cornerstone:levelaccess",  # 47 postings
        # Vendor infrastructure names and pilot copies that the same sweep read as tenants and the
        # liveness probe left `unknown`, read 2026-09-30 by DNS and by request (8 of the 32
        # spot-checked again the same day). `repo` and `nuget` resolve to private 10.x addresses,
        # `status` is a Statuspage CNAME (`stspg-customer.com`), `autodiscover` is Outlook's,
        # `cdn` is Cornerstone's own CDN host (`glb-cdn.cdn-ext`), `bitbucket`, `jira`, `ns1`,
        # `ns2`, `www`, `help`, `sso`, `ssp`, `tracker`, `maintenance` and `legacy` are likewise
        # infrastructure; `app`, `apps`, `application`, `qap`, `qar`, `ws-app`, `live-int` and
        # `ws-tcg` time out or answer 404; `demos` and `testing` are Akamai's wildcard edge
        # (`wildcard2.csod.com.edgekey.net`), not provisioned tenants. `cbapilot`, `luxairpilot`,
        # `moneygrampilot`, `nebraskapilot`, `sodexopilot` and `unhcrpilot` CNAME to the pilot
        # environment (`corporate5-pilot.csod.com`) and each has a held production tenant (`cba`,
        # `luxair`, `moneygram`, `nebraska`, `sodexo`, `unhcr`). None has an openings board.
        "cornerstone:repo",
        "cornerstone:nuget",
        "cornerstone:status",
        "cornerstone:cdn",
        "cornerstone:autodiscover",
        "cornerstone:bitbucket",
        "cornerstone:jira",
        "cornerstone:ns1",
        "cornerstone:ns2",
        "cornerstone:www",
        "cornerstone:help",
        "cornerstone:sso",
        "cornerstone:ssp",
        "cornerstone:tracker",
        "cornerstone:maintenance",
        "cornerstone:legacy",
        "cornerstone:app",
        "cornerstone:apps",
        "cornerstone:application",
        "cornerstone:qap",
        "cornerstone:qar",
        "cornerstone:ws-app",
        "cornerstone:live-int",
        "cornerstone:ws-tcg",
        "cornerstone:demos",
        "cornerstone:testing",
        "cornerstone:cbapilot",
        "cornerstone:luxairpilot",
        "cornerstone:moneygrampilot",
        "cornerstone:nebraskapilot",
        "cornerstone:sodexopilot",
        "cornerstone:unhcrpilot",
        # `eczy-test.fa.us2.oraclecloud.com` was once kept despite its "-test" slug, for want of
        # content: it reported TotalJobsCount 4,947 while serving zero rows. It serves them now,
        # 23 of 24 sampled also open on `eczy` (2026-09-23), and is dead by ADR-0034's Oracle
        # rule instead. The zero-rows shape is still handled rather than
        # excluded — `OracleScraper._listing` marks such a Board truncated, which keeps its rows
        # out of the eviction scope (ADR-0053) instead of reading them as delisted. Five smaller
        # Boards share it (7, 5, 1, 1, 1 claimed postings); 985 of 991 serve real rows.
        "darwinbox:training",  # company "training"; "Ali marketing Executive", "SK_Jr. Associate"
        # More of Darwinbox's own demo/QA/training tenants (found during darwinbox's salary-
        # extraction pass, 2026-08-22, reading real board content — not from the slug alone).
        # Confirmed by content: `minion` ("abc1", "Testing", "Animator USA" repeated, nonsensical
        # salary_range values like "INR 100-150"); `southuat` ("Test Pre Offer", "Gulf Dummy",
        # "Demo_Unicommerce"); `darwinboxdemo` (title/description MISMATCH — the "Customer Success
        # Manager" posting's own description is for a "Chemistry Teacher" role, plus literal
        # "Please enter job description" placeholders elsewhere — Darwinbox's own literal demo
        # tenant); `spoc` ("SUPERADMINSUPERADMINSUPERADMIN SUPERADMIN" x3, "Sai_Test341", "VP HR
        # Test Test", "Test 123", "Job created for 7.6 on all servers"); `treebotest` ("Tera Soft
        # Recruitment Testing", 61% empty/placeholder descriptions); `homecredituat` and
        # `partnerdemodeloitte` (unfilled template merge-fields verbatim in the description,
        # "#*Group Company*# Designation: #*Designation*#..."); `training14` (same "training"
        # family as the entry above — a numeric template ID contaminates BOTH the title AND
        # location fields identically and repeatedly: "3891_Manager" @ "3891_Singapore, Singapore,
        # Singapore, Singapore", "DB156_Manager" @ "DB156_Hyderabad, ...", "00002_Manager" @
        # "00002_Los Angeles, ...", "DB163 MANAGER" @ "DB163 Los Angeles, ..." — plus a literal
        # "Darwinbox Sample" title and gibberish ("sasasa")); `training2` (same family, weaker
        # signal — "COE senior manager472"/"Sr.Manager_KK" carry the same stray-numeric-suffix
        # shape, title-only on its 8 postings, none of `training14`'s title/location contamination
        # since there's no location field affected in this smaller sample). Checked
        # and deliberately kept: `banyanhcmuat` (a "uat"-shaped slug, same risk class as
        # `homecredituat`/`southuat`, but its content is genuinely realistic hospitality-role
        # postings — real titles like "Chief Steward", "Sushi Chef", even a Chinese-language
        # "预订经理" (Reservations Manager) — with no test/dummy signal anywhere and no separate
        # "banyantree" tenant to suggest this is a redundant staging copy; excluding it would be
        # exactly the slug-pattern reasoning this list's own rule warns against).
        "darwinbox:darwinboxdemo",  # 17 postings
        "darwinbox:homecredituat",  # 34 postings
        "darwinbox:minion",  # 135 postings
        "darwinbox:partnerdemodeloitte",  # 3 postings
        "darwinbox:southuat",  # 59 postings
        "darwinbox:spoc",  # 88 postings
        "darwinbox:training14",  # 30 postings
        "darwinbox:training2",  # 8 postings
        "darwinbox:treebotest",  # 142 postings
        # Six more Darwinbox demo tenants the DNS sieve (`mine_darwinbox_dns.py`) turned up, each
        # confirmed by reading its own postings through the scraper on 2026-09-29, not by slug.
        # `stark` names itself "Darwinbox" and its 4 postings are other employers' and pasted text,
        # none its own: a "Circle Head – Wireline Deployment" whose description is "Group Company:
        # Darwinbox ... ND Sales Director", an "Operations Manager (BYD Cubao)" under the
        # department "Privy Organics (DEP_227)", and a "Senior Sales Manager" whose HTML still
        # carries the class `font-claude-response-body` of a pasted chat answer. `windchasers`
        # (also "Darwinbox"; both postings from 2020-11) reads "Demo Job description" and "Please
        # enter job description ... Demo Description 1 2". `paisa` ("DarwinBox Digital Solutions
        # Pvt Ltd") posts "No recommendations for this Designation", a "Product AD" whose
        # description is "Please enter job description" eight times over, and a Product Manager
        # whose template says "Group Company: Google India" with every other field blank. `oneassist` is a real employer (OneAssist Consumer Solutions) but both of
        # its postings are titled "TEST" with "Please enter job description" (2020-05 and
        # 2020-09): revisit it if it ever posts a real role. `docs` (company name "Docs") has one
        # "Manager" Intern posting, description "Please enter job description", at three
        # unrelated places at once (Nizamabad, Ahmednagar, Abu Al Khasib in Iraq). `global`
        # (company name "Global") has two postings from 2020-10, one whose description is "This
        # is a sample job description for the IT Group Head position in JG Summit ... sample text
        # 1" and one that is Darwinbox's stock Sales Manager template, on a tenant whose banners
        # are Cement Industries of Malaysia's: the least clear-cut of the six, so first to revisit.
        # Read and deliberately kept: `dei` — one "HR and Admin Assistant" with a real department
        # ("Human Resources (DEI UAE_Human Resources)") and place (Dubai Internet City), whose only
        # oddity is a description reading "test", which does not make the posting a test.
        "darwinbox:stark",  # 4 postings
        "darwinbox:windchasers",  # 2 postings
        "darwinbox:paisa",  # 3 postings
        "darwinbox:oneassist",  # 2 postings
        "darwinbox:docs",  # 1 posting
        "darwinbox:global",  # 2 postings
        "greenhouse:staging",  # company "Staging Site Board"; its one posting is titled "TEST"
        "greenhouse:test1",  # company "Test"
        # Keka's own demo/QA tenants (found during keka's salary-extraction pass, 2026-08-22,
        # reading real board content — not from the slug alone). Confirmed by content: `csdemo`'s
        # organization name is literally "keka cs" (Keka's own Customer Success team), with job
        # titles including "ABC", "Bacancy - Demo", "Keka Test Engineer", and several employees'
        # own personal test postings ("Chaitanya test", "Demo Sneh"); `salesdemo`'s organization
        # name is the nonsensical "Out comes Operating" and its own LinkedIn link points to Keka's
        # own company page, not an independent client. Checked and deliberately kept:
        # `keka:lambdatest` and `keka:testsigma` (real companies whose own brand names happen to
        # contain "test"), `keka:vtest` (a single real-looking job posting, not enough evidence
        # either way to exclude) — exactly the false-positive risk this list's own rule warns
        # against.
        "keka:csdemo",  # 681 postings
        "keka:salesdemo",  # 153 postings
        # Lever's own demo/sandbox/QA tenants (found during lever's salary-extraction pass,
        # 2026-08-22, reading real board content — not from the slug alone, per this list's own
        # rule). 1,769 fabricated postings total. Confirmed by content: template/placeholder
        # titles ("[TEMPLATE] Customer Experience Specialist", "***POSTING TEMPLATE - ENGINEERING",
        # "Account Executive (copy)", "Draft External Job", "Ice cream eater", "# Test Job 123",
        # "[JEN TEST] WHITELISTED POSTING FOR RESUME REQ OVERRIDE") with no real company name
        # attached, unlike `lever:sandboxvr` (Sandbox VR, a real VR entertainment company; kept).
        "lever:leverdemo",  # 383 postings
        "lever:leverdemo-8",  # 429 postings
        "lever:leverdemo193",  # 16 postings
        "lever:leverdemo50000",  # 7 postings
        "lever:leverdemo93321",  # 4 postings
        "lever:leverdemo956",  # 15 postings
        "lever:levertest",  # 894 postings
        "lever:salesdemo-jr",  # 21 postings
        "ripplehire:itcinfotech",  # 631 postings; titles itself "ITC Infotech Demo"
        "ripplehire:labs-axisqa",  # 1,226 postings; RippleHire's own QA tenant for Axis
        # 486 postings served as "Mphasis" — more than the genuine `ripplehire:mphasis`
        # board's 189, with titles repeating verbatim. ADR-0114 declined to list it on
        # 2026-09-07 because it 502'd and exposed no title; it answers 200 now, so the
        # evidence that was missing then exists. A QA tenant wearing a real employer's
        # name is the one thing a title rule cannot catch.
        "ripplehire:labs-mph",
        "ripplehire:prodtest",  # 863 postings, company "prodtest"
        "ripplehire:qa1-tataaia",  # 209 postings, company "qa1-tataaia"
        "ripplehire:qa1-ust-app",  # 300 postings, company "qa1-ust-app"; "software developement"
        "ripplehire:rhsandbox",  # 649 postings; RippleHire's own sandbox tenant
        # 502 postings. Found because ADR-0114 made it *stop* looking fake: its board titles
        # itself "Mphasis Careers | …", so the served company became "Mphasis" — a QA tenant
        # impersonating the real employer, where the slug had at least shown what it was.
        "ripplehire:tenant1-mph",
        "ripplehire:uat2",  # 788 postings, company "uat2"
        "smartrecruiters:dev2",  # company "Dev"; SmartRecruiters demo tenant
        # Capgemini's test RMK host, which mirrors real postings under the company name
        # "careers-test". Safe to drop because their production board is in the ledger too
        # (`careers.capgemini.com`, 1338 postings) — this removes the duplicate, not the jobs.
        "successfactors:careers-test.capgemini.com",
        # LTIMindtree's retired vanity host. Its TLS cert is SAP's own unconfigured-vanity
        # placeholder (CN=certificate-not-found.jobs2web.com, no SAN for this hostname) —
        # verified live 2026-08-19, failing the same way in all 6 recent pipeline runs. Its
        # CNAME chain (-> larsenturbo.jobs2web.com -> rmk12.jobs2web.com) is identical to
        # `careers.ltm.com`, and requesting that jobs2web host with `Host: careers.ltimindtree.com`
        # 301s to `careers.ltm.com/xml/sitemap.xml` — same SuccessFactors tenant, now served
        # under its current vanity domain, which is already live in the ledger with a valid
        # cert (`careers.ltm.com`, 49 postings, comparable to this host's last-good 55). Safe to
        # drop: it removes the permanently broken duplicate, not LTIMindtree's jobs.
        "successfactors:careers.ltimindtree.com",
        # Three more SuccessFactors redirect-aliases used to sit here by hand (CONA, HCLTech,
        # Bombardier — #212, #218). They now come from `data/validate/aliases/successfactors.csv`,
        # regenerated live by `dedupe_boards.py`, so the fact has one home instead of two
        # (ADR-0111). The two above stay hand-listed: Capgemini's is a *test* board rather than a
        # duplicate, and LTIMindtree's TLS cert is permanently broken, so the scan can never reach
        # it to prove what its redirect says.
        # Trakstar Hire's own demo/QA tenants (found during trakstar's salary-extraction pass,
        # 2026-08-22, reading real board content — not from the slug alone, per this list's own
        # rule). Confirmed by content: `bbtest`'s sole posting is titled "Bug Buster"; `smoketest`
        # (66 postings, confirmed via its own `jobfeeds` RSS feed — the careers-page HTML this
        # scraper reads renders only the first 25 of them, a separate, real truncation bug under
        # its own investigation, unrelated to this exclusion) is unmistakably a vendor
        # feature-testing sandbox — "Custom Fields - No Fields", "Custom Fields - With all 9
        # Fields", "Django Upgrade Final Test", "Example Logo", "filter test" (x2), "google account
        # 2", "Google Smoke Testing", "IT Coordinator job description" — scattered across many
        # countries (Tijuana x18, Chennai x9, Bengaluru x8, and 20+ singletons), not city-clustered
        # like the other two; `testbass`'s sole posting ("Ruby on Rails Developer") reads plausibly
        # on its own but shares `bbtest`'s same Bangalore, India location and single-generic-
        # posting shape, with no independent company signal anywhere. Checked and deliberately
        # kept: `zutest` (a "System Administrator – Computing Services Department" posting in Abu
        # Dhabi, UAE — detailed, professionally formatted, no test/dummy signal in the content
        # itself) — exactly the slug-pattern-alone reasoning this list's own rule warns against.
        "trakstar:bbtest",  # 1 posting
        "trakstar:smoketest",  # 66 postings (jobfeeds RSS count)
        "trakstar:testbass",  # 1 posting
        # More of the vendor's own, read 2026-09-28 off jsapi: `demoaccount` serves 365 postings
        # titled "Test from API", "xml test", "indeed question test", "label test"; `test5694`'s
        # one "Senior Engineer" reads "Blah blah blah"; `novtest`'s "IT admin" says "I'm trying to
        # test this IT admin!"; `rectestcam` posts "hkhk" in "hkjh"; `rbdemo` posts a "Childcare
        # Provider" in "Anytown". Kept on the same reading: `abctest`, `ecgtest`, `99tests`,
        # `garamdealstest`, `testsite3`, `pushtotest`, `thetestpeople` read as real postings.
        "trakstar:demoaccount",  # 365 postings
        "trakstar:novtest",  # 8 postings
        "trakstar:rbdemo",  # 2 postings
        "trakstar:rectestcam",  # 1 posting
        "trakstar:test5694",  # 1 posting
        # SenseHQ's own dev/test tenant (found during sensehq's salary-extraction pass,
        # 2026-08-23, reading real board content). Confirmed by content: 204 postings, the
        # large majority QA/testing-tool placeholder titles — "Cypress 1" (41), "QA test" (13),
        # "TESTING" (19), "Cypress test" (3), "QA testTest Lead" (3), template stand-ins
        # "Job template"/"Crm template"/"Crm job" (6+3+3), "sdaa" (9) — plus real-looking
        # titles duplicated with " copy" appended ("Sales development Representative" /
        # "Sales development Representative copy"). A feature-testing sandbox, not a real
        # employer. The "-dev" slug matches, but per this list's own rule that alone would
        # not have been enough.
        "sensehq:trm-dev",  # 204 postings
        # Oracle's own Taleo Enterprise demo tenant, `pmg.taleo.net`. Both readable sections were
        # read on 2026-09-24 (89 and 86 postings, mostly shared): "Director of Finance (DEMO)",
        # "TEST 2 EPredix Assessment", "TN-CSW-Test", "test1-dup1T", "Sample", "Radius1". Its
        # other two sections, `qatestcs` (unknown) and `mobilecs_demo_al` (dead), serve nothing
        # readable today and are listed so a re-probe that finds them live changes nothing.
        "taleo_enterprise:https://pmg.taleo.net/careersection/brandtss_faceted",
        "taleo_enterprise:https://pmg.taleo.net/careersection/m1",
        "taleo_enterprise:https://pmg.taleo.net/careersection/mobilecs_demo_al",
        "taleo_enterprise:https://pmg.taleo.net/careersection/qatestcs",
        # Vendor and integration-partner sandboxes found by reading the served table's postings
        # (titles, companies, posted dates) on 2026-09-24, each re-read live from its ATS's
        # public listing the same day. Where the sandbox wears a real employer's name, that
        # employer's real Board is a separate ledger row this does not touch.
        #
        # Greenhouse (boards-api): `builtinintegrationsandbox` names itself "BuiltIn Integration
        # Sandbox"; it lists 0 postings live, but its 316 served rows are generated ("Cloud
        # Solutions Architect - Job 50 9/16/2026, 12:04:29 PM") and only this removes them.
        # `mergeapiintegrationsandbox` is "Merge API Integration Sandbox", 87 postings: "Account
        # Executive (Automation Test)", "AI filed = No initially but then changed", "Eng Dog
        # Walker", "Republish Test". `agodasandbox` is "Agoda Sandbox", 34 postings dated
        # 2019-2026: "Agoda Homes Test", "2 Phenom CRM", "Campaign Marketing Analyst change";
        # Agoda's real Board is `greenhouse:agoda`.
        "greenhouse:agodasandbox",
        "greenhouse:builtinintegrationsandbox",
        "greenhouse:mergeapiintegrationsandbox",
        # Jobvite (board pages, and a sample of detail pages' JSON-LD for dates): `onecoprd`
        # ("Oneco 1 Careers") is Jobvite's release-QA tenant, 614 of 1,318 titles test-shaped:
        # "2020-07 Release Testing", "Crash Test - Do not change", "10th_aug_4th". `li3` is
        # "LinkedIn Test 3", 749 postings of numbered copies ("1000 - Content Producer",
        # "TEST 1241 - Enterprise Sales Manager - Lynda.com", "Test 5 #DNP"). `deming` and
        # `deminginc` are both "SC Demo Instance", the same 34 stock postings dated 2016-2017.
        # `michaelcarrinotest` is "Michael Carrino Sandbox": "Brit's Test Job", "dfadfasdfa",
        # "Implementation Manager - Sahana Demo". `blackbear` (2026-09-25) is a sales demo: its
        # postings' `og:title` names a different company each ("Chromalloy", "CFO Services",
        # "Blackbear Manufacturing"), one carries "DO NOT EDIT - Steve T - Using for branded
        # demo", and all 129 of its detail pages are an unrendered template with no posting.
        "jobvite:blackbear",
        "jobvite:deming",
        "jobvite:deminginc",
        "jobvite:li3",
        "jobvite:michaelcarrinotest",
        "jobvite:onecoprd",
        # JazzHR (board page, detail JSON-LD `hiringOrganization`): `adptestcompanycp` is "ADP
        # Test Company- CP", stock titles ("Advertising Sales Representative", "Art Director");
        # `jobtarget` is "JobTarget - Demo", 46 postings: "Donkey Handler - JazzHr Integration Test
        # Job", "testtttttttttttt", "Test Unnleash JazzHR".
        "jazzhr:adptestcompanycp",
        "jazzhr:jobtarget",
        # Recruitee (offers API): `democompany` and `democompany3` both serve "KW Demo company"'s
        # same 12 offers: "Test Tech Support (2)", "maltest", "aaa", "dsd", "Example offer 3".
        "recruitee:democompany",
        "recruitee:democompany3",
        # SmartRecruiters (postings API, every page): `kombo` is the integrations vendor's test
        # account, 62 of 156 titles test-shaped ("Test job 2", "QA Testing guru") beside
        # "Superman", "Astronaut", "dr pepper drinker"; Kombo's real Board is `ashby:kombo`.
        # `biogensandbox` is "Biogen SANDBOX", all 327 postings released in 2019 ("SIT-Test New
        # Position", "UAT EH14 - Sr Manager, Strategic Sourcing G&A"). `rhaegalsandbox` is
        # "Rhaegal - Arago Sandbox", a partner's sandbox of 561 postings, 65 test-shaped ("test
        # publication", "[DEMO] Développeur Java", "Rhaegal Sandbox"); its plausible French ads
        # apply into a sandbox, not to an employer. `joveosandbox` is "Joveo Sandbox" ("TESTING
        # Jobad api", "Test MQ", "jojosiva", "French man"). `rebeccajdemo` is "RebeccaJ Demo",
        # 23 postings from 2016-2018 ("Rocket Surgeon", "Rattlesnake Breeder", "SEEK Test Job").
        # `angloamericansandbox1` is "Anglo American Sandbox" ("BNE TEST JOB 6 - Met Coal
        # Apprenticeships", "Template (English): Mining Manager"). `piqc` is "Partners Internal
        # Quality Control" ("Test Tanya", "Anastasiia's Test Job", "Taxi driver").
        "smartrecruiters:angloamericansandbox1",
        "smartrecruiters:biogensandbox",
        "smartrecruiters:joveosandbox",
        "smartrecruiters:kombo",
        "smartrecruiters:piqc",
        "smartrecruiters:rebeccajdemo",
        "smartrecruiters:rhaegalsandbox",
        # Found by the 2026-09-24 company-name research (ADR-0212) and re-read live through each
        # Board's own scraper on 2026-09-25. BambooHR: `implementation` names itself
        # "Implementation - BLOCKING DOMAIN" (og:site_name) and serves 3 stock postings dated
        # 2019 ("IT Security Engineer" in "Mayfaird, London, City of", "Software Engineer",
        # "Account Executive"); `whitmansandbox` ("Whitman Sandbox") serves the same three plus
        # "Best job ever" and "Test job 2" in "Walla Walla, WA, Aruba".
        "bamboohr:implementation",
        "bamboohr:whitmansandbox",
        # Gem integration sandboxes, each serving the same 6 stock postings (CFO, Data Scientist,
        # Enterprise Account Executive, Senior Software Engineer, Senior Technical Recruiter,
        # Software Engineering Intern) under a company named `ats_sandbox_yello.co`,
        # `ats_sandbox_colorblastventures.com`, `integration_sandbox_brighthire.ai` or
        # `sandbox_schonfeld.com`. Schonfeld's real Boards are separate ledger rows.
        "gem:atssandboxcolorblastventures-com",
        "gem:atssandboxyello-co",
        "gem:integrationsandboxbrighthire-ai",
        "gem:sandboxschonfeld-com",
        # Jobvite: `halogen-customer-support` is a support team's test tenant, 59 postings:
        # "Beni - Requisition A", "BL Test Req", "BLBLTest", "BL - Test Approval notes",
        # "CM Agency req", five bare "Accountant"s.
        "jobvite:halogen-customer-support",
        # SAP's own SuccessFactors demo tenants ("BestRun", bestrunsap.com on ace1950/59/61/62's
        # home pages): all eight serve the same demo requisitions ("Engineer II", "Scheduler",
        # "Project Execution Lead", "Руководство и планирование", "(SB)" copies) at SAP's demo
        # cost-centre locations ("San Francisco (0300-0003)", "Berlin 1010 0001").
        "successfactors:ace1950.jobs2web.com",
        "successfactors:ace1954.jobs2web.com",
        "successfactors:ace1955.jobs2web.com",
        "successfactors:ace1958.jobs2web.com",
        "successfactors:ace1959.jobs2web.com",
        "successfactors:ace1960.jobs2web.com",
        "successfactors:ace1961.jobs2web.com",
        "successfactors:ace1962.jobs2web.com",
        # Zoho (careers page's embedded jobs): `zohocorp2.zohorecruit.com` is Zoho's own QA
        # tenant. Its company name carries an XSS probe (`Zoho India ''>>"">>"> ... <img src=x
        # onerror=alert(100)>`), as do 3 of its 28 titles; the rest read "Java Developer CRM Deal
        # renmaed Director", "Accountant       oppoo", "job publish Kosovo", "test 3".
        "zoho:zohocorp2.zohorecruit.com",
        # `hrpresales.zohorecruit.com` is Zoho Recruit's HR presales demo account, read live
        # 2026-09-29: 60 postings under the company "ZOHO", stock titles repeated ("Accountant"
        # 27 times, "Registered Nurse" 9, "Software Engineer" 6), Zoho Recruit's own template
        # descriptions ("We're looking for a passionate Software Engineer to design, develop and
        # install software solutions"), sales demos named after prospects ("Accountant - Timac
        # Agro", "Accountant - 5ire", "Accountant - Lakshmi Interiors"), "TEST", "Bootstrap
        # Evanglist" and "Nanny", places such as "Chennai, Bihar", dated 2022-09 onward. Its
        # 2022-23 "Software Engineer" rows in Toronto led a "software engineer" search in Canada
        # (rows 1-3 on 2026-09-29).
        "zoho:hrpresales.zohorecruit.com",
        # Blackstone's own test sites; the second is named for what it serves. Workday slugs
        # ARE the careers URL, so these keys are longer than the rest.
        "workday:https://blackstone.wd1.myworkdayjobs.com/marni_test_site",
        "workday:https://blackstone.wd1.myworkdayjobs.com/marni_test_ghost_posting_site",
        # Walmart's wd5 tenant was retired (superseded by wd504's WalmartExternal, the real
        # board, 2000 postings). wd5 now 303-redirects every jobs query to a Workday
        # maintenance page; our client follows it to a 200 of maintenance-page HTML, so
        # response.json() throws JSONDecodeError on every run (confirmed live 2026-08-19, not
        # just historical logs). The ledger carries this dead URL under three duplicate rows
        # (the match is on `slug_from`'s URL, not the `tenant` column, so only the URL's two
        # casings matter) — the lowercased key here covers both.
        "workday:https://walmart.wd5.myworkdayjobs.com/non-workdayinternal",
        # Vendor demo tenants from a served-table audit (v65, 2026-09-25), each checked live the
        # same day. BambooHR and Rippling were found by one normalized description served on 8 or
        # more Boards of one ATS; the blocks below say where one was found another way. A real
        # employer's name below is a sales demo named after a prospect, not that employer's Board.
        #
        # BambooHR's stock demo set, the one `bamboohr:implementation` above serves: "IT Security
        # Engineer" in "Mayfaird, London, City of" (misspelled on most; one description on 41 of
        # these Boards), "Software Engineer" in "Sydney, NSW" (another on 40), "General
        # Application" in Lindon, UT (BambooHR's home), "Financial Analyst", "Marketing Manager",
        # "Account Executive". All 49 readable ones list that set, under prospect and partner demo
        # names ("BAT Demo Account", "KBS - Reseller Account", "Synthesia - Marketplace Account",
        # "Brett Johnson Demo Company", "Your Company Name", "{{7*7}}"), and 21 of them, including
        # `walmart`, `leidos`, `popeyes` and `usi`, answer `inTrial: true` in
        # `careers/company-info`. A few add more of the same: `nook` placeholders ("Stuff goes
        # here.", "Job description here") and a designer whose description names "tools used by
        # thousands of HR professionals" and Draper, UT; `seek` is "SEEK Test" ("Designer (youtube test)", "Job # 5
        # LinkOut Only - No AwSK"); `bjdc` "Test", `sticks` "Tester". The other five (`acme`,
        # `alice`, `catalyst`, `omise`, `pmi`) now redirect to `settings/account/expired.php`,
        # lapsed trials, so their verdict rests on their indexed rows: the same stock set.
        # 104 served rows.
        "bamboohr:acme",
        "bamboohr:ailabs",
        "bamboohr:aix",
        "bamboohr:alice",
        "bamboohr:batdemo",
        "bamboohr:bjdc",
        "bamboohr:caseys",
        "bamboohr:catalyst",
        "bamboohr:chauffeur",
        "bamboohr:clair",
        "bamboohr:college",
        "bamboohr:connecteam",
        "bamboohr:court",
        "bamboohr:demogorgon",
        "bamboohr:dubz",
        "bamboohr:echonorth",
        "bamboohr:emissary",
        "bamboohr:examplecompany",
        "bamboohr:foundever",
        "bamboohr:greta",
        "bamboohr:ihop",
        "bamboohr:iri",
        "bamboohr:jadenknightondemo",
        "bamboohr:january",
        "bamboohr:jdsc",
        "bamboohr:kbs",
        "bamboohr:kusi",
        "bamboohr:leidos",
        "bamboohr:lyla",
        "bamboohr:macaw",
        "bamboohr:mpls",
        "bamboohr:nnu",
        "bamboohr:nook",
        "bamboohr:omise",
        "bamboohr:pmi",
        "bamboohr:popeyes",
        "bamboohr:privilege",
        "bamboohr:queen",
        "bamboohr:ramonabrowndemo",
        "bamboohr:scc",
        "bamboohr:seek",
        "bamboohr:shawna",
        "bamboohr:steelers",
        "bamboohr:sticks",
        "bamboohr:supademo",
        "bamboohr:synthesia",
        "bamboohr:talentlms",
        "bamboohr:tested",
        "bamboohr:toolbox",
        "bamboohr:usi",
        "bamboohr:walmart",
        "bamboohr:workflows",
        "bamboohr:yakka",
        "bamboohr:zaxbys",
        # Rippling: one unfilled template ("Describe the role and team the candidate will be
        # joining") on 12 Boards. Five are demos: `umbrella` is "Umbrella Corp (DEMO)" in Raccoon
        # City ("123123", "Internal Job"); `kt` ("Test", "Test1", "HHH") and `abc` wear generated
        # company names ("Tucker, Hull and Gallegos", "Williams-Sheppard"); the two
        # `dunder-mifflin{uuid}` sandboxes are "Bubba Gump Shrimp Co." ("lovb test") and
        # "Primestage Productions", some of whose postings say "About Williams-Sheppard". 14
        # served rows. Six others are real employers that left the template on one posting
        # (SubBase, Ami, Ellit Groups, Get Covered, Jumper Capital, Sunshine Enterprise); they
        # stay. So does `service` for now: its company name is generated ("Cooper-Haley") but it
        # lists 0 postings today, leaving no content to confirm.
        "rippling:abc",
        "rippling:dunder-mifflin2b318e3b-60f0-4d80-98c0-8f70fa8cccf6",
        "rippling:dunder-mifflin845a6671-e3ae-4be6-ab4e-b06beb1cf02a",
        "rippling:kt",
        "rippling:umbrella",
        # SAP's own RMK test sites. `testco12` titles itself "page title changed" and lists
        # "Formatting Test", "Horbach Footer Link Test", "Post-Deployment-B1111";
        # `testrcmsyncas002` is "RCMSYNCAS002 Test Site with updated Page Title b2111" ("RMK-RCM
        # job sync 2", "KirthiURLTest"). Both serve the same demo requisitions ("Database
        # Programmer", "Help Desk Manager", "IT Project Manager" in Boston and Denver). 6 served
        # rows. Named by the same audit from their slugs and titles, not found by the shared
        # description.
        "successfactors:testco12.jobs2web.com",
        "successfactors:testrcmsyncas002.jobs2web.com",
        # Workday's own demo tenant, Global Modern Services (`hiringOrganization` "500.1 Global
        # Modern Services, Inc. (USA)", url workday.com): "HRREC (IRISH TEA) ... (Do not use)",
        # "Job Req 6220 Test", "Tester Conv5", and `tgs` repeating "QA Engineer-9"/"-10" across
        # cities from Riga to Jakarta. Its shared description sits on 5 Boards, under the 8 above,
        # because every Board is one tenant's site. All seven sites; 106 served rows.
        "workday:https://super.wd103.myworkdayjobs.com/ext",
        "workday:https://super.wd103.myworkdayjobs.com/extdisform",
        "workday:https://super.wd103.myworkdayjobs.com/glt",
        "workday:https://super.wd103.myworkdayjobs.com/gms",
        "workday:https://super.wd103.myworkdayjobs.com/search",
        "workday:https://super.wd103.myworkdayjobs.com/tgs",
        "workday:https://super.wd103.myworkdayjobs.com/wdd",
        # ADP Workforce Now's own QA and build-verification clients (ADR-0241), each confirmed on
        # 2026-09-26 by reading its `client-features` ClientName and its listing, not from the
        # GUID, which hints at nothing. 31 name themselves as ADP test clients (`WFNQA…`,
        # `WFNPJL969`, `WFN4PRODA1`, `FARM 61 BVT4`, `NAS TEST CODE- Prod Enablement`); their titles are "NEW",
        # "BVT Analyst_07/30/2026" and "RECT AUTO REQS_07/17/2025", placed at "BVT Location,
        # Anchorage, AK" (`1af96a82`'s 60 of 60 are "NEW" there). Five state no such name and were
        # read by content: `ff0a5e37` ("PARAMOUNT V23P2gh") and the unnamed `c347ef8d`/`b47d5556`
        # post the same "BVT Analyst" rows all in Anchorage; `a1630435` ("V15P1TALONNN") posts "Administrative Analyst after restart", "multiselect
        # posting" and ADP's own "About Company: At ADP…" copy; the unnamed `d096a084` posts
        # "testjob_oct09", "Test", "E only with referrals" in "Alabama, Chicago, IL". 52,765
        # postings between them by the ledger, none an employer's; ~190 of them were served.
        "adp:cee60b4c-a3ce-4449-8f18-fd3eca0b7894/19000101_000001",  # "WFNQAFR60W", 3,486 postings
        "adp:163890b0-f860-4191-b06c-881706e6d893/19000101_000001",  # "WFNQAFR46Q", 1,962 postings
        "adp:77f11391-62d0-44e8-bcdb-802b2798d815/19000101_000001",  # "WFNPJL969", 1,795 postings
        "adp:f9798566-70f0-4281-9743-2e359e66f30a/19000101_000001",  # "WFNBVT53", 1,733 postings
        "adp:f56f72a6-d98d-43bc-b8f6-4862e222cb43/19000101_000001",  # "WFNQAFR56C", 1,729 postings
        "adp:ff0a5e37-b65d-4035-b863-3097434d4915/19000101_000001",  # "PARAMOUNT V23P2gh", 1,725 postings
        "adp:eab2adc0-9f02-4e29-bf20-79b5cce8210e/19000101_000001",  # "WFNQAFR45D", 1,721 postings
        "adp:3c865fe4-0ee2-4e55-b508-b1b451d8d221/19000101_000001",  # "WFNQARTP50", 1,711 postings
        "adp:b7eb6885-fe6e-42ac-ab29-7a8c5227c15e/19000101_000001",  # "WFNQAFR52D", 1,703 postings
        "adp:8e8b931f-9f93-47ca-a52f-dffe178e0e7d/19000101_000001",  # "wfnqafr77E", 1,702 postings
        "adp:4fb7b47c-06c6-4f12-9c53-fbfcdbc0d011/19000101_000001",  # "FARM 61 BVT4", 1,696 postings
        "adp:e97c4a5e-b050-4e9d-96f9-a5757e7c85a0/19000101_000001",  # "FARM 61 BVT4", 1,696 postings
        "adp:902aa559-750d-4565-92ef-29f69097ef3f/19000101_000001",  # "WFNQAFR49D", 1,693 postings
        "adp:1af96a82-0f9c-49a0-895a-457e1347863f/19000101_000001",  # "WFN4PRODA1", 1,689 postings
        "adp:833c58fd-5bc2-40b2-9d99-7fceb4b98847/19000101_000001",  # "WFNQAFR54A", 1,687 postings
        "adp:660ec678-4d96-4fd9-9834-3f094bb98b46/19000101_000001",  # "WFNQAFRC8", 1,666 postings
        "adp:450cc0e8-55e7-4b41-9cca-3a3418e88728/19000101_000001",  # "WFNQASD5", 1,661 postings
        "adp:5f909076-d657-4269-a81f-6274bb186535/19000101_000001",  # "WFNQABVT41", 1,653 postings
        "adp:41b1b7bc-6fa1-478b-b961-0a004311c745/19000101_000001",  # "WFNQAFR59M", 1,651 postings
        "adp:fb429c02-21bb-4995-a563-9f5d77ee858e/19000101_000001",  # "WFNQARTP47", 1,624 postings
        "adp:665c6c88-3fd0-4071-b8e8-b9dd969e16b6/19000101_000001",  # "WFNQA86P2", 1,621 postings
        "adp:b758d3b7-9627-4fcd-8f39-5fff8ed94908/19000101_000001",  # "WFNQA86P2", 1,621 postings
        "adp:f1238493-f948-4881-9181-84ce545d714b/19000101_000001",  # "WFNQA88P2", 1,610 postings
        "adp:3aa15c1d-9e2a-4792-ab0f-7b36ba852f23/19000101_000001",  # "WFNQAFR45L", 1,604 postings
        "adp:3da01a47-4989-46a2-90a4-eb4dfac6dd64/19000101_000001",  # "WFNQAFR59M", 1,430 postings
        "adp:6e24c0b1-d3c5-45f6-8731-ff36924e0a37/19000101_000001",  # "WFNQAFR59M", 1,430 postings
        "adp:b47d5556-ee90-42b7-a7c5-3643f0eb2ff4/19000101_000001",  # no ClientName, 1,430 postings
        "adp:c347ef8d-b253-480d-b4f5-e3738b7c5148/19000101_000001",  # no ClientName, 1,430 postings
        "adp:d9c2098d-4a3c-4a66-aa54-edfb7e8ac72b/19000101_000001",  # "WFNQA94P28", 1,415 postings
        "adp:aedf6cdf-cf4c-4553-91c9-1e2b6d1b1227/19000101_000001",  # "WFNQAFR64D", 1,398 postings
        "adp:a1630435-4700-4d72-84cf-2c5414d66b3c/19000101_000001",  # "V15P1TALONNN", 1,202 postings
        "adp:d096a084-16ea-4e5d-b8c3-7be8b4104c6f/19000101_000001",  # no ClientName, 538 postings
        "adp:f00fb933-2eef-4643-899d-cae607d51e91/19000101_000001",  # "WFNQAv17T5", 20 postings
        "adp:aeb4c75f-7753-4ed5-9ee0-84cc931df3cd/19000101_000001",  # "WFNQAEM6", 17 postings
        "adp:3c19fd9b-0d5e-449a-81a8-b59357c53950/19000101_000001",  # "NAS WFN Prod Enablement -testnas030", 11 postings
        "adp:096c8f5e-1be4-44f5-858d-6b11303c27f2/19000101_000001",  # "NAS TEST CODE- Prod Enablement", 5 postings
        # WP Job Openings' own demo sites (ADR-0266), read 2026-09-28: the vendor's sample
        # postings, mostly tech titles ("UI/UX Designer", "DevOps Engineer", "QA Tester"), dated
        # 2018-2022 — 17 on `demo.wpjobopenings.com`, 16 on `demo.hirezoot.com`. The vendor's own
        # hiring site, `awsm.in`, is a real employer and is not here.
        "wp_job_openings:demo.hirezoot.com",
        "wp_job_openings:demo.wpjobopenings.com",
        # Pinpoint's auto-seeded sample postings, found 2026-09-30 by reading every Board landed from
        # the Common Crawl host graph, the CrUX origin list, the sitemap index and jobseek (319 live):
        # 122 of them serve only Pinpoint's own onboarding set, 471 postings none of which is an
        # employer's. 116 serve one of three fixed sets and nothing else: "Head of DEI - UK",
        # "Marketing Manager", "Customer Service Rep" (43 Boards, e.g. `100ms`, created 2025-06-04),
        # the same with "Marketing Executive" (44 more, e.g. `zendesk`, 2025-12-12) or, in the older
        # copy, "Head of DEI - Belfast" and "- US" under Hipster Ipsum text ("Vape tattooed gentrify
        # pug", 28 Boards, e.g. `assemblyosm`, 2024-11-05), and one Board with the second set minus
        # its service rep. Every one of the 116 opens its descriptions with the same three or four
        # lines, gives each posting a department and city that rotate regardless of the title
        # ("Marketing Manager" under Engineering in Paris, "Customer Service Rep" under Engineering
        # in New York), and 104 were created within 5 seconds of each other (the other 12 span
        # longer). Real employers' names sit on them (`zendesk`, `hellofresh`, `razorpay`,
        # `kraken`, `legalzoom`): trial accounts nobody published a job on. Six more serve only
        # filler: `fountain` and `jwplayer` retitle it ("Software Engineer- Platform", "Staff
        # Software Engineer (Backend)") over the same rotating departments and cities and the
        # Hipster Ipsum text, `obsidian` ("Test Job 3"), `mateusz-testing` ("Test Job") and
        # `tdpfund` ("Test Job 1", "Test Job 2", "Vet Tech") carry it in every posting, and
        # `crystalblockchain` is the seeded five plus "AB Test Job 1". Other Boards hold some of these
        # sample postings beside real ones (`ada`, `gearset`, `oviva`, `dermavant`, `kodland`,
        # `pmaconsultants`): they stay, and so do their real postings. Two more, read 2026-09-30 after
        # the landing, serve only seeded postings: `sofi` ("Head of DEI - UK", "Customer Service Rep")
        # and `innovaccer` ("Head of DEI - UK", "Marketing Manager").
        "pinpoint:100ms",
        "pinpoint:174powerglobal",
        "pinpoint:60decibels",
        "pinpoint:aiven",
        "pinpoint:alliance",
        "pinpoint:assemblyosm",
        "pinpoint:astropay",
        "pinpoint:athabascacatering",
        "pinpoint:badrobotgames",
        "pinpoint:beautybarrage",
        "pinpoint:bhhc",
        "pinpoint:boomi",
        "pinpoint:brightnetwork",
        "pinpoint:built",
        "pinpoint:buzzbingo",
        "pinpoint:chas",
        "pinpoint:chattermill",
        "pinpoint:cielotalent",
        "pinpoint:coit",
        "pinpoint:consensus",
        "pinpoint:corvias",
        "pinpoint:coverdash",
        "pinpoint:crystalblockchain",
        "pinpoint:cvent",
        "pinpoint:deltacapita",
        "pinpoint:dreamweave-designs",
        "pinpoint:drfirst",
        "pinpoint:editasmedicine",
        "pinpoint:ekimetrics",
        "pinpoint:enable",
        "pinpoint:endlesswest",
        "pinpoint:eqvilent",
        "pinpoint:everbridge",
        "pinpoint:exadel",
        "pinpoint:exp",
        "pinpoint:fetchrewards",
        "pinpoint:fountain",
        "pinpoint:fourthrev",
        "pinpoint:fundingcircle",
        "pinpoint:gett",
        "pinpoint:goldcast",
        "pinpoint:groundtruth",
        "pinpoint:hellofresh",
        "pinpoint:hermeus",
        "pinpoint:hubvisory",
        "pinpoint:humane",
        "pinpoint:improbable",
        "pinpoint:includedhealth",
        "pinpoint:innovaccer",
        "pinpoint:inspiresleep",
        "pinpoint:instrumental",
        "pinpoint:intellectt",
        "pinpoint:isscareers",
        "pinpoint:jerry",
        "pinpoint:jobandtalent",
        "pinpoint:jwplayer",
        "pinpoint:kiwi",
        "pinpoint:koleyjessen",
        "pinpoint:kraken",
        "pinpoint:legalzoom",
        "pinpoint:lhh",
        "pinpoint:lionsbot",
        "pinpoint:logicmanager",
        "pinpoint:mantelgroup",
        "pinpoint:mateusz-testing",
        "pinpoint:matrix",
        "pinpoint:mercerkitchenhall",
        "pinpoint:mks",
        "pinpoint:moengage",
        "pinpoint:moneyboxapp",
        "pinpoint:netcomlearning",
        "pinpoint:next",
        "pinpoint:nextovation",
        "pinpoint:notablefi",
        "pinpoint:obsidian",
        "pinpoint:odysseyhotelgroup",
        "pinpoint:onboardmeetings",
        "pinpoint:onepeloton",
        "pinpoint:oneworkplace",
        "pinpoint:paymentop",
        "pinpoint:pentera",
        "pinpoint:penumbrainc",
        "pinpoint:pinnaclegroup",
        "pinpoint:planhat",
        "pinpoint:planner5d",
        "pinpoint:primient",
        "pinpoint:productschool",
        "pinpoint:rand",
        "pinpoint:razorpay",
        "pinpoint:recruitaero",
        "pinpoint:renewal",
        "pinpoint:reply",
        "pinpoint:rewind",
        "pinpoint:riverflex",
        "pinpoint:saga",
        "pinpoint:shorelight",
        "pinpoint:smallgirlspr",
        "pinpoint:smarsh",
        "pinpoint:sofi",
        "pinpoint:solis-academy",
        "pinpoint:sonatype",
        "pinpoint:spartaglobal",
        "pinpoint:synopsys",
        "pinpoint:tandem",
        "pinpoint:tdpfund",
        "pinpoint:tealium",
        "pinpoint:teza",
        "pinpoint:thndr",
        "pinpoint:tidio",
        "pinpoint:trailerparkgroup",
        "pinpoint:trailofbits",
        "pinpoint:travelperk",
        "pinpoint:trint",
        "pinpoint:typescouts",
        "pinpoint:uvcyber",
        "pinpoint:vastspace",
        "pinpoint:version1",
        "pinpoint:viooh",
        "pinpoint:weightwatchers",
        "pinpoint:worldremit",
        "pinpoint:you",
        "pinpoint:zeffy",
        "pinpoint:zendesk",
        "pinpoint:zeta",
        # Gem test tenants, each read on 2026-09-30. `candoriq`, `fireflies-ats`, `getcedar-ai-ats`,
        # `pin` and `staffeto-com` serve exactly the 6 stock postings the four sandboxes above serve
        # (CFO, Data Scientist, Enterprise Account Executive, Senior Software Engineer, Senior
        # Technical Recruiter, Software Engineering Intern) under different names. `oats-testing` is
        # a test tenant: 134 postings ("iulia test posting", "Integration Test Job", "Lucia's Personal
        # Assistant", "Thrall in Tiger's Undead Army") at a location named "Toooo...ronto is a long
        # name". `frigade-co` posts "frigade-test-Jobs-1" and "frigade-test-ATS-job1" in department
        # "frigade-test-ATS-dept". `testbox-buffer2` is 12 generic postings created within two
        # minutes on 2026-08-31 with "About the role We are hiring a ..." text. `sandboxvectorsolutions-com`
        # is 4 postings whose description reads "-Recruit Jobs -Use LinkedIn -Find a new ATS -Hire BOMB
        # Candidates" or nothing.
        "gem:candoriq",
        "gem:fireflies-ats",
        "gem:frigade-co",
        "gem:getcedar-ai-ats",
        "gem:oats-testing",
        "gem:pin",
        "gem:sandboxvectorsolutions-com",
        "gem:staffeto-com",
        "gem:testbox-buffer2",
        # JazzHR (board page, postings and their descriptions), each read 2026-09-30. `bigclicinc`
        # posts "Sample Job" (JazzHR's default description text, "You can enter a detailed, formatted
        # description of your position") and "Testing Job". `demonotforactualuse16` names itself
        # "Demo: NOT FOR ACTUAL USE" and posts JazzHR's template ad ("Bob's is looking for a customer
        # service representative to join our team in our St Louis office"). `sproutsolutionsdemo`
        # posts "Registered Nurse (RN) [test only]", "Senior Graphic Designer [test only]" and "THIS
        # IS TEST ONLY Job Title: Field Technician Location: [City, State]". `prestigedemo` is
        # "Prestige Demo/NFR": four "Barista"s and JazzHR's "Sample Job" (the real `prestige` Board
        # is separate). `mevitae94` posts 36 test-shaped titles of 38 ("Accountant Demo Job -
        # MeVitae-2865269-ME1", "Test Job 639009648685488365"). `zenefitness1` posts "Test Engineer"
        # and "Test manager" with the same HR boilerplate ("Maintains staff by recruiting, selecting,
        # orienting..."). `mayweatherboxingfitnesssandbox`, `prosesandbox`, `thedesignerysandbox`
        # and `verifiedfirstsandbox` name themselves "- Sandbox": copies of a real Board's ads
        # placed at the bare location "United States", or "Come work for us!". The real Boards are
        # separate ledger rows. Deliberately NOT excluded: `csptest2025`, whose three postings are
        # real, dated "2027 Start" ads for CornerStone Partners.
        "jazzhr:bigclicinc",
        "jazzhr:demonotforactualuse16",
        "jazzhr:mayweatherboxingfitnesssandbox",
        "jazzhr:mevitae94",
        "jazzhr:prestigedemo",
        "jazzhr:prosesandbox",
        "jazzhr:sproutsolutionsdemo",
        "jazzhr:thedesignerysandbox",
        "jazzhr:verifiedfirstsandbox",
        "jazzhr:zenefitness1",
        # Personio's own demo tenant, read 2026-09-30: `demorecruiting` serves 154 postings ("adas",
        # "dsadasda", "Ana test some description", "DEMO JULY", one "Engineering Manager" title
        # repeated twelve times) at "A really long office name that is very unlikely to be so long",
        # the company named after its host. The slug is the host, so both TLDs are listed.
        "personio:demorecruiting.jobs.personio.com",
        "personio:demorecruiting.jobs.personio.de",
        # Teamtailor (jobs.json and each posting), read 2026-09-30: `airelogicsandbox` ("Aire Logic
        # Sandbox") posts "We have a business that needs analysing and we need a business analyst
        # to do the analysing." and "Someone who analyses data". `sandboxnytlaegejob-1732104705`
        # ("Sandbox: Nytlaegejob") is Teamtailor's stock sample: nine postings created within ten
        # seconds on 2024-11-20, all opening "We're committed to helping companies look their best
        # to potential candidates". `emsjotestvarjedag` posts "Account Manager for Puppy" and "Looking
        # for the next "right hand" to Darth Vader". `simoncase` ("Simon Demo") posts two "test"
        # postings and Teamtailor's own recruiting copy ("Teamtailor is an Employer Branding & ATS
        # SaaS platform used by over 5.000 companies"). Deliberately NOT excluded, though their slugs
        # invite it: `demojobbusters`, `helppokatsastusoydemo`, `hrwithyoudemo` and `kanrestaoydemo`,
        # whose postings, read, are real, current, fully described ads by the named employers.
        "teamtailor:airelogicsandbox",
        "teamtailor:emsjotestvarjedag",
        "teamtailor:sandboxnytlaegejob-1732104705",
        "teamtailor:simoncase",
        # SmartRecruiters (postings API, every page and each posting), read 2026-09-30 across all
        # 6,727 live Boards landed from the amikai roster. Seven are the vendor's own QA automation,
        # named `{Scenario}Test{yymmdd}{random}00`, whose postings are "Job 180414ToIh3u04" (a date
        # and a random id), "Job in Afghanistan" and "Job in Poland", at fixture places such as
        # Szczebrzeszyn, Coast City and Kabul: `allofferstabtest1804148clgqr00`,
        # `channeljointest171018wiojhl00`, `emailstest190829rxev1f00`, `emailnotificationssendgridtest170113thhun100`,
        # `multipleapplicationtest170516dzeoul00`, `rejectionwithdelayedrejectionfeaturetest180426kgkcxq01`
        # and `createandpublishjobwithdifferentjoblocationprecisiontest180126gn8fmf00`.
        # Eighteen are integration partners' test accounts, each posting test titles ("AC 1.7 test -
        # please ignore this job" on both `apex-linkedinacone` and `-two`; "test job post - do not
        # apply" on all 13 of `traitify`; "Test Job SR Ewa" on `moseeker`; "Smart Recruiters Test" on
        # `saassyco`; "asdgasdf", "edit to testeez" on `vinformatix`; "Demo deny list ATS" on
        # `visageinc`; "Teststelle NICHT BEWERBEN" on `time4hires`; "KATH GALBIZO - TEST 1" on
        # `uplunitedphosphoruslimited`; "CENTRL Test Job I", "Cielo Test Job I" on `joshdemotwo`;
        # "Really Really Really Long Job Name" and "Nowhere Creek, VIC" on `gem1`, a company named Gem).
        # Twelve more are test accounts, most named for it, posting placeholder content: `barbarasandbox`
        # ("Poste test", "Copy of Poste test"), the three `cobra`/`testcobra` companies ("please do
        # not apply for this job", 13 of 13 on `testcobratjcompany`), `mcdsandbox` ("job23",
        # "pentest"), `partnerssandbox` ("Job Example 2"), `clientdemosandbox163` ("Rubrik Demo"),
        # `targetcwsandbox` (five 2019 ads for a staffing firm, "TargetCW Sandbox"), `timburnettsandbox`,
        # `seeksrtestaccount` (five "Nurse" postings in Information Technology whose descriptions are
        # commercial-strategy text, a generic nurse ad or empty), `mariposainc` (5 postings, one City-benefits text
        # under "test", "driver" and "Test Longitude & Latitude") and `donscompany` ("Another Test":
        # "This is another job description"). Deliberately NOT excluded: `alignment1`, `brunel1` and
        # `monsterworldwide1`, which mix a test posting or two with real (if 2015-2019) ads, and
        # `alliedglobaljobs`-style Boards that only carry "sandbox" in the name.
        "smartrecruiters:allofferstabtest1804148clgqr00",
        "smartrecruiters:apex-linkedinacone",
        "smartrecruiters:apex-linkedinactwo",
        "smartrecruiters:barbarasandbox",
        "smartrecruiters:channeljointest171018wiojhl00",
        "smartrecruiters:clientdemosandbox163",
        "smartrecruiters:cobralivetestcompany6",
        "smartrecruiters:cobralivetestcompany7",
        "smartrecruiters:createandpublishjobwithdifferentjoblocationprecisiontest180126gn8fmf00",
        "smartrecruiters:donscompany",
        "smartrecruiters:emailnotificationssendgridtest170113thhun100",
        "smartrecruiters:emailstest190829rxev1f00",
        "smartrecruiters:ewatest",
        "smartrecruiters:gem1",
        "smartrecruiters:joshdemotwo",
        "smartrecruiters:linkedininternaltest",
        "smartrecruiters:mariposainc",
        "smartrecruiters:mcdsandbox",
        "smartrecruiters:moseeker",
        "smartrecruiters:multipleapplicationtest170516dzeoul00",
        "smartrecruiters:partnerssandbox",
        "smartrecruiters:pitchyouintegration",
        "smartrecruiters:rejectionwithdelayedrejectionfeaturetest180426kgkcxq01",
        "smartrecruiters:remotive",
        "smartrecruiters:resume-library",
        "smartrecruiters:saassyco",
        "smartrecruiters:seeksrtestaccount",
        "smartrecruiters:shazamme",
        "smartrecruiters:targetcwsandbox",
        "smartrecruiters:testcobratjcompany",
        "smartrecruiters:timburnettsandbox",
        "smartrecruiters:time4hires",
        "smartrecruiters:traitify",
        "smartrecruiters:uplunitedphosphoruslimited",
        "smartrecruiters:vinformatix",
        "smartrecruiters:visageinc",
        "smartrecruiters:work42",
        # `assets.freshteam.com`, a vendor infrastructure host that a host-graph sweep read as a
        # tenant and the liveness probe left `unknown`, read 2026-09-30: it answers S3's
        # AccessDenied XML. It has no openings board.
        "freshteam:assets",
        # Recruitee tenants landed by the 2026-09-30 passive-DNS and wordlist discovery, each
        # confirmed by reading every offer the offers API served that day, not from the label:
        # `amstelring` (a real Dutch care organisation) serves 11 offers and all 11 are titled
        # "TEST ..." ("TEST Helpende plus - op basis van vacaturetemplate", "TEST Psycholoog", "TEST
        # Interne auditor"), created 2026-09-10 to 09-21: its acceptance account. `zuyderland` (a
        # real hospital group) serves 7 offers, all test: "Communicatie test 2.0" ("test 2.0"),
        # "Test Template evaluatieformulieren" ("x x"), "AMC testvacature" ("nvsnsvd bsbvxmnnm"),
        # "Automatiseringen Test" ("testt"), "Verpleegkundige test". `auau` serves one offer, "Madz",
        # whose text is a filled-in stock template ("We are seeking a talented individual to join
        # our team as a Madz at Madz"). `rooster` serves one, "Sales", described "This is a role for
        # testing  these are the requirements". Their real Boards, if any, are other labels.
        "recruitee:amstelring",  # 11 offers, all "TEST ..."
        "recruitee:auau",  # 1 offer, "Madz"
        "recruitee:rooster",  # 1 offer, "This is a role for testing"
        "recruitee:zuyderland",  # 7 offers, all test
        # Recruitee (offers API), read 2026-09-30: `bamboohr` is "Cycle HR - Sandbox", two offers
        # whose descriptions are the same construction-worker text under "Construction Laborer" and
        # "Human Resources Recruiter". `happyhorizon` is "Sandbox HappyHorizon": 34 offers created
        # 3-4 seconds apart on 2026-07-22 whose departments do not fit their titles ("Motion
        # Designer" in Human Resources, "Frontend Developer" in Marketing) beside "Open Application
        # Text". `alliedglobaljobs` ("Allied Global Sandbox") is NOT here: its 31 offers are dated
        # over months, at real sites in Guatemala and Honduras, with real descriptions.
        "recruitee:bamboohr",
        "recruitee:happyhorizon",
        # Recruitee vendor infrastructure hosts that a host-graph sweep read as tenants and the
        # liveness probe left `unknown`, read 2026-09-30: `mobile.recruitee.com` is the vendor's app
        # page, `s.recruitee.com` answers 502 Bad Gateway and `data-warehouse-docs.recruitee.com` is
        # a documentation site. None has an openings board.
        "recruitee:mobile",
        "recruitee:s",
        "recruitee:data-warehouse-docs",
        # TurboHire's own demo organizations, both named "TurboHire - Demo Account", read
        # 2026-09-30: `thdemo` lists 27 postings such as "Hotel Operations Trainee - Copy Test"
        # and "Junior UI/UX Product Designer/ Long Temporary Title For Testing/Remove later",
        # with salaries like 12123213-432432433 INR; `democareers` lists 6, among them
        # "Sales - Enterprise - 18-11". The vendor's own hiring page, `careers` ("TurboHire
        # Technologies Private Limited (Official)"), is a real employer and is not here.
        "turbohire:democareers",
        "turbohire:thdemo",
    }
)

# Real Boards withheld *for now* — kept apart from EXCLUDED_BOARDS above, whose every member is
# not a genuine Board at all. Each entry says what un-parks it: a park that outlives its reason
# is silent lost coverage, and this one costs a large employer.
#
# Keyed on the canonical lowercased ``board_key``, NOT on ``ats:slug`` like EXCLUDED_BOARDS. The
# ledger carries one Workday Board under several hosts — Accenture sits on both `wd3` and `wd103`
# — and `board_key` is what collapses them (ADR-0023). Keyed on one URL, the park would remove
# that row and merely promote another instance's row to be `scrapable_boards._elect`'s
# survivor: the Board keeps being scraped while the entry looks effective.
#
# A parked Board also leaves `index_plan.live_keep_set`, so whatever rows it holds in the index
# are evicted as off-Board. That was first accepted on the grounds that the Board had never
# finished a scrape and so had little indexed — true of Accenture, and no longer true of the set:
# Adeeba holds 136 tech rows and Wayman 12, both of which finish. The eviction is still accepted,
# but on the other half of the original reason rather than that one — a Board we stop scraping
# cannot keep its rows fresh, so serving them would be serving a snapshot that only ages.
PARKED_BOARDS: frozenset[str] = frozenset(
    {
        # Read 2026-10-03 after the iCIMS landing: each client's whole listing
        # is covered by Scrapable iCIMS Boards (ADR-0240), so Jibe keeps none.
        "jibe:chumashcareers",  # 23 of 23 postings
        "jibe:davidsonhospitality",  # 928 of 928
        "jibe:jointcommission",  # 1 of 1
        "jibe:oraucareers",  # 14 of 14
        "jibe:paveamerica",  # 2 of 2
        # New Manatal adapter, ADR-0384: the company's own /careers redirects to its
        # legacy Board (32 Jobs); this alternative advanced Board has 40 JobPosts
        # with substantial overlap and different ids. Prefer the endorsed surface
        # until including both collections is explicitly decided. No existing
        # Manatal support predates this initial publication choice.
        "manatal:manatal.careers-page.com",
        # Same-label legacy and advanced surfaces both read live on 2026-10-03.
        # Keep the initial legacy source while employer endorsement/overlap is
        # unresolved; these are not asserted to be exact aliases (ADR-0384).
        "manatal:10folders.careers-page.com",
        "manatal:aperiohub.careers-page.com",
        "manatal:bandwidth-global.careers-page.com",
        "manatal:barthhaas.careers-page.com",
        "manatal:chefra-solutions.careers-page.com",
        "manatal:city-care-partnership.careers-page.com",
        "manatal:cmsistemiinformatici.careers-page.com",
        "manatal:empire.careers-page.com",
        "manatal:find-job-latam.careers-page.com",
        "manatal:gigalabs-private-ltd.careers-page.com",
        "manatal:global-staff-network.careers-page.com",
        "manatal:integrated-office-solutions-inc.careers-page.com",
        "manatal:intelus-agency.careers-page.com",
        # Masimo's retired Oracle Board (#873), rechecked 2026-10-02: both served
        # job 4018 and 2562 redirect to CandidateExperience/errors/404 (HTTP 200).
        # The API still lists them, so absence-based sync cannot remove them.
        # Prune removes this Board's held rows; unpark only when public applications work.
        "oracle:egcu.fa.us6.oraclecloud.com",
        # 48,369 jobs. Workday reports a query's total as at most 2,000, so the scraper
        # subdivides by facet (depth 3 here) and pages each leaf 20 at a time — thousands of
        # sequential requests against a Board no per-board budget bounds. It finished in none of
        # the three runs of 2026-08-13 (03:36 / 06:53 / 08:48 UTC), and because a running thread
        # cannot be cancelled, `scrape_all`'s shutdown then outlived the 6 min between the 60m
        # inner budget and the 66m step timeout (75m and 81m since ADR-0229, the same 6 min) —
        # failing the whole shard, not just this Board.
        # Un-park once a per-board deadline bounds it.
        "workday:accenture/accenturecareers",
        # 1,162 postings, real and un-fabricated — unlike Accenture above this board finishes
        # every run, it's just consistently the worst floor-bound shard once it does: the single
        # most expensive board across 10+ consecutive pipeline runs (~19-37 min each,
        # docs/pipeline/2026-08-20_cadence-settle-in-and-critical-path.md §3), now the run-owning
        # straggler after the six Workday retail boards were narrowed instead of parked (§6).
        # No per-category narrowing exists for this scraper the way Workday's
        # `_FIXED_FACETS_BY_SLUG` does — SuccessFactors' listing surfaces (sitemap/search/RSS)
        # carry no facet mechanism to fetch only a tech-labeled subset. Un-park once one exists,
        # or a per-board timeout bounds the cost instead.
        "successfactors:careers.ey.com",
        # 23,806 postings for **136 tech jobs** — the worst cost-to-yield Board in the corpus, and
        # the run-owning straggler in all 7 of the runs 33065892407..33151091246. Measured from
        # those runs' own `scrape_run` lines: 1,466 s, against a shard total of 1,471 s. It is not
        # merely the slowest board in its shard, it *is* its shard — everything else had finished
        # 5 s earlier, and `board seconds` for that shard reads p50 0.4, p99 118, max 1,466.
        # Scrape is 42% of the run's wall clock and straggler-bound, so this one Board costs ~10
        # min of critical path per run to contribute 0.04% of the index (136 of 330,487 rows,
        # `data/state/board_priority.csv` 2026-08-28) — 10.8 s of makespan per tech job.
        # Un-park if its tech yield ever justifies the floor, or once a per-board deadline bounds
        # it — the same condition that would un-park Accenture above.
        "smartrecruiters:adeebaeservicespvtltd",
        # Fails ADR-0064's value test on the gate's own numbers and escapes only its floor.
        # `board_cost.csv` measures it at 562 s for 56,527 postings (2026-09-11); against the 12
        # tech jobs `board_priority.csv` credits it, that is **1.28 tech/min, under the gate's 2.0
        # threshold** — but 562 s is under the 900 s floor (and under the 600 s one since
        # 2026-09-24), so its yield is never consulted. It is not a straggler like the three
        # above; it is fast and enormous, the shape that floor was
        # never meant to catch. It is also unreliable: in 3 of the 5 runs
        # 34450830376..34470668397 it raised `HTTP Error 400` after 44-271 s and produced nothing.
        #
        # Content read before parking, per EXCLUDED_BOARDS' rule above: **Wayman Learning Trust**,
        # a real UK teacher-recruitment agency — "Maths ECT — Outstanding Secondary School —
        # Bristol", "Physics Teacher Needed". Real postings, simply not tech, which is why this is
        # a park and not an exclusion. (`tech_filter` keeps 0 of 100 sampled titles, but note that
        # a credited 12 in 56,527 predicts 0.02 hits in a sample that size, so the sample bounds
        # the rate low and cannot show the 12 are gone. The 1.28 tech/min above is the argument.)
        #
        # Parked as one Board rather than gated as a class: ADR-0136 records the gap analysis that
        # rejected a volume dimension, and `docs/pipeline/2026-09-10_five-run-log-review.md` §3 has
        # the run figures. Its 100-job row in `data/validate/liveness/teamtailor.csv` is one page,
        # so every ledger-driven view of this Board is 565x too small — which is why it stayed
        # invisible, and is a probe-side gap this park does not close.
        #
        # Un-park once a per-Board row budget bounds the cost — the same condition as Accenture
        # above — or if the index ever serves non-tech roles. Both are observable here; "if the
        # trust posts tech roles" is not, because parking is what stops us looking.
        "teamtailor:waymaneducation-1710232669",
        # The run-owning floor-bound straggler in 8 of 8 pipeline runs sampled 2026-09-16 (98% of
        # its shard, 18.7-43.7 min each run). Real data, not a demo tenant — a live sample of 300
        # titles that same day came back 0.0% test-marked, ordinary hospitality postings ("Commis
        # (Uzbek national)" in Tashkent, "Junior Sous Chef" in Bucharest). Not fetch-volume-bound
        # either: live-measured 2026-09-17, a direct (non-WARP) connection cleared the board's
        # real workload — 20 concurrent listing pages and 100 detail calls at 16 workers (matching
        # `_DETAIL_WORKERS`) — 100% 200s, 0 429s, 25.5 req/s, predicting ~8-9 min end to end. The
        # pipeline's own WARP-routed runs take 2-5x that, and that shard alone carries the highest
        # 429/network retry ratio of any shard measured (0.30 vs 0.07-0.17 elsewhere) despite the
        # scraper already rotating egress on every 429. So the cost is specific to the WARP path,
        # not this board's size — and it is also permanently offset-capped at 9,926 of 13,642
        # postings regardless (Oracle serves no offset past 10,000, ADR-0053 scope-excludes it
        # from eviction every run). Un-park once the WARP-path slowdown is understood and fixed,
        # or a direct route exists for this host.
        "oracle:ejwl.fa.us2.oraclecloud.com",
        # Parked on landing, before any run read it. Found 2026-09-26 by the six-letter code DNS
        # sieve (`{code}.fa.ocs.oraclecloud.com`): **Dollar General**'s Board (named in its
        # postings' detail), reporting **90,694 postings**. Content read before parking: real
        # store-manager and warehouse roles, not a demo tenant. **2 of the first 1,000 titles
        # pass `tech_filter.is_tech`** (0.2%). That sample is the oldest end of the listing, not a
        # random draw, so it bounds the rate only roughly. Oracle serves no offset past 10,000, so
        # a scrape reads up to ~20,000 postings from both ends (ADR-0239): at 0.2%, about 40 tech
        # jobs for 20,000 rows read. Un-park if its tech yield ever justifies that, or once a
        # per-Board deadline or row budget bounds the cost.
        "oracle:ibxwjb.fa.ocs.oraclecloud.com",
        # The two below are the first parked for what they *serve* rather than what they cost:
        # near-duplicate spam, holding a top-5 priority slot each. They are the same defect from
        # opposite ends — one role across 2,352 cities, and one city repeating a handful of roles
        # 8,478 times — so neither shape describes both; see each entry. `index prune`'s duplicate
        # check cannot reach either: every posting carries its own id, so they are duplicates
        # semantically, not by identity (ADR-0023 groups on identity).
        #
        # Measured live 2026-09-21 (`registry.get_scraper(...).fetch_raw()`/`.parse()`, captures in
        # `experiment/near-duplicate-spam-boards/`): **4,377 postings, 2,562 distinct titles across
        # 2,352 distinct locations** — and no exact title repeats more than 4x, which is why a
        # distinct-title ratio reads this Board as ordinary. Strip each title's per-city tail and
        # **4,249 of the 4,377 (97.1%) are the one stem "Data Center Technician"**, over 70 stems
        # total: "... - Saudi Arabia - Khobar - On-site", "... - Nigeria - Lagos - On-site",
        # "... - PR - Guaynabo - On-site". Ranked #3 in `data/state/board_priority.csv` on the same
        # date, credited 4,334 tech rows — **40.6% of every tech row the ledger credits to
        # recruitee at all**, across its 1,304 scored Boards.
        # Un-park if the postings ever stop being one templated role, or once a near-duplicate gate
        # on the index path can collapse them. That gate was deliberately **not** built here, on a
        # measured prevalence sample of the top 30 priority Boards (same experiment folder): this
        # Board's 97.1% is 5.3x the worst of the other 23 measured, so a rule keyed on it would
        # fire on exactly one Board — a hardcoded park with extra steps.
        "recruitee:rebootmonkey",
        # The same shape reached the other way: not one role across every city, but one city
        # posting the same handful of roles over and over. Measured live 2026-09-21: **8,478
        # postings, 8,454 of them (99.7%) in "Indore, MP, India"** — 5 distinct locations in total
        # — dominated by exact-title repeats: `Internship / Training for PHP` x49, `Fresher Android
        # Developer Training Program` x43, `Internship For IOS from an IT solution` x40, `Android
        # Developer` x39, `PHP Developer` x38, `Internship for Fresher` x38. Ranked #5 in
        # `data/state/board_priority.csv`, credited 4,252 tech rows — 6.5% of every tech row the
        # ledger credits to smartrecruiters, from one of its 3,606 scored Boards.
        # Content read before parking, per EXCLUDED_BOARDS' rule above: these are real postings,
        # not a vendor sandbox — a real Indore IT-training shop advertising the same trainee intake
        # repeatedly — which is why this is a park and not an exclusion.
        # This one is parked on the content, not on a ratio, and that is deliberate: the same
        # prevalence sample found **no cheap ratio separates it at all**. It ranks 19th of 25 on
        # top-stem share (2.9%), and on its own two markers `successfactors:careers.hcltech.com`
        # looks worse — 2,975 postings across 4 locations with 399 copies of one exact title,
        # against Endeavor's 49 — while being a real employer doing genuine bulk hiring. Any gate
        # strong enough to catch this Board evicts HCLTech and Wipro first.
        # Un-park on the same condition as Reboot Monkey above. Note the ledger also carries six
        # sibling `EndeavorIt...` tenants (`EndeavorITSolution10` at 808 postings,
        # `EndeavorItSolution9` at 158, four at 0-10); they are separate Boards, left alone here
        # because only this one is large enough to have been measured.
        "smartrecruiters:endeavoritsolution",
        # Monzo's referral-only board, served as a company named "Referrals Only". Greenhouse's
        # own text for such a board: "If you want to accept referrals for a role, but you don't
        # want it to be live on the website you need to create a copy of the job post and
        # publish it to this job board only". Read 2026-09-30 (MCP critique round 5, R5-P2-7):
        # of its 13 served postings, 7 are the same title, place and pay as a served posting on
        # `greenhouse:monzo` (8188575 = 7194922, 7861424 = 6180814, 7861417 = 6369658,
        # 8035000 = 7115379, 8059751 = 6635595, 8059754 = 6635837, 8059757 = 6636147), 1 is
        # a near copy (5636930 "Data Scientist" against 8242603 "Data Scientist, L30"), and 5
        # are roles nobody can apply to without a referral. Un-park if Monzo starts posting
        # public roles only here.
        "greenhouse:monzoreferrals",
        # Jibe clients whose every posting is on a Board another ledger already holds (ADR-0189),
        # so each posting would serve twice under two ATS labels — the Phenom rule. Measured
        # 2026-09-24 by walking each client's whole listing and joining every `apply_url` host to
        # the ledgers: stjude 163 of 163 on `stjude.wd1.myworkdayjobs.com`, spglobal 292 of 292 on
        # `spgi.wd5`, fedexfreight 677 of 677 on `freight.wd108`, mercy 2,257 of 2,257 on
        # `mercy.wd1` (all live Workday rows), mountsinai 1,821 of 1,821 on `ejis.fa.us6` and
        # marriott 1 of 1 on `ejwl.fa.us2` (live Oracle rows). `aidt` was a candidate from its
        # page 1 and is not parked: 1 of its 25 postings is on Workday. Un-park a client if its
        # postings move off the held Board, or once cross-ATS dedup exists.
        "jibe:stjude",
        "jibe:spglobal",
        "jibe:fedexfreight",
        "jibe:mercy",
        "jibe:mountsinai",
        "jibe:marriott",
        # Jibe clients whose every posting is on an iCIMS Board we scrape (ADR-0240): `parse` drops
        # each one as iCIMS-covered, so the client reads 0 Jobs at 5 s a page, every run —
        # commonspirit 515 s, selectmedicalcorp and aus 205 s each. Measured 2026-09-26 by walking
        # each Scrapable client's whole listing (`scripts/validate/jibe_icims_covered_clients.py`):
        # 288 of 1,121 clients, 73,027 postings, every one on a Scrapable iCIMS Board. A client
        # with a single posting elsewhere is not here. Un-park a client whose postings move off
        # those Boards, or once cross-ATS dedup exists. Re-walked 2026-09-28 after the iCIMS
        # ledger refresh and ADR-0254's burials: curo (144 of 144) and peraton (1,559 of 1,559)
        # joined; goauto and uti left, because their iCIMS Boards (careersen-goauto,
        # careers-concorde, careers-uti) now serve `Disallow: /` and 403 their sitemaps, so
        # iCIMS reads none of their postings.
        "jibe:1supply",
        "jibe:actslife",
        "jibe:adastragrp",
        "jibe:advsmo",
        "jibe:airmethods",
        "jibe:alaskaair",
        "jibe:alcami",
        "jibe:alexlee",
        "jibe:alliancelaundry",
        "jibe:altapointe",
        "jibe:alumus",
        "jibe:americanaddictioncenters",
        "jibe:americanseniorbenefits",
        "jibe:americansystems",
        "jibe:apderm",
        "jibe:apogeeusa",
        "jibe:aptiveresources",
        "jibe:arh",
        "jibe:arrowtransportation",
        "jibe:ars",
        "jibe:ashp",
        "jibe:asm",
        "jibe:atipt",
        "jibe:atlassian",
        "jibe:aus",
        "jibe:bancorpbank",
        "jibe:bartonassociates",
        "jibe:bdsmktg",
        "jibe:bernhard",
        "jibe:betterhealthgroup",
        "jibe:bimart",
        "jibe:blackhawk",
        "jibe:blackhawknetwork",
        "jibe:bostonpizza",
        "jibe:brightspring",
        "jibe:brightviewhealth",
        "jibe:brinkmannconstructors",
        "jibe:brookdalecc",
        "jibe:cadence-education",
        "jibe:campero",
        "jibe:captrust",
        "jibe:carecentrix",
        "jibe:carollo",
        "jibe:casella",
        "jibe:cayuseholdings",
        "jibe:celanese",
        "jibe:celldex",
        "jibe:centralhealth",
        "jibe:cfsny",
        "jibe:chordsdp",
        "jibe:citynational",
        "jibe:commonspirit",
        "jibe:compsych",
        "jibe:concentrahealthservices",
        "jibe:conehealth",
        "jibe:connection",
        "jibe:connectiverx",
        "jibe:convergeone",
        "jibe:cooperhealth",
        "jibe:cooperparry",
        "jibe:cordobacorp",
        "jibe:covenanthealth",
        "jibe:crashchampions",
        "jibe:csuglobal",
        "jibe:curanahealth",
        "jibe:curo",
        "jibe:daddario",
        "jibe:dallascatholic",
        "jibe:damar",
        "jibe:danos",
        "jibe:dchsystem",
        "jibe:dentalcarealliance",
        "jibe:dminc",
        "jibe:dunbia",
        "jibe:eaglebankcorp",
        "jibe:ebscoind",
        "jibe:edf-re",
        "jibe:edmundoptics",
        "jibe:edsisolutions",
        "jibe:electronic-therapy",
        "jibe:emerus",
        "jibe:empiricalfoods",
        "jibe:empowerai",
        "jibe:epeconsulting",
        "jibe:equans",
        "jibe:ermcoeci",
        "jibe:eskenazihealth",
        "jibe:fastenterprises",
        "jibe:federatedinsurance",
        "jibe:feeltheadvantage",
        "jibe:fortiss",
        "jibe:four-paws",
        "jibe:fult",
        "jibe:generalrv",
        "jibe:getchampion",
        "jibe:gilbaneco",
        "jibe:globalcu",
        "jibe:goodshepherdhospice",
        "jibe:gr8affinity",
        "jibe:granicus",
        "jibe:greatdane",
        "jibe:greenstate",
        "jibe:guard",
        "jibe:harvard",
        "jibe:healthedge",
        "jibe:healthequity",
        "jibe:healthmanagement",
        "jibe:heartlandvetpartners",
        "jibe:here",
        "jibe:heywood",
        "jibe:hightideinc",
        "jibe:honuservices",
        "jibe:hracuity",
        "jibe:i3-corps",
        "jibe:ideagen",
        "jibe:idlo",
        "jibe:ieedu",
        "jibe:iehp",
        "jibe:imagefirst",
        "jibe:indevets",
        "jibe:integreon",
        "jibe:intellisiq",
        "jibe:intrepidsolutions",
        "jibe:ipipeline",
        "jibe:iqt",
        "jibe:johnsiskandson",
        "jibe:johnstonesupply",
        "jibe:joybakinggroup",
        "jibe:kgpco",
        "jibe:kidscarehh",
        "jibe:kilpatricktownsend",
        "jibe:kinaxis",
        "jibe:kingfisher2",
        "jibe:legacyscs",
        "jibe:leye",
        "jibe:linesight",
        "jibe:livech",
        "jibe:livelmh",
        "jibe:lmi",
        "jibe:lorienhealth",
        "jibe:lspower",
        "jibe:lumanity",
        "jibe:lvccld",
        "jibe:macny",
        "jibe:mambu",
        "jibe:markonsolutions",
        "jibe:massywoodltd",
        "jibe:mccarthyca",
        "jibe:medicalsolutions",
        "jibe:meridianbioscience",
        "jibe:mgocpa",
        "jibe:mhsil",
        "jibe:middlesexsavings",
        "jibe:millgroupinc",
        "jibe:mineralstech",
        "jibe:miniso-us",
        "jibe:mjhs",
        "jibe:msci",
        "jibe:mtfbiologics",
        "jibe:myacuity",
        "jibe:mynorthsidecareer",
        "jibe:mysagedental",
        "jibe:naphcare",
        "jibe:natdcp",
        "jibe:naturalgrocers",
        "jibe:navitus",
        "jibe:nelsonmullins",
        "jibe:netimpactstrategies",
        "jibe:nhbc",
        "jibe:noodles",
        "jibe:nortal",
        "jibe:northstarnm",
        "jibe:nscglobal",
        "jibe:nvisioncenters",
        "jibe:nybg",
        "jibe:nybloodcenter",
        "jibe:odysseyconsult",
        "jibe:ohsu",
        "jibe:omegatechserv",
        "jibe:oneadvanced",
        "jibe:oneblood",
        "jibe:opml",
        "jibe:pactworld",
        "jibe:pamhealth",
        "jibe:parkdental",
        "jibe:parkhill",
        "jibe:partsauthority",
        "jibe:peagroup",
        "jibe:pennentertainment",
        "jibe:pepperpointe",
        "jibe:peraton",
        "jibe:petsuppliesplus",
        "jibe:phypartners",
        "jibe:piedmont",
        "jibe:pinkerton",
        "jibe:pioneermetal",
        "jibe:pistongroup",
        "jibe:pittohio",
        "jibe:plslogistics",
        "jibe:postacute-affiliates",
        "jibe:premierdentistpartners",
        "jibe:preshomes",
        "jibe:proco",
        "jibe:psaairlines",
        "jibe:pyramidsystems",
        "jibe:rainbird",
        "jibe:rambus",
        "jibe:rbscorp",
        "jibe:realpagepms",
        "jibe:redlobster",
        "jibe:reliant-rehab",
        "jibe:rfacareers",
        "jibe:rhi-group",
        "jibe:ringpower",
        "jibe:riversideresearch",
        "jibe:rocketpharma",
        "jibe:rodeodentaltexas",
        "jibe:roomandboard",
        "jibe:ropesgray",
        "jibe:rovisys",
        "jibe:rsandh",
        "jibe:sagehospitality",
        "jibe:saintmarysuniversity",
        "jibe:scires",
        "jibe:scsengineers",
        "jibe:secure",
        "jibe:securityusainc",
        "jibe:selectmedicalcorp",
        "jibe:sesi-md",
        "jibe:sevenhills",
        "jibe:shaneco",
        "jibe:sigmaconnected",
        "jibe:simventions",
        "jibe:sixflags",
        "jibe:solairus",
        "jibe:somniainc",
        "jibe:sscgp",
        "jibe:ssoe",
        "jibe:stardental",
        "jibe:stifel",
        "jibe:stlukes-stl",
        "jibe:suffolkconstruction",
        "jibe:suncrestcare",
        "jibe:supportuw",
        "jibe:sutterbay",
        "jibe:sutterhealth",
        "jibe:symplr",
        "jibe:tabacalerausa",
        "jibe:thefreshmarket",
        "jibe:thesilverlining",
        "jibe:thezenith",
        "jibe:thinktogether",
        "jibe:tighebond",
        "jibe:tlcmgmt",
        "jibe:tmo",
        "jibe:togetherwomenshealth",
        "jibe:topco",
        "jibe:townandcountrymarkets",
        "jibe:toyota-europe",
        "jibe:transmed",
        "jibe:tutera",
        "jibe:uhnjcareers",
        "jibe:umms",
        "jibe:unisonglobal",
        "jibe:unitypoint",
        "jibe:usap",
        "jibe:valiantsolutions",
        "jibe:verathon",
        "jibe:versiti",
        "jibe:vetcor",
        "jibe:vinfen",
        "jibe:virginiahospitalcenter",
        "jibe:vistaglobal",
        "jibe:vistrygroup",
        "jibe:walterpmoore",
        "jibe:wcpss",
        "jibe:wel",
        "jibe:werfen",
        "jibe:westcoastuniversity",
        "jibe:westerndental",
        "jibe:wforce",
        "jibe:willkie",
        "jibe:wipfli",
        "jibe:wrhrealty",
        "jibe:wth",
        "jibe:yelp",
        "jibe:ymcasd",
        "jibe:yptc",
        # Radancy's employee-only career fronts (ADR-0246), each the internal twin of a public
        # front we scrape. Measured 2026-09-26 by city and title in the job URL: 76% of
        # `internal.commonspirit.careers`'s 4,585 postings also sit on `www.commonspirit.careers`,
        # 70% of `employees.kaiserpermanentejobs.org`'s on `www.kaiserpermanentejobs.org`, 82%
        # of `internalcareers.primark.com`'s on `careers.primark.com`, 88% of
        # `internal.santanderjobsus.com`'s on `www.santandercareers.com` and 40% of
        # `internal.jobs.chsinc.com`'s on `jobs.chsinc.com` — under different job ids, so nothing
        # would serve them once. The rest are postings an outside applicant cannot apply to (the
        # **non-public site** of CONTEXT.md's **Requisition**). Un-park once cross-front
        # deduplication exists and the index wants internal postings.
        "radancy:employees.kaiserpermanentejobs.org",
        "radancy:internal.commonspirit.careers",
        "radancy:internal.jobs.chsinc.com",
        "radancy:internal.santanderjobsus.com",
        "radancy:internalcareers.primark.com",
        # iCIMS internal portals whose job pages are employee-only (#810): each page answers "You
        # must log in to access this page". All 20 internal-labelled iCIMS portals were measured on
        # 2026-09-29. Three are walled: scsk12's (3 of 3 job pages sampled), and East Penn's
        # `internal9v` (1 of 1) and `internala5` (2 of 2). Three stay because their sampled pages
        # opened without a sign-in: beaumonthospital (0 of 3 walled), gnapartners (0 of 3) and
        # knowledgeservices (0 of 1). The other 14 listed no posting, so none could be sampled, and
        # they stay too.
        # scsk12's portal lists 556 postings, and 555 sit under the same job id on the district's
        # public portals: 252 on `instructional-scsk12` and 309 on `schoolsupportapply-scsk12`.
        # ADR-0254's `subset-reqs` election buries an internal portal only onto one public portal
        # that lists all of its postings. No single one does here, and one posting is
        # internal-only, so unparked, those 555 would be read twice and served twice wherever they
        # pass the tech filter. East Penn's three postings are on none of its 12 public portals,
        # and none is a tech role.
        # Un-park a portal if its job pages open to outside applicants.
        "icims:internal-instructional-scsk12.icims.com",
        "icims:internal9v-eastpennmanufacturing.icims.com",
        "icims:internala5-eastpennmanufacturing.icims.com",
        # A front whose robots.txt is `Disallow: /` (2026-09-28): nothing on it may be read.
        # Un-park if its robots.txt opens the sitemap.
        "radancy:www.intel-jobs.com",
        # Second hosts of Tata Communications' Spire2Grow workspace (ADR-0362): each resolves
        # to `TCLPROD-c62po`, the workspace `spire2grow:jobs.tatacommunications.com` reads
        # (2026-09-30, 210 postings on all four), so each would serve every posting again under
        # its own job ids. The company's own host is the one kept. Un-park none of them while
        # that host resolves.
        "spire2grow:i-exchange-row.web.app",
        "spire2grow:tcl-career.iexchange.ai",
        "spire2grow:tcl-career.spire2grow.com",
        # Phenom skins over a Board we already hold (CLAUDE.md's Phenom landing rule): every
        # posting's `applyUrl` sits on that Board, so each would be served twice under two ATS
        # labels. Measured 2026-09-28 by walking each skin's whole listing: jobs.sutterhealth.org
        # 1,232 of 1,232 on `wd1.myworkdaysite.com` (1,224 of its req ids are on the held
        # `workday:sutterhealth/sh`, 1,226 postings); jobs.corecivic.com 488 of 488 on
        # `corecivic.csod.com` (held `cornerstone:corecivic`, 495); careers.associaonline.com
        # 378 of 378 on `recruiting.adp.com` (held `adp_recruiting:associacareers`, 364);
        # careers.soprasteria.co.uk 84 of 84 on `soprasteria-uk.csod.com` (held
        # `cornerstone:soprasteria-uk`, 148). Un-park if the held Board goes dead, or once
        # cross-ATS deduplication exists.
        "phenom:jobs.sutterhealth.org",
        "phenom:jobs.corecivic.com",
        "phenom:careers.associaonline.com",
        "phenom:careers.soprasteria.co.uk",
        # A Taleo section a held Phenom front already serves: `vontier/4` lists 46 postings and 38
        # of those reqs are on the held `phenom:careers.vontier.com` (133 postings; Vontier's Taleo
        # `external` section redirects to that site), so each would be served twice under two ATS
        # labels. Read 2026-09-29. Its twin host `aa246` is already buried onto `vontier`. Un-park
        # if the Phenom front goes dead, or once cross-ATS deduplication exists.
        "taleo_enterprise:https://vontier.taleo.net/careersection/4",
        # Cornerstone tenants of real employers whose only postings are their own test copies, read
        # 2026-09-29 by the csod.com sweep: `evolus` (Newport Beach) lists six, all titled TEST or
        # TESTING, dated Aug-Sep 2026 ("Talent Specialist - TEST", "Systems Administrator - TEST 2"),
        # and `ncp` (National Car Parks Ltd.) lists one, "TEST ADMIN", dated 2022. Un-park either
        # once it lists a posting that is not a test.
        "cornerstone:evolus",
        "cornerstone:ncp",
        # Happydance career fronts whose Backing Board is a Scrapable Board (ADR-0264): each
        # would serve its postings a second time under the front's key. Measured 2026-09-28 by
        # the apply URLs of up to 25 sampled job pages each, by Greenhouse job id (Box, Dropbox,
        # Pinterest and Coupang: 100% of the front's reqs on the held Board) or, for fronts that
        # apply on the front itself, by employer and registrable domain (Fidelity, Warburtons,
        # Aristocrat); the per-front shares are in the ADR. Landing them or not is the owner's
        # open decision (Radancy lands every front and logs its duplication; Phenom lands none
        # of these). Un-park one to land it.
        "happydance:careers.aristocrat.com",
        "happydance:careers.box.com",
        "happydance:careers.caterpillar.com",
        "happydance:careers.coupa.com",
        "happydance:careers.criteo.com",
        "happydance:careers.draftkings.com",
        "happydance:careers.equifax.com",
        "happydance:careers.fluttergroup.com",
        "happydance:careers.flutteruki.com",
        "happydance:careers.hilti.group",
        "happydance:careers.ingrammicro.com",
        "happydance:careers.mgmresorts.com",
        "happydance:careers.mimecast.com",
        "happydance:careers.prismahealth.org",
        "happydance:careers.regeneron.com",
        "happydance:careers.richemont.com",
        "happydance:careers.rjet.com",
        "happydance:careers.thrivent.com",
        "happydance:careers.warburtons.co.uk",
        "happydance:jobs.centene.com",
        "happydance:jobs.fidelity.com",
        "happydance:jobs.nationalgrid.com",
        "happydance:mycareer.verizon.com",
        "happydance:retailcareers.paddypower.com",
        "happydance:www.bairdcareers.com",
        "happydance:www.careers.astemo.com",
        "happydance:www.careers.jnj.com",
        "happydance:www.coupang.jobs",
        "happydance:www.dropbox.jobs",
        "happydance:www.grab.careers",
        "happydance:www.pinterestcareers.com",
        # A content site using WP Job Openings for its articles (ADR-0266): 8,008 "postings",
        # and of the newest 100 read 2026-09-28, 8 were job-ad-shaped rewrites of other
        # employers' ads ("LPN Jobs in Lloydminster 2026 – Apply Online") and the rest articles
        # ("NY SPORTS SCHEDULE TODAY", "ALL NEW YORK SPORTS TEAMS"). Un-park if it ever lists
        # openings of its own.
        "wp_job_openings:ndangira.net",
        # Cipla's TurboHire pages mirror its SuccessFactors Board, which the successfactors
        # ledger holds (`careers.cipla.com`, 80 postings): read 2026-09-30, 17 of `cipla`'s 18
        # distinct titles (35 postings) and 2 of `ciplasouthafrica`'s 3 are on that Board, so
        # their postings would be served twice under two ATS labels. Un-park if the held Board
        # goes dead, or once cross-ATS deduplication exists.
        "turbohire:cipla",
        "turbohire:ciplasouthafrica",
        # Carrier's gr8people feed retains 4,167 postings while its held Workday Board
        # lists 1,054. All 41 gr8people requisitions dated Sep 26–Oct 2 are held there;
        # all 36 recent gr8people-only redirects failed at Workday (S22), with three
        # live positive controls and one browser-confirmed missing page. Parked by
        # the owner on 2026-10-02; revisit after a stale-feed audit (ADR-0373).
        "gr8people:carriernoam.workgr8.com",
    }
)

# Workday `alias_key()`: three measurements, full transcripts (2026-09-11)

Evidence for ADR-0111's 2026-09-11 amendment. Committed for the same reason SuccessFactors'
23-row alias CSV was: a reproducible artifact, not just a number in prose. Nothing here is
sampled for this document — each run below is the complete output of the real tool (or the real
script) that produced it, transcribed in full.

## 1. Full-population redirect scan — the real tool, all 12,844 live Boards

`python scripts/validate/dedupe_boards.py --ats workday` (dry run), against the committed
`data/validate/liveness/workday.csv`:

```
workday: probing 12844 of 12844 live Board(s)

1 duplicate cluster(s), 1 Board(s) to bury
  keep https://gatesfoundation.wd1.myworkdayjobs.com/Gates
       bury https://gatesfoundation.wd1.myworkdayjobs.com/Gates?source=gatesfoundation.org

summary: {'migrated': 2, 'tombstone': 38} | duplicates 1
```

Not written to `data/validate/aliases/workday.csv` — see ADR-0111's amendment for why (the sole
survivor of `config._dedupe_boards`' own pre-existing fold for this exact Board is also the row
the alias scan names as the duplicate, and recording it broke
`tests/test_board_counts.py`'s cross-check invariant).

**The 2 migrated** (resolved to a real host, not a live Board — reported only, per ADR-0111):

```
https://aussiebroadband.wd3.myworkdayjobs.com/Symbio-Internals -> https://aussiebroadband4.wd3.myworkdayjobs-impl.com/External
https://dssmith.wd3.myworkdayjobs.com/ip_dss -> https://www.dssmith.com/people/careers
```

**The 38 tombstones** (Workday's own outage page, `alias_vendor_hosts`), full list:

```
https://adobe.wd5.myworkdayjobs.com/external_university
https://amplify.wd1.myworkdayjobs.com/amplify_careers
https://ascentsolutions.wd1.myworkdayjobs.com/External
https://ascentsolutions.wd1.myworkdayjobs.com/external
https://ashealthnet.wd1.myworkdayjobs.com/MendHealth
https://ashealthnet.wd1.myworkdayjobs.com/theohiostateuniversitywexnermedicalcenterhomecare
https://boarshead.wd1.myworkdayjobs.com/BHC
https://boarshead.wd1.myworkdayjobs.com/bhc
https://brunswick.wd1.myworkdayjobs.com/searchemea
https://byuh.wd501.myworkdayjobs.com/byu_h_s
https://centrify.wd1.myworkdayjobs.com/Internship
https://centrify.wd1.myworkdayjobs.com/external
https://finastra.wd3.myworkdayjobs.com/Teciem
https://franklintempleton.wd5.myworkdayjobs.com/Jobs-WAM
https://franklintempleton.wd5.myworkdayjobs.com/Jobs_OSAM
https://franklintempleton.wd5.myworkdayjobs.com/jobs-clearbridge
https://franklintempleton.wd5.myworkdayjobs.com/jobs-wam
https://globalblue.wd3.myworkdayjobs.com/external
https://gtsgbu.wd3.myworkdayjobs.com/Careers
https://gtsgbu.wd3.myworkdayjobs.com/careers
https://hhs.wd12.myworkdayjobs.com/HHSNextGeneration
https://hhs.wd12.myworkdayjobs.com/HHSNextMission
https://johnsonbrothers.wd5.myworkdayjobs.com/eQuest
https://juliusbaer.wd3.myworkdayjobs.com/JB_Be_Baer_Women_Rise_and_Connect
https://klook.wd3.myworkdayjobs.com/KlookCareers
https://klook.wd3.myworkdayjobs.com/klookcareers
https://koppers.wd5.myworkdayjobs.com/RailroadCareers
https://landolakes.wd1.myworkdayjobs.com/members
https://nuskin.wd5.myworkdayjobs.com/BeautyBio
https://oxy.wd5.myworkdayjobs.com/OxyChem
https://performant.wd1.myworkdayjobs.com/performant
https://petretailbrands.wd5.myworkdayjobs.com/external_career_site_pet_supermarket_inc
https://qlar.wd3.myworkdayjobs.com/Qlar_Careers
https://qlar.wd3.myworkdayjobs.com/qlar_careers
https://ryan.wd1.myworkdayjobs.com/TaxJobs
https://shawinc.wd1.myworkdayjobs.com/International
https://telusinternational.wd3.myworkdayjobs.com/External
https://unilever.wd3.myworkdayjobs.com/TMICC
```

The remaining 12,803 of 12,844 resolved to themselves.

## 2. Same-company content-overlap — the deferred signal, 217 pairs

Before the full-population scan existed, a companion pass tested the *other* signal ADR-0111
anticipates — id-set overlap between independently-served Boards, the shape Eightfold needs.
Companies running `>=2` distinct Workday site slugs (after casefold), each holding `>=5` jobs in
the ledger: 972 candidate companies, 217 pairs sampled (seed 11), one page (`limit=20`) of
`{title, externalPath}` fetched from each site via the real CXS listing endpoint and compared.

```
companies with >=2 distinct sites each holding >=5 jobs: 972

pairs compared: 217
pairs with ANY shared posting (title+externalPath): 0

top pairs by shared postings:
   hendrick             hendrickcareers      vs hmscareers            shared=0/20,20  ledger_jobs=(532,40)
   trumpf               trumpf_graduates_and vs trumpf_apprenticeshi  shared=0/20,20  ledger_jobs=(222,59)
   trumpf               trumpf_graduates_and vs trumpf_students       shared=0/20,20  ledger_jobs=(222,148)
   trumpf               trumpf_apprenticeshi vs trumpf_students       shared=0/20,20  ledger_jobs=(59,148)
   kmkp                 kacecareers          vs careers               shared=0/20,14  ledger_jobs=(30,11)
   kmkp                 kacecareers          vs tpgcareers            shared=0/20,8  ledger_jobs=(30,10)
   kmkp                 careers              vs tpgcareers            shared=0/14,8  ledger_jobs=(11,10)
   topcon               topconhealthcarecare vs topconpositioningcar  shared=0/10,20  ledger_jobs=(18,48)
   wellstar             wellstarcareers      vs wellstarprovidercare  shared=0/20,20  ledger_jobs=(822,254)
   sagility             sagility             vs sagility_careers_ind  shared=0/15,20  ledger_jobs=(16,122)
   sagility             sagility             vs equest                shared=0/15,20  ledger_jobs=(16,42)
   sagility             sagility             vs er_portal_ind         shared=0/15,12  ledger_jobs=(16,11)
   sagility             sagility             vs php_bulk_hiring_job_  shared=0/15,13  ledger_jobs=(16,12)
   sagility             sagility             vs sagility_careers_php  shared=0/15,20  ledger_jobs=(16,37)
   sagility             sagility             vs sagilityusa           shared=0/15,20  ledger_jobs=(16,40)
   sagility             sagility_careers_ind vs equest                shared=0/20,20  ledger_jobs=(122,42)
   sagility             sagility_careers_ind vs er_portal_ind         shared=0/20,12  ledger_jobs=(122,11)
   sagility             sagility_careers_ind vs php_bulk_hiring_job_  shared=0/20,13  ledger_jobs=(122,12)
   sagility             sagility_careers_ind vs sagility_careers_php  shared=0/20,20  ledger_jobs=(122,37)
   sagility             sagility_careers_ind vs sagilityusa           shared=0/20,20  ledger_jobs=(122,40)
   sagility             equest               vs er_portal_ind         shared=0/20,12  ledger_jobs=(42,11)
   sagility             equest               vs php_bulk_hiring_job_  shared=0/20,13  ledger_jobs=(42,12)
   sagility             equest               vs sagility_careers_php  shared=0/20,20  ledger_jobs=(42,37)
   sagility             equest               vs sagilityusa           shared=0/20,20  ledger_jobs=(42,40)
   sagility             er_portal_ind        vs php_bulk_hiring_job_  shared=0/12,13  ledger_jobs=(11,12)
```

25 of the 217 pairs shown (the rest carried the same `shared=0`); none of any 217 shared a posting.

## 3. Same-company, two data centres — 50 instance-split pairs

Tests the OTHER known Workday duplicate shape (`config.py`'s "Accenture sits on both `wd3` and
`wd103`") — already handled by `board_key`'s instance-blind fold (ADR-0023); this checks whether
that fold is ever unsafe, i.e. whether both data centres are ever simultaneously live for the same
Board. Same company + same site, different `wdN` instance, 50 pairs sampled (seed 3), one `POST`
each against the live CXS endpoint (`limit=1`, to read only whether it answers):

```
sampled 50 same-company+site instance-split pairs
  both instances answer  : 0
  exactly one answers    : 49
  neither answers        : 1
```

## 4. Public-page redirect — first pass, 400 boards, superseded by §1

The pilot that motivated building the full scan. Kept for the record; §1 supersedes it as the
population-wide answer.

```
live workday boards: 17163

sampled 400
  resolves to itself         : 384
  redirects, same company    : 0
  redirects, different company: 0
  errors: {'http-500': 14}

examples of redirects:
   [non-workday-target] https://klook.wd3.myworkdayjobs.com/klookcareers -> https://community.workday.com/maintenance-page?d=3&s=1&e=1&o=
   [non-workday-target] https://centrify.wd1.myworkdayjobs.com/Internship -> https://community.workday.com/maintenance-page?d=1&s=1&e=1&o=
```

The 400-board pilot's tombstone rate (2/400, 0.5%) and the full scan's (38/12,844, 0.3%) are the
same phenomenon at two sample sizes, not two different findings — flagged here since neither the
ADR nor the code comment states that explicitly.

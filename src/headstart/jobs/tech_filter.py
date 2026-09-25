"""Classify a Job as a software/tech role, and filter a jobs dir down to the tech subset (ADR-0017).

Every job is scraped, but only software/tech roles get embedded, indexed, and shown — that is where
the expensive work (the embedding model, the vector index) lives. This gate decides which jobs pass.

It is deliberately **recall-biased**: a non-tech job creeping through is acceptable, but dropping a
real tech job is not. Classification is on the ``title`` (+ ``department``) via regex — cheap enough
for millions of jobs. (An LLM is far too costly per job; it is the *verification* layer instead —
see ``scripts/filter/verify_tech.py``, the reasoning gate that samples the dropped pile.)

Precedence (first match wins):

  0. a phrase that trips a strong signal but
     names another trade ("CNC Programmer",
     "Front End Manager") is set aside before
     rules 1-3; what is left is judged on its
     own, and a remainder naming software
     work ("… - MES") is tech               -> tech, or on to rules 1-5
  1. a strong, unambiguous software signal  -> tech      (overrides any disqualifier)
  2. a generic role token (engineer/developer/…) *with* a non-software qualifier (mechanical,
     sales, civil, …) in the title, or in a
     department that names a discipline, or a
     construction trade (site, MEP, QA/QC,
     highway) with no IT/network/software word
     in the title or department              -> not tech
  3. a generic role token alone              -> tech      (recall: keep the ambiguous ones)
  4. a clearly-technical department, unless
     it names a hiring function, or means
     something other than software, or the
     title names a different profession       -> tech      (recall booster for vague titles)
  5. otherwise                               -> not tech

**Rules 1-3 read the title; rule 4 reads the department.** Two narrow exceptions: rule 2's
discipline veto also reads a department that names a discipline, and its trade veto stands down
when the department names infrastructure work (`DC Ops`, `IT`). Until 2026-09-17 rules 1 and 2 ran
over ``title + department``, so a department could settle a title question — "Software development"
contains "software dev", so a Content Creator in it scored a strong software signal. The department
has one rule, and that is where its guards live.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

# Bumped whenever a pattern change below moves the tech/not-tech line for input that's already
# been scraped and filtered — the same discipline as `doc_prep.DERIVATIONS_VERSION`, and for the
# same reason: this gate's output feeds `role_trends`, whose per-tick counts silently absorb a
# widened or narrowed regex as if the market moved. Reading this value once per tick lets a
# reader tell "we changed who counts" from "conditions changed" instead of conflating the two.
# 2 (2026-09-17, `git log 277b5e2a..1fd0f843 -- src/headstart/jobs/tech_filter.py`): rules 1 and 2
# stopped reading the department, rule 4 stopped promoting a title
# that names a different profession, and the strong list gained the roles the department had been
# covering for. Net **+0.45%** on a 489,661-posting sample — 4 of run 35193130454's 15 scrape
# fragments — at 107,684 -> 108,165, +5,868 in and -5,387 out, so the composition moves much
# further than the total. See
# docs/tech-filter/2026-09-17_the-title-decides.md. This is exactly the shape the counter exists
# for: `role_trends` would otherwise read "Security Officer" leaving the index as the market
# shedding security jobs.
# 3 (2026-09-21, `git log 96c0c702..c2c026ed -- src/headstart/jobs/tech_filter.py`): the strong
# list gained the role families still outside it after version 2 moved rules 1-2 onto the title —
# Member of Technical Staff, Forward Deployed Engineer, AI/ML research and applied scientists,
# business intelligence, silicon design, security operations, the QA role words, bioinformatics —
# and the enterprise-platform arm stopped requiring the product and the role word to be adjacent.
# Net **+1.62%** on the 332,383-posting pre-filter snapshot, 67,506 -> 68,600: **+1,094 in, 0 out**,
# so unlike version 2 this one is purely additive and the composition moves exactly as far as the
# total. See docs/tech-filter/2026-09-21_the-families-still-outside-the-gate.md.
# 4 (2026-09-23, `git log da0565b7..f6811db8 -- src/headstart/jobs/tech_filter.py`): English spellings
# and families the gate could not see (`_` as a separator, glued levels, plurals, Engr/Engg/Dev,
# DevSecOps, IT/cyber/IAM/SOC/support/stack-word families, most found in the Indeed harvest's
# rejects), and the "…engineer" trades it admitted in bulk (site, MEP, QA/QC, highway and its
# siblings, business developer, the retail front end, CNC, discipline engineering managers),
# each veto standing down where the title or department names IT or software work; physical-
# security titles under a bare "Security" department. Two-sided: on the served table (v654,
# 514,163 rows) **-17,294 out, +1 in**, every lost title read by hand; on the 332,383-posting
# pre-filter snapshot 68,600 -> 69,192, **+1,852 in, -1,260 out**. A blind 800-title hold-out
# puts recall at ~84.6% and precision at ~82.0%. See
# docs/tech-filter/2026-09-23_spellings-and-trades.md.
# 5 (2026-09-24, `git log 6458fbac..6e05d8cc -- src/headstart/jobs/tech_filter.py`): rule 0 sets aside
# a cashier on either side of "front end" ("Cashier (Front End)", "FRONT END/CASHIER"), which the
# strong `front[\s-]?end` signal had been reading as a front-end developer. Purely subtractive: on
# the served table (v654, 514,163 rows) **-148 out, 0 in**, every one a cashier title read by hand;
# on the 1,135,079-posting pre-filter corpus (July scrape + Indeed harvest) **-3 out, 0 in**. Jibe
# was not yet in v654: `jibe:costco` alone carried 1,581 such rows (live keyword sample 2026-09-24:
# 1,213 of 2,500 hits were its only two kept titles). The blind hold-out is unchanged (recall
# 84.6%, precision 82.0%). See docs/pipeline/2026-09-24_five-run-log-review.md finding 1.
TECH_FILTER_VERSION = 5

# 1. Strong, software-specific signals. A match here means tech regardless of any disqualifier.
_STRONG_TERMS = [
    r"software (engineer|developer|dev|architect)",
    r"\b(swe|sde|sdet)\b",
    r"full[\s-]?stack",
    r"back[\s-]?end",
    r"front[\s-]?end",
    r"\bfullstack\b",
    r"web (developer|engineer)",
    r"mobile (developer|engineer)",
    r"(ios|android) (developer|engineer)",
    r"machine learning",
    r"deep learning",
    r"generative ai",
    r"\bllm\b",
    r"large language model",
    r"computer vision",
    r"\bnlp\b",
    r"\b(ai|ml)[\s/&,-]*(engineer|scientist|researcher|developer|ops|platform)",
    r"data (engineer|scientist)",
    r"data science",
    r"\bmlops\b",
    r"\bdevops\b",
    r"dev ops",
    r"\bsre\b",
    r"site reliability",
    r"platform engineer",
    r"infrastructure engineer",
    r"(multi|hybrid)?[\s-]?cloud (engineer|architect|developer)",
    r"(security|appsec) engineer",
    r"application security",
    r"([lfs]?qa|test) (engineer|automation)",
    r"\bsdet\b",
    r"automation engineer",
    r"embedded (software|engineer|developer|systems)",
    r"\bfirmware\b",
    (
        r"(software|(sub|eco)?systems?|solutions?|technical|technology|cloud|data|security|c?iam"
        r"|platform"
        r"|enterprise|integration|application|infrastructure|network|devops|ai|ml|api"
        r"|java|\.net|dotnet|python|salesforce|servicenow|sap|azure|aws|oracle|mobile|frontend"
        r"|backend|full[\s-]?stack) architect"
    ),
    r"\bprogrammer\b",
    r"\bblockchain\b",
    r"smart contract",
    r"\bweb3\b",
    r"game (developer|engineer|programmer)",
    r"api (developer|engineer)",
    # `systems?`, not `systems`: "System Administrator" and "System Engineer" are the commoner
    # singular spellings and were falling through — measured on a jobvite board serving
    # "IT System Administrator (TS/SCI with Polygraph)", a cleared sysadmin role, as non-tech.
    r"((sub)?systems?|network|database|devops|cloud|linux) (administrator|admin|engineer)",
    r"engineering manager",
    r"(director|s?vp|vice president|head) of (engineering|ai|ml|data|software|platform|infrastructure|technology|security)",
    r"\bcto\b",
    r"\bchief (technology|information|technical|digital|information security) officer\b",
    r"\bciso\b",
    r"tech(nical)? lead",
    r"(ai|ml|data|software|cloud|security|cyber\w*|systems|chief|principal|staff) technologist",
    r"developer (advocate|relations)",
    r"\bdevrel\b",
    r"(react|angular|vue|node|python|java|golang|rust|kubernetes) (developer|engineer)",
    # Roles whose title names the discipline without using "engineer"/"developer" — these were
    # reaching the index only because their *department* said "Technology", which is what makes
    # them invisible to a pre-detail gate (ADR-0166).
    r"(penetration|software|automation|[lfs]?qa|(video)?game|performance) tester",
    r"\bpentest(er)?\b",
    r"scrum master",
    r"(systems?|business systems|technical|data|security|soc|cyber|network|application) analyst",
    (
        r"\b(it|ict)[\s/-]+(manager|director|support|specialist|analyst|technician"
        r"|administrator|lead|engineer|operations|officer|consultant|coordinator)\b"
    ),
    r"(information|business) systems",
    r"\binformation security\b",
    r"\binfosec\b",
    r"help[\s-]?desk",
    r"desktop support",
    (
        r"(technology|technical) (support|operations|lead|specialist|writer|consultant"
        r"|program manager|project manager|product manager)"
    ),
    r"(power ?bi|tableau|looker|qlik) (developer|analyst|specialist|consultant)",
    # A bounded gap, not a single space: the platform's own module name almost always sits
    # between the product and the role — "SAP FICO Consultant", "SAP Basis Migration Consultant",
    # "Salesforce Pre-Sales Architect", "ServiceNow Business Analyst". Requiring adjacency left
    # 589 such rows outside the gate. The second arm is the reverse order ("Business Analyst -
    # ServiceNow"), which the single-arm form could not express at all. `s?\b` because the
    # role word is routinely plural ("ServiceNow Developers", "Workday Consultants"): a bare
    # closing `\b` drops both, and the adjacent form this replaces had no closing boundary,
    # so tightening it without the `s?` would have been a recall regression.
    # `peoplesoft`/`zendesk`/`aem` and the `admin` spelling: version 4, from the labelled set
    # ("NetSuite Admin", "Consultant Zendesk", "Sr. PeopleSoft 9.2 Functional Lead", "AEM Lead").
    (
        r"\b(salesforce|servicenow|sharepoint|sap|abap|apex|workday|netsuite|oracle"
        r"|dynamics|peoplesoft|zendesk|aem)\b"
        r".{0,24}\b(developer|administrator|admin|consultant|analyst|specialist|architect"
        r"|lead)s?\b"
    ),
    (
        r"\b(developer|administrator|admin|consultant|analyst|specialist|architect|lead)s?\b"
        r".{0,24}\b(salesforce|servicenow|sharepoint|sap|abap|apex|workday|netsuite|oracle"
        r"|dynamics|peoplesoft|zendesk|aem)\b"
    ),
    r"\b(etl|rpa|middleware|integration) (developer|specialist|consultant|lead)\b",
    r"database (administrator|analyst|specialist|developer)",
    r"\bdba\b",
    # The standard IC title at AI labs and at Bell-lineage firms, and it names no discipline of
    # its own, so nothing in this list covered it. Since rules 1-2 read the title only (ADR-0166)
    # it had no department left to rescue it either: 124 rows, on boards whose departments read
    # Modeling, Research, Inference, Technical Staff.
    # Also the spellings without "of" and the initialism ("Senior Member Technical Staff",
    # "SMTS"), qualified so a bare "MTS" (a company, a test-systems vendor) does not count.
    r"member,?\s+(of\s+)?(the\s+)?technical\s+staff",
    r"\b(s|sr|senior|principal|lead)\s?mts\b|\b[sp]mts\b",
    # Qualified so "Forward Deployed Creative" stays out. It has to be a strong signal rather
    # than a generic one because these sit under a Sales/GTM department, which rule 2 vetoes on.
    r"forward[- ]deployed (software |ai )?engineer",
    # The `\b(ai|ml)[\s/&,-]*(…|scientist|researcher)` arm above needs the role word adjacent to
    # `ai`/`ml`, so "AI Research Scientist" — one word in between — matched nothing at all.
    r"\b(ai|ml|artificial intelligence) research (scientist|engineer|lead|manager)",
    r"applied scientist",
    r"analytics engineer",
    r"business intelligence (analyst|developer|engineer|consultant|specialist)",
    r"\bbi (developer|analyst|engineer)s?\b",
    # Computational biology is applied computing — the code is the job — which is why it ships
    # while unqualified "Research Scientist" does not: that one is a bench role at a biotech.
    r"bioinformatic",
    r"computational biolog",
    # Silicon design is HDL/EDA work — software by any reading, and the premise ADR-0068 already
    # applied to `hardware` as an org label. `rtl` is qualified because the bare acronym matches
    # the broadcaster: of the 6 dropped titles `\brtl\b` matches, 3 are media roles ("Data /
    # Distributie Redacteur RTL Nieuws" and two "Media Consultant (Mensch) RTL / Veltins").
    r"\b(vlsi|fpga|asic)\b",
    r"rtl (design|verification)",
    r"design verification",
    r"physical design",
    # `security operations`/`soc` beyond the `… analyst` arm above — "SOC Specialist II",
    # "SOC Manager", "Security Operations Lead". Deliberately not bare `security specialist`,
    # which matches "EHS and Security Specialist", i.e. physical security.
    r"security operations",
    r"\bsoc (specialist|manager|engineer|architect|lead)",
    # `qa`/`test` role words the `… tester` and `… analyst` arms above do not carry. Both this
    # and `security operations` admit some creep the narrowed patterns above refuse — 4 pharma/AML
    # rows of the 65 this promotes, 1 ambiguous "Security Operations Officer" of 24. That is the
    # recall-bias trade this gate is built on, and a different ratio from the 3-of-6 that made
    # bare `\brtl\b` not worth keeping.
    r"\bqa (analyst|lead|manager)s?\b",
    r"test analyst",
    r"manual tester",
    # --- version 4 ------------------------------------------------------------------------
    # `\bdevops\b` cannot match inside "devsecops", so "DevSecOps Specialist" had no signal.
    r"\bdevsecops\b",
    # Abbreviated role words. A bare `eng`/`dev` is never enough — "ENG/SPA" is a language pair
    # and "Business Dev" is sales — so each is tied to a word that names the discipline:
    # "Software Engr II", "SW Eng", "iOS Dev", "Java Lead", "Dev Lead".
    (
        r"\b(software|sw|systems?|data|ml|ai|qa|test|devops|cloud|platform|firmware|backend"
        r"|frontend|app) (engr|engg|eng)s?\b"
    ),
    r"\bsw (engineer|developer|dev|design|development|test\w*|architect\w*|lead)s?\b",
    # Spellings the per-word-start matching no longer reaches mid-word (v3 matched "ml
    # architect" inside "AIML Architect", "ai architect" inside "CCAI Architect").
    r"\b(aiml|ccai|genai|conversational ai) (architect|engineer|developer|consultant)s?\b",
    r"\bmeta ?data (engineer|scientist|architect|analyst)s?\b",
    r"\b(mar|ed|fin|ad|reg|legal|insur)tech (lead|manager|engineer|developer|specialist)s?\b",
    r"\bcyber\w* (architecture|engineering|operations|governance)\b",
    r"\b(pc|desktop|computer|it) technicians?\b",
    r"\bplm (administrator|admin|developer|architect|consultant|engineer)s?\b",
    r"\bmask design",
    # From the critique's riskier groups, only the forms whose samples read as tech: AI/ML
    # *directly* before the role ("AI Lead", "Artificial Intelligence Team Lead", "AI Principal
    # Architect") — not "AI Strategy/Transformation/GTM …", which are consulting and sales —
    # and the IT spellings of development/delivery leads. Bare "QA" is not added: its sample
    # was mostly food, pharma, lab and call-centre QA.
    (
        r"\b(ai|ml|ai/ml|gen ?ai|artificial intelligence|machine learning)\s+(team\s+)?"
        r"(principal\s+|senior\s+|chief\s+)?(lead|architect|intern|internship)s?\b"
        r"(?! generation)"
    ),
    r"\bapp(lication)?s? development\b",
    r"\btechnical (delivery (manager|lead)|team lead)s?\b",
    r"\bsoftware (expert|team lead|delivery manager|development (manager|lead))s?\b",
    r"\bmaster ?data (analyst|specialist|engineer|lead|manager|management)s?\b",
    r"\bsr\.net (devs?|lead|developer|engineer)s?\b",
    r"\b(analyst|specialist|engineer|administrator|support)\W+(systems\W+)?it\s*$",
    (
        r"\b(ios|android|java|python|php|react|node|dotnet|backend|frontend"
        r"|full[\s-]?stack|golang|kotlin|scala|ruby|c\+\+|c#) (devs?|lead)\b"
    ),
    # `web`/`mobile`/`game` name a setting as often as a discipline, so only `dev` follows them:
    # "Web Lead" and "Game Lead" are not strong signals.
    r"\b(web|mobile|game|unity) devs?\b",
    r"(?<!business )(?<!biz )\bdev (lead|manager|team lead)\b",
    # "Lead Dev", "Senior Dev" — but not a fundraising "Senior Dev Officer" or "Dev Director".
    (
        r"\b(lead|senior|sr|junior|jr|principal|staff) devs?\b(?! (officer|director|associate"
        r"|coordinator|rep|representative))"
    ),
    r"(software|systems?|application) development engineer",
    # IT roles with a word between `IT` and the role ("IT Project Manager"), which the
    # adjacent-only `\b(it|ict)[\s/-]+(manager|…)` arm above cannot express.
    (
        r"\bit (project|program|service|infrastructure|systems?|security|operations|support|asset|site"
        r"|field"
        r"|delivery|change|release|business|risk|audit|compliance|governance) (manager|lead"
        r"|coordinator|analyst|specialist|engineer|consultant|partner|director|officer|owner"
        r"|administrator|auditor|leader)s?\b"
    ),
    # IT support under its ITIL name — the same role `help[\s-]?desk` already keeps.
    r"\bservice[\s-]?desk\b",
    # Security roles named for the domain rather than an engineer/analyst title, qualified by
    # that domain word — bare "Security Consultant" is also a physical-security trade. `cyber`
    # needs a practitioner's role word after it: on its own it also names the *market* a sales,
    # marketing or support role serves ("Business Development Representative - Cybersecurity",
    # "Marketing Specialist - Cybersecurity", "Cybersecurity Customer Experts"). The reverse
    # order is admitted only for the unambiguous role words.
    # The gap may not cross a sales or insurance word: "Cyber Sales Manager", "Cybersecurity
    # Sales Director" and "Cyber Claims Specialist" sell or insure it (critique of v4, 33 rows).
    (
        r"\bcyber\w*(?:(?!(?<!pre-)(?<!pre )sales|claims|underwrit|insurance|broker|marketing|account exec"
        r"|business develop|recruit).){0,30}\b(engineer|analyst|architect|consultant|responder"
        r"|tester|researcher|hunter|auditor|specialist|manager|lead|advisor|officer|operator"
        r"|director)s?\b"
    ),
    # The reverse order gets the forward arm's sales/insurance exclusion too ("Sales Engineer –
    # Cybersecurity", "Consultant – Cyber Insurance"; review of #573).
    (
        r"^(?!.*(?<!pre-)(?<!pre )\b(sales|claims|underwrit|insurance|broker|marketing)).*?"
        r"\b(analyst|engineer|architect|consultant)s?\b.{0,20}\bcyber"
    ),
    # "Assoc Analyst, Ai" (Canon; evaluates AI/ML models) — v3 kept it only through a
    # substring hit that word-start matching retired.
    r"\b(analyst|scientist)s?,\s*(ai|ml|ai/ml)\s*$",
    (
        r"\b(information|network|cloud|application|data|it|ot|product) security (consultant|specialist"
        r"|manager|architect|lead|advisor|officer|expert)s?\b"
    ),
    r"\bred team\b",
    (
        r"\b(identity (and|&) access|iam|pam) (engineer|consultant|specialist|analyst|architect"
        r"|manager|lead)"
    ),
    r"\bquant(itative)? (developer|researcher|engineer|dev)s?\b",
    r"computer scientist",
    r"\bkernel (engineer|developer|hacker)",
    r"\btest (lead|manager|architect)s?\b",
    (
        r"\b(erp|uat|etl|sap|oracle|api|web|mobile|functional|selenium|automation|regression"
        r"|integration)\b.{0,6}\btesters?\b"
    ),
    # "Research Scientist, AI" — the reverse order of the `(ai|ml) … scientist` arm above.
    # Not `engineer`: a strong arm overrides every veto, and "Sales Engineer – AI" or "Civil
    # Engineer – ML" are the vetoed trades. A bare "Engineer – AI" still passes as generic.
    r"\b(scientist|researcher)s?\b.{0,20}\b(ai|ml|machine learning|nlp|llms?|genai)\b",
    r"reinforcement learning",
    # Not `specialist`/`consultant`: "Legal AI Specialist", "Equity Research AI Specialist" are
    # the crowdwork labelling roles ADR-0087 keeps out, under an "AI Training" department.
    r"\b(ai|ml|ai/ml|genai|gen ai|agentic ai)[\s/&,-]*(architect|developer)s?\b",
    # `application` takes only the admin words: "Field Applications Engineer" is hardware presales.
    r"\bapplications? (administrator|admin)s?\b",
    (
        r"\b(server|windows|unix|m365|middleware|storage|backup|vmware|citrix|active directory"
        r"|exchange|sharepoint) (administrator|admin|engineer)s?\b"
    ),
    r"\b(system|systems|application) and (application|network|database) administrator",
    # Manufacturing Execution Systems and process/data mining are software; the `manufacturing`
    # and `mining` vetoes used to drop them.
    r"\bmes (engineer|developer|analyst|consultant|specialist|administrator|lead|architect)s?\b",
    r"manufacturing execution system",
    r"\b(data|process|text) mining\b",
    # JD Edwards is an ERP: every JDE title is enterprise-platform work, "CNC" ones included.
    r"\b(jd ?edwards?|jde)\b",
    # "Quality engineer" is generic since v4 (it kept 5,565 manufacturing/supplier QE rows as a
    # strong signal); beside software, data or test work it is the QA role and stays strong.
    r"\b(software|data|analytics|systems?|test|qa)\b.{0,20}\bquality engineer",
    r"\bquality engineer\b.{0,20}\b(software|data|analytics)\b",
    # --- from the Indeed harvest's English rejects (critique of v4) ------------------------
    # IT spelled out, in reverse order, or before `&`/an early-career word.
    r"\binformation technology\b",
    r"\b(head|director|vp|vice president|chief)\W+(of\W+)?(it|information technology)\b",
    r"\bit (intern|internship|executive|head|trainee|associate|infrastructure|and)\b|\bit &",
    # Support tiers for software, not machines.
    r"\b(applications?|app|production|prod)[\s-]+support\b",
    (
        r"\b(l[1-3]|level ?[1-3]|tier ?[1-3])[\s-]+(application |technical |tech |it |production )?"
        r"support\b"
    ),
    r"\btech support\b",
    # A named stack or platform beside a non-developer role ("Kubernetes L3 Lead", "Snowflake
    # Admin", "Mainframe SME"). A lookbehind rather than `\b`, which cannot precede ".NET".
    (
        r"(?<![\w.])(\.net|dot ?net|node\.?js|react(\.?js| native)?|angular|kubernetes|k8s"
        r"|terraform|snowflake|databricks|hadoop|mainframe|pega|guidewire|linux|unix|aws|azure"
        r"|gcp|google cloud|mulesoft|informatica|d365|s/?4 ?hana|java|python|golang|php|mern"
        r"|out ?systems|mendix|appian"
        r"|mean stack)\b.{0,25}\b(lead|admin|administrator|architect|specialist|consultant"
        r"|intern|internship|trainee|sme|subject matter expert|expert|support|analyst)s?\b"
    ),
    r"(?<!\w)\.net (devs?|lead|developer|engineer)s?\b",
    # Security families named for the practice rather than a role.
    (
        r"\b(threat (intel\w*|hunt\w*|analyst|detection)|vulnerabilit\w* (manage\w*|analyst"
        r"|assessment|specialist|engineer)|malware|incident respon\w*|siem|secops"
        r"|infrastructure security|endpoint security|email security|cloud security|dlp analyst"
        r"|grc (analyst|specialist|lead|manager)|identity (and |& )?access|forensic\w* (analyst"
        r"|examiner|investigator)|ethical hack\w*)\b"
    ),
    r"\biam\b.{0,20}\b(analyst|engineer|consultant|specialist|architect|lead|manager)",
    (
        r"\bcloud (consultant|specialist|operations|ops|support|administrator|admin|analyst|lead"
        r"|technician|associate)s?\b"
    ),
    (
        r"\bsys ?admin|\bdb ?admin|\b(hadoop|snowflake|kubernetes|k8s|databricks|office ?365"
        r"|o365|m365|ms365|splunk|jira|atlassian|okta|intune|sccm|citrix|vmware|websphere"
        r"|weblogic|tomcat|jboss|ab ?initio) (admin|administrator|engineer|consultant"
        r"|specialist)s?\b|\bplatform (administrator|administration|admin)\b"
    ),
    # From the labelled evaluation set's misses (tests/fixtures/tech_filter_eval.tsv).
    # One SOC arm: "(SOC) Analyst", "SOC L3 Analyst", "CSOC Analyst" — not the physical "GSOC".
    r"\(?\b(c|cyber )?soc\b\)?.{0,8}\b(analyst|specialist|manager|engineer|architect|lead)",
    r"\b(analyst|manager|lead|engineer)s?\b.{0,15}\bsoc\b",
    r"\bdfir\b",
    # Only `technician`: "Data Center Engineer/Operations" is as often the building's mechanical
    # and electrical plant ("Mechanical Field Engineer — Data Center Operations").
    r"\bdata ?cent(er|re) technicians?\b",
    r"\b(software|etl|automation|manual|api|performance|mobile|regression) testing\b",
    r"\b(consultant|developer|analyst)s?\b.{0,15}\b(power ?bi|tableau|looker|qlik)\b",
    r"\bweb development\b",
]
# Tried only where a word starts. Python's `re` walks every alternative at every character, so
# the lookbehind — one cheap test per position — cuts the list's cost ~3x (10.5s -> 3.3s over
# 150k titles), which is what version 4's longer list had cost on top of version 3's.
# A word also starts where two are glued without a space ("SeniorSolution Architect", "Tier
# 1Technical Support", "GenAI Technologist"): a lowercase letter or digit, then a capital. The
# inline `(?-i:…)` keeps that test case-sensitive inside the case-insensitive pattern.
_STRONG = re.compile(
    r"(?:(?<![a-z0-9])|(?-i:(?<=[a-z0-9])(?=[A-Z])))(?:"
    + "|".join(_STRONG_TERMS)
    + ")",
    re.IGNORECASE,
)

# 0.  Phrases that trip a strong signal while naming a different trade: the retail "Front End
#     Manager", the machinist's "CNC Programmer", a law "JD/LLM", and "Mechanical Engineering
#     Manager". They are set aside before rules 1-3 read the title, so a real signal elsewhere in
#     the title still counts ("CNC Programmer / Software Developer"), and so does software work
#     named beside the trade ("Industrial Engineering Manager - MES"). A title with neither goes
#     on to rule 4, whose own guards keep the trades out (`cnc` is in `_NON_TECH_ROLE`, the
#     disciplines in `_NON_SOFTWARE`) while a technical department can still keep a front-end
#     manager: "Frontend Manager" at a streaming platform is a software role.
_STRONG_NOT = re.compile(
    # A separator is required: the unspaced "Frontend Manager" is the software spelling.
    # Not `team member`/`associate`: both also title front-end developers ("Front End Team
    # Member" at a software firm is a React role), and v3 kept every such row.
    r"\bfront[\s-]end (manager|clerk|supervisor|cashier|attendant|service|lead clerk"
    r"|coordinator|host)s?\b"
    # The cashier on either side of "front end", as a retailer titles it: "Cashier (Front End)"
    # and "Cashier Assistant (Front End)" were 1,213 of 2,500 sampled Costco listings (live
    # 2026-09-24), and "Front End Associate/Cashier" names the same role. Adjacent only, so a
    # software title that merely mentions a cashier system ("Front End Developer - Cashier
    # Systems") keeps its signal.
    r"|\bcashier(s|\s+assistants?)?\s*[(/,:|–—-]?\s*front[\s-]end\b"
    r"|\bfront[\s-]end([\s/-]+associates?)?[\s/-]+cashiers?\b"
    r"|\bcnc\b[\s/-]*(programmer|machinist)s?"
    r"|\bj\.?d\.?\W+ll\.?m\b|\bll\.?m\.?\s+(tax|law|candidate|graduate)"
    # Not after `&`, `/`, `,` or `and`: a joint title ("Firmware & Electrical Engineering
    # Manager", "Software, Electrical Engineering Manager") names software work in its other half.
    r"|(?<![&/,] )(?<![&/,])(?<!\band )\b(mechanical|civil|electrical|chemical|structural"
    r"|manufacturing|industrial|mep|hvac|facilities|facility|construction|process)"
    r" engineering manager",
    re.IGNORECASE,
)

# 2. Generic role tokens — ambiguous on their own; tech unless a non-software qualifier is present.
#    Plurals ("PHP Developers", "Engineers – .NET & React") and `engr`/`engg` are the same word.
_GENERIC = re.compile(
    r"\b(engineers?|engineering|developers?|programmers?|engrs?|engg)\b", re.IGNORECASE
)

# 3. Non-software qualifiers that turn a generic "…engineer" into a non-tech role. Kept to the
#    unambiguous non-software engineering disciplines + sales.
#
#    This is read from the title, and from the department only after _ORG_NOT_ROLE is stripped —
#    see there. It used to be read from the concatenation, on the premise that the disqualifier
#    "never drops a genuine software role (which would already have tripped a strong signal above
#    anyway)". That premise is untrue by construction: a title tripping a strong signal returns
#    before this branch, so the only titles the disqualifier ever sees are the ambiguous ones the
#    strong list does not cover — and for those, an org label was deciding the answer.
_NON_SOFTWARE = re.compile(
    r"\b("
    r"sales|mechanical|civil|chemical|electrical|industrial|biomedical|biochemical|structural"
    r"|aerospace|petroleum|geotechnical|mining|marine|nuclear|agricultural|metallurg|materials"
    r"|hardware|hvac|plumbing|welding|drilling|mechanic|manufacturing"
    r")\b",
    re.IGNORECASE,
)

# 3b. Non-software words that name the ORG rather than the role, and so must not veto from a
#     department. A hardware org employs the engineers whose work is code — RTL design, design
#     verification, physical design are all HDL/EDA, i.e. software by any reading — so "Hardware
#     Engineering" in `department` says who the role reports to, not what it is. Measured over the
#     332,383-row pre-filter snapshot this recovers 287 rows, 0 lost (ADR-0068).
#
#     `sales` is deliberately NOT here: a "Solutions Engineer" under Sales is the pre-sales role
#     this filter already classifies non-tech when the title says so ("Sales Engineer"), so there
#     the department corroborates rather than misleads.
_ORG_NOT_ROLE = re.compile(r"\bhardware\b", re.IGNORECASE)

# 3c. Of the `_NON_SOFTWARE` words, the ones that more often name the *setting* than the
#     discipline: a "Hardware Test and Validation Engineer" or a "Manufacturing Software Support
#     Engineer" writes code for hardware or a factory. In a title that also names software work
#     (`_SOFTWARE_WORK`) they no longer veto; on their own ("Hardware Engineer") they still do.
_SETTING_NOT_ROLE = re.compile(r"\b(hardware|manufacturing|mining)\b", re.IGNORECASE)

# 3d. Vetoes for the construction and plant trades the "…engineer" token admits in bulk: "Site
#     Engineer", "MEP Engineer", "QA/QC Engineer", "Highway Engineer" and its siblings, supplier
#     quality, and the business developer. Rule 2 reads them from the title only — the same words
#     name software orgs often enough ("Site Operations", "Business Development") that a
#     department carrying them says nothing about a titled role. Rule 4 reads them from the
#     department too (see `_dept_names_another_discipline`), where the title is vague.
_TRADE_TITLE = re.compile(
    r"\bsite (engineer|engineering)|\bmep\b|\bqa\s*/\s*qc\b|\bqc\s*/\s*qa\b"
    r"|\bhighways?\b|\bbridges?\b|(?<!air )\btraffic\b|\b(waste)?water\b|\btransportation\b"
    r"|\benvironmental\b|\bsubstations?\b|\brail(way|road)?s?\b(?<!\brails)|\bdrainage\b"
    r"|\bfacilit(y|ies)\b|\bsupplier quality\b"
    r"|\bbusiness developers?\b|\bbusiness development (engineer|manager|representative"
    r"|executive|associate|lead|director|specialist)",
    re.IGNORECASE,
)

# 3e. Words that name software or infrastructure work, and so make vetoes stand down. Three
#     strengths, one vocabulary:
#       `_CODE_WORK`     — the work is code. Keeps a rule-0 title whose remainder names it, and
#                          lifts rule 2's department veto for a titled role.
#       `_SOFTWARE_WORK` — code, plus test/validation/automation/tools. Lifts the 3c setting
#                          vetoes ("Hardware Test and Validation Engineer").
#       `_INFRA_CONTEXT` — code, plus IT, network, telecom, data-center, data/AI, enterprise
#                          platforms, integration, communications, cyber. Read over title and
#                          department; lifts the 3d trade vetoes and rule 4's department veto.
#     `_INFRA_CONTEXT` grew by reading every served row version 4 drops (6,865 titles, 2026-09-23):
#     each word is there because a real tech job carrying it would otherwise have been lost —
#     Oracle's "Site Engineer II" in `DC Ops` (dc ops), "Site Engineer - IP Network" (network),
#     an Epic "Bridges EDI Developer" (epic/edi), "Senior Communications Engineer, Rail Systems"
#     (communications), a Blue Origin "Systems Integration Engineer" (integration), "Senior
#     Solution Engineer (SLED & Water Treatment)" at a security vendor (solutions). The breadth
#     is the recall-first trade: it also spares some trade rows ("Water Solutions Engineer").
_CODE_WORDS = r"software|sw|firmware|embedded|mes|gis|react|angular|vue|javascript|typescript|labview"
# Rule 0's rescue reads only the code words: `tools`/`test`/`automation` beside a trade are the
# trade's own ("CNC Programmer/Tool & Die Maker", "Manufacturing Engineering Manager -
# Automation"; critique of v4: 7 of the 9 rows the wider list rescued were not tech).
_CODE_WORK = re.compile(rf"\b({_CODE_WORDS})\b", re.IGNORECASE)
# The 3c setting vetoes also stand down for test and validation work on hardware.
_SOFTWARE_WORK = re.compile(
    rf"\b({_CODE_WORDS}|validation|verification|test|automation|tools?)\b",
    re.IGNORECASE,
)
_INFRA_CONTEXT = re.compile(
    rf"\b({_CODE_WORDS}|automation|it|ict|network\w*|telecom\w*|rf|bts|radio|wireless|5g|ran"
    r"|fib(er|re)|optical|scada|signall?ing|data ?cent(er|re)s?|dc ops|dco|mission critical"
    r"|server\w*|information technology|ruby|api|cloud|aws|azure|gcp|ai|ml|sap|oracle"
    r"|salesforce|servicenow|data|analytics|digitali[sz]ation|edi|hl7|epic|interfaces?|s?d?-?wan"
    r"|lan|developer experience|a?iot|plm|teamcenter|windchill|image processing|signal processing"
    r"|verification|communications?|solutions?|hpc|integration|crm|cyber\w*|cabling|osp|towers"
    r"|informatics|web)\b",
    re.IGNORECASE,
)

# 3f. Rule 4's department veto. A department naming another discipline ("Building Engineering",
#     "Electrical Engineering") or a trade says what a vague title is — except for `sales`
#     (software presales sits in "Sales Engineering") and `bridge` (a company name as often as a
#     structure), which it ignores.
_DEPT_NOT_A_VETO = re.compile(r"\b(sales|bridges?)\b", re.IGNORECASE)
_DEV_ROLE = re.compile(r"(?<!business )\b(developers?|programmers?)\b", re.IGNORECASE)
_INFOSEC_WORD = re.compile(
    r"\b(information|technical|cyber\w*|privacy|data|digital|it)\b", re.IGNORECASE
)
_BUILT_ENV_DEPT = re.compile(r"\b(building|construction)\b", re.IGNORECASE)
# Physical-security titles, refused under a department of just "Security". The department is
# not vetoed outright — at a software company it holds the infosec team ("Security Researcher",
# "Incident Manager - Detection & Response") — but these titles never are infosec.
_PHYSICAL_SECURITY_TITLE = re.compile(
    r"\b(officers?|patrol|correctional|police|sergeant|lieutenant|event (security|staff)"
    r"|public safety|surveillance|cctv|screener|lifeguard|host|guest|protective|protection"
    r"|investigat\w*|parking|ranger|marina|patient|dining|security (supervisor|agent|associate"
    r"|attendant|guard)|supervisor security|facility security|building (security|supervisor))\b",
    re.IGNORECASE,
)


def _dept_names_another_discipline(dept: str) -> bool:
    """Whether a department names a non-software discipline or trade (3f)."""
    kept = _DEPT_NOT_A_VETO.sub(" ", dept)
    return bool(
        _NON_SOFTWARE.search(_ORG_NOT_ROLE.sub(" ", kept))
        or _TRADE_TITLE.search(kept)
        or _BUILT_ENV_DEPT.search(dept)
    )


# 4. Departments that clearly denote software/tech — a recall booster for otherwise-vague titles.
_TECH_DEPT = re.compile(
    r"\b(engineering|software|technology|developer|data|platform|infrastructure"
    r"|information technology|\bit\b|r&d|devops|security)\b",
    re.IGNORECASE,
)

# 4b. Departments naming a *hiring* function, which must not act as that recall booster: such a
#     label says who does the recruiting, not what discipline the role is in, so a technical word
#     landing inside one is incidental. "Human Data Recruitment" is a team that recruits humans to
#     produce data, and `\bdata\b` matching it promoted 2,427 crowdwork listings into the tech
#     index off a single Board. Reasoning, measurements and rejected alternatives: ADR-0087.
#
#     Scoped to rule 4, and not a disqualifier — a title naming a software role still passes on
#     its own signal at rules 1-3.
#
#     `sourcing` is deliberately NOT here, though it is a hiring term of art: in Department labels
#     it overwhelmingly means procurement, not candidates. All 10 live occurrences in a
#     418-Board, 22,573-job survey were supply-chain ("Category Sourcing", "Sourcing & Quality",
#     "Global Sourcing", "Product Sourcing"), so including it would veto on the wrong meaning.
_HIRING_DEPT = re.compile(
    r"\b(recruit\w*|staffing|talent acquisition)\b", re.IGNORECASE
)

# 4c. Departments whose technical-looking word does not mean software. `security` promotes
#     physical guards ("Security Officer" x1,338 on one sweep) and `engineering` promotes the
#     trades out of "Engineering & Facilities" (plumbers, painters, carpenters, electricians).
#     Neither title carries a software signal of its own, so rule 4 is the only thing keeping
#     them and it is keeping them for the wrong reason.
#
#     Scoped to rule 4 exactly like :data:`_HIRING_DEPT`, and not a disqualifier: a genuine
#     software title inside a facilities org still passes on its own signal at rules 1-3, which
#     is what keeps "Software Engineer, Facilities Systems" working.
#
#     **Its marginal contribution is 1,457 of the change's 5,185 removals**, measured by ablation
#     over that same 489,661-posting sample — not the 3,030 rows it matches, because `_NON_TECH_ROLE` already
#     refuses most of those by title. Security Officer, Plumber, Painter, Carpenter and Electrician
#     are all in that list too and would be refused without this rule; what only *this* rule
#     catches is the title that names no profession at all — a bare "Technician", "General
#     Technician", "Maintenance Manager", "Security Site Supervisor", or the hotel trades
#     ("Laundry & Kitchen Technician", "Technicien(-ne) de maintenance").
#
#     **The overlap with `_NON_TECH_ROLE` is deliberate, and measured rather than assumed.**
#     Trimming the five shared members (`security officer`, `security guard`, `loss prevention`,
#     `janitor`, `housekeep`) out of this list lets **581** rows back in, because here they are
#     *department* labels whose titles that list does not match: "Campus Safety & Security" over
#     "PRIA Specialist", "Engineering and Safety" over "Shift Technician (Buggy & Generator)",
#     "Security & Life Safety" over "Regional Security Manager". Two lists, two inputs.
#     See docs/tech-filter/2026-09-17_the-title-decides.md.
_NOT_TECH_DEPT = re.compile(
    # No trailing \b: the plural is the common spelling ("Security Officers" is the department,
    # "Security Officer" the title) and a closing boundary fails on it.
    # `security guard`, not bare `guard`: the bare word matched the department
    # "Guardian Data Platform".
    r"\b(security officer|loss prevention|physical security|security guard|safety"
    r"|facilit|maintenance|janitor|custodial|housekeep|hotel)",
    re.IGNORECASE,
)

# 4d. Roles whose title says plainly that they are not software, so a technical department must
#     not promote them. This is what makes "Administrative Assistant" in "Software Engineering"
#     a non-tech job: the department says who they sit with, the title says what they do, and on
#     this question the title wins.
#
#     Deliberately a list of *clear* non-software roles, not of merely vague ones — the gate stays
#     recall-biased, so "Analyst" or "Associate" in a Software Engineering department is still
#     kept. Only titles that name a different profession are refused.
_NON_TECH_ROLE = re.compile(
    r"\b("
    r"administrative assistant|admin assistant|executive assistant|personal assistant"
    r"|receptionist|secretar\w+|office (manager|assistant|administrator)"
    r"|account(ant|ing)|bookkeep\w+|payroll|auditor|tax \w+"
    r"|sales (executive|manager|representative|associate|consultant|director)"
    r"|business development|account (manager|executive)|telecaller|telesales"
    r"|customer (service|support|success|care)|call cent\w+"
    # `human resources`, not `\bhr\b`: the initialism refused `HR Technology Manager` while
    # `HRIS Specialist` stayed — an inconsistency with no defence.
    r"|recruit\w+|human resources|talent acquisition"
    r"|content (writer|creator)|copywriter|social media|graphic design\w*"
    r"|nurse|nursing|physician|pharmacist|therapist|caregiver|medical assistant"
    r"|driver|warehouse|forklift|cashier|janitor\w*|housekeep\w+|custodian"
    r"|security officer|security guard|loss prevention"
    # No bare `server`: it refused `Windows Server Administrator`, `SQL Server Specialist` and
    # `Server Support Specialist`, all of which the filter kept before. In job titles the machine
    # sense dominates and the restaurant sense is rare.
    r"|chef|cook|bartender|barista|waiter|waitress"
    r"|teacher|tutor|instructor|lecturer"
    # No `technician - \w+`: it reads as "a technician of some trade" but matched
    # `Technician - Software` and `Technician - Network Operations`, and was brittle anyway —
    # it needed spaces and an ASCII hyphen, so `Technician-HVAC` and `Technician – HVAC` missed.
    r"|plumber|painter|carpenter|electrician|welder|machinist"
    # `cnc` is the machine shop — except in JD Edwards, where "CNC" is the ERP's own system
    # administration layer ("JD Edwards CNC Administrator", "Edwards CNC" in `IT Services`).
    r"|(?<!edwards )(?<!jde )cnc"
    r")\b",
    re.IGNORECASE,
)


# A level glued to a role word ("SDE3", "Developer3", "SRE2") leaves no `\b` between them.
# Spaced apart here; `web` is excluded because "Web3" is itself a signal.
_GLUED_LEVEL = re.compile(r"(?<=[a-z]{3})(?<!web)(?=[1-9]\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether a job is a tech role, and the rule that decided it (for the verification gate)."""

    is_tech: bool
    reason: str


def classify(title: str | None, department: str | None = None) -> Verdict:
    """Decide whether a job is a software/tech role, with the reason (recall-biased; see module doc)."""
    dept = (department or "").strip()
    # `_` is a word character to `\b`, so every pattern here was blind to a title that uses it
    # as a separator: "IN_Senior Associate_Azure Devops_…", "Application Developer_5".
    title_text = _GLUED_LEVEL.sub(" ", (title or "").replace("_", " ")).strip()
    # Rules 1 and 2 read the **title**, not the title and department concatenated. Reading both
    # let a department decide a title question: "Software development" contains "software dev",
    # so `Content Creator` in it scored a strong software signal, and `Engineering & Facilities`
    # contains "engineering", so `Plumber` scored a generic one. The department already has its
    # own rule below, with its own guards; letting it also fire rules 1-2 counted it twice and
    # bypassed those guards. Measured over a 489,661-posting pre-filter sample of 2026-09-17 (4 of run 35193130454's 15
    # scrape fragments),
    # **~8,900** postings were reaching the index on a department-only rule-1/2 match.
    signal_text = _STRONG_NOT.sub(" ", title_text)
    if _STRONG.search(signal_text):
        return Verdict(True, "strong-software-signal")
    if _GENERIC.search(signal_text):
        # A department vetoes only through a discipline that names the role; strip the org-only
        # words first, so "Hardware and Mechanical Engineering" still vetoes on `mechanical`.
        title_veto = (
            _SETTING_NOT_ROLE.sub(" ", signal_text)
            if _SOFTWARE_WORK.search(signal_text)
            else signal_text
        )
        if (
            _NON_SOFTWARE.search(title_veto)
            or (
                _TRADE_TITLE.search(signal_text)
                and not _INFRA_CONTEXT.search(f"{signal_text} {dept}")
                # A developer or programmer is writing software whatever the sector ("Traffic
                # Simulation Developer", "Rail Systems Developer"; review of #573) — but a
                # "Business Developer" is the vetoed sales role itself.
                and not (
                    _DEV_ROLE.search(signal_text)
                    and not re.search(r"\bbusiness develop", signal_text, re.IGNORECASE)
                )
            )
            or (
                _NON_SOFTWARE.search(_ORG_NOT_ROLE.sub(" ", dept))
                # A title naming code is software work whatever org it sits in: "LabVIEW
                # Quality Engineer" in `Aerospace`.
                and not _CODE_WORK.search(signal_text)
            )
        ):
            return Verdict(False, "generic-token-but-non-software")
        return Verdict(True, "generic-tech-token")
    if signal_text != title_text and _CODE_WORK.search(signal_text):
        # Rule 0 set a trade aside, and what is left names software work beside it:
        # "Industrial Engineering Manager - MES", "CNC Programmer - CAM Software".
        return Verdict(True, "software-work-beside-a-trade")
    if (
        dept
        and _TECH_DEPT.search(dept)
        and not _HIRING_DEPT.search(dept)
        and not _NOT_TECH_DEPT.search(dept)
        # 3f: a department naming another discipline or trade says what a vague title is —
        # unless the title or department itself names IT or software work ("IT Project Leader"
        # in `Manufacturing Engineering`, anything in "Vehicle Software & Electrical Eng.").
        and (
            not _dept_names_another_discipline(dept)
            or _INFRA_CONTEXT.search(f"{title_text} {dept}")
        )
        and not (
            re.fullmatch(r"\W*security\W*", dept, re.IGNORECASE)
            and _PHYSICAL_SECURITY_TITLE.search(title_text)
            # "Information Protection Manager", "Technical Privacy Investigator" are infosec.
            and not _INFOSEC_WORD.search(title_text)
        )
        and not _NON_TECH_ROLE.search(title_text)
        # ADR-0068's veto, which rule 4 now has to apply itself. While rules 1-2 read
        # `title + department`, a non-software title always tripped rule 2's generic token off
        # the department's own "engineering" and was vetoed there. Reading the title only closes
        # that path, so without this line rule 4 promoted them instead: `Civil Designer`,
        # `Welding Inspector`, `HVAC Journeyman Chiller Mechanic` and `Structural
        # EIT/Coordinator` all flipped to tech, and the population of tech rows with a
        # `_NON_SOFTWARE` title rose 52%. The title says what they do — including when what it
        # says is "a different kind of engineer".
        and not _NON_SOFTWARE.search(title_text)
    ):
        return Verdict(True, "tech-department")
    return Verdict(False, "no-tech-signal")


def is_tech(title: str | None, department: str | None = None) -> bool:
    """Recall-biased tech/non-tech decision on a job's title (+ department)."""
    return classify(title, department).is_tech


def _filter_file(pair: tuple[Path, Path]) -> tuple[str, int, int]:
    """Filter one ``{ats}.jsonl`` into its tech subset, returning ``(ats, kept, total)``.

    Module-level and single-argument so :func:`filter_jobs` can hand it to a process pool; the
    body is what that loop always did, lifted unchanged.
    """
    src, dst = pair
    kept = total = 0
    with (
        src.open(encoding="utf-8") as fin,
        dst.open("w", encoding="utf-8") as fout,
    ):
        for lineno, line in enumerate(fin, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                job = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{src}:{lineno}: malformed JSON ({exc})") from exc
            if is_tech(job.get("title"), job.get("department")):
                fout.write(json.dumps(job, ensure_ascii=False) + "\n")
                kept += 1
        fout.flush()
    return src.stem, kept, total


def filter_jobs(
    src_dir: str | Path,
    dst_dir: str | Path,
    *,
    workers: int | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, tuple[int, int]]:
    """Filter every ``{src_dir}/{ats}.jsonl`` down to its tech rows in ``{dst_dir}/{ats}.jsonl``.

    Streams line-by-line (never buffering a whole file) and flushes per file, per the repo's
    incremental-output rule. Returns ``{ats: (kept, total)}``. Non-tech rows are dropped; the source
    files (the full scrape output) are left untouched.

    **On a mid-file failure (a malformed line), one difference from the prior single-threaded
    version**: pooled, sibling files already in flight still finish and get written before the
    exception propagates; inline, the loop stops at the failing file and nothing after it (in
    submission order) is written. Verified: a forced-largest bad file raises in both, but the
    pooled run leaves 3 good ``dst`` files on disk where the inline run leaves 0. Benign either
    way — the stage aborts on the exception regardless (``filter_tech.main`` has no partial-output
    contract) — but it is a real behaviour difference, not "no behaviour change".

    One ATS file is one independent unit of work, so the files fan out across a process pool —
    the same shape ``update_meta``'s sweep uses, and for the same reason: this stage sits on
    ``join``'s serial critical path, where it measured 185 s of a 930 s job on the 2026-09-16
    nightly (2,095,569 rows at 11,327 rows/s).

    **Submitted largest-file-first** (an LPT schedule, by byte size as the cost proxy — cheaper
    to read than counting rows and the two track closely). This is a real but partial share of the
    speed-up, not "the whole of it" as an earlier version of this docstring claimed: the work is
    heavily skewed — ``workday`` alone was 500,679 of the corpus's ~2.1M rows — and at 4 workers
    that file is just *under* an even share, so an LPT schedule lands close to the even share.
    Alphabetical submission still parallelises the other files; it only straggles on the last one
    started, worth roughly the file's own runtime minus what the other workers absorbed while it
    waited its turn — a real cost, but well short of the full serial 185 s.

    ``workers`` defaults to the machine's CPU count; 1 (or a single input file) runs inline, with
    no pool, since pool start-up would then cost more than it saves. Also the seam the
    pooled-vs-inline equivalence test uses — ``filter_jobs_and_report`` never threads it, so
    production always gets the default.

    Uses an explicit **spawn** context for the pool, not the platform default. `__main__.main()`
    calls this right after ``scrape_all``, whose ``ThreadPoolExecutor`` is torn down with
    ``shutdown(wait=False, ...)`` (harvest.py) — the worker threads are signalled to stop but not
    guaranteed to have exited before this function runs. On Linux (`ubuntu-latest`, every CI
    caller) the default start method is *fork*, which duplicates the whole process including any
    still-live thread and whatever lock it might hold mid-teardown — the exact deadlock hazard
    Python's own multiprocessing docs warn about for a multi-threaded parent. Forcing spawn avoids
    it unconditionally, for every caller, rather than relying on a caller-specific safety argument
    that a future caller could quietly invalidate.

    ``logger``, when given, gets one INFO line per file as it lands — this stage sits ~185 s on
    ``join``'s critical path, and ``report`` speaks only once every file is done. Worded so
    ``scripts/runlog/fanout_corpus.py``'s per-ATS table regex never reads it as a table row.
    """
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    started = time.monotonic()
    dst_dir.mkdir(parents=True, exist_ok=True)
    # Largest first: an LPT schedule. `report` sorts, so completion order never reaches the log.
    pairs = sorted(
        ((src, dst_dir / src.name) for src in src_dir.glob("*.jsonl")),
        key=lambda pair: pair[0].stat().st_size,
        reverse=True,
    )
    if workers is None:
        workers = os.cpu_count() or 1
    if logger:
        # A run killed before its first file lands still says what it started with.
        megabytes = sum(src.stat().st_size for src, _ in pairs) / 1e6
        logger.info(
            f"filtering {len(pairs)} files ({megabytes:.0f} MB) across "
            f"{max(1, min(workers, len(pairs)))} worker(s)"
        )
    stats: dict[str, tuple[int, int]] = {}

    def landed(ats: str, kept: int, total: int) -> None:
        stats[ats] = (kept, total)
        if logger:
            logger.info(
                f"filtered {ats}: {kept}/{total} kept, "
                f"{time.monotonic() - started:.1f}s elapsed ({len(stats)}/{len(pairs)} files)"
            )

    if workers <= 1 or len(pairs) <= 1:
        for pair in pairs:
            landed(*_filter_file(pair))
        return stats
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(workers, len(pairs)), mp_context=ctx
    ) as pool:
        futures = [pool.submit(_filter_file, pair) for pair in pairs]
        # Results collected as each file lands, not blocking on the slowest submitted first
        # (a plain `pool.map` would preserve submission order and wait on shard 0 even if shard 3
        # finishes first), so each file's progress line lands as soon as the file does.
        for future in as_completed(futures):
            landed(*future.result())
    return stats


def report(
    stats: dict[str, tuple[int, int]], dst_dir: str | Path, logger: logging.Logger
) -> None:
    """Log ``filter_jobs``' per-run table plus the two zero-output warnings, through ``logger``.

    Takes the caller's logger rather than opening one of its own: the pipeline-stage entry point
    (``headstart.ingest.filter_tech``) owns the ``[filter_tech]`` tag (ADR-0039), and this module
    is not that entry point — logging through the passed-in logger keeps every line under that
    tag, unchanged from before this reporting logic lived here.
    """
    logger.info(f"{'ATS':<16}{'kept':>9}{'total':>9}{'kept%':>8}")
    kept = total = 0
    empty = []
    for ats, (k, t) in sorted(stats.items()):
        kept += k
        total += t
        if t:
            logger.info(f"{ats:<16}{k:>9}{t:>9}{100 * k / t:>7.1f}%")
        else:
            # An ATS that scraped nothing used to be skipped here, leaving a wholly broken
            # scraper no trace in this table at all.
            #
            # Every ATS reaching `stats` was in this run's slice: `filter_jobs` keys off
            # `src_dir.glob("*.jsonl")`, and `harvest` opens one handle per ATS *in the shard's
            # list* precisely so a zero-yield ATS still leaves an empty file. An ATS outside the
            # slice has no file at all and never lands here — so "not in the slice" is not one of
            # the readings, and offering it would blunt the signal this line exists to give.
            #
            # Deferral IS one, though: `harvest` opens those handles before the resume filter, so
            # an ATS whose every Board was deferred by a budget kill also leaves an empty file and
            # arrives here having been neither attempted nor empty. `scrape_join`'s own
            # "deferred boards" line is where that is diagnosed.
            empty.append(ats)
    if empty:
        logger.warning(
            f"{len(empty)} ATS(es) were in this run's slice but contributed zero rows: "
            f"{', '.join(empty)} — their boards failed, were deferred, or are genuinely empty"
        )
    if total:
        logger.info(
            f"{'TOTAL':<16}{kept:>9}{total:>9}{100 * kept / total:>7.1f}%"
            f"  (dropped {total - kept} non-tech) -> {dst_dir}"
        )
    else:
        # A zero-row run used to be near-silent: the table printed its header and stopped, which
        # is a hard shape to notice in a green log. Everything downstream reads this corpus, so
        # say it plainly. Not an abort — this stage does not own that call, so WARNING, not ERROR.
        logger.warning(f"no rows at all reached the tech filter -> {dst_dir} is empty")


def filter_jobs_and_report(
    src_dir: str | Path, dst_dir: str | Path, logger: logging.Logger
) -> dict[str, tuple[int, int]]:
    """``filter_jobs`` plus its run report (see ``report``) — what ``filter_tech.main()`` runs."""
    stats = filter_jobs(src_dir, dst_dir, logger=logger)
    report(stats, dst_dir, logger)
    return stats

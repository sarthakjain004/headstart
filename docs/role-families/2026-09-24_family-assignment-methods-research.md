# Assigning a job posting to a role family: methods, evidence, and fit for HeadStart

**Date:** 2026-09-24 · **Status:** research (no code changed) · **Scope:** how agencies, job
platforms and the literature assign a posting to an occupation or role family, and which method
best replaces the k-means centroid assignment behind Trends (ADR-0040).

## Short answer

- **Everyone else classifies the title first.** Agencies (BLS, Census, ONS, Eurostat/Cedefop),
  vendors (Lightcast, LinkedIn, Indeed, Glassdoor) and the research literature all start from the
  job title. They normalise it, match it against a dictionary or rules, and then fall back to a
  supervised model. Agencies (NIOCCS aside) accept an automatic code only above a fixed confidence
  threshold and send the rest to a human coder.
- **LLMs are entering as retrieve-then-rerank coders, or as teachers for a small distilled
  model.** LinkedIn's 2026 production system is both.
- **Nobody found assigns occupations by nearest unsupervised centroid**, which is what HeadStart
  does. The one vendor that clusters (Revelio) clusters *titles*, with seniority stripped out.
- **Every taxonomy examined keeps function, specialisation and seniority on separate axes.**
  HeadStart's 24 families mix all three.
- **Accuracy has a human ceiling.** Human coders agree only about 50–80% at detailed occupation
  level and about 70–90% at major-group level. HeadStart's 24 families match the major-group level
  in *count*, but they split one field (tech) into sub-functions, which is detailed-level work. So
  HeadStart's ceiling lies somewhere in between, and has to be measured. Published methods land at:
  - retrieval-constrained LLMs: 0.78–0.83 major-group agreement with human coding;
  - fine-tuned or supervised classifiers: 0.77–0.95 at 22–34 classes;
  - LLM-labelled students: within about 0.006 F1 of their teacher.
- **Best fit for HeadStart (recommended; ranked options below):**
  1. Redesign the family list onto one axis, function. Move language, vendor and level into facets
     and bands.
  2. Assign families per **normalised title key** (company plus title where the title is vague)
     through a cascade: high-precision title rules → a kNN or linear classifier over labelled
     exemplar titles in a title-embedding space → an LLM through the router, **offline and only
     for low-margin keys**, whose answers are cached and become new exemplars.
  3. Keep each key's label **sticky** until a versioned classifier change, and mark that change in
     `trends_epochs`.

  This fixes copy disagreement by construction, takes boilerplate out of the input, makes every
  assignment explainable (a rule, named neighbours, or a cached LLM label), and keeps the router
  off the CI path. The dollar cost is negligible at every scale checked.

## HeadStart today, and what a replacement must fix

**The index.** About 410k live postings from company ATS boards worldwide, 5–10k new per day,
English-only. Every posting has a stored 768-dim `nomic-ai/nomic-embed-text-v1.5` vector of
the `search_document:` prefix + title + markdown-stripped description, up to 4,096 tokens
(`src/headstart/search.py`, `src/headstart/ingest/doc_prep.py`). The title is therefore a few
tokens inside a document of up to 4,096, and the vector was built for *retrieval*, not for
classification.

**The current assignment** (ADR-0040, amended by ADR-0051/0052). A one-off MiniBatch k-means
(K=72, 438,424 vectors, sampled silhouette 0.038) produced frozen centroids. Each cluster was hand
mapped in `config/role_families.json` onto 24 families plus `non_tech`. Every run re-assigns every
row to its nearest centroid. Fifteen title-regex **watch roles** (`config/role_watchlist.json`:
frontend, backend, full stack, FDE, ML engineer, …) are counted on top as an overlay, never a
partition. `role_assignments` records `id -> family` each tick so that a re-embed that moves a
posting shows as a reassignment instead of a closure.

**What the 2026-09-24 critique measured** (local, uncommitted log
`experiment/role-family-critique/LOG.md`; snapshot of the served table dated 2026-09-23):

| Defect | Measurement |
| --- | --- |
| Title-evident postings land elsewhere | only 76.7% of postings whose title names a family land in it |
| Boilerplate and employer drive clusters | some clusters are one employer's template; cloud is ~17–22% Amazon boilerplate |
| Knife-edge boundaries | 52% of tech rows lie within 0.02 cosine of another family; two copies of one posting move centroid cosines by a median max \|Δ\| of 0.024 |
| Copies disagree | 11.9% of non-identical same-company-same-title copy pairs (n=22,646) land in different families |
| Centroid geometry | median cross-family centroid cosine 0.875 is *higher* than same-family 0.823; 29 of 55 tech clusters' nearest tech centroid is in another family |
| Input-type split | seven seniority clusters are made of title-only vectors (37–69% have no description vs ~0.6% overall) |
| Purity (hand read, n=60–100 per family) | engineering-management 37/100 clearly non-tech; systems-engineering 33/100; product-management only ~17/100 actual PM/PO/TPM |
| Overlay contamination | the Frontend watch role is 33% grocery-store "Front End" clerks (2,194 of 6,606) |
| Axes mixed | families mix language (java, python), platform/vendor (enterprise-platform, cloud), function (qa-test, security) and level (tech-leadership, engineering-management) |

**Constraints any replacement must respect.**

1. *LLM calls go only through the self-hosted OpenAI-compatible router* (CLAUDE.md, ADR-0032). The
   router is deliberately not reachable from CI. ADR-0040 rejected an LLM in the per-run path for
   exactly that reason, so any LLM step has to run off the critical path, with its results cached
   in HF state and read back by the pipeline.
2. *Heavy embedding runs only on CPU GitHub Actions runners*, so a second embedding per posting
   (e.g. a title-only vector) must be cheap.
3. *Trends needs stable assignments*: a posting that flips family between runs reads as fake trend
   movement (the `role_assignments` side ledger exists because this already happened).
4. *Per-posting explainability is valued*: "why is this posting in `data-engineering`?" should
   have an answer a person can check.

## Methods, one by one

Every figure below names its dataset, metric and table. Where a figure could only be seen through
another paper's citation, or a paywall blocked the primary text, it says so. "No primary source
found" means a search was made and nothing first-party turned up.

### 0. What "accuracy" can mean: the human ceiling

Occupation coding is scored against human coders, and human coders disagree:

- **US Current Population Survey.** 32,362 descriptions were double-coded to 3-digit Census codes.
  The two coders agreed on a substantive code **71.4%** of the time, and disagreed on the first
  digit in 3.9% of cases. Longer descriptions were coded *less* reliably (Conrad, Couper & Sakshaug,
  JOS 2016, Table 1, <https://content.sciendo.com/downloadpdf/journals/jos/32/1/article-p75.pdf>).
- **Germany, ISCO-08.** Inter-agency Cohen's κ was **0.683–0.760 at 1 digit** and
  **0.475–0.566 at 4 digits** (ALLBUS n=5,130, PIAAC n=4,159; Massing et al., JOS 2019, Table 5,
  <https://content.sciendo.com/downloadpdf/journals/jos/35/1/article-p167.pdf>).
- **BLS injury survey (SOII).** Expert coders agreed pairwise **70.4%** of the time. Production
  human coding scored 68.3% at SOC detailed and 83.4% at 2-digit against an adjudicated gold
  standard, and the BLS neural autocoder scored 78.6% and 90.2% (Measure, BLS 2017,
  <https://www.bls.gov/iif/automated-coding/deep-neural-networks.pdf>).
- **Polish online job ads.** Experts agreed pairwise **74.7–78.7%** at ISCO 4 digits and 66.1%
  across three experts (Beręsewicz et al., arXiv 2411.03779, Table 4,
  <https://arxiv.org/abs/2411.03779>).

Across these studies, humans agree at roughly **50–80% at detailed level** and **70–90% at
major-group level**:

- German κ 0.68–0.76 at 1 digit;
- BLS production coders 83.4% at 2 digits;
- Polish experts 86% at 1 digit.

Most published *method* results are for taxonomies of 400–3,000 classes. HeadStart's 24 families
match SOC's 23 major groups in *count* but not in *content*. SOC puts nearly all of tech into one
major group (15, Computer and Mathematical). HeadStart splits that group into sub-functions, which
is closer to SOC's detailed codes within 15-1200, and those are among the hardest for existing
coders (§1). The realistic ceiling therefore lies between the two ranges. HeadStart has never
measured its own human–human agreement on its families, and that one number bounds every accuracy
claim that follows (see Open questions).

### 1. Official occupation autocoders

Every agency coder follows the same pattern:

1. Match the title (sometimes with industry and description) against a dictionary of known titles.
2. Fall back to a statistical model.
3. **Accept the code only above a confidence threshold tuned on an expert-coded gold standard,
   and send the rest to people.**

The one exception, NIOCCS's ML version, codes every record.

- **BLS injury survey autocoder** (<https://www.bls.gov/iif/automated-coding.htm>; code on
  [GitHub](https://github.com/USDepartmentofLabor/soii_neural_autocoder)).
  - The model moved from logistic regression (2014–17) to a character CNN + LSTM (2018–20), then a
    transformer (from 2021).
  - Share of codes assigned automatically: 26% of occupation codes in 2014, about 92% of all codes
    in 2021–22.
  - Accuracy: 78.6% at 6-digit SOC, against 68.3% for the original manual process, on a 1,000-case
    adjudicated gold standard (Measure 2017, <https://www.bls.gov/iif/automated-coding/deep-neural-networks.pdf>).
  - Threshold: BLS sweeps it, uses the human code below it, and keeps "humans in the loop; hold
    back a sample… continually reassess" (FedCASIC 2019, <https://census.gov/fedcasic/fc2019/ppt/1CMeasure.pdf>).
  - The OEWS coder returns up to two SOC codes with probabilities, as recommendations for human
    coders ([DOL AI inventory](https://www.dol.gov/agencies/oasam/centers-offices/ocio/ai-inventory)).
- **US Census Bureau, ACS.**
  - The 2012 production coder (dictionaries, logistic regression, hand-written overrides) shows the
    coverage–precision trade-off plainly (Thompson, Kornbau & Vesely, Table 5,
    <https://www.census.gov/content/dam/Census/library/working-papers/2012/demo/2012-io-coding-asa-paper-final.pdf>):

    | Share of cases coded automatically | Occupation disagreement with clerical coding |
    | --- | --- |
    | 30% | 4.49% |
    | 40% | 5.30% |
    | 50% | 8.35% |
    | 60% | 13.43% |

  - **The probability cutoff is held constant, not the coding rate.** The current cutoff is 0.88
    over about 570 codes ([FCSM 2024](https://statspolicy.gov/assets/fcsm/files/docs/2024-conference-docs/A/A3.5_Zakrzeski.pdf)).
  - A BERT-Tiny model matched the final code 81% top-1 and 96% top-5 (Bryant et al., SEHSD WP
    2024-26, Table 9, <https://www2.census.gov/library/working-papers/2024/demo/sehsd-wp2024-26.pdf>).
- **NIOSH NIOCCS** (<https://csams.cdc.gov/nioccs/About.aspx>).
  - Machine learning since 2021, "every record receives a code", free API.
  - It has an explicit **00-9900 "Insufficient Information"** code
    ([coding schemes](https://csams.cdc.gov/nioccs/HelpCodingSchemes.aspx)).
  - Input quality dominates: on 700,000+ records, major-group discordance with manual coding fell
    from 53.6% with raw inputs to 5.0% with refined inputs (Roberts et al., JOEH 2022,
    <https://pubmed.ncbi.nlm.nih.gov/35537195/>).
- **O*NET-SOC AutoCoder** (R.M. Wilson Consulting, <https://www.onetsocautocoder.com/plus/onetmatch?action=guide>).
  - Commercial: weighted matching of words and phrases against analyst-weighted terms.
  - The vendor claims 80% on titles and 85% on titles plus descriptions, with no published dataset.
- **Independent head-to-head of the US coders** (Laughlin et al. 2024, 10,449 ACS write-ins, exact
  match to 568 Census codes, Table 2, <https://occautocoder.com/static/paper/occupation_autocoding_May2024.pdf>).
  - NIOCCS: 59.58% on title only, 61.82% with description and industry.
  - O*NET-SOC AutoCoder: 54.68% on title only, 57.21% with description.
  - **Computer and Mathematical occupations are among the worst-coded groups:** NIOCCS 56.46%,
    AutoCoder 47.28% (Tables A1, A3).
- **A small live probe run for this research** (14 titles, title only; evidence, not proof).
  Both US coders gave high confidence to wrong codes for modern tech titles:
  - NIOCCS put "devops engineer", "site reliability engineer" and "frontend engineer" in 17-2199
    *Engineers, All Other* at probability 0.97–0.998.
  - Adding the industry text "software company" flipped all of them to 15-1252 Software Developers.
  - The AutoCoder put "QA automation engineer" in Industrial Engineers (score 83).
  - All seniority and misspelling variants of "software engineer" landed on 15-1252 in both. These
    coders are robust to *seniority* tokens and weak on *specialisation* titles.
- **ONS (UK).**
  - The Census 2021 tool ran a not-codable knowledge base, then an ambiguous-title knowledge base,
    then exact and fuzzy matching. It coded 65.3% of occupations automatically at 97.1% achieved
    quality ([ONS](https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates/methodologies/automatedtextcodingcensus2021)).
  - Logistic regression on TF-IDF reached 0.95 accuracy on the 73% of records it matched, at a
    global threshold of 0.6 ([Data Science Campus, Table 1](https://datasciencecampus.ons.gov.uk/projects/automated-coding-of-standard-industrial-and-occupational-classifications-sic-soc/)).
  - [ClassifAI](https://github.com/datasciencecampus/classifai) is a semantic search plus RAG coder
    over previously coded examples, described as "in production in the ONS". The ONS has published
    no accuracy for it.
  - CASCOT (Warwick) exposes a certainty score from 0 to 100 and recommends automating at about 64
    ([FAQ](https://warwick.ac.uk/fac/soc/ier/data_group/cascot/faq/)).
- **ESCO.**
  - The public ESCO API is a label *search*, not a classifier. Measured today, "machine learning
    engineer" returned "packing machinery engineer" top-1, and "DevOps" returned nothing
    ([API](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api)).
  - The Commission's fine-tuned XLM-R mapper got about 30% top-1 on the JobBERT title benchmark,
    and it is not public ([ESCO ML part 2](https://esco.ec.europa.eu/en/about-esco/data-science-and-esco/machine-learning-assisted-mapping-multilingual-occupational-data-esco-part-2)).
- **Eurostat / Cedefop Web Intelligence Hub (WIH-OJA)** (metadata annex,
  <https://ec.europa.eu/eurostat/cache/metadata/Annexes/isoc_sk_oja_esmsip2_an_1.pdf>).
  - Each ad gets **one** ISCO-08 code.
  - Method: ontology matching on title or description first, then a per-language Naïve Bayes
    trained on 70,000+ ads.
  - Cedefop's 2025 working paper (<https://www.cedefop.europa.eu/files/5610_en.pdf>, pp. 37–41)
    states that "all classifiers rely on the job titles".
  - Asking an LLM directly took about 3 s per ad, impractical at 65M ads a year. They moved to
    pre-computed embeddings of ads and ESCO occupations, with top-3 suggestions and human
    validation.
  - Table 2, ISCO 4-digit accuracy, production vs the new method: UK 94.0% vs 96.2%, Germany 67.1%
    vs 78.4%, Finland 45.9% vs 63.4%. (The table's header prints "LLM 5-digit" twice; the text
    implies the first of those columns is 4-digit.)
  - The paper warns that a confidence threshold "could disproportionately affect occupations
    rarely appearing".

**What transfers to HeadStart.** Four things do: title-first matching; a known-title dictionary as
the first stage; a *fixed* confidence cutoff with a fallback coder (human there, LLM here) for the
rest; and a gold standard that is re-scored continually. The coders themselves do not transfer:
they are weakest exactly on modern tech specialisations.

### 2. Commercial title normalisation

Vendors disclose far less than agencies. What they do disclose converges on four points:

1. Normalise the title first.
2. Apply rules before models.
3. Keep **function, specialisation and seniority as separate dimensions**, even when the displayed
   label joins them.
4. Re-code history whenever the classifier changes.

**Lightcast (formerly Emsi Burning Glass).**

- **The Lightcast Occupation Taxonomy (LOT)** has four levels: career area → occupation group →
  occupation → specialised occupation. Each specialised occupation maps to exactly one parent
  (<https://lightcast.io/resources/blog/new-occupation-taxonomy>).
- **The IT career area**, enumerated for this research from Lightcast's own pages (LOT 7.22.0), has
  8 occupation groups, 42 occupations and 179 specialised occupations. Under the single occupation
  *Software Developer/Engineer* sit 36 specialised occupations, mostly by language, framework or
  vendor: .NET, Java, Python, React.js, SAP ABAP, Workday, Embedded Software, DevOps Engineer,
  Full Stack and others
  (<https://lightcast.io/lot/occupations/23171541/software-developer---engineer>).
  **Language is a leaf under the function, not a sibling of it.** Mobile, Web (with Front-End and
  Back-End leaves), QA, Data Engineer, AI Engineer, DBA, Security and UI/UX are separate
  occupations.
- **Classification** (<https://kb.lightcast.io/en/articles/7907688-lightcast-occupations-taxonomy-lot-classification-methodology>).
  - Input: title, description, country and language.
  - Before classifying, "common words, company boilerplates, and benefits information are removed".
  - **"Rules are applied first, and the model is used only if no rule matches"**
    (<https://kb.lightcast.io/en/articles/6957446-job-posting-analytics-jpa-methodology>).
  - The model type is undisclosed.
  - Lightcast re-classifies "all of the historic and current raw postings data" every 4 weeks
    (same page).
- **Accuracy.** Lightcast gives none with a stated dataset: "close to 90%"
  (<https://kb.lightcast.io/en/articles/9113156-april-2024-changes-to-o-net-soc-2019-classification-on-us-job-postings>).
  The one independent audit found over 80% at 2-digit SOC and about 73% at 6 digits (Georgetown CEW
  2014, p. 17).
- **Taxonomy change is managed.** LOT version 7 (2025) kept 92% of occupations and 89% of
  specialised occupations unchanged, and published a v6→v7 correspondence
  (<https://kb.lightcast.io/en/articles/10430137-lightcast-occupation-taxonomy-update>).
- **Titles.** Lightcast Titles (more than 70,000) are assigned by "a vector-based machine learning
  model". Seniority stays inside the normalised title ("Lead Data Scientist" is its own title), and
  a separate `jobLevels` field exists (<https://docs.lightcast.io/lightcast-api/reference/titles-use-cases>).
- **Deduplication.** Postings are matched on normalised title, company and location over 60 days
  (JPA page).

**LinkedIn.**

- **Hierarchy:** standardised title → *supertitle* → *function*. For example, "Programmer" and "Web
  Developer" → Software Developer → Engineering. It was built from rule-based candidates,
  disambiguation and word2vec clustering, validated by taxonomists, with calibrated confidence
  (<https://www.linkedin.com/blog/engineering/knowledge/building-the-linkedin-knowledge-graph>).
- **Functions.** There are 26, and "each standardized job title maps to a single job function".
  *Engineering* and *Information Technology* are separate functions, as are *Product Management*,
  *Program and Project Management* and *Quality Assurance*
  (<https://www.linkedin.com/help/talent-insights/answer/a187044/data-in-talent-insights-faq>).
- **Seniority is a separate field**: 10 member levels, and `experienceLevel` on postings
  (<https://learn.microsoft.com/en-us/linkedin/shared/references/reference-tables/seniority-codes>).
- **Patent US10339612B2** proposes a multi-dimensional title of role, seniority, specialty,
  accreditation and status: "part time registered java developer" → role *software engineer*,
  specialty *java*. It criticises monolithic title lists that have "senior software engineer" but
  not "senior data scientist" (<https://patents.google.com/patent/US10339612>).
- **Current production classifier** (Xu et al., SIGIR 2026, <https://arxiv.org/abs/2607.24783>).
  - Stage 1: a bi-encoder retrieves taxonomy candidates from title plus description.
  - Stage 2: a **LoRA-tuned Flan-T5-XL trained on GPT-4 synthetic data** picks one. This is
    retrieve-then-rerank *and* LLM distillation in production.
  - Table 3: occupation precision/recall 83%/83%, against 71%/25% for the legacy model. The
    evaluation set was built only from cases where the legacy model failed, so these are hard
    cases.
  - Adding descriptions to taxonomy entries added 4.7% recall@20.
  - Cost: more than 50 A100s at about 1 job per second per GPU. Jobs with identical descriptions
    are cached.

**Indeed.**

- **Hiring Lab sectors** are "an Indeed categorization based on normalized job titles". There are 47;
  the tech-relevant ones are Software Development, IT Operations & Helpdesk, Mathematics
  (including data scientist) and Information Design & Documentation (including data analyst)
  (<https://github.com/hiring-lab/job_postings_tracker>). The classifier behind them is not
  described.
- **Indeed's internal occupation taxonomy** is a DAG of more than a thousand occupations, and
  **"each job posted on Indeed is labeled with one or more occupations"**. 45% of 4,555 test jobs
  carry more than one (Lake, RecSys-in-HR 2022, Table 2, <https://arxiv.org/abs/2209.12678>).
  - Model: BERT over title, employer and description.
  - Multi-label LRAP 0.697 with all classes seen, 0.645 with half the classes unseen (Table 3).
  - A bi-encoder shortlist of 16, then a cross-encoder, cost 2% LRAP and saved about 98% of
    compute (Fig. 4).
- **Seniority** (Entry/Mid/Senior) comes from a separate task-based classifier, one label per
  posting, about 95% correct on a hand-checked sample
  (<https://hiringlab.indeed.com/2026/07/23/the-labor-market-is-tilting-toward-seniority/>).

**Glassdoor.** Patent US10409866B1 (<https://patents.google.com/patent/US10409866>) normalises
tokens ("Sr." → "senior"). It then splits a title into **occupation, seniority and modifier**, and
classifies the occupation with a model trained on user search clicks into several thousand
normalised occupations. No accuracy is disclosed. No first-party engineering post on title
normalisation was found.

**Revelio Labs.** This is the one vendor that clusters, as HeadStart does, and how it differs is
instructive (<https://www.data-dictionary.reveliolabs.com/methodology.html>).

- Title vectors are built from activity text (profile descriptions and posting responsibilities).
  They are made "independent of seniority-related keywords like 'senior' or 'principal'".
- Titles are then grouped by agglomerative clustering forced to roughly equal cluster sizes, at
  published granularities k10 to k1500.
- Seniority is a separate 7-level model.
- Unseen titles are placed with fastText.
- **The unit clustered is the title, not the posting,** so every posting with the same title gets
  the same role.

**Others.**

- **CareerBuilder's Carotene** (<https://arxiv.org/abs/1606.00917>) is a production SVM → kNN
  cascade (§5, §7). Its motivation was that O*NET's two software codes lump together "Hadoop
  Engineer, .Net Developer, Machine Learning Engineer and Java Developer". Its API versions the
  taxonomy immutably, but lets "classifiers … occasionally be updated without a version change"
  (<https://github.com/careerbuilder/DataScienceAPIDocumentation/blob/master/JobTitle.md>).
- **People Data Labs** uses class → role → sub_role, with job levels in a separate array
  (<https://docs.peopledatalabs.com/docs/title-subroles-to-roles>).

**Correction to a common assumption.** The Stack Overflow survey's developer-type question was
"select all that apply" in 2022. Since 2023 it has been **"Please select only one"**
(<https://survey.stackoverflow.co/2023/>, <https://survey.stackoverflow.co/2024/developer-profile>).

### 3. Job-title embedding models and title-normalisation benchmarks

These models encode a **title** (a few tokens, not a 4,096-token document) into a vector space
trained to put equivalent titles together. A posting is then linked to the nearest taxonomy entry,
or to the nearest labelled title.

- **JobBERT** (Decorte et al. 2021, <https://arxiv.org/abs/2109.09605>). BERT-base with a gated
  weighted average, trained to predict the *skills* that co-occur with a title across 300M
  vacancies. Its benchmark links 30,926 English vacancy titles from Malaysia's government job
  board to 2,675 ESCO leaf occupations
  ([HF dataset](https://huggingface.co/datasets/TechWolf/JobBERT-evaluation-dataset), MIT).
  - Table 1 (macro / micro): off-the-shelf SBERT R@1 .186/.193, MRR .270/.265; JobBERT
    fine-tuned R@1 .267/.225, MRR .364/.309.
  - The authors estimate that only **about 65% of vacancy titles can be linked to their ESCO label
    without ambiguity**. That is a hard ceiling for title-only coding at fine granularity.
  - Per-token gates show the model learns to ignore locations in titles (Appendix B), a small
    explainability win.
- **JobBERT-v2** ([model card](https://huggingface.co/TechWolf/JobBERT-v2);
  [arXiv 2505.24640](https://arxiv.org/abs/2505.24640)).
  - Model: `all-mpnet-base-v2` (109M parameters) with an asymmetric 768→1024 head, 64-token maximum
    input, MIT licence.
  - Training: 5.58M US job ads (2020–2024), with each ad's skills as the target.
  - JobBERT benchmark, micro MRR (Table 3): **.390**, against .309 for v1.
- **JobBERT-v3** ([model card](https://huggingface.co/TechWolf/JobBERT-v3);
  [arXiv 2507.21609](https://arxiv.org/abs/2507.21609)). A multilingual variant (278M). TalentCLEF
  2025 Task A test MAP (Table 4) is .533 for English, and going multilingual costs 1.6% MAP in
  English against v2 (Table 3).
- **WorkBench / UWE** (De Lange, Decorte, Van Hautte, <https://arxiv.org/abs/2511.07969>).
  - On the JobBERT normalisation task (Table 2, MAP %): UWE (109M) 39.3, JobBERT-v2 39.0,
    E5-mistral-7B 35.3, Qwen3-8B 35.3, MPNet 32.9. **Generalist 4–8B embedders do not beat the 109M
    domain models.**
  - Latency (Table 3, A100, batch 1): 13.4 ms for JobBERT-v2.
  - No public UWE checkpoint was found.
- **MELO** (Avature, <https://arxiv.org/abs/2410.08319>). 48 datasets linking national occupation
  names to ESCO. USA en-en MRR (Table 2): OpenAI text-embedding-3-large .684, E5 .674, BGE-M3 .623,
  character TF-IDF .580, ESCOXLM-R .345. Performance tracks lexical overlap (Spearman −0.62 to
  −0.74).
- **Robustness to title edits.** This is the strongest primary evidence on stability.
  - Textkernel's siamese character BiLSTM, doing nearest-neighbour search over 19,927 titles in
    4,431 groups (Neculoiu et al. 2016, Table 3, <https://aclanthology.org/W16-1617.pdf>):
    typos 1.00, synonyms .84 (n-gram baseline .61), extra words .76, annotated real inputs .87
    (baseline .83).
  - StepStone's ontology-aligned SBERT, using kNN over labelled German titles
    (<https://arxiv.org/abs/2509.04942>, Table 5 and §4.2): macro-F1 .886 at the finest KldB level,
    against .858 for TF-IDF kNN and .780 for JobBERT kNN. Changing the head noun cost 1.7 pp,
    gender inflection under 0.6 pp, and a word-order swap 1.9 pp.
- **Cost.** No primary source reports CPU latency for JobBERT. A local measurement made for this
  research on an Apple laptop (not a primary source; x86 CI runners will be slower) gave 11.5 ms per
  title at batch 1 and **2.6 ms per title at batch 32**. Titles are capped at 64 tokens, so encoding
  every *distinct* title is orders of magnitude cheaper than the existing 4,096-token description
  embedding.
- **Caveat.** Every benchmark here ranks titles against ESCO or O*NET entries. None measures
  assignment to a small set of tech families, which is HeadStart's task.

### 4. Zero-shot classification

**Label embedding.** Embed each class's text (a definition, seed titles, alternative titles) and
assign each posting to the most similar class.

- **Turrell et al.** (Bank of England / NBER, <https://www.nber.org/system/files/chapters/c14277/revisions/c14277.rev0.pdf>).
  - Coded UK job ads to 3-digit SOC with no training labels.
  - Each class's reference document is its official description plus every known alternative title
    (the ONS index holds about 30,000). TF-IDF retrieves the top five classes, then a fuzzy match on
    the ad's title picks among them.
  - Table 6: **76%** against 330 volunteer-coded titles; 91% against the ONS coder, but only on the
    34% of ads that coder could code confidently.
- **Exemplars beat label text.** CareerBERT (Rosenberger et al. 2025, Table 5,
  <https://arxiv.org/abs/2503.02056>) linked German EURES ads to ESCO occupations. Measured as
  MRR@100 on 2,250 test ads, **centroids of labelled ads scored .482, against .321 for embedding
  the ESCO label and .328 for its description.** This is the single most relevant result for
  HeadStart's "embed the family definitions" option: seed *postings* beat seed *definitions* by
  about 50% relative.
  - Caveat: ad centroids existed for only 1,700 of 3,008 occupations. A hybrid covering all of
    them scored .434, still well above label text.
- **Title-only SBERT** against 2,675 ESCO classes: R@1 about .19 (JobBERT, Table 1, above).
- **Label keywords vs NLI.** Label-keyword embedding similarity beats NLI zero-shot on general
  topic benchmarks (Schopf et al. 2022, Table 2, <https://arxiv.org/abs/2211.16285>, micro-F1).
  On Yahoo: SBERT 51.25, against 40.21 for BART-large-MNLI.
- **Failure modes.**
  - *Hubness:* a few vectors sit near many items and absorb assignments
    (Dinu et al., <https://arxiv.org/abs/1412.6568>).
  - *Anisotropy:* contextual embeddings share dominant directions
    (Ethayarajh 2019, <https://arxiv.org/abs/1909.00512>). Removing the common mean and a few top
    directions is a standard fix (Mu & Viswanath, <https://arxiv.org/abs/1702.01417>).
  - Both plausibly contribute to HeadStart's measured centroid geometry, where cross-family
    centroids are closer than same-family ones. No paper measures this for occupation coding.

**NLI-based zero-shot** (Yin, Hay & Roth 2019, <https://arxiv.org/abs/1909.00161>). Each label
becomes an entailment hypothesis.

- Yahoo topics, 10 classes, label-unseen (Table 6): accuracy 37.9–45.7.
- Replacing label words with WordNet definitions collapsed accuracy from 43.4 to 17.2 (Table 7).
  The method is fragile to how a label is worded.
- Cost scales linearly with the number of labels: one forward pass per (posting, label) pair
  ([transformers pipeline source](https://github.com/huggingface/transformers/blob/main/src/transformers/pipelines/zero_shot_classification.py)).
- SetFit's docs measured `bart-large-mnli` at 31.18 ms per sentence and 37.65% accuracy on a
  6-class emotion set, against 0.46 ms and 59.1% for SetFit's own zero-shot mode
  (<https://huggingface.co/docs/setfit/how_to/zero_shot>).
- **Application to occupations or job ads: no primary source found.**

### 5. Few-shot and supervised classifiers

**SetFit** (Tunstall et al. 2022, <https://arxiv.org/abs/2209.11055>). Contrastive fine-tuning of
a sentence encoder, then a logistic-regression head.

- Table 2, averaged over test sets of 2–6 classes: **62.3 with 8 labels per class, 75.3 with 64**,
  against 84.8 for full-data fine-tuning.
- Cost: training takes about 30 s at N=8 on one GPU.
- No test set had more than 6 classes. **Application to occupations: no primary source found.**

**Linear head on frozen embeddings.**

- MTEB's classification score *is* this: logistic regression on frozen embeddings (MTEB,
  <https://arxiv.org/abs/2210.07316>). The current `mteb` code defaults to 8 samples per label
  ([source](https://github.com/embeddings-benchmark/mteb/blob/main/mteb/abstasks/classification.py)).
- `nomic-embed-text-v1` averages **74.1** on MTEB classification (Nomic report, Table 4,
  <https://arxiv.org/abs/2402.01613>).
- **Prefixes matter for HeadStart.** The nomic model cards say input *must* carry a task prefix,
  and list `classification:` for "features for a classification model", `clustering:` for
  grouping, and `search_document:` for index documents
  ([v1.5 card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)). The report says prefixes
  exist "to break the symmetry of the biencoder", so the prefixes are meant to produce different
  vectors. It also found classification worked better *without* L2 normalisation.
- HeadStart's stored vectors use `search_document:` and are L2-normalised. Both its k-means fit
  and any linear head trained on them are therefore off-card usage. **No primary source quantifies
  the gap between `search_document:` and `classification:` features.**
- On occupation tasks specifically:
  - A frozen occupation-trained encoder with a trained head matches full fine-tuning (OccCANINE,
    Table 6, <https://arxiv.org/abs/2402.13604>: ISCO-68 0.822 vs 0.829).
  - A frozen general multilingual BERT stays below 0.3 on an 11-profession task, against 0.68–0.80
    fine-tuned (Gnehm & Clematide 2020, fn. 15, <https://aclanthology.org/2020.nlpcss-1.10/>).
  - **Logistic regression on a general sentence embedder (nomic, E5, SBERT) for occupation
    coding: no primary source found.** This is cheap to measure on HeadStart's own vectors.

**Fine-tuned small encoders.** The closest analogues to 24 families:

| Study | Classes | Accuracy | Labels used |
| --- | --- | --- | --- |
| Gnehm & Clematide 2020, Table 3 | 34 professions, Swiss ads | 0.778 | ~20.7k ads |
| Gonzalez-Garcia et al. 2026, Table 6 ([CMC](https://www.techscience.com/cmc/v86n2/64766/html)) | 22 O*NET families | DeBERTa 0.768 macro-F1 0.767 | 692,600 silver labels (from the search query) |
| Beręsewicz et al., Fig. 5 | ISCO 1-digit / 4-digit | ~88.6 / 74.8–80.7 recall@1 | ~200k ads + register data |
| Laughlin et al. 2024, Table 3 ([PDF](https://occautocoder.com/static/paper/occupation_autocoding_May2024.pdf)) | 568 Census codes | FLAN-T5 71.13% (NIOCCS 61.82%) | ACS write-ins |
| OccCANINE, Table 2 | 5-digit HISCO | 96.1% in-domain, ~80% unseen strings | 15.8M pairs |

Two findings matter for design:

- **Calibration enables abstention.** Gnehm's classifier had a 12% error rate on predictions with
  p ≥ 0.9 and 75% on p ≤ 0.5. Of 20 high-confidence "errors" checked by hand, 90% were acceptable
  alternative labels.
- **Train on real postings, not taxonomy lists.** An XLM-R model trained on ILO title lists scored
  92% in-domain but **36%** on real text (IEA 2024,
  <https://www.iea.nl/sites/default/files/2024-09/Improving-Parental-Occupation-Coding-Procedures-AI.pdf>).

**TF-IDF / character n-gram linear models.**

- Gweon et al. 2017 (<https://doi.org/10.1515/JOS-2017-0006>): 399 ISCO codes, 8,223 very short
  answers. A linear SVM scored 0.52 at 4 digits; hybrid and nearest-neighbour methods about 0.65.
  Defining duplicates by n-grams rather than exact strings lifted accuracy from 0.47–0.54 to 0.65:
  exact-match lookups are brittle to trivial edits.
- Schierholz & Schonlau 2021 (<https://academic.oup.com/jssam/article/9/5/1013/5952839>; read from
  the OUP HTML, two reads agreed): multinomial logistic regression reached 58.9–76.4% over 1,286
  German codes. Cross-dataset drops exceeded 25 pp, and tripling the training data gained only a
  few points.
- Carotene (CareerBuilder, <https://arxiv.org/abs/1606.00917>): a LIBLINEAR SVM over 23 SOC major
  groups, trained on 1.8M ads silver-labelled by a third-party coder.
  - 10-fold cross-validated F1 96.11% against those silver labels.
  - 89.92% agreement with the third-party coder on 2.1M ads.
  - At most 60 ms per ad.

TF-IDF models are the most explainable of all: the weights are per n-gram.

**Label requirements, as far as the literature shows.** Zero-shot needs 0 labels but a rich label
text. SetFit's curve is for 6 classes or fewer. At 20–35 classes, fine-tuned encoders were trained
on 20k+ labelled ads. Turrell estimated that a supervised UK SOC coder would need about 30,000
labels. No primary source gives a learning curve for about 25 tech families.

### 6. LLM classification and LLM-labelled distillation

**Prompted and retrieval-constrained LLM coding.**

- **SOCbot** (Sturgis et al., Survey Futures WP 13, April 2026, Table 1,
  <https://surveyfutures.net/wp-content/uploads/2026/04/working-paper-13-using-large-language-models-measure-classify-occupations-surveys.pdf>).
  - Method: embeddings retrieve candidate UK SOC20 codes, then o4-mini or o3 shortlists, picks one
    and explains.
  - Agreement with CASCOT-assisted human coding: **0.78–0.83 at major group**, 0.59–0.63 at unit
    group.
  - Widening the shortlist from k=10 to k=30 barely moved agreement.
  - Retrieval was adopted because sending the whole SOC list cost $0.02 per prompt.
- **Taxonomy-guided reranking** (Achananuparp et al., <https://arxiv.org/html/2503.12989>, Table 4).
  - Data: 11,920 title-and-company pairs, coded to 1,016 O*NET-SOC codes.
  - Precision@1: **0.8114** for GPT-3.5 infer → retrieve → rerank, 0.4875 without retrieval and
    reranking, and 0.634 for Lightcast's own coder.
  - Caveat: the gold labels were chosen by GPT-4o from pooled predictions.
  - A knowledge probe (Table 3) shows why retrieval matters for small models: Llama-3-8B recalled
    a title's code from memory 7.7% of the time, GPT-4o 91%.
- **Unconstrained prompting fails at fine granularity.** Prompted GPT-3.5 and GPT-4 scored about
  **19%** on 568 Census codes. The authors could not report stable metrics because the models
  "might generate different occupational code outputs for the same write-in input on different
  occasions" (Laughlin et al. 2024, <https://occautocoder.com/static/paper/occupation_autocoding_May2024.pdf>).
  LLM4Jobs attributes GPT-4's collapse at fine levels to hallucinated, non-existent codes
  (<https://arxiv.org/abs/2309.09708>).
- **ONS ClassifAI / sic-soc-llm** (<https://github.com/datasciencecampus/sic-soc-llm>). MiniLM
  retrieval, then Gemini picks the code. The ONS reported only a "marginal improvement" over
  logistic regression and clerical coding, with no numbers. The repo was archived on 2026-04-15 as
  a proof of concept.
- **Job-ad classification with gpt-3.5** (Clavié et al., <https://arxiv.org/pdf/2303.07142>,
  Tables 2 and 4). Zero-shot scored 86.9 (precision at 95% recall), above a fine-tuned DeBERTa at
  79.7. But prompt edits alone moved F1 from 65.6 to 91.7.
- **LLMs disagree with each other.** Three frontier LLMs labelling Italian and Spanish ads over
  about 3,000 ESCO classes had mean pairwise κ **0.62** (Kavas et al., CLiC-it 2024,
  <https://aclanthology.org/2024.clicit-1.53.pdf>).

**Consistency.** The occupation-specific evidence is only the qualitative Census note above. The
general evidence:

- **Run-to-run:** accuracy varied by up to 15% between runs at "deterministic" settings (Atil et
  al., <https://arxiv.org/abs/2408.04667>).
- **Prompt wording:** changing "classify" to "rate" cut Krippendorff's α from above 0.9 to below 0.6
  (Reiss 2023, <https://arxiv.org/pdf/2304.11085>).
- **Formatting:** formatting-only changes moved few-shot accuracy by up to 76 points (Sclar et al.,
  ICLR 2024, <https://arxiv.org/abs/2310.11324>).
- **Option order:** reordering answer options opened gaps of 13–75% (Pezeshkpour & Hruschka,
  <https://arxiv.org/abs/2308.11483>).
- **Self-consistency as a signal:** labels that agreed across three runs at temperature 0.7 were
  **19.4% more accurate** (Pangakis & Wolken, <https://arxiv.org/pdf/2406.17633>).

**Explainability caveat.** LLM rationales are not reliably faithful:

- Chain-of-thought explanations omit the biasing features that drove the answer (Turpin et al.,
  NeurIPS 2023, <https://arxiv.org/abs/2305.04388>).
- Reasoning models mention a hint they used under 20% of the time (Chen et al. 2025,
  <https://arxiv.org/abs/2505.05410>).

A rationale is a readable story, not an audit trail.

**Distillation: an LLM labels, a small model serves.**

- Pangakis & Wolken (<https://arxiv.org/pdf/2406.17633>, Table 1): 14 tasks, 1,000 GPT-4 labels
  each.
  - The BERT *student* came within **0.006 median F1 of its GPT-4 teacher** and 0.039 of the same
    model trained on human labels.
  - Labelling 1,000 items with GPT-4 cost $15, against $8,990 to run GPT-4 over the whole 6.2M-text
    corpus (Table A1).
- Hansen et al. (NBER w31007, Table 4, <https://www.nber.org/system/files/working_papers/w31007/w31007.pdf>)
  used human labels, not LLM labels, but show the cost end state.
  - A fine-tuned DistilBERT detecting remote work in postings reached F1 0.97, against 0.94 for
    GPT-4o, on the audit sample.
  - It classified about 550M postings for $3,147, about **$0.0000057 per posting**.
- CLIMB (<https://arxiv.org/pdf/2509.15786>): a classifier trained on gpt-4o-mini's labels over
  Indeed postings agreed with its teacher 93.58%.
- Gilardi et al. (PNAS 2023, <https://arxiv.org/pdf/2303.15056>): zero-shot annotation beat MTurk
  by about 25 pp at under $0.003 per annotation.

**Cost per posting.** List prices serve as a yardstick for whatever model the router proxies.
They were read on 2026-09-24 from [OpenAI](https://developers.openai.com/api/docs/pricing),
[Anthropic](https://platform.claude.com/docs/en/about-claude/pricing) and
[Google](https://ai.google.dev/gemini-api/docs/pricing). Assumptions: 600 input tokens (title plus
a truncated description plus a 25-family prompt) and 10 output tokens.

| Model | $ per 1M in / out | $ per posting | 10k postings/day | 410k backfill |
| --- | --- | --- | --- | --- |
| gpt-5-nano | 0.05 / 0.40 | $0.000034 | $0.34 | $13.94 |
| Gemini 2.5 Flash-Lite | 0.10 / 0.40 | $0.000064 | $0.64 | $26.24 |
| gpt-5-mini | 0.25 / 2.00 | $0.000170 | $1.70 | $69.70 |
| Claude Haiku 4.5 | 1.00 / 5.00 | $0.000650 | $6.50 | $266.50 |

- OpenAI, Anthropic and Gemini batch APIs all halve these prices.
- Reasoning tokens bill as output: 300 of them would make gpt-5-mini about 4.5× dearer.
- A requested rationale of about 150 tokens roughly doubles Haiku's cost.
- Three-way self-consistency triples any row.
- Keying calls by distinct (company, normalised title) rather than by posting divides every row by
  HeadStart's duplication factor, which has not been measured.

The dollar cost is small at every scale. What actually limits an LLM step is that the router is
not reachable from CI.

### 7. kNN over labelled exemplars, and retrieve-then-rerank

Official-statistics coders have compared nearest-neighbour coding with trained classifiers for a
decade. Their standard metric is the **production rate**: the share of cases coded automatically at
a given accuracy, with the rest sent to humans. It is the right lens for a cascade.

- **Gweon et al. 2017** (<https://d-nb.info/1187197882/34>).
  - At full automation, a modified nearest-neighbour method and a hybrid method both reached about
    65%, against 59% for an SVM.
  - **At an 80% accuracy target, nearest-neighbour coded 81% of cases automatically, against 60%
    for the SVM.**
- **Schierholz & Schonlau 2021.** On 1,286 German codes at full automation (Table 4):
  - XGBoost with a coding index 77.26, adapted 1-NN 74.15.
  - Pooled data: 69.08 vs 63.82. Out of domain: 56.20 vs 53.29.
  - At low and medium production rates, only XGBoost and adapted NN were "close to optimal".
- **Bethmann et al. 2014** (<https://www.statcan.gc.ca/sites/default/files/media/14291-eng.pdf>,
  Table 1): matching against 300k previously coded answers coded 67.4% of cases at 90% agreement.
- **SOCcer** (NCI).
  - [2016, Table 1](https://pmc.ncbi.nlm.nih.gov/articles/PMC4871757/): 44.5% at 6 digits, 76.3% at
    2 digits.
  - [v2, 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10324641/): 50% and 73%. **Agreement rises
    with the top score and with the gap between the top two scores.**
  - In [assisted coding](https://pmc.ncbi.nlm.nih.gov/articles/PMC13181392/), experts chose
    SOCcer's top-1 code for 48–50% of jobs and a top-3 code for 62–64%.
- **Carotene** (<https://arxiv.org/abs/1606.00917>) is a production cascade: an SVM picks the SOC
  major group, then a Lucene kNN (k=20) over labelled titles picks the fine class. The follow-up
  ran kNN over 1,002,737 labelled titles and found k=1 "a good choice"
  (<https://arxiv.org/abs/1609.06268>). StepStone also found k=1 optimal (above).
- **ONS ClassifAI** (<https://github.com/datasciencecampus/classifai>). A kNN / RAG coder over
  previously coded examples. It is explainable (the neighbours are the reason) and editable without
  retraining (fix an exemplar and every future neighbour follows). No accuracy is published.
- **Margin thresholds.** A two-step KldB coder proposes thresholding on the top-1 minus top-2 score
  gap, not on an absolute confidence (<https://arxiv.org/abs/2607.20101>, Tables 4–5).
  HeadStart's own critique measured exactly this margin: 52% of tech rows sit within 0.02.

**Verdict of this literature.** kNN and supervised boosting finish within a few points of each
other at full automation. kNN wins when the system may abstain on hard cases. Its explanations are
concrete ("these five labelled postings"). A wrong exemplar is fixed by editing data, not by
retraining.

### 8. Hierarchical and multi-label assignment

- **Official coding is single-label by rule.**
  - [SOC 2018 principles](https://www.bls.gov/soc/2018/soc_2018_class_prin_cod_guide.pdf):
    - Principle 1: "Each occupation is assigned to only one occupational category."
    - Guideline 2: a job spanning several occupations goes to the one needing the highest skill,
      then the one where most time is spent.
    - Guideline 5: a worker is coded as a supervisor only if at least 80% of the time is
      supervisory.
  - The [ISCO-08 guide](https://www.ilo.org/media/360471/download) gives an order of precedence:
    managerial tasks, then skill level, then the predominant task, "operationalized … by selecting
    the first job title given". It warns that "Manager" in English titles needs caution.
  - [ESCO](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/international-standard-classification-occupations-isco)
    maps each occupation to exactly one ISCO-08 code.
- **Real multi-occupation ads are rare under a coarse, function-level scheme.** Experts flagged
  multiple codes on 59 of about 10k Polish ads, 0.6% (Beręsewicz et al., Table 3).
- **The counterpoint is Indeed.** Its internal DAG of more than a thousand occupations labels 45%
  of test jobs with more than one occupation (§2). Multi-label becomes common once a taxonomy is
  fine and overlapping, for example "Java developer" alongside "backend developer". That is an
  argument for keeping families coarse and single-axis, and pushing overlapping specialisations
  into facets.
- **Hierarchy helps a little.**
  - Top-down decoding adds 1–2 pp over flat (Beręsewicz et al.).
  - A hierarchical similarity-graph model beat a flat sentence-transformer by 6 pp at SOC major
    group, 0.948 vs 0.886 (CareerBuilder, SIGIR 2025, Table 1, <https://arxiv.org/abs/2507.09949>).
- **What "QA Tech Lead (Python)" becomes under these rules:**
  - one function label (`qa-test`, the predominant task);
  - a seniority band (lead, carried on HeadStart's existing band axis);
  - a technology facet (Python).

  Single-label *function* with separate *level* and *stack* fields is also how LinkedIn (one
  function per title, seniority as its own field) and Lightcast (one parent per specialised
  occupation) store it. Most of the multi-label need disappears once those axes are pulled out of
  the family list.

### 9. Title-only vs title+description, and description boilerplate

- **The title carries most of the signal.**
  - In the only direct ablation found, a fine-tuned BERT scored 58.1% title-only against 49.9%
    description-only, and combining all fields added about 1 pp (Rony & Patman, Tables 5.3 and 5.8,
    <https://arxiv.org/abs/2511.23057>; a preprint).
  - Georgetown CEW's audit of Burning Glass (now Lightcast) coding found over 80% accuracy at 2-digit
    SOC and about 73% at 6 digits. It notes that "since job ads almost always carry a job title,
    occupational coding … has greater accuracy than industry coding"
    (<https://cew.georgetown.edu/wp-content/uploads/2014/11/OCLM.Tech_.Web_.pdf>).
- **Operational systems are title-first.** Cedefop matches the title against ISCO/ESCO titles
  first, and falls back to ML over title and description (<https://www.cedefop.europa.eu/files/4172_en.pdf>).
- **The description helps as a complement, not a substitute.**
  - The ESCO team reports that title plus description beats title alone for occupation suggestions
    (<https://esco.ec.europa.eu/en/about-esco/data-science-and-esco/machine-learning-assisted-mapping-multilingual-occupational-data-esco-part-1>).
  - A fine-tuned classifier did better on the whole ad than on its job-description zone alone
    (Gnehm & Clematide, fn. 10).
- **Boilerplate is a large share of an ad.**
  - Zoning Swiss ads found 17.2% of tokens in the company description, 25.2% administrative, and
    only 32.4% in the job description. A BiLSTM-CRF zoned tokens at 0.91 accuracy (Gnehm &
    Clematide, Table 9).
  - A paragraph relevance filter removing company info, legal text and promotion reached 0.96
    accuracy (CareerBERT, <https://arxiv.org/abs/2503.02056>).
- **Boilerplate degrades whole-description embeddings.**
  - For duplicate detection, embedding the whole description scored F1 0.63, against 0.79–0.90
    for skill-based text. The authors attribute this to "overlapping boilerplate text" from the
    same company (Engelbach et al. 2024, Table 1, <https://arxiv.org/abs/2406.06257>).
  - Summarising a posting with an LLM before embedding lifted zero-shot ISCO HR@1 from 0.23 to 0.43
    (LLM4Jobs, Table 1).
  - HeadStart's stored vectors are whole-description embeddings, and its critique found
    single-employer clusters. This is the same effect.
- **Gaps.** Removing boilerplate by how often text repeats across one employer's postings is
  standard web-IR practice (Bar-Yossef & Rajagopalan, WWW 2002,
  <https://dl.acm.org/doi/10.1145/511446.511522>; abstract only). **No primary source applies it
  to job-ad occupation classification, and none directly measures a classifier gain from removing
  company boilerplate.**

### 10. Stability over time

Two different stabilities matter, and they need different tools.

**Series stability when the coder or taxonomy changes.** Agencies treat this as a planned event:

- **Census bridgecoding.** For the 2010→2018 occupation codes, about 2 million ACS cases from four
  years were double-coded. This produced per-code conversion rates. For example, old 1020 split
  into Software Developers (0.8988) and QA Analysts and Testers (0.1012)
  ([ACS TP78](https://www.census.gov/content/dam/Census/library/publications/2020/demo/acs-tp78.pdf),
  Appendix E).
- **BLS OEWS hybrid years.** Two releases mixed panels coded to SOC 2010 and SOC 2018, using
  temporary hybrid codes such as 15-1256 before the first pure release
  ([BLS](https://www.bls.gov/oes/soc_2018.htm)).
- **BLS CPS.** Conversion factors come from microdata dual-coded to both systems, and "historical
  data were not revised" ([BLS](https://www.bls.gov/cps/cpsoccind.htm)).
- **Eurostat WIH-OJA stores the raw ad text.** Ontology and classifier changes are re-applied
  "across the entire time window" (annex §15.2, §17).
- **ONS SOC2020 is the cautionary tale.**
  - A coding-index flaw went live in January 2021 and was found only in July 2022, by comparing
    old- and new-scheme codes for the same people.
  - Fixing it revised 2.5 years of LFS data, and 42% of unit groups still moved
    ([ONS 2022](https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/articles/theimpactofmiscodingofoccupationaldatainofficefornationalstatisticssocialsurveysuk/2022-09-26)).
  - SOC2020 also reused code numbers with new meanings: 2134 went from IT project managers to
    programmers ([SOC2020 Vol 1, Table 5](https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/soc2020volume1structureanddescriptionsofunitgroups)).
- **BLS SOII.** When the injury taxonomy moved to OIICS v3.02 in 2023, training data had to be
  crosswalked, and the automatic share fell from about 92% to 81%
  ([BLS](https://www.bls.gov/iif/automated-coding.htm)). A taxonomy change costs a coder accuracy
  until it has fresh labels.

Mapped onto HeadStart:

1. **Dual-run.** Run the old and new assigners side by side for an overlap window and record both
   series. The ledger stores counts, not the ids behind past ticks, so past stock cannot be fully
   recomputed. An overlap window is what makes the splice honest.
2. **Mark the boundary** in the existing `trends_epochs` ledger (ADR-0164) with a
   `classifier_version`, as `centroid_version` is marked today.
3. **Never reuse a family name with a changed meaning.** Rename instead.
4. **Detect coder faults by diffing old and new labels on the same postings.** This is how the ONS
   found its fault, and it is what HeadStart's `role_reassignments` ledger already does.

**Per-posting stability between runs.**

- **Key the assignment on the title, not the vector.** Carotene, Lightcast and the agency coders
  all classify a normalised title string. If the family is a function of (normalised title, and
  company where the title is vague), every copy of a posting agrees by construction. The measured
  11.9% copy disagreement goes to zero, and a re-embed can no longer move a posting.
- **Allow abstention.** Every official scheme has a sink for what cannot be coded well:
  - NIOCCS's 00-9900 Insufficient Information;
  - ONS's not-codable knowledge base;
  - SOC's "All Other" codes.

  For Trends, an explicit "unclassified tech" series keeps totals complete. It also stops forcing
  knife-edge rows into a family, where they add noise.
- **Threshold on the margin, not only on confidence.**
  - SOCcer's agreement rises with the gap between the top two scores.
  - A two-step KldB coder proposes a top-1 minus top-2 threshold.
  - HeadStart's own measured margin distribution (52% within 0.02) says the current method would
    abstain on half the index, which is itself the diagnosis.
- **Hysteresis and stickiness.** Once a key is assigned, keep its label. Change it only on a
  classifier or taxonomy version change, or if new evidence beats the stored label by a clear
  margin. **No primary source found** for hysteresis in occupation coding. It is an engineering
  proposal, justified by the fake-trend-movement constraint.
- **LLM labels need the same versioning.** The router's model is interchangeable, and LLM labels
  vary with model, prompt wording and run (§6). Cache each label with the model and prompt version,
  so that a model swap becomes an explicit relabel and never a silent drift.

### 11. Taxonomy design for tech roles

**How the reference taxonomies cut tech work.**

| Taxonomy | Function (what the job does) | Specialisation (language, platform, vendor) | Seniority / management |
| --- | --- | --- | --- |
| US SOC 2018 ([BLS](https://www.bls.gov/soc/2018/major_groups.htm)) | 15-1200 Computer Occupations: systems analysts, security analysts, research scientists, network and user support, network architects, DBAs, database architects, sysadmins, programmers, software developers (incl. mobile), QA analysts and testers, web developers, web and digital interface designers, all other; plus 15-2051 data scientists and 17-2061 hardware engineers | Only web vs software vs QA; no language codes | Managers are a separate group (11-3021); supervisors are coded with the workers they supervise; code "by work performed" |
| O*NET-SOC 2019 ([data](https://www.onetcenter.org/dl_files/database/db_31_0_csv/occupation_data.csv)) | Adds detail under 15-1299: penetration testers, security engineers, blockchain engineers, computer systems engineers/architects, IT project managers; BI analysts under 15-2051 | A few (blockchain, video games) | None of the 93 titles for 15-1252 has a seniority prefix |
| ISCO-08 / ESCO ([ESCO](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/international-standard-classification-occupations-isco)) | 2511–2529 ICT professionals, 35xx ICT technicians, 1330 ICT service managers | ESCO leaves: embedded and mobile developers under 2514; no ESCO occupation found for DevOps, cloud, SRE or ML engineer | Precedence rule codes managerial tasks first |
| UK SOC 2020 ([Vol. 2](https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/soc2020volume2codingrulesandconventions)) | Function | — | Titles prefixed "senior, principal, chief, head, assistant, deputy, trainee" are "coded as though the prefix words were not present" |
| Lightcast LOT | 42 IT occupations in 8 groups | **179 specialised leaves**, many by language or vendor, *under* the function | Kept in titles; QA Lead and Engineering Manager exist |
| LinkedIn | 26 functions (Engineering, IT, Product Management, Program and Project Management, Quality Assurance, …) | Specialty as its own dimension in the patent | Separate 10-level field |
| Stack Overflow 2025 ([survey](https://survey.stackoverflow.co/2025/developers)) | 34 single-select types: full-stack, back-end, front-end, desktop, mobile, embedded, game, QA/test, AI apps developers; architect; engineering manager; DevOps; data engineer; AI/ML engineer; data scientist; security; sysadmin; cloud infrastructure; SRE; DBA; data/business analyst; applied scientist; support; UX/UI; product manager; project manager; … | None | Engineering manager and senior executive are types; no seniority |

**The consensus is one rule applied everywhere.** The family axis is *function*.

- Seniority is a separate axis. Every source keeps it out of the occupation code, and HeadStart
  already has the band axis for it.
- Management is a function of its own, but it is coded only when managing is the job:
  - SOC's 80% rule for supervisors, and its separate managers group;
  - ISCO's managerial precedence;
  - LinkedIn and Stack Overflow's engineering-manager entries.
- Language, platform and vendor sit *below* the function (Lightcast) or on their own dimension
  (LinkedIn patent), never beside it.

HeadStart's current list breaks this rule four ways:

| Current family | Why it breaks the rule |
| --- | --- |
| `java-development`, `python-development` | languages as siblings of `software-engineering` |
| `tech-leadership` | a level, not a function |
| `cloud-infrastructure`, `enterprise-platform` | a vendor or platform axis |
| `systems-engineering` | a homonym: the critique found a third of it is non-IT systems engineering |

**A sound single-axis tech family list.** This is a proposal to decide on, not a finding. Each
family is anchored to a reference code, so its definition does not rest on a cluster:

| Proposed family | Anchor(s) | Absorbs from today's list |
| --- | --- | --- |
| software-engineering (general, back-end, full stack) | SOC 15-1252; SO back-end/full-stack; LOT Software Developer/Engineer | software-engineering, java-development, python-development |
| frontend-web | SOC 15-1254; SO front-end; LOT Web Developer | web-development |
| mobile | LOT Mobile Applications Developer; ESCO 2514.2.2; SO mobile | mobile |
| embedded-firmware | ESCO 2514.2.1; SO embedded | software half of hardware-embedded |
| hardware-engineering | SOC 17-2061 | hardware half of hardware-embedded |
| qa-test | SOC 15-1253; SO QA/test; LOT Software QA | qa-test |
| devops-cloud-platform (DevOps, SRE, platform, cloud infrastructure) | SO DevOps / SRE / cloud infrastructure; LOT Computer Systems Engineer/Architect | devops, sre-platform, cloud-infrastructure (whether to merge is a measured decision, below) |
| security | SOC 15-1212; O*NET 15-1299.04/.05; SO security | security |
| data-engineering | LOT Data Engineer; SO data engineer | data-engineering |
| database-administration | SOC 15-1242/1243; SO DBA | DBA clusters (a candidate; merge into data-engineering if small) |
| ml-ai-engineering | LOT AI Engineer; SO AI/ML engineer | ai-ml (ML part) |
| data-science-research | SOC 15-2051, 15-1221; SO data scientist, applied scientist | data-science |
| data-analytics-bi | O*NET 15-2051.01; SO data/business analyst; LOT BI | data-analytics |
| enterprise-applications (SAP, Salesforce, ERP/CRM implementation) | SOC 15-1211 systems analysts; LOT BI/vendor leaves | enterprise-platform (kept as a function: configuring packaged business software) |
| network-engineering | SOC 15-1241, 15-1231; ISCO 2523 | network |
| systems-administration-it-operations | SOC 15-1244; ISCO 2522 | it-operations, the IT part of systems-engineering |
| it-support | SOC 15-1232 | it-support |
| architecture | O*NET 15-1299.08; SO architect | architecture |
| ux-ui-design | SOC 15-1255; SO UX/UI; LOT UI/UX | *new*, if the tech filter admits it |
| product-management | LinkedIn Product Management; SO product manager | product-management (strict: PM/PO) |
| program-project-management | O*NET 15-1299.09; LinkedIn Program and Project Management | TPM/IT PM share of product-management |
| engineering-management | SOC 11-3021; SO engineering manager | engineering-management (strict: manages engineers) |
| unclassified-tech | NIOCCS 00-9900 / SOC "All Other" analogue | low-margin rows, pending |
| non-tech | — | non_tech |

What moves off the family axis:

- **Language and framework** (Java, Python, .NET, React, …) → a `stack` facet read from the title
  and description. It is counted like today's watch roles, as an overlay. This is Lightcast's
  specialised-occupation layer.
- **Level** (lead, staff, principal, "tech leadership") → the existing seniority band, plus a
  title-level token if wanted.
- **Named sub-roles** (Forward Deployed, SRE, platform, …) → watch roles, as today.
- **Employer and industry** → never an input to the family.

Which merges are right (devops/SRE/cloud; DBA into data-engineering; ML vs data science) should be
decided by measured human agreement on each pair. A pair two careful humans cannot separate is one
family.

### 12. Added: weak supervision (labelling functions)

Not on the original list, but it matches what HeadStart already has: title regexes.

- Snorkel (Ratner et al., <https://arxiv.org/abs/1711.10160>) treats heuristic rules as noisy
  **labelling functions** of unknown accuracy. A generative label model reconciles their overlaps
  and conflicts, and its output trains an ordinary classifier.
- The abstract reports models built 2.8× faster than with hand-labelling, coming "within an
  average 3.60%" of the performance of large hand-curated training sets.
- For HeadStart, the fifteen watch-role regexes and any family title rules become labelling
  functions instead of final answers. The classifier trained on their reconciled output then
  generalises past the literal patterns.
- It also gives a principled place to add *guards*. A rule "front end → frontend" can abstain when
  the description is grocery retail, where today the Frontend watch role is 33% grocery clerks.
- **Application to occupation coding: no primary source found.**

## Comparison table

Accuracy figures come from different benchmarks, class counts and gold standards. They rank
methods *within* a row's context, not across rows. The costs are per posting; "≈0" means a
vector operation on something already computed.

| Method | Reported accuracy (benchmark, metric) | Cost per posting | Explainability | Stability under small edits | Needs |
| --- | --- | --- | --- | --- | --- |
| **HeadStart today**: nearest k-means centroid on whole-doc retrieval vectors, hand-mapped | 76.7% of title-evident postings land in their named family; 11.9% of copy pairs split (own critique, 2026-09-23 snapshot) | ≈0 (one matmul) | Low: cluster id → hand map | Poor: 52% within 0.02 cosine; re-embed moves rows | A refit and re-curation per version |
| Agency autocoders (NIOCCS, O*NET-SOC AutoCoder) | 56–62% / 55–58% exact over 568 Census codes; computer occupations 47–56% (Laughlin et al. Tables 2, A1, A3) | Free API / commercial | Probability or score | Robust to seniority and typos; brittle on modern specialisations (live probe) | Their taxonomy (SOC) |
| Agency pattern: dictionary → ML → threshold → human | BLS 78.6% SOC 6-digit vs 68.3% human; ONS 0.95 on the 73% matched | Internal | Dictionary hit or probability | Fixed cutoff keeps precision constant | Gold standard, coders for the remainder |
| Commercial title normalisation (Lightcast, LinkedIn) | Lightcast "close to 90%" (no dataset); BGT 2-digit >80%, 6-digit ~73% (CEW audit); LinkedIn occupation P/R 83/83 on hard cases (SIGIR'26 Table 3) | Not public | Normalised title shown | Titles are keyed, so copies agree | A proprietary taxonomy; not usable directly |
| Title embedding + nearest taxonomy label (JobBERT-v2) | MRR .390 on 2,675 ESCO leaves (JobBERT benchmark); ceiling ~65% linkable | 2.6–11.5 ms CPU per *distinct* title (local measurement) | Nearest labels | Siamese title models: typos 1.00, synonyms .84 (Textkernel Table 3) | Taxonomy text only |
| Zero-shot label embedding (definitions / seed titles) | Turrell 76% at 3-digit SOC (n=330); label text .321 vs exemplar centroids .482 MRR@100 (CareerBERT Table 5) | One embedding | Similarity to a label | Margin-dependent; hubness and anisotropy | Rich label text, 0 labels |
| NLI zero-shot | 37.9–45.7 on Yahoo topics (Yin Table 6, general); no occupation evidence | One pass per label: 24× BART-large | Per-label entailment | Fragile to label wording (43.4 → 17.2, Table 7) | Label phrasing |
| SetFit | 62.3 (8/class) → 75.3 (64/class), ≤6 classes (Tunstall Table 2, general) | One small encoder pass | Low | Not reported | 8–64 labels per class |
| Linear head on frozen embeddings | nomic v1 74.1 MTEB classification (general); no occupation evidence with general embedders | ≈0 on stored vectors | Low | Inherits the vector's instability | Hundreds to thousands of labels (no curve for ~25 classes) |
| Fine-tuned small encoder | 0.768–0.778 at 22–34 classes (Gonzalez-Garcia; Gnehm); 71.1% at 568 codes (T5-OCC) | ~$0.0000057 on GPU (Hansen et al.); CPU slower | Calibrated probability | Deterministic given the text | 10⁴+ labels, GPU training |
| TF-IDF / char n-gram linear | Carotene F1 96.1% at 23 SOC majors (silver labels); 58.9–76.4% at 1,286 codes (Schierholz) | ≤60 ms (Carotene) | **High**: n-gram weights | n-gram matching beats exact strings (0.47–0.54 → 0.65, Gweon) | 10³–10⁵ labels |
| kNN over labelled exemplars | NN 65% at full automation, **81% coded at an 80% accuracy target vs 60% for SVM** (Gweon); k=1 macro-F1 .886 (StepStone) | One embedding + ANN lookup | **Highest**: the neighbours | −0.6 to −1.9 pp under edits (StepStone) | Labelled exemplars, editable without retraining |
| LLM, unconstrained prompt | ~19% at 568 codes (T5-OCC paper); P@1 .4875 without retrieval (Achananuparp) | $0.00003–0.00065 at list price | Rationale, not faithful | Poor: run-to-run and prompt sensitivity | A prompt, router access |
| Retrieve-then-LLM-rerank | Major group 0.78–0.83, unit 0.59–0.63 vs human coding (SOCbot Table 1); P@1 .81 (LLM-judged gold) | Same + one embedding | Shortlist + rationale | Better than unconstrained; cache makes it fixed | Taxonomy definitions, retrieval index, router |
| LLM-labelled distillation | Student within 0.006 median F1 of the GPT-4 teacher (Pangakis & Wolken Table 1); LinkedIn in production | Teacher ~$15 per 1k labels (GPT-4 era); student ≈0 | That of the student | Deterministic | A teacher pass plus a human gold audit |
| Weak supervision (Snorkel) | Within 3.6% of large hand-labelled sets (abstract, general) | ≈0 | Rule-level | Deterministic | Labelling functions (HeadStart has 15 regexes already) |
| Hierarchical top-down | +1–2 pp over flat (Beręsewicz) | ≈0 extra | Path through the tree | — | A tree |

## Fit for HeadStart: ranked candidate designs

Four findings from the evidence decide the ranking.

1. **The title is the primary signal, and whole-description vectors carry the boilerplate.**
   - Title-first is what Cedefop, Lightcast, LinkedIn and every agency do.
   - The one ablation found puts title-only above description-only.
   - The critique's single-employer clusters are the boilerplate effect the literature describes.
2. **Supervised assignment beats unsupervised clustering.** Nobody in production assigns
   occupations by nearest unsupervised centroid. The one vendor that clusters (Revelio) clusters
   *titles* with seniority stripped, so copies agree by construction. Exemplars also beat label
   text by about 50% (CareerBERT).
3. **An abstaining cascade beats a single model that must answer.** kNN coded 81% of cases at an
   80% accuracy target against 60% for an SVM. Every agency sends low-confidence cases to a
   fallback coder, and Lightcast and Cedefop run rules or ontology matching before any model.
4. **LLM labels are best used offline and cached.** LinkedIn distils GPT-4 labels into a small
   reranker, and students land within about 0.006 F1 of their teacher. Live LLM output varies
   between runs and prompt wordings, and HeadStart's router is not reachable from CI.

### Rank 1: a title-keyed cascade with LLM-labelled exemplars and sticky labels (recommended)

1. **Key.** Normalise each title into a `title_key`:
   - lowercase;
   - strip seniority prefixes, following UK SOC 2020's rule;
   - strip requisition ids, locations, parentheticals such as "(Locals Only)", and gender markers.

   Where the title is vague ("Engineer II", "Consultant", "Member of Technical Staff"), the key is
   (company, `title_key`). A family is a property of the key, so every copy of a posting agrees,
   and a re-embed cannot move one.
2. **Stage 1: rules.** High-precision title rules, promoted from the watch list and given guards,
   assign a family only when unambiguous. For example, "front end" does not mean frontend when the
   description is grocery retail. This is Lightcast's "rules first" and Cedefop's ontology match.
   The rules double as Snorkel-style labelling functions.
3. **Stage 2: exemplar classifier.** Run kNN, or logistic regression, over labelled exemplar titles
   in a *title* embedding space: JobBERT-v2, or nomic with the `classification:` prefix on the
   title alone. For vague keys, add a description feature: the already-stored vector, or a
   responsibilities-zone embedding. The classifier outputs a family and a **top-1 minus top-2
   margin**.
4. **Stage 3: LLM on low margins only.** Keys below the margin threshold, or whose nearest exemplar
   is far away (a new kind of title), are queued for a retrieval-constrained LLM call through the
   router.
   - The call runs **off CI**, where the router is reachable, as a scheduled batch.
   - The prompt carries the shortlisted families with their definitions, the nearest labelled
     exemplars, the title, and the start of the responsibilities text.
   - The answer is one family or `unclassified-tech`.
   - Answers are cached in HF state as (key → family, model, prompt version, date).
   - **Each answer becomes a new exemplar**, so stage 2 improves without retraining. This is the
     ONS ClassifAI pattern.
5. **Stickiness.** A key's family is frozen until `classifier_version` or the taxonomy changes, and
   `trends_epochs` marks that change. Until the LLM answers, a key sits in `unclassified-tech`, or
   carries stage 2's label marked provisional. That choice is open (see Open questions).
6. **Bootstrapping.**
   - The LLM labels a stratified sample of distinct keys, weighted by posting volume, to seed the
     exemplars.
   - Humans double-label a gold set of about 500–1,000 postings. This measures both LLM–human and
     human–human agreement, and the second number is the ceiling.
7. **Explainability.** Every posting has a checkable reason: "rule R matched", "nearest labelled
   titles were A, B, C", or "labelled by model M on date D".

Why it ranks first: it answers every measured defect.

| Defect | How this design answers it |
| --- | --- |
| Title-evident postings land elsewhere (76.7% recall) | rules and a title classifier |
| Boilerplate-driven clusters | the input is the title, and the description is used only for vague titles |
| Knife-edge margins | abstention plus stickiness |
| 11.9% copy disagreement | key-level labels |
| Mixed axes | the §11 taxonomy |

Its costs are small:

- Encoding *distinct* titles on CPU takes milliseconds each.
- LLM calls go only to new low-margin keys. Even at 10k per day at Claude Haiku 4.5 list price
  that is $6.50 a day, and the real volume is a fraction of that.

Its risks:

- Vague titles still need description evidence.
- A backlog of LLM answers leaves new keys provisional for up to a day.
- The rule set needs versioning like the classifier does.

### Rank 2: retrieve-then-LLM for every new key, cached, with no trained classifier

This is rank 1 without stages 1–2: every new `title_key` goes to the router with a family shortlist.

- **Strengths.** It is the simplest high-accuracy design: the one SOCbot and Achananuparp et al.
  measure, and the shape LinkedIn runs in production (with a distilled reranker in place of a
  hosted LLM). Cost is not the constraint.
- **Weaknesses.**
  - Every new key waits for an off-CI pass, so a provisional label is needed anyway. Building that
    fallback turns it into rank 1.
  - A router model swap relabels everything that is re-asked.
  - The cache is the only source of stability.
  - The explanations are rationales, which are not reliably faithful.

It is the right way to *generate labels* for rank 1, and a reasonable first milestone.

### Rank 3: a supervised head on the stored vectors

Train logistic regression, or kNN, on the existing `search_document:` vectors, using LLM labels.

- **Strengths.** Zero new embedding cost, and the same deployment shape as today's centroid
  matmul. Supervised boundaries follow family definitions, where k-means follows variance, so it
  should beat centroids clearly.
- **Weaknesses.**
  - The input still carries boilerplate.
  - Title-only rows and full-description rows still produce different kinds of vector.
  - A re-embed still moves a posting (median 0.024), unless per-key stickiness is added on top.
  - The prefix is off-card for classification.

Its best uses are a one-day baseline that shows how much a supervised boundary alone recovers, and
a stage-2 feature for vague titles in rank 1.

### Rank 4: zero-shot label or seed-title embedding

Embed family definitions and seed titles, and assign each posting to the nearest.

- **Strengths.** No labels and no LLM.
- **Weaknesses.**
  - Label text is about a third weaker than exemplar centroids (CareerBERT).
  - It is exposed to hubness and anisotropy.
  - There is no evidence for it on about 25 tech families.

Use it to propose the first exemplars for human or LLM review, not as the assigner.

### Not recommended

| Option | Why not |
| --- | --- |
| Refitting k-means (on titles, or on cleaned descriptions) | Still unsupervised; every refit re-bases the series; the boundaries are not the family definitions |
| NLI zero-shot | 24 cross-encoder passes per posting on CPU; fragile to label wording; no occupation evidence |
| SetFit | Evidence only up to 6 classes |
| Fine-tuning an encoder now | Needs GPU training and 10⁴+ labels for small gains at 24 classes; revisit if rank 1's stage 2 plateaus below the human ceiling |

**Independent of the method: redesign the taxonomy first (§11).** A classifier can separate
functions, but it cannot make "java-development vs software-engineering" or "tech-leadership vs
architecture" into meaningful single-label decisions. Also run the change the way agencies do:
dual-run old and new for an overlap window, stamp `classifier_version` in `trends_epochs`, and do
not reuse a family name whose meaning changed.

## Open questions only HeadStart's own data can settle

1. **What is the human ceiling on these families?** Double-label about 500 stratified postings with
   two people, and report agreement per family pair. Pairs below about 0.8 are candidates to
   merge. Every other accuracy number is read against this one.
2. **Title-only vs title plus description, on HeadStart's postings.** The literature has one
   ablation, from a preprint. Measure a title-embedding kNN, a head on the stored vectors, and the
   two combined, against the gold set.
3. **How concentrated are the titles?** How many distinct `title_key`s cover 80%, 95% and 99% of
   postings, and how many *new* keys arrive per day? This sets the labelling budget, the LLM
   volume and how long new keys stay provisional.
4. **How often does one `title_key` truly span families?** Measure how often the same key within
   one company, and across companies, gets different gold labels. This decides where the key must
   include the company, and which titles are vague.
5. **How does the router's model agree with humans, and with itself?** Measure agreement with the
   gold set, the run-to-run agreement of the same prompt at temperature 0, and the effect of
   swapping the router's model. Self-consistency over three runs is a candidate confidence signal
   (Pangakis & Wolken).
6. **`search_document:` vs `classification:` features.** Does re-embedding titles, or a truncated
   responsibilities zone, with the `classification:` prefix beat a head on the stored vectors
   enough to justify the CPU time? No primary source answers this.
7. **Which margin threshold?** Plot accuracy against the share of postings coded automatically (the
   agencies' production-rate curve) to choose it. Check that it does not starve small families of
   labels, which is Cedefop's warning about rare occupations.
8. **How does boilerplate removal affect the description signal?** Measure cross-posting text
   frequency within each employer as a boilerplate detector, and its effect on stage-2 accuracy for
   vague titles. No primary source measures it.
9. **Which taxonomy merges survive measurement?** Test devops/SRE/cloud as one family, DBA inside
   data-engineering, and ML engineering vs data science. Also: is UX/UI design in scope, given
   what the tech filter admits?
10. **Provisional label or `unclassified-tech` while a new key waits for the LLM?** The first
    keeps counts in families but risks a later flip. The second shows an honest "pending" series.
    Measure the typical wait and volume before choosing.
11. **How large is the flip rate under the new design?** Replay a week of ticks through the new
    assigner and count `role_reassignments`. Stickiness should make it near zero outside version
    boundaries.

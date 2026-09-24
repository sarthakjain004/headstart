# Assigning a posting to its Trends role family: a measured bake-off

**Date:** 2026-09-25 · **Status:** measurement (no product code, state or centroids changed) ·
**Data:** local LanceDB served-table snapshot **v654 (2026-09-23)**, 514,163 rows · **Companion:**
`2026-09-24_family-assignment-methods-research.md` (literature survey)

> **What shipped:** [ADR-0215](../adr/0215-a-title-rule-decides-a-role-family-before-the-centroid.md)
> adopts the rules-first cascade with the centroid as fallback (A1a, 0.708 on the fresh holdout), a
> smaller first step than the design this write-up recommends. The ADR says why, and keeps the
> learned title classifier as the next step.

## Short answer

- **Replace the k-means centroid assignment. Every supervised method beats it by 11–18 points.**
  On a fresh, uniform held-out sample (n=250), today's assigner (A0) is right on **0.600
  [0.540, 0.660]** of postings. The three best candidates score 0.76–0.78, each +16 to +18 points
  over A0 in a paired bootstrap (95% CIs exclude zero).
- **The best candidates are a statistical tie. Stability and cost separate them:**

  | Candidate | Fresh holdout accuracy | Copy-pair disagreement |
  | --- | --- | --- |
  | BE: logistic regression on the stored vector plus a JobBERT-v2 title embedding | 0.780 [0.724, 0.828] | 0.2% |
  | E2: logistic regression on the JobBERT-v2 title embedding alone | 0.768 [0.712, 0.816] | 0% by construction |
  | K2: logistic regression on the stored vector, trained on the title rules' labels | 0.760 [0.708, 0.812] | 11.0% |
  | A0: today's centroids | 0.600 [0.540, 0.660] | 11.9% |

  At n=250 none of the top three is significantly better than the others.
- **Title regex rules work better as a teacher than as the final decider.** A rule classifier
  built like `headstart.tech_filter` decides 75% of the corpus at 0.79 precision (fresh). On
  those same rows the learned models are at least as accurate (BE 0.841, K2 0.820, n=189). A model
  trained only on the rules' own labels (K2) matches or beats every rules-first cascade.
- **Recommended design:** a title-first supervised classifier (BE, or E2 if the description
  vector is not wanted), trained on the rules' labels plus a small human gold set. It needs:
  - an explicit abstain / `unclassified-tech` output;
  - per-posting stickiness;
  - the rules kept as labelling functions and as the human-readable explanation.

  It also needs the family-list fixes below. Details are in "Recommended design".
- **The family list itself costs accuracy.**
  - About a third of postings (fresh: 34.8%, n=250) have a legitimate second family.
  - About 38% of the best candidates' errors are that second family.
  - `tech-leadership` is about 1% of the corpus once function takes precedence over level.
  - About 38% of what A0 calls `systems-engineering` is non-tech.
  - About 23% of the served table is not tech at all (fresh: 22.8%, n=250).
- **The first test split is contaminated for the rule-based candidates, and this document says
  so.** The rules were written after the labeller had read all 605 gold rows. Test-split
  numbers for A1 were therefore about 5–11 points optimistic, so the headline numbers come from a
  fresh holdout that was drawn and labelled only after every candidate was frozen. See "The
  contamination, and how it was caught".

## Why this was done

A measured critique of Trends' role families (2026-09-24, same snapshot) scored the current
assignment 3/10:

- The method: each posting goes to its nearest of 72 k-means centroids, fitted on 768-dim
  `nomic-embed-text-v1.5` vectors of title + full description, then mapped by hand onto 24
  families plus `non_tech`.
- Only 76.7% of title-evident postings landed in the family their title names.
- Four clusters were each one employer's boilerplate.
- The median margin to another family was 0.019.
- 11.9% of same-company, same-title copy pairs (n≈22.6k) split across families.

This bake-off measures replacements against hand-labelled truth.

**Constraints.**

- No LLM calls: every label was assigned by hand.
- The only local embedding was a one-off, CPU-only pass over at most ~20k short titles; no
  description was embedded.
- The production `embed_run` step was never run.
- Nothing tracked changed.

## Method

### Data

- **Population.** Every row of the served table snapshot v654 (2026-09-23, 514,163 rows).
  Today's `role_assignments` (HF, fetched 2026-09-24 22:04 IST) holds tech-family rows only, so it
  cannot say which non-tech rows are live. The population is therefore the whole snapshot. About
  89% of today's live ids are in it.
- **Stored vectors and descriptions.** Taken from the snapshot itself (`vector`, `description`).
  A0 is recomputed from the committed centroids (`centroid_version` 2) and family map. The
  recomputation matches the pipeline's own family on 99.99% of joined rows.
- **Copy pairs.** Rebuilt exactly as the critique built them: the first two tech rows of every
  (company, title) group. This gives 22,656 non-identical pairs (the critique had 22,646), with
  A0 disagreement 11.9%.

### Gold labels

1. **A rubric, frozen before sampling.** It covers the 24 current families plus non-tech, with
   explicit precedence for multi-axis titles:
   1. tech or not;
   2. people management beats everything;
   3. function beats language, level and "architect" (so **"QA Tech Lead (Python)" is
      `qa-test`**);
   4. a named language or stack beats the generic SWE catch-all;
   5. level-only titles go to `tech-leadership` / `architecture`;
   6. a vague title is decided by its department, then its description.

   Nine clarifications arose during labelling and were applied to all rows. Examples:
   integration middleware (TIBCO, MuleSoft, Apigee) is `enterprise-platform`; PLC/SCADA and
   technical writing are non-tech with a "forced" flag.
2. **The first gold sample (605 rows)**, seed 20260925, drawn in four parts:
   - 100 uniform rows;
   - every A0 family topped up to 19 rows;
   - 100 extra knife-edge rows (cross-family margin < 0.01);
   - 30 copy-pair partners.

   It was split **by copy group** 2:1 into test (404) and dev (201).
3. **Labelling.** Each row was labelled from its title, department and a ~1,000-character excerpt
   (the opening 200 characters plus 800 from the first role heading). Every label line echoes the
   title, and a script fuzzy-matched every echo to the row's real title: 605/605 aligned. Each row
   records a primary family, an optional secondary (225/605 have one), a "the list forced a bad
   choice" flag (23/605) and a note.
4. **Self-consistency.** 60 rows were relabelled blind a day later: 60/60 agreed. This measures
   one labeller's consistency, not inter-annotator agreement.
5. **The fresh holdout (250 rows)**, seed 20260926.
   - Uniform over the snapshot, excluding every copy of a gold row.
   - Drawn and labelled after every candidate was frozen, blind to all predictions.
   - Being uniform, its raw accuracy estimates corpus accuracy directly, with no reweighting.
   - **This is the headline evaluation.**

### Candidates

| Id | Candidate | Input | Trained on |
| --- | --- | --- | --- |
| A0 | Current centroids v2 + hand family map | stored vector | — |
| A1a | Title/department regex rules → A0 for undecided | title, dept | hand-written (dev + corpus lists) |
| A1b | Rules only; generic software cue → SWE; else **unclassified** | title, dept | hand-written |
| A1c | Rules → best learned model by dev CV (B) for undecided | title, dept, vector | rules + B |
| A1d | Rules → K2 for undecided | title, dept, vector | rules + K2 |
| A1e | Rules → BE for undecided | title, dept, vector, title emb. | rules + BE |
| B / B2 | Logistic regression / 512-unit MLP on stored vectors | stored vector | silver + dev |
| C | kNN (k=15, cosine) on stored vectors | stored vector | silver + dev |
| D1–D3 | Same production model, title only (`search_document: {title}\n\n`, as `doc_prep.build_doc` makes for a description-less posting): seed prototypes / logreg / kNN | title emb. (nomic) | seeds / silver titles + dev |
| E1–E3 | As D, with TechWolf **JobBERT-v2** (model card's `anchor` title branch, 1024-d) | title emb. (JobBERT) | as D |
| F1–F3 | Zero-shot: definitions (as `search_query:`) vs stored vectors / vs title embedding; seed-title prototypes vs stored vectors | vector or title emb. | none |
| G / G2 | Char 2–5-gram TF-IDF logreg on title / title + department | text | silver + dev |
| H | Word TF-IDF logreg on the description's role section only (company blurb and benefits cut by heading rules) | description | silver + dev |
| K1 / K2 | Weak supervision: char TF-IDF on title / logreg on stored vectors, **trained on the A1 rules' own labels** (≤3,000 rule-decided rows per family) | text / vector | A1 labels + dev |
| BE | One logreg on [stored vector ; JobBERT-v2 title] | vector + title emb. | silver + dev |

**Silver labels.** The critique's per-family title cues, aligned to the rubric, plus a
generic-SWE cue, a non-tech cue and a narrow systems-engineering cue. 274,032 snapshot rows are
title-evident. 49,701 were sampled (≤2,000 per family), excluding every copy of a test row. The
dev gold rows carry weight 5.

**Title embeddings.** 10,802 distinct strings per model on first pass, and 250 more for the
fresh holdout, all CPU-only: nomic 3.93 ms/string, JobBERT-v2 2.75 ms/string on 8 threads.

**Reproducibility.** Every model was refit for the fresh holdout. Each refit reproduced its saved
test predictions exactly (100%).

### Metrics

- **Accuracy.** Share of rows whose predicted family equals the gold primary family.
  `UNCLASSIFIED` counts as wrong.
- **Lenient accuracy.** The prediction equals the gold primary or the gold secondary.
- **Macro-F1.** Over the gold families present.
- **Bootstrap.** 2,000 row resamples; percentile 95% CIs; paired bootstrap for differences.
- **Test-split weighting.** Test-split figures are also post-stratified to the snapshot by
  (A0 family × knife-edge) cells.
- **Copy-pair disagreement.** Measured on both copies of the 22,656 pairs. For title-only models
  it is 0 by construction, because 97% of pairs have byte-identical titles and 100% match ignoring
  case. BE was measured on the 4,652 pairs whose titles were both already embedded.
- **Cost.** Measured CPU inference time per posting, excluding one-off training.

## Results

### Main table

The fresh holdout (n=250, uniform, clean) is the headline. The first test split (n=404,
stratified, knife-edge oversampled) is shown because it was the briefed split. Rule-based
candidates are contaminated on it (†).

| Candidate | **Fresh acc. [95% CI]** | Fresh macro-F1 | Test acc. [95% CI] | Copy-pair disagreement | CPU ms/posting | Explainability |
| --- | --- | --- | --- | --- | --- | --- |
| BE vector + JobBERT title | **0.780 [0.724, 0.828]** | 0.773 | 0.703 [0.658, 0.748] | 0.2% (4,652-pair subset; A0 11.3% on same) | 2.75 per new title + ≈0 | Low–medium: probabilities; can list nearest titles |
| E2 JobBERT title logreg | **0.768 [0.712, 0.816]** | 0.766 | 0.708 [0.666, 0.750] | 0% (title only) | 2.75 per new title | Medium: title-driven |
| K2 vector logreg on rule labels | **0.760 [0.708, 0.812]** | 0.765 | 0.715 [0.671, 0.757] | 11.0% | ≈0 (stored vector) | Low |
| A1e rules → BE | 0.744 [0.692, 0.796] | 0.708 | 0.777 [0.733, 0.817] † | ≈rules + BE (not measured on all pairs) | 0.06 + BE | High for 75% of rows |
| A1d rules → K2 | 0.740 [0.684, 0.792] | 0.709 | 0.800 [0.762, 0.837] † | 2.9% | 0.06 | High for 75% of rows |
| B vector logreg | 0.736 [0.680, 0.788] | 0.730 | 0.691 [0.644, 0.738] | 10.9% | ≈0 | Low |
| B2 vector MLP | 0.724 [0.664, 0.776] | 0.698 | 0.678 [0.634, 0.720] | 12.2% | ≈0 | Low |
| A1c rules → B | 0.724 [0.664, 0.780] | 0.687 | 0.790 [0.752, 0.829] † | 2.9% | 0.06 | High for 75% of rows |
| K1 title TF-IDF on rule labels | 0.708 [0.652, 0.760] | 0.690 | 0.723 [0.681, 0.770] † | 0% | 0.02 | Medium: n-gram weights |
| A1a rules → A0 | 0.708 [0.652, 0.764] | 0.683 | 0.787 [0.748, 0.827] † | 2.7% | 0.06 | High for 75% of rows |
| H role-section TF-IDF | 0.664 [0.604, 0.724] | 0.637 | 0.663 [0.619, 0.706] | 20.2% | 0.61 | Medium |
| D2 nomic title logreg | 0.664 [0.608, 0.720] | 0.676 | 0.644 [0.599, 0.688] | 0% | 3.93 per new title | Medium |
| C vector kNN | 0.660 [0.600, 0.716] | 0.652 | 0.644 [0.599, 0.691] | 13.4% | 0.35 | High: named neighbours |
| G2 title+dept TF-IDF | 0.660 [0.600, 0.716] | 0.659 | 0.641 [0.592, 0.691] | 0.4% | 0.02 | Medium |
| E3 JobBERT title kNN | 0.656 [0.600, 0.712] | 0.653 | 0.641 [0.594, 0.688] | 0% | 2.75 + lookup | High |
| G title TF-IDF | 0.652 [0.592, 0.712] | 0.663 | 0.629 [0.584, 0.676] | 0% | 0.02 | Medium |
| E1 JobBERT seed prototypes | 0.636 [0.580, 0.696] | 0.666 | 0.649 [0.599, 0.693] | 0% | 2.75 | High |
| A1b rules only (24% unclassified) | 0.600 [0.544, 0.660] | 0.671 | 0.750 [0.705, 0.790] † | 0.14% | 0.06 | **Highest**: named rule |
| **A0 centroids (today)** | **0.600 [0.540, 0.660]** | 0.604 | 0.537 [0.488, 0.587] | **11.9%** | ≈0 | Low: cluster id |
| D3 nomic title kNN | 0.580 [0.524, 0.640] | 0.573 | 0.582 [0.532, 0.629] | 0% | 3.93 + lookup | High |
| F1 zero-shot definitions vs vectors | 0.516 [0.452, 0.576] | 0.600 | 0.515 [0.465, 0.562] | 19.0% | ≈0 | Medium |
| D1 nomic seed prototypes | 0.516 [0.452, 0.576] | 0.583 | 0.520 [0.473, 0.569] | 0% | 3.93 | High |
| F2 zero-shot definitions vs title | 0.392 [0.332, 0.452] | 0.459 | 0.458 [0.411, 0.505] | 0% | 3.93 | Medium |
| F3 zero-shot seed titles vs vectors | 0.392 [0.332, 0.452] | 0.543 | 0.525 [0.475, 0.577] | 15.8% | ≈0 | Medium |

**Paired differences on the fresh holdout** (n=250, 95% CI):

- **Against A0:** BE +18.0 [+11.2, +25.2]; E2 +16.8 [+9.6, +23.6]; K2 +16.0 [+8.4, +22.8];
  A1d +14.0 [+6.8, +20.8]; B +13.6 [+7.6, +20.0]; A1c +12.4 [+5.6, +19.2]; A1a +10.8 [+4.8, +16.8].
- **Among the leaders, all within noise:** BE − B +4.4 [−0.4, +9.6]; E2 − B +3.2 [−2.0, +8.8];
  K2 − B +2.4 [−2.0, +6.8]; BE − A1d +4.0 [−0.8, +9.2]; A1d − K2 −2.0 [−6.0, +1.6];
  A1d − A1a +3.2 [−0.4, +6.8].
- **Design choices that are *not* noise:**
  - logistic regression over kNN on the same features: E2 − E3 +11.2 [+6.8, +16.4];
    B − C +7.6 [+2.8, +12.8];
  - JobBERT-v2 over nomic on titles: E2 − D2 +10.4 [+5.6, +15.2].
- **Test split:** the same ordering among non-rule candidates. B − A0 is +15.3 [+10.6, +20.3].

**Non-tech detection** (fresh, 57 gold non-tech rows):

| Candidate | Recall | Precision | Tech rows called non-tech |
| --- | --- | --- | --- |
| BE | 0.70 | 0.89 | 2.6% |
| E2 | 0.70 | 0.85 | 3.6% |
| K2 | 0.56 | 0.97 | 0.5% |
| A1d | 0.56 | 0.89 | 2.1% |
| A0 | 0.63 | 0.75 | 6.2% |

**Cost and pipeline needs.**

| Candidate family | CPU cost | What the pipeline needs |
| --- | --- | --- |
| A0, B, K2 | ≈0 ms: one matmul on the already-stored vector | B/K2 need training labels and a retrain on taxonomy change; A0 needs a centroid refit and hand re-curation |
| A1 | 0.06 ms | A rule file (59 rules, 508 regex alternatives) and a labelled regression set |
| E / BE | 2.75 ms per *new distinct* title | One JobBERT-v2 pass (445 MB model) per new title on CI: seconds per day at 5–10k new postings |
| D | 3.93 ms per new title | Same shape as E |
| H | 0.61 ms | Description text plus a 300k-feature vocabulary |
| C | 0.35 ms | A 50k-vector exemplar store |

### Stability

- **Copy-pair disagreement.**
  - Title-driven models are copy-stable almost by construction: E\*, D\*, G, K1 at 0%; A1b 0.14%;
    BE 0.2%.
  - Stored-vector models are not: B 10.9%, K2 11.0%, C 13.4%, H 20.2%, A0 11.9%.
  - Cascades inherit their fallback's instability on the ~25% of rows the rules leave undecided:
    A1a–d 2.7–2.9%.
- **Rows within copy noise of switching.** This is the share of copy-A rows whose top-2 margin is
  below the method's own median copy-to-copy score change.

  | Candidate | Share within copy noise |
  | --- | --- |
  | A0 | 44.7% (median copy noise 0.018) |
  | F1 | 52.6% |
  | F3 | 55.1% |
  | H | 11.9% |
  | C | 10.0% |
  | B | 4.7% |
  | K2 | 4.2% |
  | B2 | 0.6% |
  | title-only models | 0 |
- **Hysteresis.** Copy A plays the old version and copy B the new; B keeps A's family unless its
  own winner beats A's family by more than m. Pair flip rates:

  | Candidate | m=0 | m=0.1 | m=0.2 | m=0.5 |
  | --- | --- | --- | --- | --- |
  | B | 10.9% | 7.6% | 5.6% | 2.5% |
  | C | 13.4% | 9.1% | 6.7% | 2.9% |
  | H | 20.2% | 15.3% | 12.0% | 5.2% |

  | Candidate | m=0 | m=0.01 | m=0.02 | m=0.05 |
  | --- | --- | --- | --- | --- |
  | A0 (cosine margin) | 11.9% | 6.3% | 4.1% | 1.4% |

  Hysteresis helps, but none of these reaches a title-keyed model's ≈0%.
- **Group voting per (company, normalised title) hurts accuracy.** Every member of a test row's
  group was predicted (10,145 snapshot rows) and the group vote replaced each row's own
  prediction. Test accuracy fell for every candidate tried:

  | Candidate | Accuracy change |
  | --- | --- |
  | A1c | −4.2 [−6.4, −2.0] |
  | B | −3.2 [−6.2, −0.5] |
  | G2 | −2.5 [−4.7, −0.2] |
  | A0 | −2.0 [−4.7, +0.7] |

  The normaliser drops qualifiers after " - " and inside parentheses. Some company-title keys
  genuinely span families: HCLTech's "Senior Technical Lead" covers SAP, iOS, QA and SRE jobs.
  Stability should come from a title-driven model or per-posting stickiness, not from voting over
  a normalised key.

### Title rules in depth (A1a / A1b / A1c, plus A1d / A1e)

A1 is built in `headstart.tech_filter`'s shape, extended to families:

1. **Negative rules** (non-software disciplines, trades, retail, sales, design and writing) make a
   row non-tech unless a *strong* family cue also fires.
2. **Positive cues** are grouped into precedence classes: people management > product/program
   management > support > specialty > language > cloud platform word > web-stack word >
   level/architect > generic software. Within a class, the first-named cue wins.
3. **Department cues** apply only when the title has no cue. Org-only departments ("Security &
   IT", "All", "Company") are ignored, in the spirit of ADR-0068.

Size: 59 rules (38 positive cues, 10 negative rule groups, 11 department cues) with 508 top-level
regex alternatives. For comparison, `tech_filter` has 154 strong terms in 20 compiled regexes
with about 336 top-level alternatives.

**Coverage tiers and precision.** Corpus shares are exact over v654 (514,163 rows). Precision is
on the fresh holdout, whose uniform shares agree with the corpus.

| Tier | Corpus share | Fresh share (n=250) | Fresh precision | Test precision † |
| --- | --- | --- | --- | --- |
| Decided by one specific family cue (maps to one family on the title alone) | 33.6% | 36.4% | 0.824 (n=91) | 0.819 (n=188) |
| Several family cues, resolved by precedence | 18.9% | 18.4% | 0.783 (n=46) | 0.863 (n=117) |
| Generic-SWE default ("Software Engineer", "Developer", "SDE", "Member of Technical Staff", …) | 10.8% | 13.2% | 0.697 (n=33) | 0.750 (n=16) |
| Negative rule → non-tech | 11.2% | 7.2% | 0.833 (n=18) | 0.944 (n=36) |
| Department cue | 0.7% | 0.4% | 1/1 | 2/3 |
| Undecided (→ fallback, or unclassified) | 24.7% | 24.4% | — | — |

**Fallback accuracy on the 61 fresh rows the rules leave undecided.**

| Variant | Fallback | Accuracy on undecided rows |
| --- | --- | --- |
| A1a | A0 | 0.443 |
| A1c | B | 0.508 |
| A1d | K2 | 0.574 |
| A1e | BE | 0.590 |
| A1b | — | 0 (unclassified by design) |

**Overall accuracy by variant.**

| Variant | Fresh accuracy | Note |
| --- | --- | --- |
| A1a | 0.708 | |
| A1b | 0.600 | precision 0.794 on the 75.6% it decides |
| A1c | 0.724 | |
| A1d | 0.740 | |
| A1e | 0.744 | |

**On the rows the rules decide, the models they could fall back to are at least as good.** Rules
score 0.794 on those 189 fresh rows. On the same rows K2 scores 0.820 (−2.6 [−7.9, +2.6]), BE
0.841 (−4.8 [−9.5, 0.0]) and E2 0.831 (−3.7 [−9.0, +1.1]).

This is post-hoc, and no design was chosen on it. The rules remain the most explainable
candidate, and they are the cheapest source of training labels: K2 learned only from them.

**False-positive classes the regexes hit**, over snapshot v654, with the final rules:

- **Stopped by a negative rule.** 6,676 rows carried a weak family cue that a negative rule
  overrode. The largest classes:

  | Title class | Cue that would have claimed it | Rows |
  | --- | --- | --- |
  | Building, highway and environmental "Project Manager" | product-management | 1,108 |
  | "Business Developer" | software-engineering | 636 |
  | "CNC Programmer" and similar | software-engineering | 518 |
  | "Manufacturing/Mechanical/Process Engineering Manager" | engineering-management | 607 |
  | Hotel "(Assistant) Director of Engineering" | engineering-management | 557 |
  | "Manufacturing / Flight / Mechanical Test Engineer" | qa-test | 428 |
  | "Substation Engineering Manager" | engineering-management | 409 |
  | "Transmission Planning Project Manager" | product-management | 376 |
  | "Content/Training Content Developer" | software-engineering | 201 |
  | Data-centre construction "Project Manager" | product-management | 159 |
  | "PLC Programmer" and "Control Systems Developer" | software-engineering | 149 |
- **Named probes: what A1 answers for each class.**

  | Title class | Rows | A1's answer |
  | --- | --- | --- |
  | Grocery/retail "Front End" clerks | 886 | 67% non-tech, 20% undecided, **8% still web-development** |
  | Apparel/food "Product Developer" | 555 | 35% non-tech, 35% undecided, 7% SWE, 5% engineering-management |
  | "Business Developer" | 505 | 92% non-tech |
  | "Water/Civil Infrastructure Engineer" | 63 | 100% non-tech |
  | Hotel "Director / Assistant Director of Engineering" | 494 | 84% non-tech, 16% engineering-management |
  | Hospitality "Chief Engineer" | 843 | 99% non-tech |
  | Construction "Project Engineer" | 6,428 | 99% non-tech |
  | Industrial "Controls Engineer" | 3,228 | 84% non-tech |
  | Crowdwork / data entry | 1,280 | 57% non-tech, 35% undecided, **7% ai-ml** |
  | Unqualified "Test Engineer" | 1,519 | **97% qa-test**, though most are hardware or manufacturing test (the rule scored 0/5 on test) |
  | Unqualified "Systems Engineer" | 2,284 | 95% undecided |
  | Physical security officers | 35 | 54% security-engineering |
- **Residual false positives seen on the fresh holdout:**
  - the `ai-ml` cue on company descriptors ("AI Designer", "Agentic Search", "Ai & Data
    Platforms");
  - "Flutter" read as the mobile framework in *Flutter Entertainment*;
  - non-software "Quality Engineering" and call-centre "QA Lead";
  - a bare "Developer" in "eLearning Developer" and "CDM Programmer";
  - construction TPMs and PMs;
  - a food-manufacturing "Director Engineering".
- **Negative rules the other way (true tech called non-tech):**
  - "Circuit Design Engineer" hit by the mechanical "design engineer" rule;
  - "Solutions Architect – Manufacturing" hit by the manufacturing rule;
  - "Sales Manager – Digital Solution Architecture" hit by the sales rule.

**Negative rule groups needed**, with snapshot rows hit:

| Negative rule group | Rows hit |
| --- | --- |
| mechanical-manufacturing | 17,382 |
| civil-building-utilities | 13,952 |
| field-service-trades | 8,980 |
| electrical-power | 6,242 |
| facilities-hospitality | 5,916 |
| design-writing-training | 2,235 |
| sales-marketing-business | 1,282 |
| retail-front-end | 1,221 |
| healthcare-lab-science | 288 |
| physical-security | 174 |

**Reusing `headstart.tech_filter` instead of writing family rules from scratch.** Its tiers map
one to one:

| `tech_filter` tier | Family-rule equivalent |
| --- | --- |
| Rule 0 set-asides (`_STRONG_NOT`: "Cashier (Front End)") | The retail negative rule |
| Rule 1 strong terms (`_STRONG_TERMS`) | Family cues: nearly every term already names one family ("data (engineer\|scientist)", "machine learning", "devops", "site reliability", "firmware", "scrum master", "help desk", "database administrator", the enterprise-platform arms) |
| Rule 2 generic token plus a non-software qualifier (`_NON_SOFTWARE`, `_TRADE_TITLE`, the `_INFRA_CONTEXT` stand-down) | Weak cue versus negative rule |
| Rule 3 generic token alone | The generic-SWE default |
| Rule 4 department, with the `_HIRING_DEPT` / `_NOT_TECH_DEPT` guards and ADR-0068's "a department names the org, not the role" | The department tier |

The cheapest extension:

1. Tag each strong term with a family and return `Verdict(is_tech, reason, family)`.
2. Add only the precedence classes on top.
3. Reuse `_NON_SOFTWARE`, `_TRADE_TITLE`, `_NON_TECH_ROLE` and `_PHYSICAL_SECURITY_TITLE`
   verbatim as the negative rules.

Two differences matter:

- `tech_filter` is recall-biased (ambiguous means tech), while a family label needs precision
  per family plus an honest "undecided".
- The measurements above say the rules' output should *train* the assigner (weak supervision),
  not *be* the assigner.

### Description boilerplate (candidate H)

The heading-based cut found a role-section start in 90% of descriptions and a benefits/about-us/
EEO end in 61–65%. Both cuts fired in 57–62%, neither in 7%. The median kept length was about
2,500 characters (gold rows n=601; training rows n=49,341).

A classifier on the cut text scored 0.664 on the fresh holdout. That is below the plain
stored-vector head (B 0.736), and H had the worst copy stability of all (20.2%). Heuristic
boilerplate removal plus a bag of words is not a route to better families here. Re-embedding
cleaned text was not tested.

### What the family list itself gets wrong

These findings come from the labels, independent of method.

- **A0's families are impure, and the worst are the level and homonym families.** This is gold
  family purity of what A0 puts in each family, over 575 design rows weighted to the snapshot:

  | A0 family | Pure | Main contamination |
  | --- | --- | --- |
  | tech-leadership | 11% | — |
  | systems-engineering | 14% | 38% non-tech, 23% hardware |
  | engineering-management | 20% | 53% non-tech |
  | product-management | 26% | 31% non-tech |
  | web-development | 33% | 23% SWE |
  | sre-platform | 54% | |
  | software-engineering | 54% | |
  | enterprise-platform | 96% | |
  | it-support | 90% | |
  | mobile-development | 89% | |
- **Some families are nearly empty once function takes precedence over level or language.**
  Estimated shares of all snapshot rows (fresh uniform, n=250):

  | Family | Share |
  | --- | --- |
  | tech-leadership | 0.8% |
  | python-development | 0.8% |
  | java-development | 1.2% |
  | systems-engineering | 1.2% |
  | data-science | 0.4% |

  The weighted first sample agrees: tech-leadership 0.1%, python 0.5%, systems-engineering 1.4%.
  `tech-leadership` in particular is a level, not a job; almost every "Technical Lead" names a
  specialty in its title or description.
- **About a third of postings are genuinely two families.** 34.8% of fresh rows (29.7% weighted,
  first sample) carry a defensible secondary. Among the best candidates, 38–40% of test errors
  *are* the gold secondary, so lenient accuracy runs 6–8 points above strict (BE fresh 0.840 vs
  0.780). The commonest axis pairs are function vs function (devops / SRE / cloud, data
  engineering / analytics), non-tech vs function, and function vs level. A single-label list that
  mixes function, language, platform and level forces these arbitrary choices, and they cap
  achievable accuracy.
- **Postings the list has no home for.** 3% (fresh 2.8%, n=250; first sample 23/605). All
  methods fail these; the best score 0.15 on the 20 test examples. They are:
  - DSP and signal-processing algorithm engineers;
  - industrial controls and PLC programmers;
  - technical writers and trainers;
  - designers, including "AI designers";
  - clinical data-system builders;
  - GIS;
  - implementation or delivery engineers.
- **Non-tech creep is large, and the non-tech bucket leaks.**
  - Gold calls 22.8% of served rows non-tech (fresh, n=250; weighted first sample 28.8%
    [21.3, 36.4]).
  - Of the rows A0 puts in a tech family, 10.4% are non-tech.
  - Of truly tech rows, 6.2% land in A0's non-tech bucket.
  - Even the best assigners find only 56–70% of the non-tech rows.

## The contamination, and how it was caught

The rules were written after the labeller had read all 605 gold rows, test rows included. Two
checks exposed it.

1. **A coverage check.** The corpus-weighted gold sample put the rule-undecided share at 15.5%
   [10.5, 21.4], but the exact corpus count is 24.7%. The 100 uniform gold rows were only 8%
   undecided; at p=0.247 that has probability 1.6×10⁻⁵. Replaying the seeded draw reproduced all
   100 positions, so the draw was uniform. The rules had simply been fitted to titles the
   labeller had seen.
2. **A token audit.** 16 of 30 rare rule tokens match exactly one gold row, and that row is in
   the test split: "Megento" (a misspelling), "Field Serviced Tech", "Teamcenter", "DataPower",
   "Tibco", "MMIC", "PI Engineer", …

**Size of the effect.** Going from the stratified test split to the uniform fresh holdout,
non-rule candidates *gained* about 5 points (A0 +6.3, B +4.5, K2 +4.5, E2 +6.0), because the test
split oversampled knife-edge rows. Rules-first candidates *lost* 4–8 points instead (A1d −6.0,
A1a −7.9, A1b −15.0 with its extra unclassified rows). Relative to the others, that is roughly 10
points of inflation.

Only the fresh holdout was drawn after freezing, so it is the only fair comparison. The copy-pair
stability, group-voting and hysteresis results are unaffected.

## How these measurements bear on the literature survey

- **"Title first" — confirmed, with a twist.**
  - The best models are title-driven: E2 (title only) and BE (title plus vector) are within noise
    of each other and ahead of vector-only B.
  - Title-evident rows are far easier for every method: test A1d 0.88 vs 0.66 on rows without a
    clear title cue.
  - But the survey's rank-1 design puts **high-precision rules as stage 1**. On fresh data the
    rules' precision (0.79) is not higher than the learned model's on the same rows (0.82–0.84).
    The survey's own §12, weak supervision, is where the rules pay off: K2 was trained only on
    rule labels and reached 0.760.
- **"Supervised beats unsupervised centroids" — confirmed strongly.** +11 to +18 points on fresh
  data; every CI excludes zero.
- **"Exemplars beat label text" — confirmed.** Zero-shot definitions and seed prototypes score
  0.39–0.52 on fresh, at or below A0's 0.60. Prototypes from 10 seed titles are the weakest
  exemplar method.
- **"kNN over labelled exemplars for stage 2" — contradicted here.** Logistic regression beat kNN
  in every space: +11.2 points on JobBERT titles and +7.6 on stored vectors, both significant.
  kNN's one advantage, naming its neighbours, can be kept as an explanation beside an LR
  decision.
- **"JobBERT-v2 or nomic with the `classification:` prefix on titles."** JobBERT-v2 beat nomic
  with the production `search_document:` prefix by +10.4 points. The `classification:` prefix was
  not tested.
- **"Key the family on the title so copies agree" — confirmed, for title-driven models.** E2 has
  0% copy disagreement and BE 0.2%, against A0's 11.9%. **Keying or voting on a *normalised*
  (company, title) group cost 2–4 points**, because the normaliser discards qualifiers and some
  company titles span families. Keep the model title-driven, but key stickiness on the posting.
- **"An abstaining cascade beats a single model that must answer" — partly confirmed.**
  Abstention buys precision, at a steep coverage cost:

  | Cascade on fresh (decisive rule tiers, else K2 above a confidence threshold) | Coverage | Accuracy on covered rows |
  | --- | --- | --- |
  | No threshold | 100% | 0.744 |
  | K2 confidence ≥0.5 | 80.8% | 0.797 |
  | K2 confidence ≥0.9 | 66.0% | 0.818 |

  No configuration reached the ~0.90 precision agencies accept at useful coverage. That is the
  gap the survey's offline LLM stage (not testable here) would fill.
- **Open question 2 (title-only vs title plus description) — answered, within noise.** Title-only
  E2 0.768, vector-only B 0.736, combined BE 0.780. The description vector adds at most a few
  points, and none significantly at n=250. It helps most on vague titles (fresh rule-undecided
  rows: BE 0.590, E2 0.574, B 0.508, n=61).
- **Open question 8 (boilerplate removal) — no gain from heuristic section cutting** (H 0.664),
  and it was the least stable candidate.
- **The survey's one-axis family list — supported by the labels.** They support dropping
  `tech-leadership` (≈1%, 11% pure), moving Java/Python to a facet (≈1% each, decided purely by a
  title word), and splitting `systems-engineering` (1.2–1.4% true MBSE, 38% of A0's family
  non-tech). The proposed `ux-ui-design` family would be small here (3 designers in 855 labelled
  rows) because the tech filter mostly excludes design. An `unclassified-tech` sink is warranted
  (3% forced, plus vague titles).
- **Human ceiling (open question 1) — not measured.** One labeller agreed with themself 60/60 a
  day later. That bounds label noise from inconsistency, not from ambiguity. The 35% of postings
  with a defensible second family suggest two people would disagree well above that rate.

## Recommended design

1. **Assigner: a supervised title-first classifier.**
   - Logistic regression on [JobBERT-v2 title embedding ; stored nomic vector] (BE). The
     title-only E2 is a simpler variant if storing a second vector is unwanted.
   - Why: it is at least as accurate as every alternative measured (fresh 0.780), copy-stable by
     construction (0.2% vs 11.9%), and cheap (one CPU title pass per *new distinct* title, a
     logistic regression per posting).
2. **Labels: weak supervision from title rules plus a small human gold set.**
   - Build the rules by extending `tech_filter`'s tiers with family tags (above). Use their
     decisions on the ~75% of rows they cover as training labels, as K2 did, alongside a few
     hundred human-labelled postings.
   - When the rules change, retrain; do not hand-curate clusters.
   - Keep the rule verdict as the stored explanation ("rule X; model agrees at p=0.93").
3. **Abstain.** Below a confidence threshold, emit `unclassified-tech` (or queue the posting for
   the survey's offline LLM or human stage) instead of forcing a family.
4. **Stickiness per posting id,** with a margin rule (hysteresis, m≈0.2–0.3 on probabilities).
   Do not use group voting. Mark every classifier or taxonomy version in `trends_epochs`.
5. **Fix the list first.**
   - Drop `tech-leadership` into the seniority band.
   - Fold `java-development` and `python-development` into SWE with a language facet.
   - Split `systems-engineering` into MBSE systems engineering vs IT systems (IT operations).
   - Give `engineering-management` and `product-management` strict definitions (management of
     engineers; PM/PO/TPM).
   - Decide whether DSP/algorithms, industrial controls and technical writing get a home or go
     to `unclassified-tech` / non-tech.
   - Relabel the gold set against the new list before choosing thresholds.

Stopping points if a smaller step is wanted:

| Option | Fresh accuracy | New embedding step | Copy stability |
| --- | --- | --- | --- |
| **K2**: stored vectors plus rule labels | 0.760 | none | poor (11%): needs stickiness and hysteresis to be usable for Trends |
| **E2**: JobBERT title only | 0.768 | yes | 0% |

## Caveats

- **Data dates.** LanceDB snapshot v654 (2026-09-23) for titles, vectors and descriptions.
  `role_assignments` and the description store came from HF on 2026-09-24. The production
  centroids are v2 (fitted 2026-08-18).
- **Sample sizes.**
  - Fresh holdout n=250: CIs are about ±5–6 points, and the top three candidates are not
    separable.
  - First test split n=404: rule-based rows are contaminated; weighted CIs are wider still.
  - Per-family numbers rest on 1–31 rows each.
- **One labeller.** All 855 gold labels and the rubric are one person's. Self-agreement 60/60
  does not measure inter-annotator ambiguity. The same person also wrote the rules; that is how
  the test split was contaminated.
- **The fresh holdout is not rule-blind for the labeller.** It was labelled after the rules
  existed, blind to every prediction. The labeller knew the rubric the rules encode, so the rules'
  fresh precision may still be slightly optimistic.
- **Silver-label bias.**
  - B, C, D, E, G, H and BE learned from title-cue silver labels, which exist only for
    title-evident rows. K1 and K2 learned from the rules' labels, a similar bias with more
    coverage.
  - None of them saw many vague titles in training, which is where every method is weakest.
  - Training rows can be copies of evaluated rows: 72 of 250 fresh rows have a copy in the
    training sample, a realistic deployment condition. On the 178 without one, K2 scores 0.725
    and B 0.674.
- **Untested.**
  - nomic's `classification:` prefix;
  - a BE trained on the rules' labels;
  - re-embedding cleaned descriptions;
  - any LLM stage;
  - accuracy under the redesigned family list.
- **Cost figures** are CPU timings on one Apple-silicon laptop (8 threads), excluding training.
  CI runners will be slower per posting, but the volumes are small.

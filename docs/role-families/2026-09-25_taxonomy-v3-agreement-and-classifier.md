# Role families v3: the family list, how far labellers agree, and the title classifier

**Date:** 2026-09-25 · **Data:** served-table snapshot v654 (2026-09-23), 514,163 rows ·
**Decision record:** [ADR-0220](../adr/0220-a-trained-title-classifier-decides-a-role-family.md) ·
**Earlier work:** `2026-09-24_family-assignment-methods-research.md` (the literature) and
`2026-09-25_family-assignment-bakeoff.md` (measured assigners on the old list)

## Short answer

- **The family list.** It has one axis, what the Job does: 24 families, plus `unclassified-tech`
  and the `non-tech` diagnostic. Language, level and employer are never a family; Java and Python
  became watch roles. The list is the research's proposal plus two families added at rubric
  time (below), reshaped by measurement. Two blind labellers could not tell ML engineering from
  data science (pair κ 0.41), so those became one family. No other merge candidate fell under
  the merge rule.
- **How far labellers agree.** Two blind labellers agreed on **88.8%** of 250 uniform served Jobs
  (κ 0.870) against the finer list. Against the final list they agreed on **92.0%** of a fresh 200
  (κ 0.907). The labellers were two different AI models, not people, so this measures how
  separable the families are under a written rubric, not how often humans agree.
- **The classifier.** A logistic-regression head over JobBERT-v2 title embeddings scores **0.755**
  [0.695, 0.810] on the fresh 200. That is 82% of the labellers' agreement, and 14.5 points above
  the title rules alone (paired CI +8.0 to +21.0). It abstains to `unclassified-tech` on 3% of Jobs.

## The family list

The literature survey found that every occupation taxonomy it examined keeps function,
specialisation and seniority on separate axes, and it proposed a single-axis list. That proposal
was written into a labelling rubric, which was frozen before any Job was sampled. It kept every
merge candidate as its own label, so that agreement could decide:

- devops / sre-platform / cloud-infrastructure;
- database-administration / data-engineering / systems-administration;
- ML engineering / data science;
- solutions engineering / architecture;
- systems-engineering / hardware / non-tech;
- product / program-project management;
- frontend-web / software-engineering;
- embedded-firmware / hardware.

Three departures from the proposal were rubric choices made before labelling:

- **`solutions-engineering` was added.** The research's own gap list sized pre-sales and
  solutions engineering at about 5,000 titles. The data did not decide whether to add it, because
  the merge rule can only merge; it did find the new family separable from architecture (κ
  0.665).
- **`systems-engineering` was kept, redefined** as engineering of whole physical systems
  (aerospace, defence, automotive). Its survival was measured: labellers told it apart from
  non-tech at κ 0.632, above the merge bar. Keeping it charts non-software engineering as tech,
  as the tech filter already admits it.
- **`ux-ui-design` was dropped.** The tech filter rejects designers before they are served (6 of
  6 designer titles tried).

The owner reviewed the first two after the measurement and kept both (2026-09-25).

Two rules were fixed before any label was read:

- **Merge a candidate pair** when the pair-restricted Cohen's κ is under 0.60 over at least 15
  rows.
- **Fold a family** into its most-confused neighbour only when both tests hold: under 0.5% of tech
  rows on the uniform sample, and under 2,000 served titles matching a title pattern for it. 250
  rows cannot size a 0.5% family alone.

## Agreement

- **Sample.** 570 Jobs from snapshot v654, excluding the 855 the bake-off had labelled: 250
  uniform, plus 20 targeted rows for each of 16 merge-candidate regions.
- **Labellers.** Two blind labellers, Claude Opus and Claude Sonnet. Two different models, because
  two copies of one model share their mistakes and would overstate agreement. Each read the title,
  the department and a role excerpt of the description, and labelled against the frozen rubric.
  Neither saw the other's labels, the sampling key or any earlier label.

| Measure | Value |
| --- | --- |
| Raw agreement, uniform rows (n=250) | 0.888 [0.848, 0.924] |
| Cohen's κ, uniform rows | 0.870 [0.823, 0.913] |

Every candidate pair, as pair-restricted κ over the rows where either labeller used either member
(uniform and targeted rows). Each pair had at least 15 rows, so each could have merged:

| Pair | Rows | κ | |
| --- | --- | --- | --- |
| ml-ai-engineering / data-science-research | 46 | **0.409** | **merged** |
| frontend-web / software-engineering | 75 | 0.604 | kept |
| sre-platform / cloud-infrastructure | 43 | 0.628 | kept |
| systems-engineering / non-tech | 135 | 0.632 | kept |
| solutions-engineering / architecture | 39 | 0.665 | kept |
| devops / sre-platform | 46 | 0.669 | kept |
| hardware-engineering / non-tech | 144 | 0.718 | kept |
| database-administration / systems-administration | 46 | 0.735 | kept |
| data-engineering / systems-administration | 50 | 0.753 | kept |
| devops / cloud-infrastructure | 38 | 0.766 | kept |
| product / program-project management | 44 | 0.795 | kept |
| systems-engineering / hardware-engineering | 44 | 0.867 | kept |
| embedded-firmware / hardware-engineering | 49 | 0.884 | kept |
| database-administration / data-engineering | 46 | 0.916 | kept |

- **The merge.** It rested on 11 disagreements like "Senior Data Scientist" or "Staff Research
  Scientist, AI Agents & LLMs", where one labeller read the title and the other read the LLM
  engineering in the description.
- **Folds.** `database-administration` held 0 of the 250 uniform rows, so it met the first test
  of the fold rule. It survived on the second: 2,447 served titles match a DBA title pattern,
  above the 2,000 floor. That pattern was written after the labels were read, and 2,447 is close
  to the floor, so the survival is narrow. Every other family held some uniform rows.
- **Adjudication.** A third pass settled each disagreement from the rubric. It chose labeller A's
  label 44 times and B's 8 times. That adjudicator was the same model as labeller A, which may
  tilt it; the fresh test's adjudicator was a third model, and chose 10 and 6.
- **The fresh 200** (below) is a second agreement measurement, against the final list: 0.920
  [0.880, 0.955], κ 0.907.

## The classifier

**Design.**

- A family is a function of the normalised title alone. Every copy of a posting therefore agrees,
  and a re-embedded description cannot move a Job.
- The title is embedded with JobBERT-v2's title branch (MIT licence, pinned revision `a480476`). A
  logistic-regression head turns the embedding into one of the 24 families or non-tech.
- Below the cutoff the Job goes to `unclassified-tech`.

**Training labels.**

- **Silver:** up to 800 distinct served titles per family that the v3 title rules decide, 20,000
  in all. The rules are the labelling functions, never the answer. On dev they decide 90% of rows
  at 73.4% precision.
- **Dev gold:** the 401 agreement-sample rows outside the test split.

**The first head failed, and why.**

- Head v1 was trained on silver alone, with balanced classes. On that sample's 169 test rows it
  scored 0.580, with 25% abstained.
- That read **burned** the split: no later head is scored on it.
- The diagnosis used dev only. Silver is balanced, while served rows are not: non-tech is 32% of
  the uniform gold, and software-engineering 13%. And the cutoff had been tuned on dev rows
  over-sampled from hard regions.

**Head v2 protocol** (fixed in the experiment log before it ran):

- **Candidates.** Dev-gold weight w ∈ {0, 5, 20}, crossed with class priors ∈ {balanced, the
  rules' mix over served rows}. The priors are applied as a shift of the bias.
- **Selection.** Five-fold cross-validation over dev, grouped by copy. Every step helped: the
  best, w=20 with served-row priors, reached 0.771 out of fold, against 0.731 for v1's recipe
  (w=0, balanced) in the same cross-validation.
- **Cutoff.** From the out-of-fold probabilities on the 81 uniform dev rows: the best accuracy on
  covered rows at coverage ≥ 0.90. Result: 0.4.
- **Test.** A fresh 200 Jobs, uniform over the snapshot and excluding all 1,425 rows labelled in
  any earlier round, double-labelled blind and adjudicated. Read once.

| On the fresh 200 | Accuracy | Coverage | On covered |
| --- | --- | --- | --- |
| **Head v2 (shipped, cutoff 0.4)** | **0.755** [0.695, 0.810] | 0.970 | 0.778 |
| Head v2, never abstaining | 0.765 | 1.000 | 0.765 |
| v3 title rules alone | 0.610 | 0.715 | 0.839 |
| Labeller agreement (the ceiling) | 0.920 | — | — |

- **Non-tech:** of the 65 non-tech Jobs, the head finds 75.4%, and 86.0% of what it calls non-tech
  is non-tech.
- **Abstentions:** of the 6 Jobs it abstains on, 4 would have gone to the wrong family.
- **Remaining errors:** mostly tech-sounding non-tech roles ("IT Support" at a non-IT desk, test
  and QA titles in manufacturing), and management titles whose work is hands-on.

## In the pipeline

- **Where it runs.** `role_trends` in the merge job classifies each served title through a cache
  (`data/state/role_title_families.parquet`: normalised title → family and confidence). Written
  with the snapshot's 268k titles and stand-in labels, the file is 4.7 MB. Each run encodes only titles the cache has not seen under the current
  head.
- **Warm-up.** A new head starts with an empty cache. Each run spends up to 12 minutes encoding,
  saving after every 4,096 titles, and writes no trend rows until 99% of served rows have a
  decided title, rather than charting the backlog as unclassified.
  - Meanwhile the Space keeps serving the last series under the old families, which gains no new
    points. Their labels stay readable through the config's `retired` list, but the watch-role
    drills already follow the new parents.
  - The new series starts at the first run past 99%.
- **Measured on this laptop.** About 270 titles/s. Peak memory is 2.6 GB, against 6.8 GB when the
  step read every row's 768-d vector for the centroids.
- **Retired.** The centroid store, its fit and its diff tooling.

## Caveats

- **Agents, not people.** The labellers are AI models reading a written rubric. Their agreement
  bounds what the rubric can separate, not what two people would agree on.
- **Sample sizes.** n=200 carries ±5–6 points. The cutoff rests on 81 uniform dev rows.
- **Silver noise.** The silver labels carry the rules' errors. Dev gold (weighted 20×) and the
  prior shift correct much of it, not all.
- **Stale snapshot.** Titles come from snapshot v654 (2026-09-23). The live corpus has moved since.

Scripts and every capture are in `experiment/role-family-taxonomy-v3/` (local, not committed); its
`LOG.md` records each rule before it was applied.

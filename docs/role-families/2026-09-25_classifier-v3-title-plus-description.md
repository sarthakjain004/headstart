# Role-family classifier v3: title plus description (2026-09-25)

The measurements behind [ADR-0224](../adr/0224-a-rows-description-vector-joins-its-title-in-deciding-its-role-family.md).

Head v2 ([ADR-0220](../adr/0220-a-trained-title-classifier-decides-a-role-family.md)) decides a
Job's role family from its title alone. This round tried three levers the owner chose:
- more labelled Jobs;
- the Job's department as an input;
- fine-tuning JobBERT.

None of the three cleared the bar set in advance. A fourth input tried afterwards did: the row's
own served description vector. Head v3 is built on it.

## Labels

**The draw.** 1,000 served Jobs were drawn from snapshot v654 (2026-09-23), excluding every Job
labelled before and every copy of one:
- **Sealed test:** 400 uniform rows.
- **Training:** 600 rows:
  - 150 uniform;
  - 100 whose title names a management or delivery role;
  - 350 where head v2 was least certain (smallest top-two margin, out of a 20,000-row pool).

All 1,000 were shuffled into 20 sheets, so the labellers could not tell test rows from training
rows.

**The protocol** was round 1's:
- the frozen v3 rubric;
- labeller A (Claude Opus) and labeller B (Claude Sonnet), each reading title, department,
  company and a description excerpt;
- a blind adjudicator (Claude Fable) for the 118 disagreements. It took A's label on 72, B's on
  41 and neither on 5.

**Agreement:**

| rows | raw agreement | Cohen's κ |
| --- | --- | --- |
| 400 test rows | 0.925 [0.897, 0.950] | 0.914 |
| all 1,000 | 0.882 | 0.866 |

By training source: uniform 0.900, management 0.840, least-certain 0.837. The ceiling round 1
measured (0.920 on its fresh 200) held.

## Candidates

**How they were compared.** Every candidate trains on the same data:
- 20,000 rule-labelled titles, 800 per family, as silver;
- the training folds' gold at weight 20;
- a bias shift to the served-row family mix.

The comparison is 5-fold cross-validation over 1,201 gold Jobs:
- round 1's 401 dev Jobs;
- round 1's fresh 200, which head v2's test read had already spent;
- the new 600.

Folds are grouped by copy. The metric is accuracy with no abstain on the 431 uniform-drawn rows.
A richer candidate had to beat a simpler one by 0.02.

| candidate | uniform (431) | all (1,201) |
| --- | --- | --- |
| frozen JobBERT, title | 0.749 | 0.685 |
| frozen JobBERT, title + department | 0.761 | 0.693 |
| LoRA JobBERT, title | 0.742 | 0.679 |
| LoRA JobBERT, title + department | 0.759 | 0.698 |
| *exploratory:* frozen JobBERT, title + description vector | **0.780** | 0.704 |
| *exploratory:* frozen, title + department + description vector | 0.780 | 0.705 |

**The LoRA setup:**
- rank 16 on q, k, v and o in all 12 layers, with the anchor Dense also trained;
- 3 epochs, learning rate 2e-4, fixed before any run.

Paired differences on the uniform rows, with 95% bootstrap intervals:
- department − title: +0.012 [−0.009, +0.032];
- LoRA title − frozen title: −0.007 [−0.032, +0.016];
- **description vector − title: +0.030 [+0.009, +0.051];**
- description vector − department: +0.019 [−0.002, +0.039].

Non-tech on the uniform rows:

| | recall | precision |
| --- | --- | --- |
| title only | 0.782 | 0.816 |
| title + description vector | 0.803 | 0.868 |

Against title only, the description vector fixed 17 uniform rows and broke 4. The fixes are
titles whose industry decides them:
- "Systems Engineer" at a non-IT firm, which is non-tech;
- a physical-security "Security Manager";
- "Optimization Engineer";
- "QA Manager", which is engineering-management.

**What the vector is.** It is the served table's `vector` column: nomic-embed-text-v1.5 over the
title plus cleaned description (ADR-0006). The exploratory candidates were added after the four
pre-registered ones were seen, and the experiment log records it that way.

## Serving cost

Measured on snapshot v654, 514,163 rows:
- **Row part:** streaming every served vector through the head's row weights took 0.9 s.
- **Whole step:** `role_trends` end to end, with a cache covering every title, took 7.7 s at a
  peak of 2.81 GB, against 2.6 GB for the title-only step.
- **Cache size:** 27.1 MB (25 float32 logits for each of 267,849 titles), against 4.7 MB before.
- **Split rate:** among titles with at least three served rows (3,000 sampled, 31,549 rows),
  1.5% of rows fall outside their title's majority family, and 4.9% of those titles have any row
  that does. The off-majority rows go mostly to non-tech (207) and software-engineering (126).

## Head v3 and the test

**Training.** The trainer (`scripts/embed/train_role_family_classifier.py --version 3`) chose its
settings by cross-validation over all 1,201 gold rows:
- weight 20;
- balanced priors, at 0.714 against 0.712 for the served-row mix;
- cutoff 0.4, which on the uniform rows covered 0.928 at 0.810 accuracy on covered rows.

**The test.** The sealed 400 rows were read once, after the protocol was written down:

| | accuracy as served | coverage | non-tech recall | non-tech precision |
| --- | --- | --- | --- | --- |
| head v3 | **0.777** [0.738, 0.818] | 0.953 | 0.869 | 0.876 |
| head v2 | 0.748 [0.705, 0.790] | 0.963 | 0.820 | 0.847 |
| title rules alone | 0.585 [0.537, 0.635] | 0.730 | 0.344 | 0.894 |

Without abstaining, head v3 scores 0.795 and head v2 0.765.

**The ship decision.** Paired, v3 − v2 is +0.030 [−0.005, +0.065]. The ship rule fixed in
advance asked for at least +0.03 with the interval excluding 0, and **it was not met**. The owner
shipped v3 anyway, because:
- the cross-validation gave the same +0.030 on different rows, with an interval that excludes 0;
- the worst case is about half a point;
- non-tech recall improves by five points.

This test is now spent.

## Also measured this round

- **Laya zero-shot is no substitute** (Convai's System One model; ModernBERT-large, 421M
  parameters). On round 1's 401 dev Jobs on an Actions runner:

  | Laya setup | accuracy | titles/s |
  | --- | --- | --- |
  | label names only | 0.411 | 1.63 |
  | default description budget | 0.342 | 1.12 |
  | wider description budget | 0.125 | 0.41 |

  Its non-tech recall stayed under 6%. JobBERT on the same runner ran at 43.6 titles/s.
- **The runner encodes titles far slower than a laptop:** about 40 titles/s against about 270.
  Warm-ups therefore take about 12 runs, not the 3–4 first estimated. #674 batches titles by
  token count, which encodes 2.2× faster with identical vectors.

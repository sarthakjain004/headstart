# ADR-0224: A row's description vector joins its title in deciding its role family

**Status:** accepted · **Date:** 2026-09-25 · **Amends:**
[ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) (what the classifier head
reads, what its cache holds, and whether a re-embed can move a row) · **Relates to:**
[ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (the reassignment ledger)
and [ADR-0006](0006-what-we-embed.md) (the served vector is title plus cleaned description)

## Context

Head v2 (ADR-0220) decides a role family from the title alone. It scored 0.755 on its fresh
200-row test, against a labeller agreement ceiling of 0.920. The owner asked to push accuracy
further (2026-09-25).

Head v2 made 47 errors on that test. Sorted by what each would have needed to be right (a
judgement call):

| error kind | errors | example |
| --- | --- | --- |
| manager or programme role outranks the function | ~9 | "QA Manager" |
| settled by the department | ~7 | "Engineering Manager" in Hotel-Engineering |
| rubric knowledge | ~13 | Genesys, Analytics Engineer |
| settled only by the description or company | ~15 | "Technical Support Engineer" at an electrical distributor |

The owner chose three levers:
- about 1,000 new labelled Jobs;
- the Job's department as a second input;
- a LoRA-fine-tuned JobBERT.

Labelling followed ADR-0220's protocol: a frozen rubric, two blind labellers (Opus and Sonnet),
and a blind adjudicator. It gave a sealed 400-row uniform test and 600 training rows:
- 150 uniform;
- 100 with management or delivery titles;
- 350 where head v2 was least certain.

The labellers agreed on 0.925 of the test rows (κ 0.914), so the ceiling held.

Four candidates were cross-validated over 1,201 gold Jobs (five folds grouped by copy). The
metric is accuracy on the 431 uniform-drawn rows, with no abstain:

| candidate | accuracy |
| --- | --- |
| frozen JobBERT, title only | 0.749 |
| frozen JobBERT, title plus department | 0.761 |
| LoRA-fine-tuned JobBERT, title only | 0.742 |
| LoRA-fine-tuned JobBERT, title plus department | 0.759 |

None cleared the pre-registered +0.02 margin. Fine-tuning fits the rules' silver errors as
readily as it fits gold, and the regularised linear head smooths them over.

Then the errors that needed the description were tried directly, exploratorily, since this was
not in the pre-registered set. The served row's own `vector` (nomic, title plus cleaned
description, ADR-0006) went into the frozen head next to the title embedding:
- **Accuracy:** 0.780, paired +0.030 [+0.009, +0.051] over title-only.
- **Non-tech:** recall 0.803 against 0.782, precision 0.868 against 0.816.
- **Department:** adding it on top gained nothing (0.780).

The owner chose that design. The trainer then fitted head v3 on 20,000 rule-labelled titles plus
the 1,201 gold Jobs at weight 20, with the cutoff at 0.4. Its own cross-validation chose
**balanced** priors, not the served-row shift the exploratory read used. That was a 0.714 against
0.712 tie on all 1,201 rows, a metric dominated by the hard rows. So the shipped head differs from
the cross-validated one only in its bias shift. Then the sealed test was read, once:

| | head v3 | head v2 |
| --- | --- | --- |
| accuracy as served | 0.777 [0.738, 0.818] | 0.748 [0.705, 0.790] |
| non-tech recall | 0.869 | 0.820 |
| non-tech precision | 0.876 | 0.847 |

- **Paired difference:** +0.030 [−0.005, +0.065].
- **Ship rule (fixed in advance):** +0.03 with the interval excluding 0. **It was not met:** 400
  rows cannot confirm three points.
- **The owner shipped anyway,** on four grounds:
  - both reads agree on +0.030, although they measure two heads that differ in their bias shift
    (served-row priors in the cross-validation, balanced in the shipped head);
  - the worst case is about half a point;
  - the largest error class, non-tech, improves by five points;
  - Trends was already paused on head v2's warm-up.

Evidence: `docs/role-families/2026-09-25_classifier-v3-title-plus-description.md`.

## Decision

1. **The head reads two inputs.**
   - **Title:** the JobBERT-v2 title embedding, as before.
   - **Description:** the row's served `vector`.
   - **One logistic regression** covers both. `head.npz` holds `title_weights`, `row_weights` and
     `bias`.
   - **The manifest names the description input:** a `row_vector` block gives the embedder and
     width.
   - **`role_trends` refuses a mismatch.** A head errors, and writes no rows, instead of
     scoring vectors it never learned, when either:
     - its `row_vector` model is not the embedder the pipeline embeds with
       (`embedding_conventions.MODEL`; the table does not record its own);
     - or its width is not the served table's.
   - **`role_trends` refuses ids that do not line up.** Row vectors are matched to rows by id, so
     repeated ids, or vectors that do not cover the rows exactly, also stop the run with an
     error.
2. **The logits split, so serving stays cheap.** Because the head is linear, a row's logits are:
   - a **title part**, `JobBERT(title) @ W_title`;
   - plus a **row part**, `vector @ W_row + bias`.

   The title part is what costs an encoding, and it is cached per normalised title. The cache is
   `data/state/role_title_families.parquet`, which keeps its name. The name still says what the
   file is: each title's role-family scores. Renaming it would also strand the old file on HF,
   because merge's state upload only ever adds files. It now holds one logit per family instead
   of a decided family. The row part is one matrix product over the served vectors
   each run.
3. **No department input.** It added nothing once the description vector was in, and it would
   have grown the cache key by 21%.
4. **No fine-tuning.** Both LoRA variants measured at or below frozen JobBERT.
5. **Head v3 is a new version, so it is a re-base.** The series version is 3003. The title cache
   is discarded and warms up again, as under ADR-0220.

## Consequences

- **A re-embed can now move a row between families.** ADR-0220's "a title-keyed family cannot
  move under a re-embed" no longer holds. The ADR-0057 reassignment ledger records such moves
  like any other.
- **Rows that share a title still mostly share a family.** On snapshot v654, over titles with at
  least three rows:
  - 1.5% of rows left their title's majority family;
  - 4.9% of those titles had any row do so.
  The movers go mostly to non-tech and software-engineering: the "Systems Engineer" whose
  description is power-grid work, the case the change is for.
- **The step reads the vector column again,** but through the head's row weights, so it stays
  light. End to end on snapshot v654 (514,163 rows, stand-in cache): 7.7 s, peak memory 2.81 GB.
  Under ADR-0220 it was 2.6 GB; the centroid era peaked at 6.8 GB.
- **The title cache grows from 4.7 MB to 27.1 MB,** at 25 float32 logits per title.
- **The embedder is now a dependency of the head.** A change to the served embedder (ADR-0005) or
  its width stops Trends with an error until the head is retrained against the new vectors.
- **Retraining needs the snapshot the gold was drawn from.** The trainer reads each gold row's
  vector from `--table`, and it refuses to train when a gold id is missing.
- **The sealed 400 is spent.** The next head needs a fresh labelled test, larger than 400 if it is
  to confirm a gain of a few points.

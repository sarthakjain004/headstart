# ADR-0220: A trained title classifier decides a role family, over a one-axis family list

**Status:** accepted, amended by
[ADR-0224](0224-a-rows-description-vector-joins-its-title-in-deciding-its-role-family.md) (the
head also reads the row's description vector) · **Date:** 2026-09-25 · **Supersedes:**
[ADR-0215](0215-a-title-rule-decides-a-role-family-before-the-centroid.md) (title rules before
the centroid) · **Amends:** [ADR-0040](0040-role-trend-ledger.md) (the family list, and what
decides a row's family), [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md) (watch-role
parents) and [ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (the epoch
ledger's sixth column) · **Relates to:**
[ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (the reassignment ledger)

## Context

ADR-0215 put title rules before the centroids. ADR-0215 also left three next steps, which the
owner asked for together on 2026-09-25:

- a family list on one axis;
- a measured agreement ceiling;
- a learned title classifier.

That work is written up in `docs/role-families/2026-09-25_taxonomy-v3-agreement-and-classifier.md`.

- **The family list.** The research doc's single-axis proposal was frozen into a rubric with every
  merge candidate kept apart. The rubric also added `solutions-engineering` and kept a redefined
  `systems-engineering`, and it dropped `ux-ui-design`, which the tech filter rejects. Those were
  rubric choices, since the merge rule can only merge. The owner reviewed the two additions after
  the measurement and kept both (2026-09-25). Two blind labellers (Claude Opus and Claude Sonnet) labelled 570
  served Jobs against it. A pair merged if its pair-restricted κ fell under 0.60, a rule fixed
  before any label was read. Only ML engineering and data science fell under it (κ 0.41).
- **Agreement.** 88.8% [84.8, 92.4], κ 0.870, on the 250 uniform Jobs; 92.0% [88.0, 95.5],
  κ 0.907, on a fresh 200 against the final list.
- **The classifier.** A logistic-regression head over JobBERT-v2 title embeddings. It trains on
  titles the v3 rules label (silver) plus 401 dev gold Jobs weighted 20×, with its bias shifted to
  the rules' class mix over served rows. On the fresh 200, read once, it scores 0.755 [0.695,
  0.810], 97% coverage. The v3 rules alone score 0.610; the difference is +14.5, paired CI
  [+8.0, +21.0].
- **A burned first attempt.** A first head, v1 (silver only, balanced classes), scored 0.580 on
  the agreement sample's test split. That split is burned. Head v2's protocol was fixed in the
  experiment log before v2 was built, and v2 was scored only on the fresh 200.

## Decision

1. **The family list is v3, one axis.** 24 families plus `unclassified-tech`, in
   `config/role_families.json` with a label and the rubric's definition each; `non-tech` stays
   the reserved diagnostic. Java and Python become watch roles. Tech-leadership, the language
   families and the old catch-all splits are gone. So is `systems-engineering`'s IT half, which
   goes to systems-administration; `systems-engineering` itself stays, covering physical-systems
   engineering, which labellers separated from non-tech (κ 0.632).
2. **A Job's family is its title's verdict under the classifier head.** The head lives in git
   (`config/role_family_classifier/`: the manifest and ~100 KB of weights). Its manifest pins the
   model and revision, the families and the cutoff. `role_family_classifier.Head` refuses a
   manifest that disagrees with its weights.
3. **Below the cutoff, `unclassified-tech`.** The cutoff is 0.4, chosen on uniform dev rows. It
   abstains on 3% of the fresh test, and 4 of those 6 would have been wrong.
4. **A title cache, and a warm-up.** Families are cached per normalised title in
   `data/state/role_title_families.parquet`, which travels with the rest of `data/state`, and are
   stamped with the head version.
   - Each run spends up to 12 minutes encoding titles the cache lacks, saving after every chunk.
   - Trends writes no rows until 99% of served rows have a decided title. The Space keeps serving
     the last old series, with its labels held in the config's `retired` list.
   - The step gets a 20-minute cap and runs only when the corpus artifact (which carries the
     cache) arrived. The merge job's cap rises from 98 to 118 minutes, and the model is cached by
     revision; a test ties the cache key to the manifest.
   - A separate backfill workflow was rejected: merge uploads its whole `data/state` folder, so it
     would overwrite a backfilled cache with its own older copy.
5. **A new head is a re-base.** Ledgers are stamped `series_version = 3000 + head.version`, above
   every earlier era (centroid versions 1 and 2, and 2001 under ADR-0215).
6. **The epoch ledger's sixth column is renamed** from `family_rules_fingerprint` to
   `family_classifier_version`, in place, keeping its old rows' values.
   - Its `centroid_version` column reads `none` from now on.
   - The Space labels a change there "role family assignment changed".
   - The Trends UI and `hot_boards` treat it as a counting change.
7. **The centroids are retired.**
   - Retired: the centroid functions in `roles.py`, `scripts/embed/cluster_roles.py`,
     `cluster-roles.yml`, `scripts/eval/diff_role_assignments.py` and `diff-role-assignments.yml`.
   - The diff tool re-scored past table versions against the centroids, to separate re-embeds from
     closures. A title-keyed family cannot move under a re-embed, and the reassignment ledger
     already records each run's transitions going forward.
   - The HF copy of `data/state/role_centroids` is left untouched, and merge's upload still
     excludes it.
8. **The title rules move to `scripts/embed/`.** They are the training labeller and the pipeline
   never runs them. They were measured as the final decider under ADR-0215. The classifier trained
   on their verdicts beats them, which the bake-off also found.
9. **Watch roles move under v3 parents.** Full stack and backend under software-engineering;
   frontend under frontend-web; the AI roles under ai-ml-data-science.

## Consequences

- **Trends and its neighbours freeze for a few runs, then restart on the new series.**
  - While the cache warms up, `role_trends` writes no rows. The chart shows the last old series
    with no new points, and its watch-role drills already follow the new parents.
  - "Hiring now" (`hot_boards`) and the company directory read what `role_trends` writes, so they
    re-serve their last pre-warm-up state too.
  - After the switch, `hot_boards` treats the new series' first tick as its baseline, as it does
    after any re-base.
  - Older series stay in the ledger.
- **Retraining is a process, not a tweak.**
  - A new head needs `--version` bumped, and a fresh test set drawn and labelled blind: the fresh
    200 is spent the moment it informs any choice.
  - Relabelling costs the most, so the gold and rubric live with the experiment. The rubric's
    final form is summarised in the write-up.
- **The labellers are models, not people.** Agreement is a ceiling on what the rubric can
  separate, not on human judgement. Round 1's adjudicator shared labeller A's model.
- **Tech-sounding non-tech roles remain the main error.** Non-tech recall is 75%, on 65 Jobs.
  Better silver labels for non-tech, which the rules only catch by trade word, is the lever.
- **The step gets lighter.** It no longer reads the 768-d vector column: peak memory falls from
  6.8 GB to 2.6 GB on snapshot v654.

# ADR-0215: A title rule decides a role family before the centroid does

**Status:** accepted · **Date:** 2026-09-25 · **Amends:** [ADR-0040](0040-role-trend-ledger.md)
(how a row gets its family, and what re-bases a series) and
[ADR-0051](0051-trends-as-share-flow-and-watched-roles.md) (a watched role counts tech rows only) ·
**Relates to:**
[ADR-0017](0017-tech-role-filter.md) and [ADR-0068](0068-a-department-names-the-org-not-the-role.md)
(the tech filter whose shape the rules follow),
[ADR-0052](0052-watch-the-large-domain-roles-too.md) (watch roles),
[ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (the reassignment ledger),
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) and
[ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the epoch ledger this adds a column to)

## Context

Since ADR-0040, a served row's Trends family has been its nearest of 72 frozen k-means centroids,
hand-mapped onto 24 families plus `non_tech`. The centroids were fitted on `nomic-embed-text-v1.5`
vectors of title plus full description. A measured critique on 2026-09-24 scored the assignment
3/10:

- Only 76.7% of rows whose title names a family landed in it.
- Four clusters were each one employer's template (c21 100% Anduril, c31 100% Wipro).
- Cross-family centroids were closer than same-family ones (median cosine 0.875 against 0.823).
- Two copies of one Job at one company landed in different families 11.9% of the time
  (n=22,646 pairs; the bake-off's rebuild found 22,656 and the same rate).

A literature survey (`docs/role-families/2026-09-24_family-assignment-methods-research.md`) found
no agency coder or job platform that assigns occupations by nearest unsupervised centroid. All of
them start from the title.

A bake-off (`docs/role-families/2026-09-25_family-assignment-bakeoff.md`) scored candidate
assigners against hand labels. Its first test split turned out to be contaminated for the rule
candidates, because the rules were written after the labeller had read those rows. So the figures
below come from a fresh holdout: 250 served Jobs drawn uniformly from served-table snapshot
v654 and labelled only after every candidate was frozen. Accuracy intervals are the bake-off's.
The "vs rules-first" column was computed for this ADR: a paired bootstrap, 10,000 resamples of the
same 250 rows, of each assigner minus the one adopted here.

| Assigner | Accuracy [95% CI] | vs rules-first | Copies disagree | Needs |
| --- | --- | --- | --- | --- |
| Centroids alone (ADR-0040) | 0.600 [0.540, 0.660] | −10.8 [−16.8, −4.8] | 11.9% | nothing new |
| **Title rules, centroid for the rest** | **0.708 [0.652, 0.764]** | — | **2.7%** | a rules module |
| Title rules, then a logreg on the stored vector | 0.740 [0.684, 0.792] | +3.2 [−0.4, +6.8] | 2.9% | a trained model |
| Logreg on a JobBERT-v2 title embedding | 0.768 [0.712, 0.816] | +6.0 [+0.4, +11.6] | 0% | JobBERT on CI, a title cache, a trained model |
| Logreg on JobBERT-v2 title + stored vector | 0.780 [0.724, 0.828] | +7.2 [+1.6, +12.8] | 0.2% | the same |
| Zero-shot on family definitions or seed titles | 0.39–0.52 | worse | up to 19% | — |

The rules decided 75.2% of the holdout at 79.3% precision.

## Decision

1. **The title decides first.** `headstart.ingest.role_family_rules.classify(title)` returns a
   family, `non-tech`, or no decision. `role_trends` uses the centroid only when no rule decides, which is
   about a quarter of served rows. The rules follow `tech_filter`'s shape: a negative rule for a
   non-software discipline, trade, retail or sales word makes the row non-tech unless a strong
   cue also matched. Otherwise a precedence order settles the family: people management, then
   product and program management, support, specialty, language, cloud platform, web stack,
   level, and finally generic software. So a cloud-provider word outranks the stack word:
   "Backend Engineer (AWS)" is `cloud-infrastructure`.
2. **The title only.** The bake-off's department tier decided 0.7% of rows at unmeasurable
   precision, and ADR-0068 found that a department names the org, not the role. Without it the
   holdout score is unchanged at 0.708.
3. **This is a re-base, not a marked step.** Nearly 30% of served rows change family (153,681 of
   514,163 in snapshot v654), and tech rows grow from 389,652 to 396,263. ADR-0188 marks a small
   change and leaves the step drawn. A step this size would dominate every family's line, and it
   would reach "Hiring now" and the company directory as Board deltas. So every ledger
   `role_trends` writes is stamped with a new series version,
   `role_trends.series_version(centroid_version) = centroid_version * 1000 + RULES_GENERATION`.
   The Space charts the newest version, and every snapshot compares its stamp for equality. A
   refit and a new rules generation therefore both start fresh series through machinery a refit
   already exercises. The metadata key stays `centroid_version`, because the Space,
   `hot_boards` and `company_directory` read it and older files carry it.
4. **A rule edit is marked, not re-based.** `trends_epochs` gains a `family_rules_fingerprint`
   column, a content hash like the family map's. It is upgraded in place the way ADR-0188's
   column was: rows from before the rules existed take `none`. The Space labels a change "role
   family title rules changed". `RULES_GENERATION` is bumped by hand only for a rules change as
   sweeping as this one.
5. **Watch roles count tech rows only, under the family most of their rows land in.** The chart
   excludes non-tech, yet 2,426 of the Frontend watch role's 6,606 matches were non-tech rows,
   grocery "Front End" clerks among them. Watch roles now count only rows whose family is tech.
   Six roles whose rows now land mostly outside `software-engineering` move to the family they
   land in:

   | Watch role | New parent | Share of its rows there |
   | --- | --- | --- |
   | fullstack | web-development | 73% |
   | frontend | web-development | 82% |
   | devops-by-title | devops | 64% |
   | qa-by-title | qa-test | 92% |
   | security-by-title | security-engineering | 90% |
   | platform-by-title | sre-platform | 59% |

   `fde` moves from `ai-ml` to `software-engineering` (69%).

## This is a smaller step than the bake-off recommends

The bake-off write-up recommends a different end state. It wants a supervised title classifier
(JobBERT-v2, with or without the stored vector) trained on these rules' labels plus a human gold
set. It adds an explicit abstain (`unclassified-tech`) output, per-Job stickiness, and a
fixed family list. In its words the rules work better "as a teacher than as the final decider":
on the rows they decide, the learned models are at least as accurate. And on the quarter they
leave undecided, the centroid fallback adopted here gets 0.443 right, against 0.574 for rules →
stored-vector logreg.

This ADR ships the rules-first step anyway, for three reasons:

- It is a significant gain (+10.8 points) that needs no new model, cache or training loop.
- Its rules are exactly the labelling functions the recommended classifier would train on.
- Each remaining piece is a design with its own cost:
  - the classifier needs JobBERT on CI and a title cache in HF state;
  - abstain adds a new series to the Trends chart;
  - the family list is a new map.

Each is recorded below as a next step, not rejected.

## Alternatives not taken now

- **A learned JobBERT-v2 title classifier** scores 6–7 points higher and never splits copies. It
  needs a sentence-transformers model in the merge job, a cache of per-title predictions in HF
  state, where storage is the binding cost (ADR-0168), and a training loop with labels. That is a
  design of its own, and this ADR's rules are the labelling functions it would train on. It is
  the measured next step, not a rejection.
- **A logreg on the stored vectors alone** (0.736–0.760) needs no new embedding, but it splits
  11% of copy pairs, as badly as the centroids. Trends cannot use it without stickiness. As the
  fallback behind these rules it scores +3.2 [−0.4, +6.8], not separable at n=250, and it would
  need a trained model kept in step with the rules.
- **Abstaining** (`unclassified-tech`) below a confidence threshold was measured at 80.8%
  coverage and 0.797 accuracy (rules, then that logreg). It adds a series the chart and its
  drills do not have yet.
- **Refitting the centroids** on titles or on boilerplate-stripped text still leaves clusters
  that need hand-mapping onto families. Stripping boilerplate by heuristic did not help in the
  bake-off.
- **An LLM per Job** was not measured. The router is not reachable from CI, so it could only
  run off the pipeline with cached labels.

## Consequences

- **The Trends chart restarts at the first tick that runs this.** Older series stay in the
  ledger but are no longer charted. "Hiring now" and the company directory take that tick as a
  fresh baseline, as they do after a refit. The reassignment ledger starts over too, and from
  then on it also records rows that moved because their title changed.
- **The step takes longer.** On served-table snapshot v654 (514,163 rows, this laptop),
  `role_trends` took 33 s against 13 s before. Rule verdicts are memoised per title, and 53% of
  titles are distinct. Peak memory is unchanged at 6.8 GB, which is the vector read.
- **The rules are content that needs upkeep:** 38 family cues and 10 negative groups. Known leaks
  on snapshot v654:
  - an unqualified "Test Engineer" goes to `qa-test` (1,519 rows), though most are hardware or
    manufacturing test;
  - a few grocery "Front End" titles still reach `web-development`;
  - "Flutter" in Flutter Entertainment's titles reads as the mobile framework;
  - a cloud-provider word outranks the stack, so "Full Stack Developer (AWS)" is
    `cloud-infrastructure`, not `web-development`. That precedence decides 2,007 rows, 444 of
    them at Amazon, where "AWS" names the org rather than the work: "Software Development
    Engineer, AWS OpenSearch" is `cloud-infrastructure`.

  A counter-rule for each changes the fingerprint and marks the tick.
- **The family list is now the larger error.** On the holdout, 35% of Jobs have a
  defensible second family, and 38% of the best models' errors are exactly that second family.
  `tech-leadership`, `java-development` and `python-development` hold about 1% each once function
  outranks level and language. The survey proposes a list with a single axis, function, which
  would be a new family map and a new rules generation.
- The four `-by-title` watch roles now largely repeat their parent family, since that family is
  decided by title too. They stay until the family list is redone.
- Accuracy claims rest on one labeller. Human–human agreement on these families has never been
  measured, and published figures at this granularity run 70–90%.

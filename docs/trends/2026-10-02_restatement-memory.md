# Restatement memory fix

PR934 base: `49092710c4e52d4b6b1a3eae1c875ccc23682e1d`.
Baseline Actions run: [36999380267](https://github.com/sarthakjain004/headstart/actions/runs/36999380267).
During Job-version loading RSS reached 13,731 MiB; the wrapper killed the replay
above its 12 GiB watchdog. This is watchdog evidence, not evidence of kernel OOM.

## Mechanism and correctness boundary

Scan the facts in batches for IDs that any listed/changed observation admits under
the current tech gate. Replay **all events** for those IDs, including subsequent
rejected edits, unlisted and off-Board events. Include baseline incumbent IDs even
if the recorded facts never listed them. Raw facts remain unchanged: a future wider
filter reruns this selection and recovers previously unserved pre-baseline Jobs.

Dormancy depends on non-tech observations too. Compute it separately from all-Job
versions projected to ID, Board and posted date, partitioning by ATS while applying
the existing Board-local rule. Seed these narrow versions from the complete baseline
as well. Never apply the candidate tech filter to Dormancy evidence.

Keep served duplicate folding global. Partitioning served replay by ATS would split
Eightfold/backing requisition groups; company partitions would require a new,
complete grouping policy. Targeted IDs avoid either change to duplicate semantics.
Memory still grows with eligible history; this is not a constant-memory algorithm.

Arrow shift kernels replace NumPy object-string arrays. Baseline seeding converts
8,192 rows at a time. Reference inputs are read without unused logits; baseline
vectors/text load only after narrow Dormancy inputs are released, in 4,096-row
batches. Baseline version sources remain authoritative. Classifier matrices also
hold at most 4,096 rows. Description reads retain wanted IDs only, preserving blank
updates; archived vectors are filtered/batched, and retained vectors own their data.

## Verification

Tests compare full and selected lifecycle replay, including changes into/out of tech,
absence and relisting, and compare whole/batched placement with baseline sources.
Combined regression compares full and selected served stock/turnover with non-tech
Dormancy evidence and a real Eightfold/Workday duplicate group.
124 targeted tests pass, including state-fetch and workflow checks; Ruff passes.
An explicit initially non-tech → tech → removed fixture checks that admission is
not backdated and every selected-ID event matches full replay. Dormancy clipping
rejects whole-table conversion in its regression. Counting receives only its nine
required columns; placement input stores are released before counting.

Global duplicate folding still materializes selected served rows while grouping
incumbents. Counting sorts its narrow rows and retains event counters. Candidate
selection, all-Job narrow Arrow tables, Board reads and baseline sources are also
proportional to their inputs. These are intentionally not described as constant
memory; the complete replay's measured high-water mark is the acceptance check.

Production acceptance requires the whole replay below 10 GiB, plus an independent
live-history comparison. The read-only workflow pins an immutable HF revision and
retains the input inventory, resource samples, high-water mark and comparison output.
No pipeline dispatch, HF publication, rule change or merge is part of this fix.

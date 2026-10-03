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

Production acceptance requires the whole replay below 10 GB (10,000,000,000 bytes), plus an independent
live-history comparison. The read-only workflow pins an immutable HF revision and
retains the input inventory, resource samples, high-water mark and comparison output.
No full-pipeline dispatch, HF publication, rule change or PR merge is part of this fix.

## First measured attempt

[37000940207](https://github.com/sarthakjain004/headstart/actions/runs/37000940207)
used exactly `d644c8de3670a3580f190b67ef48f7c28a2b3d54` (934 files), the baseline
`2026-10-02T10:27:41+00:00` and frozen rules
`09379c2acca919c65ec448674969de1c21605769435b42b8a88b709521731d04`.
Version loading, seeding and Dormancy peaked at 5,746 MiB. Later assembly of baseline
version sources / latest descriptions reached 12,779 MiB and triggered the watchdog.
No counts or correctness comparison were produced, so this was not acceptance.

The next change removes Python lists of baseline vector components: Arrow float16
buffers supply equivalent NumPy row views. Every baseline version source is retained.
Latest description/vector stores are read only for IDs with a served version lacking
a baseline source; future versions of baseline IDs remain included. This avoids
loading a second text/vector copy that the baseline override would never use.
Regression starts red when a baseline-only replay requests latest inputs, then
passes with the preserved baseline text/vector used instead.

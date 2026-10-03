# ADR-0381: Replay selects whole Job lifecycles and keeps all-Job Dormancy evidence

Accepted by the owner on 2026-10-02. The frozen replay on PR934 reached 13,731 MiB
RSS while loading Job versions and was killed by its 12 GiB watchdog. Select IDs
ever admitted by the current tech gate, plus every complete-baseline incumbent,
before loading wide facts; retain every event for those IDs. Separately replay
all Jobs' narrow dated observations for Board-local Dormancy. Keep served duplicate
folding global so Eightfold and its backing ATS cannot be split across partitions.

Partitioning the served replay by ATS breaks cross-ATS duplicate groups, while
company partitions require a new exhaustive grouping policy. Selecting whole ID
lifecycles keeps those rules intact; discarding rejected observations themselves
would lose later edits and closure evidence. Raw facts remain unchanged, so future
wider filters recover pre-baseline unserved non-tech observations. Baseline vectors,
descriptions and version sources are retained, with bounded conversions and matrices.
Memory still grows with eligible history; whole-replay CI measurement is required.

Implementation and measurement: [Restatement memory fix](../trends/2026-10-02_restatement-memory.md).

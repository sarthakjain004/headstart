# Workday listing-response reproduction

Manual GitHub-runner reproduction for the 3,912 Workday listing `JSONDecodeError` events in the
2026-09-12 five-run audit. The input inventory is the audit's 3,057 distinct raw Boards.

The probe partitions that inventory across four runners and compares direct, fixed-WARP, and
classified-non-JSON-triggered rotation. It is read-only: one first-page POST per Board and arm.
Output is uploaded by the manual `probe-workday-400` workflow; no pipeline state is read or written.

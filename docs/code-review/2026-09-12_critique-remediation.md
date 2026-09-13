# Critique remediation — 2026-09-12

Implementation follow-up to [the pinned critique](2026-09-12_src-headstart-critique-129ca666.md).
Changes are applied to the current working tree, preserving pre-existing work.
The historical 7.3/10 assessment is not silently rescored.

The user confirmed: preserve deferred product choices and add measurements/disclosures;
use a durable authoritative-Board journal; keep HF with durable opt-out intent and
conditional Subscription writes. No production mutation or deferred-feature rollout
is authorized by this fix pass.

| Finding | Work | Status |
| --- | --- | --- |
| HS-C01 | Durable complete-Board journal through join/sync; empty/full/error/truncation and two-absence checks | Implemented; targeted tests passed |
| HS-C02 | Opt-out marker, explicit re-enable, no Invite-driven recreation | Implemented; targeted tests passed |
| HS-C03 | Distinguish missing from unreadable Subscription state; HTTP 503 | Implemented; targeted tests passed |
| HS-C04 | Current-request guards for Search/Facets, Matches and Trends; deleted-set invalidation | Implemented; JS and desktop/mobile browser checks passed |
| HS-C05 | Shared preparation progress every five seconds or 500 completed Jobs | Implemented; targeted tests passed |
| HS-C06 | Conditional Subscription commits and atomic Saved-set projection changes; failed-toggle rollback | Implemented; adapter and Space endpoint tests passed |
| HS-C07 | Compressed per-Board freshness history and per-ATS protected-row/age reporting | Implemented; synthetic transition tests passed; production history starts after deployment |
| HS-C08 | Lifecycle/order tests, isolated sync/async Oracle tests, enforced schema checks and Chromium smoke in CI | Implemented; clean-environment verification in progress |
| HS-C09 | Cross-stage mapping contract documentation and schema/projection/ATS-shape checks | Implemented; checks passed |
| HS-C10 | Shared delivery HTTP implementation preserving transport error types | Implemented; transport and HTTP tests passed |
| HS-C11 | Fresh HF reference plus bounded live description comparison | Implemented and run; deferred refresh policy preserved |
| HS-C12 | Saved-filter, delivery and stable-id refresh limits disclosed in the UI | Implemented; browser checks passed; product choices preserved |

Final verification and remaining limitations will be recorded here before handoff.

Supporting material: [source contracts](../agents/source-contracts.md),
[freshness measurement semantics](../pipeline/2026-09-12_freshness-measurements.md),
[ADR-0141](../adr/0141-complete-empty-boards-carry-authoritative-scrape-evidence.md),
and [ADR-0142](../adr/0142-subscription-opt-outs-survive-record-replacement.md).

# ADR-0067: A department names the org, not the role

- Status: Accepted
- Date: 2026-08-19
- Amends [ADR-0017](0017-tech-role-filter.md) on where the non-software disqualifier reads from,
  and on the reach of its self-consistency verification gate

## Context

ADR-0017 set one hard constraint on the tech gate: **"a non-tech job creeping through is
acceptable, dropping a real tech job is not"** (recall ≈ 100%). Its precedence is: a strong
software signal wins outright; else a generic role token (`engineer`/`developer`/…) plus a
non-software qualifier (`mechanical`, `sales`, `civil`, `hardware`, …) means not tech; else a
generic token alone means tech.

`classify()` evaluated every tier against `f"{title} {department}"`. For the strong tier and the
generic tier that is right — both are recall *boosters*, and more text can only help. For the
**disqualifier** it inverts the constraint, because the disqualifier is the one tier that can
*remove* a job. A non-software department was therefore vetoing a title that was itself tech.

The comment defending it said the disqualifier "never drops a genuine software role (which would
already have tripped a strong signal above anyway)". That is untrue by construction: a title
tripping a strong signal returns one branch earlier and never reaches the disqualifier. The only
titles the disqualifier can ever see are precisely the ambiguous ones the strong list does not
cover — and for those, an org label was deciding the answer.

**ADR-0017's verification gate cannot see this class.** Gate (1) is a deterministic
self-consistency check: *"no dropped job may match a strong signal"*. Every job in this class
falls to the generic tier **because** it matches no strong signal, so the gate reports 0 and is
structurally blind. The class was found by reading the precedence, not by the gate.

## Decision

**The disqualifier reads the title, and reads the department only through a discipline that names
the role.** One word is stripped from the department before the test:

```python
_ORG_NOT_ROLE = re.compile(r"\bhardware\b", re.IGNORECASE)
dept_discipline = _ORG_NOT_ROLE.sub(" ", dept)
if _NON_SOFTWARE.search(name) or _NON_SOFTWARE.search(dept_discipline):
```

A hardware org employs the engineers whose work is code — RTL design, design verification and
physical design are HDL/EDA, software by any reading — so "Hardware Engineering" in `department`
says who the role reports to, not what it is.

Stripping rather than discarding the whole department veto is deliberate: "Hardware **and
Mechanical** Engineering" must still veto on `mechanical`. That distinction is worth 14 rows.

**`sales` is deliberately not stripped.** Under Sales, "Solutions Engineer" is the pre-sales role
the filter already classifies non-tech when the title says so, and `tests/test_tech_filter.py`
pins `Sales Engineer` as non-tech. There the department corroborates rather than misleads;
stripping it would contradict a decision already asserted in a test.

## Consequences

Measured over the pre-filter corpus — **332,383 rows, 10 non-empty ATSes**: **+287 rows recovered,
0 rows lost.** The change is monotone by construction (it can only make the disqualifier fire less
often), and the measurement confirms it: no job that was kept becomes dropped, which is the
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) property — a recall
widening that cannot change an existing answer.

**Most of what it recovers is creep, and that should be stated plainly.** Of the 287, only **67**
carry any silicon/software token in the title on a deliberately generous reading
(`rtl|verification|physical design|asic|fpga|soc|firmware|embedded|silicon|dft|eda|verilog|vhdl|driver|software|systems`);
a strict EDA-only reading puts it at 34. The other ~220 are hardware production and manufacturing
roles that merely sit in a hardware org: `Senior Failure Analysis Engineer`, `Senior Production
Process Engineer`, `Thermal Design Engineer`, `Senior Battery Engineer`, `Compliance Engineer`,
`Senior Product Sourcing Engineer`.

The recovery is also concentrated rather than broad: **126 of 287 (44%) come from one employer**,
Anduril — a defence hardware manufacturer whose `Hardware Operations : Supply Chain` org supplies
most of the creep. The genuine silicon roles come from the chip companies behind it (cerebras 12,
furiosa-ai 11, arraylabs 10, d-matrix 9, mythic-ai 9).

So the honest summary is: **~67 real software roles bought at the price of ~220 non-software ones.**
Under ADR-0017's constraint — "a non-tech job creeping through is acceptable, dropping a real tech
job is not" — that trade is the correct *direction*, and the absolute numbers are small against a
332k-row corpus. But it is not the mostly-silicon recovery an earlier draft of this ADR described,
and a later reader deciding whether to widen further should start from these proportions.

**Three wider variants were measured and rejected**, all monotone. All three are measured under
the *strip* semantics this ADR ships, not the discard semantics — the distinction is worth 14 rows
on the hardware-only variant (287 vs 301) and matters here too:

| variant | recovered | why rejected |
| --- | --- | --- |
| disqualifier reads the title only | 1,234 | admits `Konstrukteur Maschinenbau`, `O-Calc or SPIDACalc Engineer`, `Transmission Line Engineer (PLS-CADD expert)`, `Servicetechniker` — clearly non-software |
| strip `sales` and `hardware` | 686 | inherits the sales contradiction below |
| strip `sales` only | 399 | recovers `GTM Engineer` (38), `Solutions Engineer` (27), `Go-To-Market Engineer` — arguably the same pre-sales class `Sales Engineer` is already pinned as non-tech |

**The measurement basis is weaker than [ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md)'s and this is a real limitation.** ADR-0066 measured against the served table pulled
fresh from HF. That is impossible here: the tech gate's input is the *pre-filter* corpus
`data/jobs/*.jsonl`, and **`data/jobs/` is not on the HF dataset at all** — the repo holds only
`lancedb`, `descriptions`, `state` and `embeddings` (689 files, verified via `repo_info`). The
pre-filter corpus is ephemeral stage output that exists only on whichever machine last scraped, so
these numbers come from a local snapshot dated 3–5 July 2026 covering 10 ATSes, with
`workday`, `zoho`, `keka`, `rippling`, `teamtailor` and `trakstar` empty and `eightfold`,
`freshteam`, `successfactors`, `oracle` and `sensehq` absent. The *direction* and the
zero-loss property are structural and hold regardless; the magnitude is indicative only, and is
almost certainly an undercount, since Workday — the ATS most likely to carry a large hardware org —
contributed nothing.

CLAUDE.md already records this — *"`data/jobs/` is gitignored but NOT on HF at all"* — and the
`repo_info` check above is an independent confirmation of it, not a new finding.

**The self-consistency gate is left as it is.** Widening it to catch this class would mean
asserting something about jobs that match *no* strong signal, which is the whole ambiguous
population — there is no cheap deterministic invariant there. That is what gate (2), the sampling
reasoning gate, is for, and it is the layer that should have caught this. Recording the blind spot
rather than papering over it.

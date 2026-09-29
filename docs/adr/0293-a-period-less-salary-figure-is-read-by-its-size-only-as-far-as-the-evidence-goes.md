# ADR-0293: A period-less salary figure is read by its size only as far as the evidence goes

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md) (its Consequences: a figure
with no period marker returns `None`) · **Relates to:**
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (how a changed answer
is measured)

## Context

ADR-0082 says a genuinely ambiguous salary figure, one with no unit and no period marker, returns
`None` rather than a guess. Three readings in `jobs/salary.py` now infer the period from the
figure's size instead, and none of them amended ADR-0082:

- `_field_adp_recruiting` reads a period-less figure under 200 as hourly
  (`_ADP_RECRUITING_HOURLY_BELOW`, `DERIVATIONS_VERSION` 20).
- `_declines_k_figure` refuses a "k" rupee figure read as annual, because annual Indian pay is
  written in lakhs and a "k" figure is monthly (v19). A stated month is read as written.
- `_field_zoho` reads a period-less zoho figure below its currency's floor as monthly pay, in a
  currency quoted by the month (`_QUOTED_MONTHLY`, #859, v22). "30000-40000 INR" is
  360,000-480,000 a year.

The #859 review found that the zoho reading went past its evidence. It read monthly any figure
`_field_generic` refused, and that included a "k" rupee figure the floor admits but
`_declines_k_figure` refuses. So "800K INR" read 9.6M a year and "600K - 900K INR" read
7.2M-10.8M.

Nine served rows took that path on 2026-09-29 (v45): eight "110K+ INR" and one "120k+ INR". Six
of their descriptions say contract (three of them six months), and the four that state experience
ask for three to nine years. An annual 110,000 rupees would be about ₹9,000 a month, so the
monthly reading is the right one. Every "k" rupee figure a
served string gives a period for says a month, up to 270K. There are 26 of them: 3 salary fields
and 23 held descriptions. None says a year, and no larger figure states a period. No served row
states "800K INR".

## Decision

1. **A period-less figure is read by its size only where the served evidence settles its period,
   and only as far as that evidence reaches.** Past that point it reads as nothing, as ADR-0082
   says. The evidence sits in the comment beside the constant that draws the line.
2. **A "k" rupee figure reads as monthly on zoho only below 300K** (`_MONTHLY_K_RUPEES_BELOW`).
   The largest one stated with a period is 270K. "110K+ INR" is still 1,320,000 a year. "800K INR"
   and "600K - 900K INR" read as nothing, and the zoho guard keeps the spliced description figure
   from answering for them.
3. **A new size reading lands with its evidence, a test on each side of its line, and an
   ADR-0066 measurement.**

## Alternatives considered

- **Read by the month only a figure below the floor** (the review's suggestion). Rejected: all
  nine served rows would read as nothing, although their own descriptions and all 26 stated "k"
  rupee figures say monthly. It removes nine right answers to prevent a wrong one that no served
  row has.
- **Never infer a period (ADR-0082 as written).** Rejected: it drops the 668 zoho monthly readings
  served on v45 and adp_recruiting's hourly ones. Those answers were measured right, and the
  alternative was serving monthly pay as annual.

## Consequences

- No stored row moves. Old and new `extract()` agree on all 500,568 rows of the served table
  (v45, read 2026-09-29), each with its held description where the store has one.
  `DERIVATIONS_VERSION` stays 22.
- "120k+ INR" reads 1,440,000 while "120000+ INR" reads an annual 120,000. The "k" is evidence
  of monthly pay; a figure written in full is not. INR figures from 100,000 to 199,999 that give
  a period state a month 50 times in 58 (#859's count on v277). #859 still kept them annual,
  because the floor admits them. Whether to raise INR's floor is a separate call.

# ADR-0321: An agent reads Hiring now by opened less closed, and every trend view states its turnover span first

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0272](0272-an-agent-reads-hiring-as-postings-opened-and-closed.md) (the MCP reads hiring as
postings opened and closed), [ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
(turnover), [ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) (Hot ranks Company directory
entries at boot), [ADR-0238](0238-a-mostly-re-counted-line-gives-no-percentage-and-hot-hides-only-staffing-and-job-boards.md)
(Hot's Operators), [ADR-0270](0270-the-index-view-takes-a-counting-change-out-too.md) (the index
view sizes its counting changes), [ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) (the category list
that retired the old ones), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the agent contract)

## Context

The second independent critique of the Space MCP server scored it 6.0/10 on 2026-09-29. Four of
its findings are about trends and Hiring now. Each was measured on the live server, at the
newest tick 2026-09-29T04:04:35:

- **P1-3: `hiring_now` led with rows its own flags disowned.** Expansion, the default, put Bosch
  Group first at net +442 on 23 postings opened and 33 closed, flagged "mostly re-counting". 7
  of its 15 rows were flagged. Volume led with New York Life, 504 opened against 25 open now with
  its closures not counted, and it was not flagged. The checks for "more opened than open now"
  and "closures not counted" ran only on Rate, where 7 of 10 rows were flagged "small base".
- **P1-4: a company breakdown hid how short turnover is.** "OpenAI: 9 opened, 5 closed" over a
  14-day window gave no word that turnover covered only the last 3.4 days. The note was written
  only by the first row, and a company breakdown has no first row.
- **P1-2: a long window could not answer, and did not say so first.** Turnover began at
  2026-09-25 18:16, so it covers part of every window. A window of 1 to 7 September had no hiring
  figure at all. Its lines also carried retired categories ("Security Engineering", "AI / Machine
  Learning") that cannot be lined up with today's. A future `since` got "No trend counts fall
  between 2027-01-01 and now".
- **P2: the legend and the instructions.** A concise whole-index answer was 3,706 characters, and
  1,325 of them were a legend of near-identical counting-change sentences. "[8] growth rescaled
  when …" was never explained. The server's instructions said "Numbers match the HeadStart
  website". The figures do match, but the website calls re-counting "hiring" and this server
  does not.

The website was read on 2026-09-29, through the page code of `main` against the live Space's
routes, over 30 days:

- Its index sentence now gives turnover, "about 920 more closed than opened — about 19,000
  opened, 20,000 closed since Sep 25". This agrees with the MCP.
- Its category table and tiles still call the change in openings, less the sized steps,
  "Hiring". For example: "Biggest riser: Software Engineering +66.4%", and a Hiring column of
  "+27,709 openings" for a category with 3,271 postings opened and 3,507 closed.

**What a lens on opened less closed would rank.** Measured on the HF `data/state` snapshot of
that tick, ranking every candidate the way `hot_ranking.rank` does:

- **2,195 candidates.** 1,727 have a closed count, and 1,602 have one counted on every Board.
- **Left out:** 329 opened postings with their closures not counted, and 125 had closures uncounted
  on some of their Boards.
- **446 companies have a positive opened less closed** with every Board's closures counted. With
  partial counts allowed there would be 461, among them NVIDIA (closures uncounted on 2 of 3
  Boards) and KLA (on 5 of 6).

## Decision

The owner decided four things: every artifact check applies on every Lens; a new Lens ranks
postings opened less closed; it is the MCP's default; and the site's Lenses keep the site's order
but list flagged rows last.

**Hot ranking (`trends/hot_ranking.py`, served by `/hot`):**

1. **A fourth Lens, `opened_less_closed`.** It ranks Opened − Closed over the trailing week's
   runs, largest first, among positive figures.
   - A company is ranked only when its closures were counted on every Board in scope. A partial
     closed count would make the figure high by exactly what it missed.
   - Each row carries `opened_less_closed`. It is None where the closures were not counted on
     every Board.
   - `counts.closures_partly_uncounted` counts the companies left out for a partial count.
     `closures_uncounted` still counts those whose closures were not counted at all.
   - It is ranked at boot beside the other three, from the same candidate rows, so it adds one
     sort. The whole ranking measured 49–77 s locally on this history.
2. **The agent contract rises to 8** (`_AGENT_API_VERSION`, `space_client.AGENT_API`). An MCP
   server that asks for the new Lens refuses a Space too old to serve it.

**`hiring_now`:**

3. **`opened_less_closed` is the default Lens.** Its header says "largest first", not "in the
   site's order". On the snapshot above, Wipro leads at +93 (344 opened, 251 closed), then
   Starbucks at +50 and AMD at +21.
4. **Every Lens applies every check.** A row is flagged when:
   - its net is not backed by its postings opened and closed;
   - its closures were not counted;
   - it opened more postings than are open now;
   - on Rate only, its base is small, because only Rate ranks by a share.

   A line under the rows counts each kind of flag and says what to report instead.
5. **Flagged rows go last on the site's Lenses.** On Expansion, Volume and Rate:
   - The first `limit` rows the page shows are listed unflagged first, each group in the site's
     order. The answer covers the same companies as the page's top `limit`, only reordered.
   - Each row then gives its place on the page ("site #3"), and one line says so.
   - Expansion now leads with AgileEngine (site #3), and Bosch Group follows as row 5. Volume
     leads with Wipro, and New York Life is row 6.
   - `opened_less_closed` keeps its own order. No flag questions its figure: an unbacked flag
     questions the site's net, and "more opened than open now" is churn, which cancels in opened
     less closed.
6. **The budget rises to 25,000 characters.** Fifty rows at their longest, each with every flag,
   measured 20,811 characters. The closures flag is two words per row, because the line under
   the rows explains it.

**`read_trends`:**

7. **Every view whose window starts before turnover began leads with one plain sentence,** before
   any figure: "HeadStart can measure hiring, as postings opened and closed, only from 2026-09-25
   18:16: 3.4 of this window's 29.9 days. Over the whole window it cannot say whether hiring rose
   or fell; the opened and closed below are those 3.4 days'."
   - It is written from `turnover_since`. It does not depend on the view having a first row, so
     the company breakdown now says it too. There, the notes on runs left out and closures
     uncounted go in the header as well.
   - A window that ends before turnover began says it has no hiring figure, and that its openings
     listed mix hiring with re-counting.
8. **A retired category names its successor:** "Security Engineering (retired; now Security)".
   - The successor comes from `config/role_families.json` (`role_families.successor`).
   - One line says its jobs were re-sorted, so it does not line up with the successor's figures in
     a later window. The config's successor is the family that took the old one over, not a
     job-for-job map. `hardware-embedded` split between Embedded & Firmware and Hardware &
     Silicon, and the config names the first.
9. **A `since` or `until` after today (UTC) is refused.**
10. **The legend names each counting change by short tags,** "[4] category list + duplicate check
    + category sorting". The tags come from the words `netting.METHODOLOGY_WORDS` gives each field.
    - One "Tags:" line glosses each tag once.
    - A rescaled growth refers to its change by number, "[8] growth rescaled by [1]". The glossary
      explains it: where taking change [n] out would have left a line below zero, HeadStart scaled
      the line's earlier growth down instead, and the figure is the growth that scaling removed.
    - A label not in the Space's "we …" form keeps its words.
    - The concise whole-index legend went from 1,325 to 848 characters (answer 3,706 → 3,351), and
      the 365-day full one from 2,315 to 1,234 (answer 8,906 → 7,947).

**The server's instructions** now say: "Figures are the HeadStart website's, but only postings
opened and closed are called hiring here; the website's Trends table also calls re-counting
hiring."

**The website's Hot tab shows the new Lens.** It took one radio in `hot.html` and one
`HOT_MEASURE` entry in `app.js`: "Opening more than closing: roles opened minus roles closed". The
note under the list says which companies it leaves out. Growing's hint read "opening more than
they close", which is this Lens's meaning, not Expansion's. It now reads "more open roles than a
week ago". The site's default Lens is unchanged.

**The evaluation:**

- `hot_top` judges a ranking answer by the order `hiring_now` lists (`hiring_now.in_answer_order`).
- Iteration task t04 asks on the default Lens.
- A new task, t27, asks whether hiring rose between 1 and 7 September. It passes only if the tool
  said it cannot tell and the answer passes that on.
- The sealed held-out file is untouched. Its `hot_top` tasks are now judged by this order.

## Options rejected

- **Re-rank the whole top 100, not the page's first `limit`.** Every row shown would be unflagged,
  but the answer would name companies the page's top `limit` does not show, and a flagged row
  would vanish rather than be reported. Reordering within the cut keeps the answer's companies the
  page's.
- **Rank on opened less closed with partial closed counts allowed.** That adds 15 companies, and
  each one's figure is high by the closures its uncounted Boards missed.
- **Flag a small base on every Lens.** "Small base" is about a share moving far on one posting. On
  a Lens that ranks counts it questions nothing.
- **Map a retired category's figures onto its successor's line.** Its jobs were re-sorted job by
  job, so summing old and new lines under one name would state a continuity the counts do not
  have.
- **Keep "Numbers match the HeadStart website".** Every figure matches, but the word "hiring" does
  not. An agent that checks the site's table finds +27,709 "Hiring" where this server reports a
  net of −236.

## Consequences

- **The default answer changes meaning.** "Who is hiring this week" is now read off postings
  opened less closed, over the 3.4 days turnover covers of the week, and the header says so.
  Expansion is still one argument away, reordered as above.
- **An MCP server from before this change still reads a Space after it.** The new Lens is only
  added to `/hot`. A server from this change refuses a Space below agent contract 8, as ADR-0253
  intends.
- **The site and the server now disagree on order where the site leads with a flagged row.** Each
  reordered row says its place on the page, so the two can still be matched.

### Deferred: shrinking the unsized rest in every view

ADR-0272 found that `/trends` computes `recounted` per line and run but does not serve it.
ADR-0270 sized the index view's counting changes. ADR-0304, which merged while this change was in
review, sizes its Boards found from the first per-Board count on, but not its duplicate removals.
Serving a line's re-counted sum as a sized "not hiring" cause would size the rest of the
re-counting exactly on the runs turnover counts. Every category and company view would gain it
too, because `recounted` is summed per line.

ADR-0272's measurement bounds the gain. On the 30-day whole-index window, the runs turnover counts
re-counted +19,473. Before ADR-0304 the unsized rest was +135,545, so the gain was about a seventh
of it. ADR-0304's found Boards now take a share of both, which this change did not measure again.
The rest is re-counting before turnover began.

The change is not cheap or safe here, for three reasons:

- `LineMove.turnover`, `check_reading` and the page's `checkReading` would all change.
- `hot_ranking` reads the same `Turnover`.
- The website reads that reading.

It stays deferred, as ADR-0272 left it.

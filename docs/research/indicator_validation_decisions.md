# Indicator validation — decision worksheet (G-Q)

Status: **options only; nothing locked.**  
Does not amend `g-p-v1` (`docs/research/indicator_forward_validation_protocol.md`). Filling Section 8 of that protocol happens only after explicit user choices on this sheet, and still **before** any post-discovery Spearman is computed.

Discovery JSON is context, not a menu of “winning” cells. Recommendations below are justified by **neutrality of a future test**, not by which G-N/G-O ρ looked stronger.

Protocol version this sheet refers to: `g-p-v1` (not yet decision-locked).  
Worksheet version: `g-q-v1`.

---

## How to use this sheet

For each heading: read the question, options, suggested default, then record an approval. Do not compute test ρ, extend the window, or change cuts after seeing numbers.

Suggested defaults are **not** applied until you approve them.

---

## 1. Universe policy

### Decision question

Which set of symbols is eligible at each test timestamp `t`, and how are listings and delistings treated?

### Bias already present in discovery

G-N/G-O used `data/processed/universe.json` dated **2026-10-11** on the whole April–October 2026 span.

- **Survivorship:** names that would have been in an earlier universe but were gone by 2026-10-11 never entered the sample. Failures, delistings, and rank dropouts are under-represented.
- **Listing lag / backfill:** names listed late (short files such as HYPE in G-M) appear only after they exist, but the *membership rule* is still “in the October 11 top-200,” not “in the universe as of `t`.” Applying a **current** list to **past** bars repeats that bias.
- **Intersection-only survivors:** keeping only names present in every month of a window over-weights names that stayed listed and liquid for the whole test. That is a second, tighter survivorship filter.

### Options

| ID | Policy | What it does | Advantage | Disadvantage |
|---|---|---|---|---|
| U1 | **Freeze at test lock** | One universe file (path + hash) recorded when the test is locked; same 200 names for every `t` in the test span | Comparable to G-N/G-O method; no mid-test membership churn; implementable with the current universe snapshot | Repeats survivorship/listing-lag *if* that snapshot is applied to any bar before the snapshot date. For a **forward** test that starts **after** the snapshot, membership is “who is in the list when we lock,” which is a live-book universe, not a reconstructed PIT universe |
| U2 | **Point-in-time membership** | At each `t`, only symbols that satisfy the universe rule *as of `t`* | Least survivorship if a dated membership series exists | This repo did **not** store a dated universe series in G-N/G-O. Building it now would be new data/process work, not part of G-Q. Until that series exists, U2 cannot be executed honestly |
| U3 | **Intersection over the test span** | Symbol must be in the lock-time universe **and** have valid pairs in every comparison window (e.g. every test month) | Balanced panel; fewer missing cross-sections | Extra survivorship: names that list mid-test or miss a month are dropped everywhere. Not how a live ranker would have traded |
| U4 | **Freeze at lock + listing delay, no backfill of later leavers** | Start from U1. A name may enter only after a **pre-set** count of closed 4h bars on that name *after* listing. A name that leaves the live universe is **not** inserted into earlier `t`. Mid-test dropouts are excluded only from `t` after dropout **if** a membership series exists; otherwise treat dropout as “still in frozen list until test end” and **label** that limitation | Slightly closer to a live book than U3 without requiring a full PIT tape | Still not true PIT. Dropout handling is incomplete without dated membership |

### Suggestion (not locked)

**U1 for the first future test**, with an explicit label: “frozen lock-time universe, not point-in-time.” Reason: U2 is not executable with stored artifacts; inventing a PIT tape as part of G-Q would mix new data work into a decision sheet. U3 adds survivorship that a live process would not have applied. U1 matches how G-N/G-O were actually run, so a sign comparison is a comparison of **method-plus-bias**, not a silent change of universe construction.

Do **not** choose U3 because short-history names looked noisy in discovery.

Optional later version (`g-p-v2`): implement U2 once dated universe snapshots exist, on a **new** unseen span.

### Needs your approval

- [ ] U1 freeze at lock  
- [ ] U2 PIT (only if you accept that it is **blocked** until a dated universe series exists)  
- [ ] U3 intersection  
- [ ] U4 freeze + listing delay (also set the delay in closed bars: **to be filled by you**, not from G-M min bars)  
- Listing delay if U4: _____ closed 4h bars  

---

## 2. Test start and end

### Decision question

When may the future test **start**, when is it **forced to end**, and what is forbidden after looking at results?

### Unseen-data requirement (not a calendar invention)

Discovery used closed 4h bars **2026-04-18 08:00 UTC through 2026-10-11 12:00 UTC**. A pair `(t, t+h)` is **discovery-contaminated** if `t` or `t+h` lies in that span or in any file that was already joined in G-N/G-O.

The test may start only when **all** of these hold:

1. Local processed 4h files contain closed candles with open time **strictly after** 2026-10-11 12:00 UTC.
2. For every locked horizon `h ∈ {1,3,6,12}`, there exist pairs with both `t` and `t+h` **after** discovery end (so h=12 needs twelve closed 4h steps past the first eligible `t`).
3. No indicator–forward Spearman (or equivalent rank association) has been computed or inspected on that new span.
4. This worksheet’s approved rows and `g-p-v1` Section 8 are written down **before** step 3 would run.

**Do not fill `test_start_ts` / `test_end_ts` with guessed clock times in this document.** Those fields are recorded at lock time from actual files.

### Options for duration (end rule)

| ID | End rule | Advantage | Disadvantage |
|---|---|---|---|
| T1 | **First complete UTC month** after discovery in which h=12 endpoints also lie in that month or immediately after, with start = first eligible `t` after discovery | Calendar is independent of observed ρ; one month is a natural G-O reporting grain | One month can be a single regime; CS timestamp count is still serially dependent |
| T2 | **Fixed count of eligible timestamps** (e.g. 180 closed 4h slots ≈ 30 days of a 6-bar/day calendar), counted *before* seeing ρ | Duration does not depend on month boundaries | The count must not be chosen to match G-N pair totals |
| T3 | **Fixed wall-clock span** recorded at lock (e.g. “from first eligible `t` through date D”), D set without ρ | Simple | Tempting to move D after a weak or strong print |
| T4 | **Open-ended / optional stopping** | More data | **Invalidates** confirmatory reading: stopping when ρ “looks stable” is peeking |

### Peeking / extension risk

Extending the end **after** seeing test ρ (or after a preliminary script print) is sequential testing. A later “we just waited another month” is a new look. Under `g-p-v1` that is a **new research version** and the peeked span is no longer unseen.

Running the discovery scripts on new files “just to check n” still peeks if ρ is in the output. Count-only probes must not compute Spearman.

### Suggestion (not locked)

**T1**, with a hard ban on T4. Reason: month grain is already how G-O *reports* (not how it *selected* winners). Duration is a calendar event, not a function of discovery \|ρ\| or pair counts (~191k–202k). Record `test_start_ts` / `test_end_ts` / `now_ms` only at lock, from files.

If you prefer a round number of bars instead of a calendar month, use T2 and **you** pick the count here without looking at G-N n.

### Needs your approval

- [ ] T1 first complete UTC month  
- [ ] T2 fixed eligible-timestamp count: _____  
- [ ] T3 fixed end date D: _____ (fill only at lock, not in this chat from memory)  
- [ ] Confirm **no optional stopping** (reject T4)  
- [ ] Confirm no Spearman on post-discovery files until lock  

---

## 3. Minimum sample size

### Decision question

What floors make a result **reportable**, separately for timestamps, pooled pairs, and per-symbol series? Floors are **not** power calculations fitted to discovery ρ.

Do **not** set minima so that G-N’s ~191k–202k pairs or G-O’s seven months automatically “pass.” Those are discovery sizes, not out-of-sample requirements.

Cross-section **n < 10 timestamps dropped** stays as in `g-p-v1` unless you explicitly version-change it **before** the test.

### Why the three counts are different

| Count | What it measures | Why a floor exists | What a large number does *not* mean |
|---|---|---|---|
| Eligible timestamps | Distinct 4h market states with ≥10 symbols | Shared shocks; this is the relevant order of magnitude for panel dependence | Consecutive 4h stamps are still autocorrelated |
| Pooled `(symbol, t)` pairs | Stacked panel rows | Spearman needs ≥2 non-constant pairs to be defined | **Not** independent n; do not use this as i.i.d. sample size |
| Per-symbol pairs | Length of one name’s series | A per-symbol ρ needs ≥2; a **distribution** of per-symbol ρ needs many defined names | Short names (listing lag) will fail the floor; that is a feature of U1/U3, not a reason to lower it after seeing who failed |

### Options

| ID | Floors (illustrative, not fitted to G-N ρ) | Notes |
|---|---|---|
| N1 | **Calendar-first:** no extra numeric floors beyond “T1/T2/T3 completed” + existing n&lt;10 CS rule + Spearman’s definedness (≥2, non-constant) | Honest if duration is locked. Weak months/phases stay in the log as undefined when n is tiny |
| N2 | **Reportability floors independent of discovery ρ:** e.g. ≥90 eligible CS timestamps; pooled pairs ≥ number of eligible timestamps × 10 (the CS minimum, i.e. every kept `t` already has ≥10 names); per-symbol ρ enters the distribution only if that name has ≥12 pairs at that horizon (one non-overlap cycle at h=12) | 90 ≈ 15 days of 4h bars; 12 is structural (horizon), not a G-O monthly 30. Still arbitrary — you must accept or replace the numbers **now** |
| N3 | Keep G-O `MONTH_MIN_N = 30` for monthly cells only; other analyses use N1 | Continuity of *method*, not because April/October remained defined in discovery. Must be chosen as continuity, not as “30 made those months look extreme” |

### Suggestion (not locked)

**N1 + N3:** lock duration with Section 2; keep monthly undefined below 30 **as a reporting convention carried from G-O**, not as an optimized power threshold; do not add a pooled-pair floor copied from ~200k. If a test month or phase is below 30, it is **undefined in the log**, not dropped quietly, and does not by itself void the whole test (see Section 7).

If you want a numeric timestamp floor anyway, pick it here as a round calendar translation of T1/T2 (e.g. 6 bars/day × days in the locked month), not from G-N.

### Needs your approval

- [ ] N1 calendar-first only  
- [ ] N2 with your numbers: timestamps _____ ; pooled _____ ; per-symbol _____  
- [ ] N3 monthly min 30 (method continuity)  
- [ ] Monthly min other: _____  
- [ ] Keep CS skip at n&lt;10 (default from `g-p-v1`)  

---

## 4. Uncertainty intervals

### Decision question

How (if at all) to attach an interval to Spearman ρ given (a) serial 4h features, (b) overlapping `h>1` forward windows, (c) many names at the same `t`?

### Why i.i.d. intervals are not default

Pooled n on the order of 10^5 treats every `(symbol, t)` as independent. That **understates** uncertainty: one market shock hits all names at `t`, and h-bar returns share `h−1` overlapping intervals. Naive Spearman p-values and Fisher-z intervals with n = pair count are **too narrow**. If the method cannot respect that, **omit** a numeric interval rather than print a false tight band.

### Options

| ID | Method | Clustering | Advantage | Disadvantage |
|---|---|---|---|---|
| I0 | **No interval** on pooled ρ; report n_pairs, n_timestamps, n_symbols only | None claimed | Does not fake precision | No calibrated uncertainty |
| I1 | **Timestamp-block bootstrap**: resample eligible `t` with replacement; keep the whole cross-section at each drawn `t`; recompute pooled and CS summaries | Time cluster (contemporaneous) | Matches the main dependence G-O highlighted (same `t`) | Ignores auto-correlation across adjacent 4h stamps; overlapping h still inside each block |
| I2 | **Calendar-block bootstrap** (e.g. blocks of 6 consecutive 4h stamps = 1 day, or 42 = 1 week) | Time cluster + short serial | Better for overlapping windows | Block length is a tuning choice and must be locked **now**, not after seeing interval width |
| I3 | **Two-way cluster** (symbol and time) or cluster-robust SE on ranks | Symbol + time | If a few names dominate | Heavy; Spearman is not a linear regression; easy to implement incorrectly |
| I4 | **Analytic i.i.d. Spearman SE** | None | Cheap | **Rejected as default** under `g-p-v1` §7 |

Phases already thin overlap; they are **not** an interval. They are a sensitivity table.

### Suggestion (not locked)

**I0 for pass/fail** (if any), plus **I1 as a descriptive interval** if an interval is required at all, with the limitation “contemporaneous cluster only” written on the figure. Do not lock I2’s block length from G-O monthly volatility. Do **not** use I4.

If you want I2, you must pick the block length here: 6 (1d) or 42 (1w) are calendar choices, not discovery-ρ choices.

### Needs your approval

- [ ] I0 none  
- [ ] I1 timestamp-block bootstrap (state B: _____ resamples, locked now)  
- [ ] I2 calendar-block length: _____ bars  
- [ ] I3 two-way (only with a written estimator before the test)  
- [ ] Confirm I4 not used  

---

## 5. Stability criteria

### Decision question

What does “the relationship held” mean, split into **sign**, **period variation**, and **magnitude**? Which one is primary?

G-P question A is about **sign** of Spearman on unseen bars. Magnitude and month/phase scatter are different questions. Do not collapse them after seeing which one “passes.”

### Options (treat as separate meters)

| Meter | Examples of a locked rule | Role |
|---|---|---|
| **Sign** | Pooled ρ sign equals a **pre-written** expected sign, or equals discovery sign **only if** you explicitly choose “replication of discovery sign” as the hypothesis (that is still a choice, not automatic) | Candidate **primary** for question A |
| **Period variation** | Same sign in every UTC month of the test span that meets the monthly floor; or in at least k of m phases at h=3,6,12 | **Secondary**; k/m must be set now. Do not set k to match G-O’s “most phases negative” counts |
| **Magnitude** | e.g. \|ρ\| above a number | **Do not** import 0.04 from discovery pooled \|ρ\|. Any cutoff is a new policy and is **to be filled by you** if you want magnitude as a gate |

### Suggestion (not locked)

- **Primary:** sign of **pooled** Spearman, on whatever primary cells Section 6 locks.  
- **Secondary (report, not gate unless you say so):** CS median sign; phase signs; monthly signs.  
- **Not a gate:** magnitude, unless you write a cutoff that is **not** G-N’s observed \|ρ\|.

This keeps the confirmatory claim close to G-P question A and avoids turning G-O’s phase table into a post-hoc hurdle.

### Needs your approval

- Primary meter: [ ] sign  [ ] period variation  [ ] magnitude  
- If sign: compared to [ ] pre-written expected sign per feature (table below)  [ ] discovery sign  [ ] no expected sign (descriptive only)  
- Period variation as [ ] gate  [ ] report only; if gate, rule: _____  
- Magnitude cutoff: [ ] none  [ ] _____ (not copied from G-N)  

Pre-written expected signs (fill only if you want them; **do not** copy G-N just to “win”):

| Feature | Expected sign (blank = none) |
|---|---|
| `close_vs_sma50_pct` | |
| `ma_spread_pct` | |
| `roc6_pct` | |
| `rsi14` | |
| `macd_histogram` | |
| `adx14` | |
| `atr_over_close` | |

---

## 6. Multiple testing and primary hypothesis

### Decision question

The grid is **7 features × 4 horizons = 28** pooled cells, plus CS, phases, months, and demeaned stacks. That is many looks on one panel. Discovery already spent looks on the same definitions.

**Forbidden:** naming as primary the cell with the largest \|ρ\|, the most stable G-O phases, or the “repeated observation” list in G-P. That would bake discovery peeking into the confirmatory test.

### Options

| ID | Policy | Multiple-comparison load | Advantage | Disadvantage |
|---|---|---|---|---|
| M1 | **No confirmatory cell.** All 28 pooled cells + CS/phase/month tables are **pre-specified descriptive reports**. There is no pass/fail on ρ | Avoids pretending 28 tests are one | Does not “confirm” association |
| M2 | **One operational primary, not a winner cell:** e.g. family of **all 7 features at horizon 1 only** (next closed bar; shortest overlap). Longer horizons secondary, unadjusted or Holm-within-family separately | 7 tests in the primary family, not 28 | h=1 is justified by **overlap/operations**, not by roc6’s discovery \|ρ\| at h=1 | Still 7 tests; features remain correlated with each other |
| M3 | **Holm (or Bonferroni) on all 28 pooled cells** | Harsh; many true weak effects will miss | Pre-specified family | Independence is false; Bonferroni with dependent cells is conservative in unknown degree |
| M4 | **Single (feature, horizon) pair** declared now for a **non-ρ** reason (e.g. “score uses three of the features at the last bar” is still **not** a reason to pick the strongest of those three) | One test | Easy to game by picking a discovery favorite | Only valid if the reason is operational and written **without** citing G-N/G-O magnitudes |

### Suggestion (not locked)

**M1** for the first unseen run: the honest statement after a 28-cell discovery scan is “repeat the **report**, do not mint a winner.” If you want a confirmatory family, **M2** (all features, **h=1 only**) is the option that does **not** use G-P’s repeated-observation list to pick features.

Do not auto-select `rsi14` or `close_vs_sma50_pct` because they were labelled repeated in G-P.

### Needs your approval

- [ ] M1 descriptive grid, no ρ pass/fail  
- [ ] M2 primary family = 7 features × h=1; adjustment: [ ] none  [ ] Holm  [ ] other _____  
- [ ] M3 28-cell Holm/Bonferroni  
- [ ] M4 single cell: feature _____ horizon _____ ; non-ρ reason: _____  

---

## 7. Invalidation and restart

### Decision question

When is the **whole test void**, when is only a **cell undefined**, and when does a protocol edit force a **new version and a new unseen span**?

### Separate failure classes

| Class | Meaning | Typical action |
|---|---|---|
| **Missing files / universe hash mismatch** at lock | Cannot reconstruct the locked snapshot | Do not run; fix process; still no Spearman until re-lock |
| **Data quality** | Gaps (G-M had 0 on discovery files; test files may differ), incomplete bars treated as closed, timestamp misalignment, `now_ms` after a bar that is still open | If quality rules in `g-p-v1` §5 are broken, **void the run**. If a minority of symbols have gaps, those pairs are dropped by existing gap rules; that is **cell-level missingness**, not automatically a void — unless you set a maximum-gap policy now |
| **Insufficient sample** | Duration T* not reached; or approved N* floors not met when the locked end arrives | **Inconclusive**, not a negative ρ result. Do not extend the window (Section 2). Restart only after a **new** lock on a still-unseen remainder (peeked incomplete run contaminates that span if ρ was printed) |
| **Protocol violation** | Spearman before lock; changing features/horizons/universe/n&lt;10/demean definition after seeing ρ; overwriting G-N/G-O JSON; mixing discovery bars into the test | **Void** as `g-p-v1` test. Further work is `g-p-v2` on data that is still unseen **after** the new rules are written |

### Cell vs whole test

- Spearman undefined (`need_at_least_two_pairs`, `constant_feature`, `constant_forward_return`, month n below floor): **report the reason**; do not treat as ρ = 0; **do not void** the whole test.
- CS timestamps with n&lt;10: skip that `t`; count skips; void only if you locked a maximum skip rate and it is exceeded (**to be decided**: skip-rate cap _____ or none).
- One feature missing for one symbol: drop those pairs; other features still report.

### Restart and versioning

| Change | New protocol version? | New unseen calendar span required? |
|---|---|---|
| Filling this worksheet **before** any post-discovery ρ | No (`g-p-v1` Section 8 completion) | No extra span beyond Section 2 |
| Changing U*/T*/N*/I*/M* **after** test ρ exist | Yes (`g-p-v2`) | **Yes** — the peeked test span is burned for confirmatory use |
| Changing indicator formulas, score, or adding a feature | Yes; also out of scope for this research track unless separately authorized | Yes |
| Count-only file inventory with **no** correlation | No | Span remains eligible |

### Suggestion (not locked)

- Whole-test **void:** protocol violation; treating incomplete bars as closed; computing ρ before lock.  
- Whole-test **inconclusive:** locked T* end reached but approved floors not met — **stop**, do not stretch.  
- **Cell undefined:** existing Spearman/month/CS skip reasons.  
- No skip-rate cap unless you add one now (suggestion: **none**, report skip counts).  
- Any post-ρ rule change → `g-p-v2` + new unseen data.

### Needs your approval

- [ ] Accept void vs inconclusive vs cell-undefined split above  
- [ ] Gap policy: [ ] existing G-N drop-misaligned-pairs only  [ ] other: _____  
- [ ] CS skip-rate cap: [ ] none  [ ] _____  
- [ ] Confirm peeked incomplete runs cannot be extended to “make n”  

---

## Decision log (blank until you answer)

| # | Heading | Your choice | Date |
|---|---|---|---|
| 1 | Universe | | |
| 2 | Start/end | | |
| 3 | Minima | | |
| 4 | Intervals | | |
| 5 | Stability primary | | |
| 6 | Multiple testing | | |
| 7 | Invalidation | | |

After this table is filled, a dated amendment may complete `g-p-v1` §8. Until then, `g-p-v1` remains **not decision-locked**, and any correlation run on new bars is descriptive at most.

This worksheet does not change production scores, strategies, `portfolio_sim`, broker, OHLCV, or indicator formulas.

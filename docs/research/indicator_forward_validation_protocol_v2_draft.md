# Indicator–forward-return validation protocol — `g-p-v2` **DRAFT**

Status: **draft for review. Not locked. Not in force.**  
Do not run Spearman on post-discovery bars under this file until you explicitly approve it and a dated lock record exists.

This draft merges `g-p-v1` (`docs/research/indicator_forward_validation_protocol.md`) with the G-Q2 choices below. It does **not** replace or edit `g-p-v1`. The G-Q worksheet (`docs/research/indicator_validation_decisions.md`) is unchanged.

Discovery sources (read-only):

- `data/experimental/g_n_indicator_forward.json`
- `data/experimental/g_o_forward_robustness.json`
- `data/experimental/g_m_indicator_readiness.json`
- Code: `src/tradingbot/research/` — formulas unchanged by this draft

This draft does **not** claim that a future test has been run. G-N/G-O remain discovery only.

---

## Draft vs locked

| State | Meaning |
|---|---|
| This file (`…_v2_draft.md`) | Proposed rules. Open sample-size rows stay `karar bekliyor` |
| Locked `g-p-v2` | Exists only after your approval **and** a lock record (universe path/hash, `now_ms`, month bounds from **files**, not from this chat) **before** any post-discovery ρ |
| After seeing ρ | Any rule change is `g-p-v3` (or later) on a **new** unseen span. Peeked results are not retrofitted |

---

## 1. Universe (draft decision)

**Policy:** freeze the symbol list at **test start / lock**, not a reconstructed point-in-time tape.

At lock, report all of:

- Universe file path
- Universe **build / selection time** as stored in that artifact (do not invent it here)
- **Hash** of the symbol list (canonical sorted symbols, algorithm recorded at lock, e.g. SHA-256 of UTF-8 newline-joined names)
- Count of symbols and the data coverage actually present (how many of those names have processed 4h files in the test months)

**Stated limitations (not optional):**

- This repo did **not** keep a historical universe series for G-N/G-O. True point-in-time membership is **unavailable**.
- A frozen lock-time list is a **live-book** universe going forward. It still carries **survivorship** (names that already dropped out never appear) and **listing-lag** (new names have short history; they are in the list because they exist at lock, not because they would have been eligible at every earlier `t`).
- Do not apply a later universe file backward onto earlier test months. If the lock snapshot is missing, **do not run**; do not substitute “current” names after seeing coverage.

---

## 2. Start (draft decision)

**Rule:** the test window begins at the **first complete UTC month** whose closed 4h bars (and, for h=12, whose `t+12` closes) lie **strictly after** discovery.

Discovery (contaminated, never a test): closed 4h bars **2026-04-18 08:00 UTC through 2026-10-11 12:00 UTC** as used in G-N/G-O.

- Any subset of that span (month, phase, symbol, horizon, demeaned vs raw) is **not** an independent test.
- `test_start_ts` is **not** filled in this draft. It is read from local closed files at lock.
- The first complete UTC month after 2026-10-11 12:00 UTC is a **calendar rule**, not a date typed from memory as if the files already contain it.

The test **has not started** if that month’s closed candles are not local. Do not invent ρ, n, or month labels.

Also required before start: no one has inspected indicator–forward Spearman (or equivalent) on that new span.

---

## 3. End (draft decision)

**Rule:** **three complete UTC months** from the start month (start month + the next two UTC months).  

- Do **not** extend after seeing ρ, n, or a “looks unstable / looks stable” print.
- If the third month’s closed 4h bars (and h=12 endpoints) are missing, the run is **not ready**. Do not impute, splice discovery bars, or shorten silently to “whatever we have.”
- `test_end_ts` is recorded at lock from the rule + files, not guessed here.

Optional stopping is **invalid** under this draft.

---

## 4. Features, horizons, first-run reporting (draft decision)

Keep G-N’s seven features and four horizons. Adding either requires a new version.

| Name | Construction (existing APIs) |
|---|---|
| `close_vs_sma50_pct` | `(close / sma(50) - 1) * 100` when sma50 valued and close > 0 |
| `ma_spread_pct` | `(sma(20) / sma(50) - 1) * 100` |
| `roc6_pct` | `roc(series, 6) * 100` |
| `rsi14` | `rsi(series, 14)` valued |
| `macd_histogram` | `macd(series).histogram` valued |
| `adx14` | `adx(series, 14).adx` valued |
| `atr_over_close` | `atr(series, 14) / close` when close > 0 |

Horizons: **1, 3, 6, 12**.  
`forward_return_h = close[t+h] / close[t] - 1` iff `timestamp[t+h] == timestamp[t] + h × 4h` and both closes > 0. Gaps not filled. Incomplete bars (`is_incomplete` vs `now_ms`) never count as closed.

**First run:** report **all 28** pooled cells as **descriptive**. No winner cell, no production signal, no score change. Also report, as separate tables (G-P §6): per-symbol distribution, timestamp cross-section (raw + stacked demeaned), phases, UTC months **inside the three test months only**.

---

## 5. Cross-section filter (draft decision)

Drop timestamp `t` from cross-section analyses when the number of symbols with both feature and forward return at `t` is **&lt; 10**. Unchanged from G-N/G-O / `g-p-v1`. Changing this before the test is a new version; changing it after ρ is `g-p-v3` and burns the span.

---

## 6. Sample adequacy — **not auto-filled**

Thresholds are **not** taken from G-N pair counts (~191k–202k) or from which G-O months stayed defined. Unjustified numbers stay **`karar bekliyor`**.

Join rules remain G-N/G-O: inner join of valued feature and calendar-aligned return at the same `t`; non-overlap is **per symbol then stack**; month is UTC month of `t`, not `t+h`.

### 6.1 Pooled (all `(symbol, t)` rows)

| | |
|---|---|
| **Unit counted** | Pairs where feature and `forward_return_h` are both defined |
| **Dependence** | Many names share `t`; h&gt;1 windows overlap; features are serially correlated. Pair count is **not** independent n |
| **Options (pick one before lock)** | **P-A** No extra floor: three UTC months completed + Spearman definedness (≥2, non-constant). **P-B** Floor = (eligible CS timestamps) × 10, i.e. at least the CS minimum stacked. **P-C** Your integer, chosen without looking at G-N n: _____ |
| **If unset** | **`karar bekliyor`**. Still *describe* ρ if pairs exist, but do **not** call the pooled table an adequate confirmatory sample. Missing months → do not fabricate pooled ρ for those months |

### 6.2 Per-symbol

| | |
|---|---|
| **Unit counted** | Pairs inside one symbol for one feature × horizon |
| **Dependence** | Overlapping returns inside the name; listing-lag names are short by construction of a frozen universe |
| **Options** | **S-A** Definedness only (≥2, non-constant); everyone else listed as undefined reason. **S-B** Enter the ρ **distribution** only if that name has ≥12 pairs at that horizon (one h=12 non-overlap cycle) — structural, not a G-N fit. **S-C** Your integer: _____ |
| **If unset** | **`karar bekliyor`**. Publish defined/undefined **counts** and reasons; do not treat a median of a handful of long-history names as the per-symbol result |

### 6.3 Timestamp cross-section

| | |
|---|---|
| **Unit counted** | Distinct `t` with ≥10 symbols (Section 5). Also log skip count for n&lt;10 |
| **Dependence** | Adjacent 4h stamps are autocorrelated; this unit is the order of magnitude for shared shocks, still not i.i.d. |
| **Options** | **C-A** No extra floor beyond three completed months and n&lt;10 skips. **C-B** Require at least one eligible `t` in **each** of the three UTC months. **C-C** Your integer timestamp floor: _____ (calendar translation of three months is allowed; G-N timestamp counts are not) |
| **If unset** | **`karar bekliyor`**. Report the distribution of per-`t` ρ and skip counts; do not claim the CS panel is complete |

### 6.4 Monthly

| | |
|---|---|
| **Unit counted** | Pooled pairs whose feature timestamp `t` falls in that UTC month |
| **Dependence** | Same as pooled, inside a shorter calendar slice |
| **Options** | **M-A** Spearman definedness only. **M-B** `MONTH_MIN_N = 30` as **method continuity** with G-O reporting (not because April/October stayed defined). **M-C** Your integer: _____ |
| **If unset** | **`karar bekliyor`**. Months below the (unspecified) floor are **undefined in the log**, not dropped quietly; they do not auto-void the whole test |

Non-overlap phases: no extra n floor in this draft unless you add one at approval. Phases with too few pairs after the 1-in-h filter follow the same undefined reasons as Spearman.

---

## 7. Primary result (draft decision)

**There is no primary winning cell on the first run.**

Report as **separate descriptive meters** (not one blended score, not a trade signal):

1. **Sign** of pooled ρ (28 cells)  
2. **Magnitude** of ρ (no cutoff imported from discovery \|ρ\| ~ 0–0.04)  
3. **Period variation** (three test months; missing month = undefined, not ρ = 0)  
4. **Phase sensitivity** (per-symbol then stack; h=1 equals pooled)

G-P questions B (costs/PnL) and C (live tradability) stay **out of scope**.

---

## 8. Uncertainty (draft decision)

- **No pass/fail** on ρ for the first run.  
- **No i.i.d.** Spearman p-values or Fisher-z bands that use pair count as independent n.  
- Bootstrap (timestamp-block, calendar-block, two-way, or other) is **not** in this draft. If wanted, it needs a **separate design approval**: block definition, resampling unit, B, and what is recomputed — written **before** that bootstrap is run, and still before peeking at interval width.

Until that design exists, publish n_pairs, n_timestamps, n_symbols, skip counts — not a fake tight interval.

---

## 9. Invalidation (draft decision)

| Class | Effect |
|---|---|
| Incomplete / open bar treated as closed | **Whole test invalid** |
| Timestamp misalignment ignored (using next close across a gap) | **Whole test invalid** |
| Integrity: incomplete `now_ms`, universe hash ≠ lock hash, mixing discovery timestamps into test pairs | **Whole test invalid** |
| Spearman (or equivalent) computed on the span **before** lock | **Whole test invalid** as `g-p-v2`; span is burned for this protocol |
| Insufficient sample on a cell (floors in §6, or Spearman undefined) | That **cell inconclusive / undefined**; **not** an automatic whole-test void |
| Third UTC month not in local files | Run **not started / not complete**; **do not invent** results; do not stretch the end |

Gap handling for isolated missing slots remains G-N: drop that pair. A gap-rate cap is **`karar bekliyor`** (options: none / your cap). Until set, report gap-drop counts; do not void solely because a few names have holes.

---

## 10. Change control (draft decision)

- Edits to universe policy, months, features, horizons, n&lt;10, gap rule, reporting grid, or §6 floors **after approval** require a **new version**.  
- Changes **after ρ are visible** cannot be applied backward to the same test. That run stays labelled peeked / unregistered.  
- Do not overwrite G-N/G-O JSON. Test output (when eventually authorized) must be a **new** path under `data/experimental/`.  
- This draft does not change production score, MACrossover, `portfolio_sim`, broker, OHLCV, universe builder, or indicator formulas.

---

## Sample construction (unchanged method)

Reuse `forward_returns`, `feature_series`, `paired_points`, `spearman_corr`, and G-O robustness slices. Non-overlap ≠ independence. Per-timestamp demean does not change per-`t` Spearman; stacked demeaned y is a different sample.

---

## Lock record (fill only at approval + file inspection — leave blank now)

```
protocol_version: g-p-v2          # only after approval replaces DRAFT
universe_path:
universe_build_time:
universe_symbol_list_hash:
test_start_ts:
test_end_ts:
now_ms:
pooled_floor_choice:              # P-A / P-B / P-C
per_symbol_floor_choice:          # S-A / S-B / S-C
cs_floor_choice:                  # C-A / C-B / C-C
monthly_floor_choice:             # M-A / M-B / M-C
gap_rate_cap:                     # none / integer
approved_by:
approved_at_utc:
```

---

## What is decided in this draft (pending your yes)

1. Frozen lock-time universe + hash/build-time/coverage report; no PIT tape; survivorship/listing-lag labelled.  
2. Start = first complete UTC month after discovery end; no discovery subset as test.  
3. End = three complete UTC months; no extension; no invented results if data missing.  
4. Seven features × four horizons; first run = 28-cell **descriptive** grid.  
5. CS n&lt;10 skip kept.  
6. Sample **options** listed; numeric floors **not** auto-picked.  
7. No winner cell / no trade signal; four descriptive meters.  
8. No pass/fail; no i.i.d. intervals; bootstrap needs a later design.  
9. Open-bar / alignment / integrity / pre-lock ρ → whole-test invalid; thin sample → cell inconclusive.  
10. Post-hoc rule changes → new version, not a retrofit.

## Still `karar bekliyor` (need you)

1. Pooled floor: **P-A / P-B / P-C**  
2. Per-symbol distribution floor: **S-A / S-B / S-C**  
3. Cross-section timestamp floor: **C-A / C-B / C-C**  
4. Monthly floor: **M-A / M-B (30 continuity) / M-C**  
5. Gap-rate cap: **none** vs a number  
6. Whether this draft becomes locked `g-p-v2` at all  

Approve those rows **before** any post-discovery correlation. Until then this file remains a draft and `g-p-v1` is the only written protocol (still not decision-complete on its own §8 blanks).

# Indicator–forward-return validation protocol (G-P)

Status: **discovery closed; future test not started.**  
This document does not validate G-N or G-O. It freezes how a later test on unseen closed 4h bars should be run.  
Protocol version: `g-p-v1`. Any edit after test outcomes are known is a new research version.

Sources (read-only; do not overwrite):

- `data/experimental/g_n_indicator_forward.json`
- `data/experimental/g_o_forward_robustness.json`
- `data/experimental/g_m_indicator_readiness.json`
- Code: `src/tradingbot/research/` (`forward.py`, `indicator_assoc.py`, `robustness.py`, `spearman.py`)
- Features computed via `tradingbot.indicators` (`sma`, `roc`, `rsi`, `macd`, `adx`, `atr`)

Labels below are not statistical significance, profitability, or causality claims.

---

## Discovery-period synthesis (G-N + G-O)

Examined window (discovery only): closed 4h bars from the local processed files used in G-M/G-N/G-O, spanning **2026-04-18 08:00 UTC through 2026-10-11 12:00 UTC**. No sub-window of this span is an independent test.

Universe: 200 symbols from `data/processed/universe.json` dated 2026-10-11, applied retrospectively (survivorship / listing-lag). Pooled pair counts match G-N (about 191k–202k depending on feature and horizon).

### Repeated observations

Relationships that pointed the same way in pooled G-N, most non-overlapping phases (G-O), and typical per-symbol medians, without claiming they will persist:

| Item | What repeated | What it is not |
|---|---|---|
| `close_vs_sma50_pct` vs forward return | Weak **negative** pooled Spearman at 1/3/6/12 bars; G-O union ρ identical to G-N; most non-overlap phases negative; per-symbol median negative | Not a trading rule; monthly |ρ| moved (Apr/Oct more negative) |
| `rsi14` vs forward return | Weak **negative** pooled ρ at all four horizons; all G-O phases negative; per-symbol median negative | Same caveats |
| `roc6_pct` vs forward return | Weak **negative** pooled ρ, strongest at 1 bar (~−0.04); phases at 1 and 3 bars negative | 12-bar phases mixed sign |
| `atr_over_close` vs forward return | Weak **negative** pooled ρ at all horizons; all G-O phases negative | Monthly signs mixed at longer horizons |
| Pooled vs per-symbol | Per-symbol median ρ had the same sign as pooled for the four items above | Cross-section medians were often more negative than pooled |
| Rank invariance | Per-timestamp Spearman on raw y equals Spearman after subtracting that timestamp’s mean y | Does not remove a market factor model |

### Unstable findings

| Item | How it changed |
|---|---|
| `macd_histogram` | Pooled ρ near 0 at 1 bar, slightly **positive** at 6/12; some non-overlap phases negative; stacked demeaned 12-bar ρ **negative**; months split +/− |
| `adx14` | Pooled ρ **positive** and rising with horizon; timestamp-cross-section median **negative**; stacked demeaned **negative** |
| `ma_spread_pct` | Weak negative pooled; 12-bar non-overlap phases include both + and − |
| `roc6_pct` at 12 bars | Non-overlap phases range about −0.08 to +0.06 |
| Calendar months | Apr and Oct (partial months, still n≥30) often more extreme than May–Sep; full-period \|ρ\| smaller than monthly median for several features |
| Pooled vs cross-section | Same-timestamp median ρ more negative than pooled; pooled mixes shared market moves with symbol effects |

### Insufficient evidence

None of the seven features supports a new production score, entry rule, or simulated strategy:

- Absolute pooled \|ρ\| stayed small (~0.00–0.04).
- Observations are dependent (overlapping windows, shared timestamps, serial features).
- Discovery used a current 200-name universe on history.
- No quote volume, spread, or book depth; no costs, fills, or capacity.
- Multiple features × horizons × methods were inspected on the same discovery sample.
- G-O non-overlap does **not** make pairs independent.

---

## 1. Data split

| Role | Definition |
|---|---|
| **Discovery** | Closed 4h candles whose timestamps fall in **2026-04-18 08:00 UTC – 2026-10-11 12:00 UTC** inclusive, as loaded for G-N/G-O. Feature values and forward returns that use any close inside this window are discovery. |
| **Not a test** | Any subset of discovery (single month, one phase, one symbol, one horizon, demeaned vs raw). Tuning on these subsets is still discovery. |
| **Hold-out / future test** | Only closed 4h bars that were **not** in the local discovery files when G-N/G-O were run, and whose forward-return endpoints also lie outside discovery. |

Do not call a walk-forward inside April–October 2026 “out of sample.”

---

## 2. Future test period

Start of the future test is a **decision point**, not a date invented here.

The test may begin only after **new closed 4h candles** exist locally such that:

1. The candle open time is **after** 2026-10-11 12:00 UTC, and
2. For horizon `h`, the close at `t+h` is also closed and after the discovery end, and
3. Nobody has inspected indicator–return Spearman (or equivalent) on that new span before locking Section 8.

Record, before computing test correlations:

- `test_start_ts` / `test_end_ts` (UTC, closed-bar timestamps)
- `now_ms` used to drop incomplete bars
- universe snapshot path and hash
- protocol version `g-p-v1`

If those fields are filled after seeing test ρ values, the run is not this protocol.

---

## 3. Universe policy

**Discovery fact:** G-N/G-O used the 2026-10-11 universe of 200 Binance USDT names on all historical 4h files. Names listed late (e.g. HYPE) have short history; names that left the universe before that date are absent. That is survivorship and listing-lag bias.

**Before the future test, choose exactly one and do not change it after seeing results:**

1. **Point-in-time (preferred description, not yet selected):** at each timestamp `t`, only symbols that would have been in the universe rule *as of `t`* (requires a dated universe series that this repo did not use in G-N/G-O). Or
2. **Fixed snapshot:** freeze one universe file at test lock time and apply it to the whole test span (repeats discovery bias; must be labelled as such). Or
3. **Intersection:** symbols present in both the lock-time universe and every test month.

**To be decided before seeing test data:** which of (1)/(2)/(3), and whether newly listed names need a minimum closed-bar count before entry.

Do not download or sync data as part of writing or locking this protocol.

---

## 4. Features and horizons

Keep G-N’s seven features and four horizons. Adding a feature or horizon needs a new protocol version.

| Name | Construction (existing APIs only) |
|---|---|
| `close_vs_sma50_pct` | `(close / sma(50) - 1) * 100` when sma50 is valued and close > 0 |
| `ma_spread_pct` | `(sma(20) / sma(50) - 1) * 100` |
| `roc6_pct` | `roc(series, 6) * 100` |
| `rsi14` | `rsi(series, 14)` valued points |
| `macd_histogram` | `macd(series).histogram` valued points |
| `adx14` | `adx(series, 14).adx` valued points |
| `atr_over_close` | `atr(series, 14) / close` when close > 0 |

Horizons: **1, 3, 6, 12** closed 4h bars.  
`forward_return_h = close[t+h] / close[t] - 1` only if `timestamp[t+h] == timestamp[t] + h * 4h` and both closes > 0. Last `h` bars have no return. Gaps are not filled. Indicators at `t` must not use closes after `t`.

Score-feature check from G-N: last-bar `close_vs_sma50_pct`, `ma_spread_pct`, and `roc6_pct` match `rank_features_4h`. Do not “fix” formulas if a later test disagrees with discovery; open a new version.

Quote volume, spread, and book depth are **out of scope** (not on `Candle`).

---

## 5. Sample rules

Reuse G-N/G-O join rules.

| Analysis | Sample definition |
|---|---|
| Closed bars | `CandleSeries.from_closed` after dropping `is_incomplete` vs `now_ms`. Incomplete bars are never treated as closed. |
| Pairs | Inner join of valued feature timestamp and calendar-aligned forward return at the **same** `t`. |
| Pooled | Stack all `(symbol, t)` pairs. Mixes symbols and common market moves. |
| Per-symbol | Spearman inside one symbol; report n, ρ, or undefined reason (`need_at_least_two_pairs`, `constant_feature`, `constant_forward_return`). |
| Non-overlap phase `p` | Per symbol, sort valid pairs by `t`, keep indices `i % h == p` for `p = 0..h-1`. Stack symbols **after** this filter. Phases must not be concatenated into one Spearman except as a union check (union = pooled). Non-overlap ≠ independence. |
| Calendar month | UTC month of feature timestamp `t`, not `t+h`. Do not extend missing months. Months with n below the locked minimum are **undefined**, not dropped from the log. |
| Cross-section (raw) | At each `t`, symbols with both feature and forward return; **drop `t` if n < 10**. Report the distribution of per-`t` Spearman ρ (median, min, max, timestamp count, symbol-count min/median/max). |
| Cross-section demeaned | Same `t` and same symbols. `y' = y - mean(y)` at that `t`. Per-`t` Spearman(`x`, `y'`) equals Spearman(`x`, `y`) because a constant shift does not change ranks. **Stacked** `(x, y')` over eligible timestamps is a **different** sample: it only removes that timestamp’s cross-sectional mean return, not a market model. |

G-O used `MONTH_MIN_N = 30` on discovery. Whether the future test keeps 30 is **to be decided before seeing test data** (Section 8). Do not retune it after seeing monthly ρ.

---

## 6. Reporting (future test)

Publish these as separate tables; do not blend ρ types:

1. Pooled Spearman: feature × horizon (n, ρ, undefined reason).
2. Per-symbol: distribution of ρ (count defined, min, p50, max) and undefined counts.
3. Timestamp cross-section: raw per-`t` distribution; stacked eligible raw; stacked eligible demeaned; n&lt;10 skip count.
4. Phase sensitivity: each `p` for `h ∈ {3,6,12}` (h=1 is identical to pooled).
5. Periodicity: UTC months in the **test** span only, with n and ρ or undefined reason.

Also record universe policy, `now_ms`, protocol version, and that discovery April–October 2026 was not mixed into the test sample.

---

## 7. Statistical interpretation

- **Multiple comparisons:** 7 features × 4 horizons × several methods (pooled, CS, phases, months) were already scanned in discovery. A future test that repeats the same grid is still a multiple-look problem. Treat the grid as pre-specified, not as seven independent confirmations.
- **Overlapping windows:** horizon `h>1` pooled pairs overlap in calendar time. Phase sub-sampling reduces overlap; it does not yield independent draws.
- **Cross-section and panel:** many symbols share the same `t`. Pooled n is not the number of independent market states. Timestamp count (eligible `t`) is a better order-of-magnitude for shared shocks.
- **Do not** interpret raw pair counts (~2×10^5) as independent n for p-values or intervals unless Section 8 locks a dependence-aware method **before** seeing test ρ.
- Spearman is a rank association, not an expected PnL.

---

## 8. Decision criteria (lock before seeing test data)

Numeric cutoffs are **not** taken from G-N/G-O magnitudes. Filling them after seeing test ρ invalidates `g-p-v1`.

| Criterion | Status |
|---|---|
| Minimum eligible timestamps in the test span | **To be decided before seeing test data** |
| Minimum pooled pairs per feature × horizon | **To be decided before seeing test data** |
| Minimum symbols per eligible timestamp | Locked at **10** (G-N/G-O rule), unless a new protocol version changes it *before* the test |
| Monthly minimum n | **To be decided before seeing test data** (discovery used 30) |
| Uncertainty intervals | **To be decided before seeing test data** (method must respect dependence; naive i.i.d. Spearman p-values are not default) |
| Stability | **To be decided before seeing test data**, e.g. same sign in pooled and in at least a stated fraction of phases/months — fraction not chosen here |
| Multiple-testing | **To be decided before seeing test data** (examples: single primary pair declared in advance; Bonferroni/Holm on the 28 pooled cells; hierarchical primary then secondary). Discovery must not pick the primary pair *because* it looked strongest in G-N/G-O unless that choice is written down **before** test ρ exist |
| Failure / inconclusive | **To be decided before seeing test data** (e.g. any sign flip vs discovery on the primary pair; n below minima; protocol violation) |

Until those cells are filled in a dated amendment that still predates test correlations, the future run is **descriptive only** and must not be called a pass/fail validation.

---

## 9. Three separate questions

| Question | In scope of this protocol? |
|---|---|
| A. Do the seven features still show the same *sign* of Spearman association with forward close-to-close returns on unseen closed 4h bars, under the sample rules above? | Yes, once Section 8 is locked |
| B. Would a strategy that trades those features be profitable after fees, slippage, and constraints? | **No.** Requires a separate, pre-registered execution study (not G-P) |
| C. Is the relationship tradable in live or testnet markets (capacity, inventory, outages)? | **No.** Not implied by A or B |

G-N/G-O did not answer B or C. A future correlation test still does not answer B or C.

---

## 10. Change control

1. `g-p-v1` is frozen with respect to discovery outcomes already seen.
2. Section 8 blanks must be filled **without** peeking at post-discovery feature–return correlations.
3. After test ρ exist, changing features, horizons, universe rule, n&lt;10, gap handling, demeaning definition, or pass/fail cuts creates **`g-p-v2`** (or later). `g-p-v1` results stay labelled discovery-plus-unregistered-test if rules moved.
4. Do not overwrite G-N/G-O JSON. New test output must use a new path under `data/experimental/`.
5. This file is not a production score, MACrossover, `portfolio_sim`, or broker change.

---

## Appendix: method map

| Step | Module |
|---|---|
| Forward returns | `tradingbot.research.forward.forward_returns` |
| Feature join | `tradingbot.research.indicator_assoc.feature_series`, `paired_points` |
| Spearman | `tradingbot.research.spearman.spearman_corr` |
| Non-overlap / months / CS demean | `tradingbot.research.robustness` |
| Scripts (discovery only) | `scripts/analyze_indicator_forward.py`, `scripts/analyze_indicator_forward_robustness.py` |

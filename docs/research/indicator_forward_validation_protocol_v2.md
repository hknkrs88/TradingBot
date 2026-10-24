# Indicator–forward-return validation protocol (`g-p-v2`)

Status: **rule-locked.** Not a completed validation run. Not a claim that post-discovery Spearman was never inspected.

Protocol id: `g-p-v2`  
Kind: **rule lock only.** Run-specific fields stay empty until a later run record is filled from files.

This file freezes the method approved in G-Q4. It merges `docs/research/indicator_forward_validation_protocol_v2_draft.md` with `docs/research/indicator_forward_validation_decisions_v2.md`. Those files, `g-p-v1`, and the G-Q worksheet remain historical records and are not replaced.

Rejected and **not** part of this protocol: C-B, C-C, M-B as adequacy, M-C, a gap-rate cap, a per-phase n floor, first-run bootstrap, i.i.d. intervals, pass/fail on ρ.

---

## Discovery context (not the test window)

**Discovery (contaminated):** closed 4h bars **2026-04-18 08:00 UTC through 2026-10-11 12:00 UTC**, as used in G-N/G-O. Any subset of that span is **not** an independent test.

Discovery-run `now_ms` and the 2026-10-11 `universe.json` artifact belong to discovery. They are **not** the test-run clock or the test-start universe hash. Do not copy them into a run record.

---

## 1. Universe (rule)

At **test start / run lock** (not in this document): freeze one symbol list. Record path, `generated_at` as stored in that file, SHA-256 of the canonical sorted `binance_symbol` list (UTF-8, newline-joined; algorithm written on the run record), symbol count, and how many of those names have processed 4h files in the test months.

No point-in-time membership series exists in this repo. Survivorship and listing-lag remain. Do not apply a later universe file backward. If the run-lock snapshot is missing, do not run.

**Not filled here:** test universe SHA-256, test coverage.

---

## 2. Test window (rule)

- **Start:** first **complete UTC month** whose closed 4h bars, and for h=12 whose `t+12` closes, lie **strictly after** discovery end.  
- **End:** that start month plus the next two UTC months (**three complete UTC months**).  
- **No extension** after seeing ρ, n, or a stability print. Optional stopping is invalid.  
- If those months are not in local closed files, the run has **not started**. Do not invent ρ or month labels.

**Not filled here:** `test_start_ts`, `test_end_ts`, realized coverage.

---

## 3. Features and horizons

Unchanged APIs. Adding a feature or horizon is a new protocol version.

| Name | Construction |
|---|---|
| `close_vs_sma50_pct` | `(close / sma(50) - 1) * 100` when sma50 valued and close > 0 |
| `ma_spread_pct` | `(sma(20) / sma(50) - 1) * 100` |
| `roc6_pct` | `roc(series, 6) * 100` |
| `rsi14` | `rsi(series, 14)` valued |
| `macd_histogram` | `macd(series).histogram` valued |
| `adx14` | `adx(series, 14).adx` valued |
| `atr_over_close` | `atr(series, 14) / close` when close > 0 |

Horizons: **1, 3, 6, 12**.  
`forward_return_h = close[t+h] / close[t] - 1` iff `timestamp[t+h] == timestamp[t] + h × 4h` and both closes > 0.

---

## 4. Alignment and gaps

- Incomplete bars (`is_incomplete` vs the **run** `now_ms`) are never closed.  
- Expected 4h timestamp match is required. **No gap fill.** A missing slot **drops that pair**. Using the next close across a gap **voids the whole test**.  
- **No gap-rate cap.** Report gap counts and rates. A high rate alone does not cancel the test. Integrity failures are separate (§9).

**Not filled here:** run `now_ms`.

---

## 5. Cross-section filter (**C-A**)

Drop timestamp `t` from cross-section analyses when symbols with both feature and forward return at `t` are **&lt; 10**. C-B and C-C are not in this protocol.

---

## 6. Sample rules (G-Q4)

Join: inner join of valued feature and calendar-aligned return at the same `t`. Non-overlap: **per symbol, then stack**. Month: UTC month of `t`, not `t+h`.

Undefined Spearman: &lt;2 pairs, constant feature, or constant forward return. Undefined ≠ 0. A thin **cell** does not void the whole test.

| Analysis | Locked choice | How insufficiency is shown |
|---|---|---|
| Pooled | **P-A** | Report actual **n** beside ρ. n is **not** independent sample size. No extra pooled floor. Incomplete three-month window → no pooled test result. |
| Per-symbol | **S-A** | One row per symbol × feature × horizon: ρ + n or undefined reason. No silent drop. |
| Timestamp CS | **C-A** | Skip n&lt;10; print skip counts. No extra timestamp floor. |
| Monthly | **M-A** | Defined with ≥2 valid non-constant pairs. Report monthly **n**. **30 is not adequacy or evidence.** M-C is not selected. No extra monthly integer. |
| Phases | Descriptive | Print **n per phase**. No per-phase n floor. Do not drop or pick phases after seeing ρ. |

---

## 7. First-run reporting

Report **all 28** pooled cells (7 × 4) as **descriptive**. No winner feature, no production signal, no score change.

Also as **separate** tables: per-symbol rows, timestamp cross-section (raw per-`t` and stacked demeaned), phases, UTC months **inside the three test months only**.

Meters (not one blended score): sign, magnitude (no cutoff imported from discovery \|ρ\|), period variation, phase sensitivity.

G-P questions B (costs/PnL) and C (live tradability) stay out of scope.

---

## 8. Uncertainty (first run)

- No pass/fail on ρ.  
- No i.i.d. Spearman p-values or Fisher-z bands that treat pair count as independent n.  
- No bootstrap on the first run. A later bootstrap needs a new design and a new protocol version, written before that bootstrap runs.

Publish n_pairs, n_timestamps, n_symbols, skip counts, gap counts/rates.

---

## 9. Invalidation

| Class | Effect |
|---|---|
| Incomplete / open bar treated as closed | **Whole test invalid** |
| Timestamp misalignment ignored (next close across a gap) | **Whole test invalid** |
| Integrity: run `now_ms` misused; run-record universe hash ≠ frozen list; discovery timestamps mixed into test pairs | **Whole test invalid** |
| Spearman (or equivalent) on the **test span** before the **run record** is written | **Whole test invalid** as a `g-p-v2` run; that span is burned |
| Insufficient sample / Spearman undefined on a cell | **Cell** inconclusive; not an automatic whole-test void |
| Third UTC month not in local files | Run not started / not complete; do not invent; do not stretch |

This **rule lock** does not prove that Spearman on a future span has not been inspected.

---

## 10. Change control

- Changing universe policy, window rule, features, horizons, n&lt;10, gap construction, reporting grid, or §6 choices after this rule lock is a **new protocol version**.  
- Changing rules **after test ρ are visible** cannot be applied backward. That run is peeked / unregistered.  
- A test-window rule change also requires a **new, previously unseen** data span.  
- Do not overwrite G-N/G-O JSON. Future test output must use a new path under `data/experimental/`.  
- This protocol does not change production score, MACrossover, `portfolio_sim`, broker, OHLCV, universe builder, or indicator formulas.

---

## Run record (empty until files exist)

Fill only from files at run lock, in a later authorized step. Leave blank now.

```
run_now_ms:
universe_path:
universe_generated_at:
universe_symbol_list_sha256:
test_start_ts:
test_end_ts:
realized_coverage:
g_p_v2_content_sha256_at_run:
```

---

## Lock record

- protocol_id: `g-p-v2`
- kind: rule lock only (run fields above are intentionally empty)
- git_head_at_lock: `088b78781b7cce01becb850139092ac6ff282a06`
- lock_time_utc: `2026-10-12T21:26:15.280Z`
- content_sha256: `8da3700e8a6f7ed2b6f5d060e948583287c0522b38db4438a2902f88e94c3ffe`
- hashed_object: this file `docs/research/indicator_forward_validation_protocol_v2.md` as UTF-8 on disk
- canonicalization: SHA-256 of those bytes after replacing the 64 hexadecimal digits on the `content_sha256` line with 64 ASCII `0` characters (embedding the digest would otherwise change the digest)
- this hash and timestamp are an audit trail only; they **do not prove** that Spearman results on unseen bars were not inspected

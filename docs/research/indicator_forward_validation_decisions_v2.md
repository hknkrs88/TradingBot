# Validation decisions v2 — G-Q3 / G-Q4 log (**not locked**)

Status: **method choices recorded for review. `g-p-v2` is not in force.**  
This file is the decision log. It does **not** edit:

- `docs/research/indicator_forward_validation_protocol.md` (`g-p-v1`)
- `docs/research/indicator_validation_decisions.md` (G-Q worksheet)
- `docs/research/indicator_forward_validation_protocol_v2_draft.md` (`g-p-v2` draft)

HEAD expected: `088b78781b7cce01becb850139092ac6ff282a06`.

---

## G-Q4 recorded user approvals

These IDs match the v2 draft §6 / §9 definitions (see §1). No remapping.

| ID / topic | Recorded meaning | Status |
|---|---|---|
| **P-A** | Pooled analysis reports **actual n**. n is **not** independent sample size. No extra pooled floor. Spearman definedness still ≥2 non-constant pairs. | **User-approved (G-Q4)** — not a protocol lock |
| **S-A** | Per-symbol rows separately. Insufficient series **undefined / inconclusive**, not dropped silently, not ρ = 0. | **User-approved (G-Q4)** |
| **C-A** | Cross-section: skip timestamps with **&lt; 10** valid symbols. **C-B and C-C not selected.** | **User-approved (G-Q4)** |
| **M-A** | Monthly Spearman defined only with **≥ 2** valid pairs (and non-constant). Print monthly **n**. **Not** an adequacy / evidence threshold. | **User-approved (G-Q4)** |
| **No M-B adequacy** | Do **not** treat 30 as statistical sufficiency. | **User-approved (G-Q4)** |
| **No M-C integer** | Do **not** invent a new monthly n cutoff. Extra monthly floor = **none** beyond M-A definedness. Still print n. | **User-approved (G-Q4)** |
| **Gap construction** | Expected 4h timestamp match. **No fill.** Misaligned slot **drops that pair**. Using the next close across a gap **voids the whole test**. | **User-approved (G-Q4)** |
| **No gap-rate cap** | Report gap **counts and rates**. A fixed rate cap does **not** cancel the test. Integrity failures (open bar as closed, hash mismatch, discovery mix, ρ before lock) stay **separate** (v2 draft §9). | **User-approved (G-Q4)** |
| **No phase n floor** | Phases are **descriptive**. Print **n per phase**. Do not drop or cherry-pick phases after seeing ρ. Spearman definedness still applies per phase. | **User-approved (G-Q4)** |
| **No bootstrap on first run** | First validation run does **not** include bootstrap. **No** confidence interval. **No** pass/fail on ρ. A later bootstrap would need a **new** design + version. | **User-approved (G-Q4)** |

Carried from the v2 draft (still **not locked** until you authorize `g-p-v2`):

- Frozen lock-time universe; report path, build time, symbol-list hash, coverage; no PIT tape; survivorship / listing-lag labelled.
- Start = first complete UTC month after discovery (2026-04-18 08:00 UTC–2026-10-11 12:00 UTC). No discovery subset as test.
- End = three complete UTC months; **no extension**; missing files → **no invented** ρ.
- Seven features × horizons 1/3/6/12; first run = 28-cell **descriptive** grid; no winner cell; no trade signal.
- Thin sample → **cell** inconclusive, not automatic whole-test void (except integrity / protocol breaches).
- Post-ρ rule changes → new version; no retrofit.

---

## 1. Label check (G-Q3; unchanged)

Draft definitions:

| ID | Draft meaning |
|---|---|
| **P-A** | No extra pooled floor. Three UTC months completed + Spearman definedness (≥2 pairs, non-constant). |
| **P-B** | Pooled floor = (eligible CS timestamps) × 10. |
| **P-C** | User-chosen integer pooled floor, not taken from G-N n. |
| **S-A** | Per-symbol: definedness only. Other names stay in the log with undefined reason. |
| **S-B** | Per-symbol ρ enters the **distribution** only if that name has ≥12 pairs at that horizon. |
| **S-C** | User-chosen integer per-symbol floor. |
| **C-A** | No extra CS timestamp floor beyond three completed months and **n&lt;10** skips. |
| **C-B** | Extra rule: at least one eligible `t` (n≥10) in **each** of the three UTC months. |
| **C-C** | User-chosen integer floor on the **count of eligible timestamps**. |
| **M-A** | Monthly: Spearman definedness only (≥2, non-constant). |
| **M-B** | Monthly floor `MONTH_MIN_N = 30` as G-O **reporting continuity**, not as a power guarantee. |
| **M-C** | User-chosen integer monthly floor. |
| **Gap construction** | Expected 4h timestamp match; missing slot **drops the pair**; no fill. |
| **Gap-rate cap** | Separate switch: none vs a maximum drop rate that could void the run. |

G-Q3 partial match on “monthly pre-agreed threshold” is **closed in G-Q4**: extra threshold = **none** (not M-B, not a made-up M-C).

---

## 2. Rejected options (explicit)

| ID | Not selected | Why it stays out |
|---|---|---|
| **C-B** | Month-by-month CS coverage gate | C-A only |
| **C-C** | Integer eligible-`t` floor | C-A only |
| **P-B / P-C** | Extra pooled n floor | Conflicts with “report n, do not treat as independent” |
| **S-B / S-C** | Extra per-symbol n floor | Short series stay in the log as undefined |
| **M-B as adequacy** | n≥30 ⇒ enough evidence | Forbidden |
| **M-C** | New monthly integer | Not invented |
| **Gap-rate cap** | Void test when drop rate exceeds a number | Report rates instead |
| **Phase n floor** | Drop phases by n or by ρ | Descriptive + n per phase |
| **First-run bootstrap / i.i.d. CI / pass-fail** | Confirmatory interval or gate | Out of the first run |

---

## 3. Insufficient sample (how it applies now)

Shared run rule: three complete UTC months after discovery must exist in **files**. If not, **do not invent** ρ. Discovery April–October 2026 is never a test. No extension after seeing results.

Undefined Spearman: &lt;2 pairs, constant feature, or constant forward return. Undefined ≠ 0. Cell inconclusive does not void the whole test.

| Analysis | Unit | Rule |
|---|---|---|
| Pooled **P-A** | `(symbol, t)` | Print n and ρ if defined. n is not independent n. Incomplete three-month window → run not ready. |
| Per-symbol **S-A** | pairs in one name | One row per symbol × feature × horizon: ρ + n or undefined reason. |
| CS **C-A** | timestamps with ≥10 names | Skip n&lt;10; print skip count. No C-B/C-C. |
| Monthly **M-A** | pairs with `t` in that UTC month | Print n. Undefined Spearman → inconclusive. **No** extra integer. **30 is not adequacy.** |
| Phases | per-symbol then stack | Descriptive; print n per phase; no extra floor; no post-hoc phase drop. |

---

## 4. Full decision table (G-Q4)

| Topic | Resolution | Status |
|---|---|---|
| Universe | Freeze at lock; hash + build time + coverage; not PIT | Recorded from v2 draft — **needs lock approval** |
| Start | First complete UTC month after discovery | Recorded from v2 draft — **needs lock approval** |
| End | Three complete UTC months; no extend; no invent | Recorded from v2 draft — **needs lock approval** |
| Features × horizons | 7 × 4; descriptive 28 cells; no winner | Recorded from v2 draft — **needs lock approval** |
| CS symbol filter | n&lt;10 skip | **User-approved (G-Q4)** — **needs lock approval** |
| Pooled | **P-A** | **User-approved (G-Q4)** — **needs lock approval** |
| Per-symbol | **S-A** | **User-approved (G-Q4)** — **needs lock approval** |
| CS timestamp extras | **C-A** only (not C-B, not C-C) | **User-approved (G-Q4)** — **needs lock approval** |
| Monthly | **M-A** only; n reported; no M-B adequacy; no M-C | **User-approved (G-Q4)** — **needs lock approval** |
| Gap construction | 4h match; drop pair; no fill | **User-approved (G-Q4)** — **needs lock approval** |
| Gap-rate cap | **None**; report counts/rates; no cap-based cancel | **User-approved (G-Q4)** — **needs lock approval** |
| Phase extra n floor | **None**; descriptive + n per phase; no result-based dropping | **User-approved (G-Q4)** — **needs lock approval** |
| Bootstrap / CI / pass-fail | **Not in first run** | **User-approved (G-Q4)** — **needs lock approval** |
| Invalidation split | Integrity / protocol breach → whole test; thin n → cell | Recorded from v2 draft — **needs lock approval** |
| Change control | Post-ρ edits → new version | Recorded from v2 draft — **needs lock approval** |
| **`g-p-v2` lock** | Draft + this log not promoted | **Not locked. Explicit approval required.** |

“User-approved (G-Q4)” means the **choice** is written down. It does **not** freeze the protocol, start a test, or fill lock-record timestamps from files.

---

## 5. Still required before `g-p-v2` can be locked

Method rows above are complete for a first descriptive run. Locking is a **separate** act. Until you approve the following, do not compute post-discovery Spearman as a `g-p-v2` test, and do not treat the v2 draft as frozen.

1. **Promote** this log + the v2 draft to locked protocol version `g-p-v2` (new locked document in a later stage — v1 and the draft file stay as-is until you ask).  
2. Confirm the **carried draft rules** as lock-set: frozen universe, first complete UTC month start, **three-month** end, no extension, 7×4 descriptive grid, invalidation table, change control.  
3. Confirm the **G-Q4 method set** as lock-set: P-A, S-A, C-A (not C-B/C-C), M-A with **no** extra monthly integer, gap construction **without** a rate cap, phases descriptive with n, **no** first-run bootstrap / CI / pass-fail.  
4. At lock time only, fill from **files** (not invented here): `universe_path`, universe build time, symbol-list hash, `test_start_ts`, `test_end_ts`, `now_ms`. If the three post-discovery UTC months are not in local closed 4h data, the test **has not started**.  
5. Confirm **no Spearman** on the unseen span has been inspected before that lock record.

This file does not change production score, strategy, `portfolio_sim`, broker, OHLCV, universe builder, or indicator formulas.

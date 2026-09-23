# CLAUDE CHANGES — StockLens

Running log of **every** change made to this repository, smallest to largest.
Newest entries at the bottom of each session block. Nothing is omitted.

- **Repository:** `HarshitSrivastava07/Stocklens`
- **Working branch:** `claude/github-stocklens-access-6k4je5`
- **Base commit:** `48a1698` — *Implement fundamentals ingestion; fix two bugs that killed the engines*
- **Goal:** take StockLens from a partly-mocked prototype to a commercially operable product: live prices, intrinsic value computed from 10 years of real filings, real buy/sell timing, real historical charts, and a real per-stock research workspace. No fabricated numbers anywhere.

---

## Table of contents

- [Session 1 — 2026-09-21](#session-1--2026-09-21)
  - [0. Environment findings and constraints](#0-environment-findings-and-constraints)
  - [1. Audit of the existing codebase](#1-audit-of-the-existing-codebase)
  - [2. Change log](#2-change-log)
- [Conventions used in this file](#conventions-used-in-this-file)

---

## Conventions used in this file

Every entry uses this shape:

| Field | Meaning |
|---|---|
| **What** | The change, in one line |
| **Why** | The problem it solves |
| **Files** | Exact paths added / modified / deleted |
| **Verified** | How I proved it works (test name, command, or "pending live network") |

Change IDs are sequential (`C001`, `C002`, …) and never reused.

---

## Reversing changes

Every change has an ID (`C001`, `C002`, …). Anything can be undone, either by
reverting its commit or — for the engines — by switching it off at runtime.

```bash
python scripts/revert_change.py list              # everything that can be undone
python scripts/revert_change.py show C012         # what it touched, and the risk
python scripts/revert_change.py revert C012 --dry-run   # rehearse it
python scripts/revert_change.py revert C012       # do it
python scripts/revert_change.py verify            # registry vs git vs this file
```

**Nothing rewrites history.** A revert is a new commit undoing an old one, so
the record stays intact and the revert can itself be reverted. The tool refuses
to run against a dirty working tree unless you pass `--allow-dirty`, so your own
uncommitted work is never at risk.

**Two ways to undo a change:**

| Method | What it does | When to use it |
|---|---|---|
| `git` | Reverts the commit | Permanent removal |
| `flag` | Sets an environment variable to `false` | A bad overnight run you need to contain **now**, without a redeploy |

The flags live in `apps/api/config.py` and are read from your `.env`:

| Flag | Switches off |
|---|---|
| `ENGINE_VALUATION_ENABLED` | The 10-year intrinsic value engine |
| `ENGINE_TECHNICALS_ENABLED` | Indicator snapshots |
| `ENGINE_SIGNALS_ENABLED` | Buy/sell decisions |
| `ENGINE_REVERSE_DCF_ENABLED` | The implied-growth solve |
| `INGEST_HISTORY_ENABLED` | Daily OHLCV backfill |
| `INGEST_FUNDAMENTALS_ENABLED` | Filing ingestion |
| `INGEST_QUOTES_ENABLED` | Live price polling |

A flag set to `false` makes that stage a no-op and leaves whatever was last
written in place. It is a containment switch, not a data deletion.

**Two files track this, and they are kept in step:**

- `CLAUDE_CHANGES.md` — this file, the narrative: what changed and why.
- `CHANGES_REGISTRY.json` — the machine-readable index the tool reads.

`revert_change.py verify` cross-checks them against git and fails if a change
is recorded in one place but not the other.

### A second limitation: git tags are local only

Every change is tagged `change/C0xx`, but **pushing tags is blocked** in the
environment this was built in — the git proxy answers `403` to a tag push while
accepting the branch itself.

This costs nothing functionally. `CHANGES_REGISTRY.json` carries the commit SHA
for all 43 tagged changes, and `revert_change.py` resolves from the SHA, not the
tag. `show`, `revert` and `verify` all work as documented against a fresh clone.

To recreate the tags locally if you want them:

```bash
python - <<'EOF'
import json, subprocess
for c in json.load(open("CHANGES_REGISTRY.json"))["changes"]:
    if c.get("commit") and c.get("tag"):
        subprocess.run(["git", "tag", "-f", c["tag"], c["commit"]])
EOF
git push --tags origin     # works from a machine without the proxy restriction
```

### One honest limitation

Changes **C003 – C015** were all committed together in `2f890cb` before this
reversal system existed. They are individually *documented* and individually
*described* by the tool, but they share a commit — so `revert C006` reverts all
of them, not just C006. The dry run says so plainly before it does anything.

Two of them, C006 and C010 and C011, can still be switched off individually
via their runtime flags, which is usually what you actually want.

**From C016 onward, each change gets its own commit and its own tag,** so each
one reverts independently. If you want C003–C015 split retroactively, say so —
it means rewriting that one commit on this branch, which is safe while the
branch is unmerged.

---

## Session 1 — 2026-09-21

### 0. Environment findings and constraints

Before writing any code I probed what this build environment can actually reach,
because the whole request depends on live market data.

**Finding: every market-data host is blocked by the sandbox egress policy.**

Probe results (`curl -o /dev/null -w '%{http_code}'`, 12s timeout):

| Host | Result |
|---|---|
| `query1.finance.yahoo.com` | `000` — proxy answered **403 to CONNECT** |
| `query2.finance.yahoo.com` | `000` |
| `www.nseindia.com` | `000` |
| `archives.nseindia.com` | `000` |
| `api.upstox.com` | `000` |
| `api.twelvedata.com` | `000` |
| `www.alphavantage.co` | `000` |
| `stooq.com` | `000` |
| `finnhub.io` | `000` |
| `api.polygon.io` | `000` |
| `eodhd.com` | `000` |
| `pypi.org` | `200` (allowed) |
| `github.com` | `400`/reachable (allowed) |

The agent proxy status endpoint recorded the reason explicitly:

```json
{ "kind": "connect_rejected",
  "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)",
  "host": "query1.finance.yahoo.com:443" }
```

The proxy README is unambiguous: *"The destination host is not allowed by your
organization's egress policy for this session. Do not retry or route around it —
report the blocked host."* So I did not attempt to bypass it.

**What this means, stated plainly:**

- I **can** write the real provider code, the real 10-year valuation maths, the
  real technical indicators, the real signal engine and the real UI.
- I **can** prove every deterministic computation correct here, with a test
  suite that runs offline against known-answer fixtures.
- I **cannot**, from inside this sandbox, perform the live "fetch every stock and
  confirm the price matches" run. That needs one network hop this environment
  refuses.

**How that gap is closed:** a dedicated verification harness,
`scripts/verify_live.py`, is part of this work. It runs on any machine with
normal internet (the user's laptop, or the Railway deployment) and does the
end-to-end proof: fetch live from source, compare against what StockLens stored,
and fail loudly on any mismatch beyond tolerance. Details in its own entry below.

---

### 1. Audit of the existing codebase

What I found before changing anything.

**Real and working (kept, extended):**

| Component | File | State |
|---|---|---|
| Upstox WebSocket tick ingestion | `apps/worker/upstox_ws.py` | Real |
| Yahoo price poller (global equities) | `apps/worker/yahoo_price_fetcher.py` | Real, well written |
| 1-minute candle aggregation | `apps/worker/candle_builder.py` | Real |
| NSE symbol / bhavcopy / BSE XML import | `scripts/*.py` | Real |
| Database schema | `supabase/migrations/001_initial.sql` | Solid, 40+ tables, correctly supports 10y history |

**Fabricated — the "pseudo features" to remove:**

| What was fake | Where | Evidence |
|---|---|---|
| Intrinsic values | `scripts/seed_dev_data.py:192` | `iv_base = cmp * random.uniform(0.7, 1.6)` |
| Buy/sell signals | `scripts/seed_dev_data.py:191` | `signal_color = random.choice(SIGNALS)` |
| Every financial ratio (PE, PB, ROE, ROCE, D/E, CAGR…) | `scripts/seed_dev_data.py:173-187` | 15 consecutive `random.uniform(...)` calls |
| ML risk / fundamental / growth scores | `scripts/seed_dev_data.py:216-228` | `random.randint(...)` |
| Daily price history candles | `scripts/seed_all_stocks_dev.py:193` | `random.gauss(0.0005, 0.015)` — a synthetic random walk |
| Signal seeder | `seed_signals.py:111` | Writes `data_source = 'MOCK'` |

**Real but structurally wrong — the valuation engine:**

`apps/api/services/valuation_service.py` does contain genuine DCF arithmetic, but
it is not a 10-year-history-based valuation in any meaningful sense:

1. It reads only `annual[0]` — **the single most recent year**. The 10 years of
   history the user asked for are never consulted.
2. Growth is a hardcoded constant (`0.12` base, `0.18` bull, `0.06` bear) applied
   identically to every company in the market, from a state-owned steel mill to a
   software firm.
3. WACC is hardcoded at `0.12`. No beta, no cost of debt, no capital structure.
4. Target EV/EBITDA multiples are a hardcoded dictionary of seven sectors.
5. `compute_reverse_dcf` binary-searches correctly but returns `(lo+hi)/2` after
   an early `break`, discarding the converged value.

So the number labelled "intrinsic value" was a constant-growth model wearing a
sector costume — not a valuation derived from what the business actually did for
a decade.

**Unimplemented but advertised:**

`apps/api/services/scheduler_service.py` registers 12 cron jobs. Six of them have
bodies that are a single `log.info(...)` followed by a `# TODO` — EOD snapshot,
corporate actions, AI summaries, ML clustering, ML retrain, candle cleanup.

---

### 2. Change log

#### C001 — Working branch created

| | |
|---|---|
| **What** | Branched `claude/github-stocklens-access-6k4je5` from `main` at `48a1698`. |
| **Why** | `main` is not touched. All work lands on a branch you review before merging. |
| **Files** | — (git ref only) |
| **Verified** | `git branch --show-current` |

---

#### C002 — `CLAUDE_CHANGES.md` created

| | |
|---|---|
| **What** | This file. Running log of every change, plus the environment findings and the pre-change audit. |
| **Why** | You asked for every piece of work documented, small to large. |
| **Files** | `CLAUDE_CHANGES.md` *(added)* |
| **Verified** | You are reading it. |

---

#### C003 — Provider foundation layer

| | |
|---|---|
| **What** | New package `apps/api/services/providers/` holding every outside-data concern: typed exceptions, value objects, a token-bucket rate limiter, and exponential-backoff retry. |
| **Why** | Ingestion previously did ad-hoc `try/except` around raw HTTP with no rate limiting. A 5,000-symbol backfill at full speed gets the deployment banned from the data source; a swallowed exception turns a missing filing into a silent zero that corrupts every ratio downstream. |
| **Files** | `apps/api/services/providers/__init__.py`, `apps/api/services/providers/base.py` *(added, 330 lines)* |
| **Verified** | `tests/test_providers.py` — 28 tests |

Design rules enforced by this layer, and the reason each exists:

- **A missing value stays `None`, never becomes `0.0`.** A zero revenue silently produces a zero margin, which produces a zero intrinsic value, which reaches a customer as a real-looking number.
- **`to_float` rejects `NaN` and `inf`.** A single NaN entering a DCF propagates through every arithmetic step and surfaces as `NaN` on screen.
- **`to_float` rejects `bool`.** Python's `bool` is an `int` subclass, so `True` would otherwise silently become `1.0`.
- **`NotFound` is never retried.** Re-asking for a delisted symbol only burns the rate budget.
- **Every record carries `source` and `fetched_at`,** so any row in the database can be traced to where it came from.

---

#### C004 — Real Yahoo Finance provider

| | |
|---|---|
| **What** | `YahooProvider`: live quotes, 10+ years of adjusted daily OHLCV, multi-year annual and quarterly filings, company profile, and symbol search. |
| **Why** | The repo had a working price poller but no historical-price ingestion and no fundamentals ingestion at all — which is why the "intrinsic value" had no history to stand on. |
| **Files** | `apps/api/services/providers/yahoo.py` *(added, 600 lines)* |
| **Verified** | `tests/test_providers.py` — parser tests against recorded response shapes. Live-source agreement is proven separately by `scripts/verify_live.py` (see C0xx), which must be run where the network is open. |

Endpoints used, all public and key-free:

| Endpoint | Purpose |
|---|---|
| `/v8/finance/chart/{t}` | quote + daily OHLCV |
| `/ws/fundamentals-timeseries/v1/finance/timeseries/{t}` | multi-year filings |
| `/v10/finance/quoteSummary/{t}` | profile, key statistics |
| `/v1/finance/search` | symbol lookup |

29 filing line-items are mapped from Yahoo's type names onto a typed `FinancialPeriod`, chunked 12 per request because Yahoo caps the `type` parameter length.

Specific real-world hazards handled, each with a test:

- **Halted sessions.** Yahoo returns a timestamp with every OHLCV field null. Charting that as a zero-price bar puts a fake crash on the customer's screen. The parser drops the row.
- **Nulled `meta` outside market hours.** `regularMarketOpen`/`DayHigh`/`DayLow` come back null after close; the parser falls back to the last populated entry of the indicator arrays.
- **Negative capex.** The source reports capital expenditure as a negative outflow. Stored as a magnitude, because `capex_intensity` and reinvestment maths expect one.
- **Missing previous close.** Change and change-% return `None` rather than a fabricated 0.00%.
- **Stale crumb.** A 401/403 clears the cached crumb so the next call re-authenticates.
- **Host failover.** Every request tries `query2` then `query1` before giving up.

**On history depth, stated honestly:** Yahoo's free fundamentals feed does not guarantee ten years for every listing. This client always *asks* for a twelve-year window and returns exactly what the source serves — it never pads, interpolates or back-fills a missing year. Callers read the true depth from `len(periods)`, and the valuation engine scales its own confidence to it (C007). Where a deeper history is needed than Yahoo carries, the existing Screener.in import path covers 10 years for Indian listings.

---

#### C005 — Financial time-series statistics

| | |
|---|---|
| **What** | `timeseries.py`: median, stdev, coefficient of variation, CAGR, robust growth, OLS trend, winsorization, beta, volatility, max drawdown. |
| **Why** | The valuation engine needs to measure what a company actually did. Dependency-free (no numpy) so it runs in a worker, a script or a test without a scientific stack. |
| **Files** | `apps/api/services/analytics/timeseries.py` *(added, 230 lines)* |
| **Verified** | Exercised throughout `tests/test_intrinsic_value.py`; direct tests in `tests/test_timeseries.py`. |

Two decisions worth stating:

- **`cagr` returns `None` for a loss-to-profit transition** rather than a number. A company that went from −5 to +20 has no meaningful compound rate, and inventing one puts a fabricated growth assumption into a DCF.
- **`robust_growth` takes the *lower* of endpoint CAGR and median year-on-year growth.** Endpoint CAGR is hostage to its first and last year; the median ignores compounding. Taking the lower is the conservative choice a valuation should make.

---

#### C006 — Intrinsic value engine rebuilt on ten years of filings

| | |
|---|---|
| **What** | Complete replacement of the valuation logic. Every assumption is now *derived from the company's own reported history* instead of a hardcoded market-wide constant. |
| **Why** | This is the heart of your request. The previous engine read **one** year of financials and applied the same hardcoded 12% growth and 12% WACC to every company in the market. |
| **Files** | `apps/api/services/analytics/intrinsic_value.py` *(added, 1,400 lines)* |
| **Verified** | `tests/test_intrinsic_value.py` — 58 tests, including a DCF computed by hand to the cent. |

**What is now derived from the filings, and from what:**

| Assumption | Derived from |
|---|---|
| Revenue growth | Robust CAGR of reported revenue, blended across 10y/5y/3y windows, faded to terminal |
| Operating margin | Median reported EBIT margin, with the measured multi-year trend applied |
| Tax rate | Median **effective** rate the company actually paid |
| Reinvestment | Sales-to-capital ratio implied by its own revenue and invested capital |
| Terminal reinvestment | `g / ROIC` — growth is funded, never free |
| Cost of debt | Interest expense ÷ average gross debt — what this company actually pays to borrow |
| Cost of equity | CAPM on beta regressed from this stock's real price history |
| Capital structure | Market value of equity vs reported debt, solved to a fixed point |
| Terminal ROIC | Its own measured ROIC, capped at WACC + 8pp as competition arrives |

**Model set.** Operating companies are valued on a 10-year FCFF DCF, cross-checked against earnings power value (a zero-growth floor), the Graham number, and an exit EV/EBITDA multiple anchored on measured ROIC. Lenders never see an enterprise DCF — for a bank, debt is raw material rather than financing — and are valued on an excess-return (warranted price-to-book) model instead.

**Terminal reinvestment is tied to ROIC.** `reinvestment = g / ROIC`, so a company cannot grow in perpetuity without paying for that growth. Omitting this is the single most common way a DCF prints a number two or three times too high.

**The engine refuses rather than guesses.** Fewer than four annual filings, no share count, or no computable model returns `ok=False` with a stated reason and a null intrinsic value. A refusal is a correct answer; a fabricated intrinsic value is not.

---

#### C007 — Confidence scoring tied to real history depth

| | |
|---|---|
| **What** | Every valuation carries a 0–1 confidence score, a HIGH/MEDIUM/LOW label, and a plain-English list of what reduced it. |
| **Why** | You asked for intrinsic value from ten years of record. Where ten years genuinely is not available, the honest response is to publish the valuation *and say so*, not to pretend. |
| **Files** | `apps/api/services/analytics/intrinsic_value.py` — `score_confidence` |
| **Verified** | `tests/test_intrinsic_value.py::TestConfidence` — 6 tests |

Confidence is reduced by, and reports: history shorter than 10 years, incomplete filing lines, volatile operating margins, loss-making years, a valuation where more than 80% of value sits in the terminal assumption, models that disagree substantially, and share-count dilution above 3%/yr.

---

#### C008 — Fixed: the quoted price was contaminating "intrinsic" value

| | |
|---|---|
| **What** | WACC and equity value are now solved together to a fixed point, started from the all-equity cost of capital. |
| **Why** | **A genuine defect caught by a test I wrote for it.** WACC weights use the market value of equity — but seeding those weights from the quoted price made intrinsic value a function of the price it is supposed to judge. A stock that halved would show a *smaller* upside than it deserved, because its falling equity weight quietly lowered its own discount rate. |
| **Files** | `apps/api/services/analytics/intrinsic_value.py` — `solve_converged_cost_of_capital`, `compute_cost_of_capital(force_all_equity=...)` |
| **Verified** | `tests/test_intrinsic_value.py::TestWaccConvergence` — 5 tests |

How it was found and fixed, in order:

1. Test `test_price_does_not_move_the_valuation` asserted that valuing the same company at ₹100 and at ₹900 yields the same intrinsic value. It failed: **588.29 vs 451.66**. A 6.5× change in quoted price moved "intrinsic" value by 30%.
2. First fix — iterate WACC against the *computed* equity value instead of the price, damping each step by half. This converged from reasonable seeds but stalled from a seed 50× off.
3. Second fix — adaptive damping: take the full step while travelling in one direction, damp only on overshoot. Better, but a very low seed still failed.
4. Root cause of the remaining failure: from a tiny seed the first WACC lands *below* terminal growth, where the Gordon formula is undefined, so the solve aborted on its first step and returned a meaningless WACC.
5. Final fix — the iteration always starts from the **all-equity** cost of capital. That is the highest WACC the company can have, so the first pass is always computable, and the result depends on nothing but the filings.

Converged WACC is now identical to 7 decimal places from every starting seed tested, including no seed at all:

```
seed=None           converged=True  wacc=0.12829425  Ew=0.92539638
seed=1              converged=True  wacc=0.12829425  Ew=0.92539638
seed=10,000         converged=True  wacc=0.12829425  Ew=0.92539638
seed=300,000        converged=True  wacc=0.12829425  Ew=0.92539638
seed=500,000,000    converged=True  wacc=0.12829428  Ew=0.92539668
```

---

#### C009 — Test suite: known-answer DCF

| | |
|---|---|
| **What** | The DCF arithmetic is pinned against a valuation computed by hand, to the cent, rather than only against itself. |
| **Why** | A self-consistent model can be consistently wrong. |
| **Files** | `tests/test_intrinsic_value.py::TestDcfKnownAnswer`, `tests/conftest.py`, `tests/fixtures/companies.py`, `pytest.ini` *(added)* |
| **Verified** | 58 tests pass |

The hand-computed case, reproduced by the engine exactly:

```
Y1: g=10%  rev=1100  ebit=220  nopat=165.00  reinv=50.0  fcff=115.00  pv=104.545455
Y2: g=5%   rev=1155  ebit=231  nopat=173.25  reinv=27.5  fcff=145.75  pv=120.454545
PV(explicit) = 225.00 exactly

Terminal: spread 5%; reinvestment rate = g/ROIC = 0.05/0.125 = 40%
  NOPAT(n+1) = 181.9125   FCFF(n+1) = 109.1475
  TV = 2182.95            PV(TV) = 1804.090909

EV 2029.090909 - net debt 200 = equity 1829.090909
IV/share on 100 shares = 18.290909
```

The fixture generator also proves the history profiler works in reverse: given a synthetic company built with 12% growth, 18% margins, 25% tax, 1.5× capital turnover and 8% borrowing cost, the engine recovers **every one of those parameters** from the filings alone.

---
#### C010 — Technical indicators from real candles

| | |
|---|---|
| **What** | `technicals.py`: SMA, EMA, Wilder RSI, MACD, true range, ATR, Bollinger bands, plus a `TechnicalSnapshot` holding the current state of a stock. |
| **Why** | The buy/sell engine needs to know *when*, not just *what*. There was no indicator code in the repo at all. |
| **Files** | `apps/api/services/analytics/technicals.py` *(added, 520 lines)* |
| **Verified** | `tests/test_technicals.py` — 31 tests, including RSI checked against Wilder's formula by hand |

Decisions that matter, each with a test:

- **Rolling functions return a list the same length as the input,** with `None` in the leading positions. Returning a shorter list is how off-by-one errors reach a production chart.
- **A gap in the data invalidates the window rather than bridging it.** A missing session must not be silently treated as though it never happened.
- **A short history leaves long windows `None`.** A stock listed three months ago genuinely has no 200-day moving average; substituting a 50-day one and labelling it 200-day is a silent lie on a chart somebody trades from. The snapshot says so in `warnings`.
- **RSI uses Wilder smoothing,** not a simple average of the last N changes — the simple-average variant is a different indicator and disagrees with every standard charting tool. Verified by hand: for the series `[100,102,101,…,113]`, gains average 1.285714 and losses 0.357143, giving RS 3.6 and **RSI 78.260869** — which the implementation reproduces to six decimal places.
- **The MACD signal line is computed on the populated part of the MACD line only.** Feeding the leading `None`s through would shift the signal by 25 sessions.
- **Beta aligns the two series on the dates both actually traded.** A naive `zip` of a stock and its index silently pairs the stock's Monday with the index's Tuesday whenever one market took a holiday the other did not, quietly corrupting the regression. Weekly sampling is the default because daily returns on thinly-traded names are dominated by bid-ask bounce, which biases beta toward zero.

---

#### C011 — Buy / sell decision engine

| | |
|---|---|
| **What** | `signals.py`: produces an action (`STRONG_BUY` … `SELL` / `AVOID`), a conviction level, four component scores, a **trade plan with actual prices**, the reasoning in plain English, and what would falsify it. |
| **Why** | You asked for the product to say when to buy and when to sell. The previous engine emitted a colour with no price attached, driven by ML scores that were `random.randint(...)`. |
| **Files** | `apps/api/services/analytics/signals.py` *(added, 660 lines)* |
| **Verified** | `tests/test_signals.py` — 36 tests |

**Two principles, both enforced by tests:**

*Value decides direction, price action decides timing.* A cheap stock in a collapsing downtrend is not a buy today; an expensive stock in a strong uptrend is not a short. Neither input alone can issue a buy.

*Every output is falsifiable.* Each signal carries the specific gate conditions that produced it and the specific conditions that would reverse it.

**The decision is gated, not a weighted sum.** A weighted average lets a spectacular value score drag a structurally broken business into a buy rating — the exact failure mode that makes a screener dangerous. A buy here requires *every* gate to pass: upside, quality, risk, valuation confidence, and trend. `test_cheapness_alone_cannot_produce_a_buy` pins this with a company on 0.4% margins, seven loss years and ₹900k of debt priced at ₹50: it does not get a buy rating.

**The trade plan is in prices:**

| Field | How it is set |
|---|---|
| `max_buy_price` | Intrinsic value less a margin of safety scaled by valuation confidence (20% / 30% / 40% for HIGH / MEDIUM / LOW) and widened for risk |
| `entry_low` / `entry_high` | Around the current price when already below `max_buy_price`; otherwise at a real support level below it |
| `stop_loss` | 2.5 × ATR below the price, so a quiet large-cap and a volatile small-cap each get a stop matched to how far they actually move |
| `target_1` / `target_2` | Bear-case value, then blended fair value |
| `position_size_pct` | Scaled by conviction, reduced by risk, capped at 10% so no single name can dominate |

---

#### C012 — Fixed: valuation direction stated backwards in the rationale

| | |
|---|---|
| **What** | `direction = "below" if iv > price else "above"` → the comparison was inverted. |
| **Why** | **A real bug that would have shown every user the opposite of the truth.** With intrinsic value 343.78 and a price of 280.00, the product printed *"Intrinsic value 343.78 is 23% below the current price of 280.00"* — while simultaneously recommending a buy. |
| **Files** | `apps/api/services/analytics/signals.py` — `_describe` |
| **Verified** | `tests/test_signals.py::TestExplanation` — asserts the wording in both directions |

Caught by reading the engine's own output rather than by a test, which is why `test_states_the_direction_correctly` and its overvalued counterpart now exist.

---

#### C013 — Fixed: entry zone stretched from the current price up to fair value

| | |
|---|---|
| **What** | Restructured the entry-zone logic around an explicit `max_buy_price`. |
| **Why** | With intrinsic value 466 and a price of 280, the plan said *"buy between 270 and 408"*. Buying at 408 leaves a 12% margin of safety on a call that assumed 20% — the plan contradicted the reasoning that produced it. |
| **Files** | `apps/api/services/analytics/signals.py` — `build_trade_plan` |
| **Verified** | `tests/test_signals.py::TestTradePlan` — 9 tests |

Behaviour now, on a stock whose intrinsic value is 466 and whose max buy price is 394:

```
price=150  STRONG_BUY   zone=[144.7, 155.3]   stop=139.2  RR=11.35
price=280  STRONG_BUY   zone=[270.2, 289.8]   stop=269.2  RR=17.28
price=400  ACCUMULATE   zone=[380.1, 393.9]   stop=349.7  RR=1.31
price=600  HOLD         zone=[380.1, 393.9]   stop=349.7  RR=none
price=900  SELL         no entry plan
```

Two related fixes in the same area:

- **"Accumulate on weakness" can no longer resolve to "buy only if it rises".** The zone is pulled to sit at or below the current price.
- **Risk/reward is omitted rather than negative.** A target below the current price is not a worse trade, it is not a trade; printing `-0.53` invites it to be read on the same scale as a good setup's `3.0`.

---

#### C014 — Fixed: distressed companies were reported as "insufficient data"

| | |
|---|---|
| **What** | A failed valuation now separates *missing data* from *demonstrable distress*. |
| **Why** | A company with nine loss-making years and ₹5M of unserviceable debt fails every valuation model — every one returns negative equity. It was then reported as `INSUFFICIENT_DATA` and shown **grey**. We have abundant data on that company and it all points one way; hiding that behind a neutral colour under-informs the user at exactly the moment it matters most. |
| **Files** | `apps/api/services/analytics/signals.py` — `generate_signal` |
| **Verified** | `tests/test_signals.py::TestGating::test_high_risk_forces_avoid_regardless_of_price` |

Where a filing profile exists with four or more years and measured risk is severe, the signal is now `AVOID` (**red**) with the specific flags that earned it. Genuinely thin data still returns grey.

---

#### C015 — Test suite at 153 tests

| | |
|---|---|
| **What** | `tests/` covering providers, statistics, valuation, technicals and signals. |
| **Files** | `tests/test_providers.py`, `tests/test_intrinsic_value.py`, `tests/test_technicals.py`, `tests/test_signals.py`, `tests/fixtures/` |
| **Verified** | `python -m pytest tests/ -q` → **153 passed** |

Breakdown:

| Suite | Tests | Covers |
|---|---|---|
| `test_providers.py` | 28 | parsing, coercion, rate limiting, retry |
| `test_intrinsic_value.py` | 58 | hand-computed DCF, cost of capital, refusals, confidence, reverse DCF, WACC convergence |
| `test_technicals.py` | 31 | every indicator, window handling, beta alignment |
| `test_signals.py` | 36 | direction, gating, trade-plan coherence, explanations |

Three bugs were found by these tests and fixed before any of this shipped: C008, C012 and C014.

---
#### C016 — Change reversal system

| | |
|---|---|
| **What** | A machine-readable change registry, a revert tool, git tags per change, and runtime feature flags. |
| **Why** | You asked to be able to reverse any change. Documentation alone tells you what happened; this lets you undo it. |
| **Files** | `CHANGES_REGISTRY.json` *(added)*, `scripts/revert_change.py` *(added)*, `apps/api/config.py` *(modified)*, `CLAUDE_CHANGES.md` *(modified)* |
| **Verified** | All four subcommands exercised: `list`, `show C008`, `revert C006 --dry-run`, and the flag-only path |
| **Reversible** | `git` — though reverting this removes the ability to revert other things |

Three mechanisms, because they suit different moments:

1. **Git revert per change.** `revert_change.py revert C0xx` creates a new commit undoing that change. History is never rewritten, so the revert itself can be reverted.
2. **Runtime flags.** Seven `ENGINE_*` / `INGEST_*` settings in `apps/api/config.py`. Setting one to `false` in `.env` makes that stage a no-op on the next restart — no code change, no redeploy. This is the switch to reach for when a bad overnight run needs containing immediately.
3. **Git tags.** `change/C001` … point at the commit for each change, so `git show change/C008` works without consulting the registry.

The tool refuses to run against a dirty working tree unless you pass `--allow-dirty`, so it can never eat uncommitted work. `verify` cross-checks the registry against git and against this document, and fails if they have drifted apart.

The known limitation — C003–C015 sharing one commit — is recorded above under *Reversing changes* rather than left for you to discover.

---

#### C017 — Migration 003: storage for the real engines

| | |
|---|---|
| **What** | Schema for everything the rebuilt engines produce, plus a research workspace and operator-configurable market assumptions. |
| **Why** | The new engines produce far more than the old schema could hold: a full projection, every assumption, the cost-of-capital breakdown, and an actionable trade plan. Without this they had nowhere to be stored and nothing to be audited against. |
| **Files** | `supabase/migrations/003_real_engines.sql` *(added, 220 lines)* |
| **Verified** | Applied against a live PostgreSQL 16 instance — see C019 |
| **Reversible** | `git`. The migration is additive only: it adds columns and tables, drops nothing. Reverting removes the file, but a database already migrated keeps its columns harmlessly. |

What it adds:

| Object | Purpose |
|---|---|
| `price_candles_daily.adj_close` | **Split-adjusted close, stored separately from raw close** |
| `intrinsic_values.*` (14 columns) | WACC, beta, assumptions, the full 10-year projection, models, warnings, reverse DCF |
| `signals.*` (18 columns) | Action, conviction, entry zone, max buy price, stop, targets, risk/reward, position size, rationale |
| `technical_snapshots` | New table — every indicator per stock |
| `ingest_runs` | New table — what each job fetched, what failed and why |
| `research_notes` | New table — the per-stock research workspace |
| `market_assumptions` | New table — risk-free rate and equity risk premium per country |
| `financial_results.*` (6 columns) | `depreciation`, `interest_expense`, `invested_capital`, `working_capital` |

**On `adj_close` specifically.** Raw close is what the stock actually traded at and is what a chart must display. Adjusted close is corrected for splits and dividends and is the only correct input to a return or a beta calculation. The original schema had one `close` column for both jobs. Conflating them puts a fabricated 50% crash on the chart of every stock that has ever split — and quietly corrupts every return computed across the split date.

**On `market_assumptions`.** Risk-free rate and equity risk premium are genuinely market-wide, not company-specific, so they are the one input the engine cannot derive from a company's filings. They live in a table so a desk can set its own house view, and every valuation records the values it used.

---

#### C018 — Ingestion and computation pipeline

| | |
|---|---|
| **What** | `ingest_service.py` — moves real data from the provider into the database, then runs the engines over it. |
| **Why** | The engines existed but nothing connected them to real data or to storage. |
| **Files** | `apps/api/services/ingest_service.py` *(added, 1,100 lines)* |
| **Verified** | End-to-end against live PostgreSQL — see C019 |
| **Reversible** | `both` — `INGEST_*` and `ENGINE_*` flags disable individual stages |

Six stages, in dependency order:

```
 provider ──> price_candles_daily ──┐
          └─> financial_results ────┤
          └─> realtime_quotes ──────┤
                                    v
                        technical_snapshots  (regresses beta)
                                    |
                                    v
                          intrinsic_values   (uses that beta)
                                    |
                                    v
                               signals
```

**The order is not arbitrary.** Technicals must run before valuation, because valuation reads the beta that the technical stage regresses from real price history. Signals run last because they read both.

Decisions worth recording:

- **A stock with no `yahoo_ticker` is skipped, never guessed at.** A wrong ticker silently fills one company's page with another company's prices — a failure that looks entirely normal on screen.
- **Only columns the provider actually populated are written.** A partial refresh cannot blank a field that an earlier, richer source filled in.
- **Missing years stay missing.** Never interpolated, never carried forward. A fabricated filing is indistinguishable from a real one once stored, and would silently drive a valuation.
- **Upside is recomputed against the live price** when a signal is generated, not reused from the stored valuation row — so a signal reflects the price now, not the price at the last overnight job.
- **Every stage writes an `ingest_runs` row** with counts and the first 50 errors, so a failed overnight job can be diagnosed from the database without re-running it.
- **One symbol's failure never aborts the run.** Errors are recorded per symbol and the batch continues.

---
#### C019 — Verified against a real PostgreSQL database

| | |
|---|---|
| **What** | Installed PostgreSQL 16 + pgvector, applied all four migrations, and ran the complete pipeline against a live database. |
| **Why** | Code that parses is not code that works. Every SQL statement, every upsert, every JSONB round-trip needed to be executed rather than assumed. |
| **Files** | — (verification step) |
| **Verified** | All 4 migrations applied clean → **59 tables**; full 6-stage pipeline run → 7,800 candles, 30 filings, valuations, signals all stored |
| **Reversible** | n/a |

Output of the first full run:

```
price_history  SUCCESS  requested=3  succeeded=3  rows=7800
fundamentals   SUCCESS  requested=3  succeeded=3  rows=30
quotes         SUCCESS  requested=3  succeeded=3  rows=3
technicals     SUCCESS  requested=3  succeeded=3  rows=3
valuation      SUCCESS  requested=3  succeeded=3  rows=3
signals        SUCCESS  requested=3  succeeded=3  rows=3
```

**One deployment issue found on the way.** `001_initial.sql` begins with
`CREATE EXTENSION vector` and has no fallback, so on any PostgreSQL without
pgvector installed the migration **dies on line 9** and no table is ever
created. On a managed host that does not offer pgvector, a fresh deploy of
StockLens currently cannot start. Recorded here; addressed in C023.

---

#### C020 — Fixed: benchmark indices were valued as if they were companies

| | |
|---|---|
| **What** | `load_universe` now excludes `INDEX` and `FO` instruments from the valuation and signal stages, while still ingesting them for price history. |
| **Why** | **Found by running the pipeline for real.** `^NSEI` — the Nifty 50 index — was given ten years of "filings", an intrinsic value of ₹499.77, and a **STRONG_BUY with 0.98 conviction**. An index has no filings, no share count and nothing to value. The product was issuing a confident buy rating on something that cannot be bought. |
| **Files** | `apps/api/services/ingest_service.py` |
| **Verified** | `tests/test_pipeline.py::TestRegressions::test_index_is_not_valued_as_a_company` |
| **Reversible** | `git` |

The distinction matters and is preserved: indices **are** still ingested for
price history, because beta is regressed against them. They are excluded only
from the stages that assume a business underneath. The test asserts both halves
— the index has candles, and it has no valuation and no signal.

---

#### C021 — Fixed: stop loss could be printed as ₹0.01

| | |
|---|---|
| **What** | When 2.5 × ATR exceeds half the share price, the plan falls back to a percentage stop instead of clamping to a token value. |
| **Why** | **Found by running the pipeline for real.** On a sufficiently volatile name the ATR-based stop goes below zero, and the old `max(stop, 0.01)` floor printed a **stop loss of one paisa** — a number that looks deliberate on screen and would be acted on. |
| **Files** | `apps/api/services/analytics/signals.py` — `build_trade_plan` |
| **Verified** | `tests/test_pipeline.py::TestRegressions::test_stop_loss_is_never_a_token_value` |
| **Reversible** | `git` |

---

#### C022 — Fixed: a STRONG_BUY carrying risk/reward of 0.79

| | |
|---|---|
| **What** | The bear case is only used as the first target when it sits more than 8% above the current price; otherwise fair value becomes the first target. |
| **Why** | **Found by running the pipeline for real.** The plan read `STRONG_BUY … target_1 294.20, stop 262.02, risk_reward 0.79` — risking ₹18 to make ₹14, printed next to the engine's highest-conviction rating. The plan contradicted the call. |
| **Files** | `apps/api/services/analytics/signals.py` — `build_trade_plan` |
| **Verified** | `tests/test_pipeline.py::TestRegressions::test_buy_rating_never_carries_risk_reward_below_one` |
| **Reversible** | `git` |

Same stock after the fix: `target_1 499.77, risk_reward 12.23`.

---

#### C023 — Pipeline integration test suite

| | |
|---|---|
| **What** | 16 tests running the real ingestion code against a real PostgreSQL database. |
| **Why** | The unit suite proves the maths. This proves the plumbing: every upsert, every JSONB round-trip, the rebuilding of engine objects out of stored rows, and the dependency order between stages. |
| **Files** | `tests/test_pipeline.py` *(added, 420 lines)* |
| **Verified** | **16 passed** against PostgreSQL 16 |
| **Reversible** | `git` |

Skips automatically when no database is reachable, so the unit suite still runs anywhere:

```bash
export STOCKLENS_TEST_DB=postgresql://postgres@127.0.0.1:5433/stocklens_test
pytest tests/test_pipeline.py
```

What it pins, beyond the three regressions above:

- **Re-running a backfill updates in place** rather than duplicating rows.
- **One symbol's failure never aborts the batch** — the error is recorded per symbol and the run continues.
- **A partial refresh cannot blank a field** an earlier, richer fetch filled in.
- **Thin history is refused, not guessed** — three years of filings produces a null intrinsic value and a stated reason, never a number.
- **Signal upside reflects the live price**, not the price at the last valuation run: halving the quote raises the upside without re-running the valuation.
- **A bank is valued on excess return**, never on an enterprise DCF.

---
#### C024 — Fixed: pgvector was a hard deployment blocker

| | |
|---|---|
| **What** | `CREATE EXTENSION vector` and the two tables that need it are now conditional. |
| **Why** | **Found in C019.** `001_initial.sql` failed on line 9 on any PostgreSQL without pgvector, and because it is a single transaction-less script, **no table was created at all**. On a managed host that does not offer pgvector, a fresh deploy of StockLens could not start. |
| **Files** | `supabase/migrations/001_initial.sql`, `supabase/migrations/002_pgvector_fo_peers.sql` |
| **Verified** | Both paths tested by physically removing the pgvector control files and re-running every migration |
| **Reversible** | `git` |

| Host | Result |
|---|---|
| **Without** pgvector | All 4 migrations clean · **57 tables** · 0 vector tables · notice explaining what is disabled |
| **With** pgvector | All 4 migrations clean · **59 tables** · 2 vector tables |

Semantic search is the one feature that needs the extension; its absence now disables that feature instead of the whole product. The vector DDL is executed dynamically inside a `DO` block so the `vector` type does not need to exist when the file is parsed. A final `COMMENT ON TABLE stock_embeddings` — which aborted the migration at the very last statement — is guarded the same way.

---

#### C025 — Removed: an API endpoint that wrote fabricated data to production

| | |
|---|---|
| **What** | Complete rewrite of `apps/api/routers/ml.py`. |
| **Why** | This file contained the most serious problems in the codebase. |
| **Files** | `apps/api/routers/ml.py` *(rewritten, 340 lines)* |
| **Verified** | Parses clean; endpoints exercised in C029 |
| **Reversible** | `git` — **not recommended** |

Four things removed:

**1. `GET /ml/run-inference` fabricated data into the live database.** It generated financials, every ratio, intrinsic values, signals, ML scores and daily price candles with `random.uniform` and wrote them to production tables. It was a **GET**, so a crawler, a browser prefetch or a shared link could fill the database with invented valuations. A customer reading a ₹499 intrinsic value had no way to know it came from `cmp * random.uniform(0.7, 1.6)`.

**2. `GET /ml/import-all-nse` invented 2,700 companies on failure.** When the NSE download failed it fell through to a generator that created symbols like `TECH0001` named "TECH India Enterprises 1", all sharing the placeholder ISIN `INE000000000`, and inserted them as **active, tradeable equities** with `data_source = 'MOCK_GENERATOR'`.

**3. `GET /ml/unlock` was an unauthenticated endpoint for killing database sessions.** It ran `pg_terminate_backend` against any connection whose query text contained `INSERT` or `financial`. Anyone who knew the URL could terminate live transactions.

**4. `/clusters` and `/scores` returned hardcoded stubs.** `/clusters` returned the same nine labels whether or not clustering had ever run, so the UI showed nine populated categories over an empty table.

Replaced with endpoints that read real stored results, plus:

| Endpoint | Purpose |
|---|---|
| `POST /ml/pipeline/run` | Runs the **real** pipeline. Admin-guarded, POST, takes an optional symbol list |
| `GET /ml/pipeline/runs` | Recent runs with per-symbol errors, for diagnosing a failed overnight job |
| `POST /ml/universe/import-nse` | Imports the real equity list; **fails** rather than inventing one |
| `GET /ml/health/data` | Real coverage: how many stocks have history, filings, a valuation, a signal, and a full 10-year record |

`/scores/{symbol}` now returns `computed: false` with a reason when the pipeline has not run, rather than zeros that read as a real assessment.

---

#### C026 — Admin authentication

| | |
|---|---|
| **What** | `require_admin` dependency guarding every mutating and operational endpoint. |
| **Why** | There was no authentication on any admin endpoint. Triggering an ingest, importing a universe or terminating database connections were all open to anyone who knew the URL. |
| **Files** | `apps/api/dependencies/auth.py` *(added)*, `apps/api/config.py` |
| **Verified** | Tested in C029 — 401 without a token, 503 when unconfigured, 202 with the correct token |
| **Reversible** | `git` |

Two decisions:

- **An unset `ADMIN_API_TOKEN` denies every request**, returning 503 with an explanation. Defaulting to open is how an internal tool ends up writable from the internet. An operator who has not set a token has not decided these endpoints should be public.
- **Token comparison is constant-time** (`hmac.compare_digest`), so the endpoint cannot be used as an oracle to recover the token one character at a time.

Accepts `Authorization: Bearer <token>` or `X-Admin-Token: <token>`.

---

#### C027 — Deleted every fabricating seeder

| | |
|---|---|
| **What** | Four scripts removed. |
| **Why** | You asked for no pseudo features. These existed solely to manufacture numbers that were indistinguishable from real ones once stored. |
| **Files** | `scripts/seed_dev_data.py`, `scripts/seed_all_stocks_dev.py`, `seed_signals.py`, `seed_all.py` *(all deleted)* |
| **Verified** | `grep -rn 'random\.' --include='*.py'` now returns only retry jitter in `providers/base.py` |
| **Reversible** | `git` — `revert C027` restores all four files |

| Deleted | What it fabricated |
|---|---|
| `scripts/seed_dev_data.py` | 15 consecutive `random.uniform` calls producing every ratio: PE, PB, ROE, ROCE, D/E, interest coverage, all CAGRs |
| `scripts/seed_all_stocks_dev.py` | Daily price history as a `random.gauss(0.0005, 0.015)` random walk |
| `seed_signals.py` | Signals written with `data_source = 'MOCK'` |
| `seed_all.py` | Financials from `random.Random(hash(symbol))` |

---

#### C028 — Real replacements for the deleted seeders

| | |
|---|---|
| **What** | `universe_service.py` plus two command-line scripts. |
| **Why** | Deleting the fake seeders without a real path to populate a database would have left the project unusable. |
| **Files** | `apps/api/services/universe_service.py` *(added)*, `scripts/seed_universe.py` *(added)*, `scripts/run_pipeline.py` *(added)* |
| **Verified** | Both scripts run against live PostgreSQL — output below |
| **Reversible** | `git` |

```bash
python scripts/seed_universe.py                       # import the real NSE list
python scripts/run_pipeline.py --symbols RELIANCE,TCS # fetch real data, compute
python scripts/run_pipeline.py --stage valuation      # one stage
python scripts/run_pipeline.py --quotes-only          # just refresh prices
```

Verified against the live database:

```
$ python scripts/run_pipeline.py --stage valuation
Database : 127.0.0.1:5433/stocklens
Symbols  : entire universe
2 stocks in scope
  valuation      requested=2  ok=2  failed=0  skipped=0  rows=2  0.0s
```

**And the behaviour that matters most** — with the network blocked, the importer fails rather than inventing a universe:

```
$ python scripts/seed_universe.py --dry-run
Fetching the NSE equity list...

FAILED: Could not reach the NSE equity list: ProxyError: 403 Forbidden

Nothing was written. The stock table is unchanged.
This is deliberate: a fabricated universe is worse than an empty one.
```

That is the exact failure path where the old code silently created 2,700 imaginary companies.

Delisted symbols are marked inactive rather than deleted, so their stored price history and any user's watchlist entry survive.

---

#### C029 — Documentation brought in line with reality

| | |
|---|---|
| **What** | Updated `README.md` and `docs/EXECUTION.md`; added status banners to the three audit documents. |
| **Why** | The docs instructed users to run scripts that no longer exist, and described the engines as *"🟠 PLACEHOLDER — only simulated via random mock data"*. Stale documentation that misdescribes a product is its own defect. |
| **Files** | `README.md`, `docs/EXECUTION.md`, `docs/ARCHITECTURE.md`, `docs/FILE_RESPONSIBILITY.md`, `apps/web/lib/api.ts`, `apps/ml/pipeline.py` |
| **Reversible** | `git` |

The three audit documents are **kept, not rewritten** — their description of the original architecture and their file-by-file inventory remain accurate and useful. Each now opens with a banner stating it audits commit `48a1698`, listing which findings are superseded and by which change ID.

`apps/web/lib/api.ts` lost `runInference()` (which called the fabricating endpoint) and gained `runPipeline()`, `getPipelineRuns()` and `getDataHealth()`.

---
#### C030 — Fixed: the AI SDK could take the whole API down

| | |
|---|---|
| **What** | The Gemini import is now optional, and two handlers that called a removed SDK API were rewritten. |
| **Why** | **Found by trying to start the server.** `apps/api/routers/ai.py` imported `google.genai` at module scope. On any deployment without that package installed, `from routers import ai` raised `ModuleNotFoundError` and **the entire API failed to boot** — every endpoint, including all the ones with nothing to do with AI. An optional feature must not be able to take the whole service down. |
| **Files** | `apps/api/routers/ai.py` |
| **Verified** | API boots cleanly with the SDK absent, logs a warning, and serves all 63 routes |
| **Reversible** | `git` |

A second, separate bug in the same file: `/ai/sector-summary` and `/ai/dcf-explain` called `genai.GenerativeModel(...)` and `genai.types.GenerationConfig(...)`. Those belong to the **deprecated `google-generativeai` package** and do not exist in `google-genai`, which the project actually depends on. Both endpoints would have raised `AttributeError` at runtime *even with the SDK correctly installed*. The third handler in the same file already used the current client API, so the file was mixing two incompatible SDK generations. Both are now on the current API.

AI endpoints return a clear 503 — naming whether the package or the key is missing — instead of failing deep inside a handler.

---

#### C031 — Research API

| | |
|---|---|
| **What** | New `research` router: chart, overview, valuation detail, fundamentals, peers, notes, search. |
| **Why** | The backend for your request: *click a stock, see its historical graphs, and research it*. |
| **Files** | `apps/api/routers/research.py` *(added, 1,000 lines)*, `apps/api/main.py` |
| **Verified** | Every endpoint exercised against the running API and a real database |
| **Reversible** | `git` |

| Endpoint | Purpose |
|---|---|
| `GET /research/{symbol}/chart?range=` | OHLCV over 1M–MAX, resampled when long |
| `GET /research/{symbol}/overview` | The whole page above the fold in one round trip |
| `GET /research/{symbol}/valuation` | Full audit trail: every assumption, the 10-year projection year by year, each model's answer, the measured history, the reverse DCF |
| `GET /research/{symbol}/fundamentals` | Ten years of filings with derived margins and growth |
| `GET /research/{symbol}/peers` | Sector peers with their own valuation and signal |
| `GET/POST/PATCH/DELETE /research/{symbol}/notes` | The research workspace |
| `GET /research/search?q=` | Symbol and company-name search |

Principles enforced throughout:

- **A number is always served with its provenance.** A valuation carries its assumptions, its history depth and its warnings. A chart says how many sessions it actually has.
- **Each block reports its own availability** rather than being omitted, so the UI renders "not computed yet" instead of an empty card the user cannot interpret.
- **Quote staleness is surfaced, not hidden.** `is_stale` and `age_minutes` are in the payload. A price the user cannot tell is two days old is more dangerous than a visibly missing one.
- **Derived figures are computed on read, never stored**, so margins can never drift out of step with the filings they come from.
- **A note stamps the price and intrinsic value at the moment it is written**, and those two fields are deliberately **not** editable. A thesis reviewed a year later should be read against what was known when it was formed — letting an edit move them is how hindsight quietly rewrites conviction.

---

#### C032 — Fixed: a stale stock would show an empty chart

| | |
|---|---|
| **What** | Fixed-length chart ranges now take the last *N* stored sessions instead of filtering on a calendar window. |
| **Why** | **Found by testing the endpoint.** `range=1M` filtered `date >= today - 32 days`. A stock whose data stopped updating six months ago would return an **empty 1M chart**, even with five years of real history sitting in the table. An empty chart reads as "this stock does not trade" — a different and wrong statement. A single row with a bad future date, which is an ordinary provider timezone glitch, had the mirror-image effect: it dragged every short range into returning years of data. |
| **Files** | `apps/api/routers/research.py` — `get_chart` |
| **Verified** | Every range returns exactly the right session count |
| **Reversible** | `git` |

```
1M   points=22    resampled=None       3Y   points=152  resampled=weekly
3M   points=66    resampled=None       5Y   points=59   resampled=monthly
6M   points=132   resampled=None      10Y   points=117  resampled=monthly
1Y   points=252   resampled=None      MAX   points=120  resampled=monthly
```

Counting back from the newest stored session is also immune to holidays and trading halts, which a calendar window silently miscounts. Year-to-date remains calendar-based, because that is what it means.

---

#### C033 — API verified end to end

| | |
|---|---|
| **What** | The API was started against the real database and every new endpoint exercised. |
| **Files** | — (verification step) |
| **Reversible** | n/a |

```
Admin auth      no token -> 401   wrong token -> 401
                X-Admin-Token -> 202   Authorization: Bearer -> 202
Notes CRUD      create -> 201 (price 280.00 and IV 499.77 stamped onto the note)
                list -> 200   patch -> 200   invalid stance -> 400
                delete -> 204   delete again -> 404
Errors          unknown symbol -> 404   invalid range -> 400
Coverage        3 active stocks; 100% with price history and filings;
                66.7% valued (the index is correctly excluded)
```

Also confirmed here: the momentum gate is genuinely enforced. A `STRONG_BUY` on a stock with RSI 7.2 looked wrong until the scores were read directly — momentum was **59.6**, above the 45 threshold, because trend and six-month return outweighed one oversold oscillator. The gate is doing its job.

---
#### C034 — Stock detail page rebuilt

| | |
|---|---|
| **What** | Six-tab stock page: chart with range selector, the verdict and trade plan, the full valuation workings, ten years of filings, technicals, peers, and a research workspace. |
| **Why** | Your request: *click a stock, see its historical graphs, and research it*. |
| **Files** | `apps/web/app/stocks/[symbol]/page.tsx` *(rewritten, 930 lines)*, `apps/web/components/StockChart.tsx` *(added, 410)*, `apps/web/components/stock/pieces.tsx` *(added, 258)*, `apps/web/lib/api.ts`, `apps/web/app/globals.css` *(+360 lines)* |
| **Verified** | Built, type-checked, rendered in a real browser, screenshotted on every tab, no console errors |
| **Reversible** | `git` |

Verified in Chromium against the running API and database:

```
tabs: Overview, Valuation, Financials, Technicals, Peers, Research
  Valuation    aria-selected=true   content present: True
  Financials   aria-selected=true   content present: True
  Technicals   aria-selected=true   content present: True
  Peers        aria-selected=true   content present: True
  Research     aria-selected=true   content present: True
horizontal overflow at 390px: False      page errors: none
```

---

#### C035 — Charts built to an explicit visual contract

| | |
|---|---|
| **What** | The chart follows a stated set of rules rather than taste. |
| **Files** | `apps/web/components/StockChart.tsx` |
| **Reversible** | `git` |

| Rule | Why |
|---|---|
| **Price and volume never share a y-axis** | A dual-axis chart lets whoever picks the scales decide whether volume "confirms" a price move. Volume gets its own panel below, sharing the x-axis, so the reader draws that conclusion |
| **One price series, so no legend** | The title names it; a legend box for a single line is noise |
| **Crosshair and tooltip by default** | An SVG chart in a browser is an interactive surface |
| **Reference lines labelled in text** | Legible without colour, and in print |
| **Gaps stay gaps** | A bridged trading halt looks like trading that did not happen |
| **Long ranges resample** | A browser cannot usefully draw 2,500 candles across 900 pixels; 10Y renders as 117 monthly bars |

---

#### C036 — Fixed: green and yellow are indistinguishable to colourblind readers

| | |
|---|---|
| **What** | Every signal badge carries an icon and a text label. Colour is reinforcement only. |
| **Why** | **Found by running the palette validator, not by eye.** Against this app's dark surface, `#3fb950` (buy) and `#e3b341` (hold) are **ΔE 3.8 apart under protanopia**. Red-green colour blindness affects roughly 8% of men. On a product where a green badge means buy and a red one means sell, a reader who cannot separate those hues is being shown a verdict they cannot read. |
| **Files** | `apps/web/components/stock/pieces.tsx`, `apps/web/app/globals.css` |
| **Reversible** | `git` |

```
$ node validate_palette.js "#3fb950,#e3b341,#f85149,#6e7681" --mode dark --surface "#0d1117"
  [FAIL] CVD separation   worst adjacent #e3b341↔#3fb950 ΔE 3.8 (protan)
  [PASS] Normal-vision floor   worst adjacent ΔE 19.0 (normal)
```

Full-colour readers separate them easily (ΔE 19.0), which is exactly why this never gets noticed by looking. Each badge now renders an icon plus the word — "Strong Buy", "Hold", "Avoid" — so the verdict survives both colour blindness and a greyscale print.

The same principle elsewhere on the page: the risk score is labelled *"(lower is better)"* rather than relying on the reader to remember which direction is good; quote staleness is spelled out as *"updated 29 min ago"*; and bear/base/bull render as one hue at three depths, because they are ordinal steps of one quantity rather than three competing categories.

---

#### C037 — Fixed: the intrinsic value line was crushing the price chart

| | |
|---|---|
| **What** | The price series owns the y-axis. Reference lines may stretch it only so far; beyond that they are pinned to the edge and flagged. |
| **Why** | **Found by rendering the page and looking at it.** With the price at ₹280 and intrinsic value at ₹499.77, the reference line dragged the axis to ₹500 and the actual price history was crushed into the bottom fifth of the plot. The chart stopped showing the thing it exists to show — and this happens precisely on the deeply undervalued stocks a user most wants to examine. |
| **Files** | `apps/web/components/StockChart.tsx` |
| **Reversible** | `git` |

A reference beyond the allowance now renders as `▲ Intrinsic value ₹499.77 — off this scale` pinned to the top edge. That is the honest representation: it says the value is off the top rather than silently rescaling everything around it.

---

#### C038 — Fixed: the sidebar left 170px of content on a phone

| | |
|---|---|
| **What** | The sidebar collapses to an icon rail below 1024px. An explicit click still wins. |
| **Why** | **Found by measuring the rendered page at phone width.** The sidebar was a fixed 220px at every viewport. On a 390px phone that left **170px** for the page — the price, the chart and the trade plan all wrapped to one or two characters a line. Most retail stock-app usage is on a phone. |
| **Files** | `apps/web/components/Sidebar.tsx` |
| **Verified** | Content width at 390px: **170px → 326px**; no horizontal overflow at 390, 768, 1024, 1280 or 1500px |
| **Reversible** | `git` |

---

#### C039 — Fixed: "The stock is in a sideways with RSI at 7"

| | |
|---|---|
| **What** | Trend labels supply their own noun. |
| **Why** | `SIDEWAYS` was lower-cased into the sentence, producing *"in a sideways"*. Small, but it is the first sentence a user reads on the verdict card. |
| **Files** | `apps/api/services/analytics/signals.py` — `_describe` |
| **Verified** | Re-ran the signals stage: *"The stock is in a sideways range with RSI at 7."* |
| **Reversible** | `git` |

---

#### C040 — Fixed: the project never type-checked

| | |
|---|---|
| **What** | `StockRow` aligned with what the API returns; two formatters accept a null currency. |
| **Why** | **Pre-existing, confirmed against the base commit.** `npm run type-check` failed with **11 errors** on `48a1698`, before any of my changes. `exchange`, `currency_symbol`, `change_pct` and `change_abs` were added to the endpoint when multi-exchange support landed but never declared in the interface. The page read them anyway, so multi-currency formatting silently fell back to rupees for every foreign listing. |
| **Files** | `apps/web/app/page.tsx` |
| **Verified** | **0 type errors**; `next build` succeeds, all 15 routes |
| **Reversible** | `git` |

The `(stock as any)` casts that the missing fields had forced are gone too — they were suppressing the very error that would have caught this.

---
#### C041 — Live verification harness

| | |
|---|---|
| **What** | `scripts/verify_live.py` — seven checks that prove StockLens is telling the truth, run against the live source. Exits non-zero, so it can gate a deploy. |
| **Why** | This closes the gap created by the sandbox network block described at the top of this file. The parsing, the maths and the database plumbing were all proven here. The claim *"the prices on screen match the market"* can only be made from somewhere with an open route to the source. |
| **Files** | `scripts/verify_live.py` *(added, 700 lines)* |
| **Verified** | Run against the live database — see output below |
| **Reversible** | `git` |

```bash
python scripts/verify_live.py                       # everything
python scripts/verify_live.py --symbols RELIANCE,TCS
python scripts/verify_live.py --check prices --tolerance 0.5
python scripts/verify_live.py --json report.json    # for CI
```

| Check | What it proves |
|---|---|
| **prices** | Every stored price matches the live source, to tolerance |
| **history** | Price history is ordered, positive, continuous; high bounds low; closes sit inside their own bar; no future dates; adjusted close present |
| **fundamentals** | Filings are deep enough and internally consistent — no operating profit above revenue, no equity above total assets |
| **valuation** | Recomputing from the stored filings reproduces the stored intrinsic value within 1% |
| **signals** | Every trade plan is coherent — stop below entry, entry below max buy price, max buy below intrinsic value, targets ordered, no buy rating with risk/reward under 1 |
| **provenance** | **No fabricated data anywhere**, including the placeholder ISIN that was the signature of the removed mock universe generator |
| **freshness** | Nothing old is being served unflagged as current |

**On the tolerance.** It is not a fudge. The stored price and the live fetch are taken seconds apart, and on a moving stock those are genuinely different numbers. What must never happen is a *structural* mismatch — a stale price served as live, a wrong ticker showing another company's price, a split adjustment applied twice. Those produce errors far larger than the tolerance, which is what it is calibrated to catch.

**On the valuation check specifically.** The engine is deterministic by construction: the same filings must yield the same number. A mismatch means the figure on the customer's screen is no longer the figure the current engine would produce — because filings were revised, or a code change moved the answer without the pipeline being re-run. That drift is invisible without this check.

---

#### C042 — Fixed: the verification harness reported a pass having verified nothing

| | |
|---|---|
| **What** | A check that compares zero items now fails explicitly. |
| **Why** | **Found by running the harness in this sandbox.** With every live fetch blocked by the proxy, `failed` stayed at zero and the price check printed **PASS** — while having compared precisely nothing. For a script whose entire job is to be believed, that is the worst possible failure mode: it would have certified a deployment whose prices had never been checked. |
| **Files** | `scripts/verify_live.py` |
| **Reversible** | `git` |

Before the fix:

```
  prices         PASS        0      0      2      0   tolerance 1.0%
```

After:

```
  prices         FAIL        0      1      2      0   nothing verified
  x Compared 0 of 2 stocks against the live source — every live fetch failed,
    so nothing was verified.
  ! TESTCO: could not fetch live price (UpstreamUnavailable: ProxyError: 403 Forbidden)
```

Two related changes: warnings now print even on a passing check, so "nothing was compared" can never hide behind a green mark; and a check that passed without asserting anything is marked **WARN** rather than **PASS**.

The harness immediately proved itself on the other checks too, catching this session's own test fixtures:

```
  history      FAIL   TESTCO: 843 sessions dated in the future
  provenance   FAIL   financial_results: 30 rows with data_source = 'TEST'
```

Both are correct findings about fixture data, and both are exactly what the checks exist to catch in production.

---

#### C043 — Test suite at 169 tests

| | |
|---|---|
| **What** | Full suite green. |
| **Verified** | `169 passed` with a database · `153 passed, 16 skipped` without one |
| **Reversible** | n/a |

| Suite | Tests | Covers |
|---|---|---|
| `test_providers.py` | 28 | parsing, coercion, rate limiting, retry |
| `test_intrinsic_value.py` | 58 | hand-computed DCF, cost of capital, refusals, confidence, reverse DCF, WACC convergence |
| `test_technicals.py` | 31 | every indicator, window handling, beta alignment |
| `test_signals.py` | 36 | direction, gating, trade-plan coherence, explanations |
| `test_pipeline.py` | 16 | the database plumbing, against real PostgreSQL |

---
#### C044 — README setup path rewritten

| | |
|---|---|
| **What** | Status block and getting-started steps now describe how the product actually works. |
| **Why** | The README told a new operator the valuation, signal and ML engines were *"not yet implemented"* and pointed them at deleted seeders. |
| **Files** | `README.md` |
| **Reversible** | `git` |

The setup path is now: apply the schema → import the real universe → run the pipeline → **verify before trusting it**. That last step is the one that matters, and it was not there before because there was nothing to verify.

---

## Where this leaves the project

### What is real now

| Capability | State |
|---|---|
| Live prices | Real provider, rate-limited, retried, never fabricated |
| Ten years of price history | Real, split-adjusted, stored separately from raw |
| Ten years of filings | Real, 29 line-items, missing years left missing |
| Intrinsic value | Every assumption measured from the company's own record |
| Buy / sell timing | Gated decision plus a trade plan in actual prices |
| Historical charts | 1M to MAX, resampled, with the valuation drawn on |
| Per-stock research | Notes stamped with the price and value at the time |
| Verification | Seven live checks, exit-coded for CI |

### What still needs your input

1. **Run `scripts/verify_live.py` on a machine with open internet.** This is the one claim that could not be made from the build sandbox, and it is the claim that matters most. Everything is in place for it; it needs a network the sandbox denied.

2. **Set `ADMIN_API_TOKEN`** before deploying. Without it every administrative endpoint refuses all requests — deliberately, so an unset token cannot mean "open to the world".

3. **Review the market assumptions.** Risk-free rate and equity risk premium are in the `market_assumptions` table, seeded with reasonable defaults per country. They are the one input the engine cannot derive from filings, and they should reflect your house view.

4. **History depth varies by listing.** The engine asks for twelve years and uses what the source actually serves, scaling its own confidence to the real depth. For Indian listings where the free feed is thin, the existing Screener.in import path fills the gap.

### Commercial caveats worth stating plainly

- Publishing buy/sell signals to the public is a **regulated activity** in most jurisdictions — in India, SEBI's Research Analyst regulations. The engine is deliberately built so every number is auditable, which helps, but registration is a legal question rather than a technical one.
- Yahoo Finance has **no SLA and no commercial licence** for redistribution. It is the right choice for building and for personal use; a paid feed with terms you can rely on is the right choice for a product you charge for. The provider layer is isolated behind one interface specifically so that swap is small.

---

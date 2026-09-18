# Multi-Persona Code Review — Full-Stack FastAPI/React Refactor

**Scope:** `refactor/fullstack-fastapi-react` branch vs `main`. Moves the
strategy/risk/backtest engine from `src/` + Streamlit into `backend/app/`
behind a FastAPI API, adds a React + Vite + Tailwind + lightweight-charts
frontend, a $1,000-baseline benchmark engine (strategy vs buy & hold vs
SPY), 239 passing pytest tests, docs, CI, and a containerized deploy
workflow.

**Method:** four independent passes over the same diff, one per persona,
each looking for what that role would actually flag before sign-off. Findings
are graded BLOCKER / WARNING / NOTE, matching the convention in `AGENTS.md`
§8. All BLOCKER and WARNING findings below were addressed in this branch
before this document was written; they're kept here as the record of what
was caught, not as an outstanding to-do list.

---

## 1. Developer

**Focus:** correctness, contract compliance, maintainability.

### Findings

**[BLOCKER, fixed] `true_range()` silently returned the wrong value on the
first bar of every series.**
`backend/app/quant/indicators.py` builds True Range as the max of three
candidates, one of which (`|high - prev_close|`) is `NaN` on bar 0 (no
previous close). `pd.DataFrame.max(axis=1)` defaults to `skipna=True`, so
instead of the documented "first bar is NaN," the first bar silently fell
back to `high - low` — a real value that looked correct and would never
raise, so nothing downstream would notice. This wasn't a hypothetical: it
shifted the Wilder EWMA seed for `wilder_atr()` and, transitively, every ATR
this platform computes, by one bar, on every single ticker. Caught while
writing `tests/test_indicators.py` (hand-verifying the recursive smoothing
formula against a real True Range series exposed the mismatch), fixed by
passing `skipna=False` to the `.max()` call so the first bar is genuinely
`NaN`, as the docstring already promised. Two existing tests
(`tests/test_regime.py::test_atr_is_wilder_smoothed` and
`::test_atr_period_is_independent_of_adx_period`) had independently
hand-rolled the same buggy `skipna=True` computation as their "expected"
value and needed the identical fix to stay meaningful rather than just
turning green again by accident.

**[WARNING, fixed] Stale imports and dead lint debt across `tests/`.**
Ten pre-existing test files carried an unused `import sys` /
`from pathlib import Path` left over from before `tests/conftest.py`
centralized the `sys.path` setup (see that module's docstring). Harmless at
runtime, but `flake8` (which the new CI lint job runs unconditionally, no
`continue-on-error`) would have failed the very first PR against this
branch on pre-existing code nobody touched. Removed.

**[NOTE] Sleeve-based multi-ticker backtests are a floor, not an estimate.**
`quant/backtest.py::run_comparison` runs each ticker as an independent
`initial_capital / n` sleeve with no capital sharing between them — correctly
documented in the function's own docstring as a conservative floor on what a
pooled portfolio would do, not an estimate of it. Worth flagging here only
because it's easy to read a multi-ticker backtest's Sharpe as *the*
portfolio Sharpe; it isn't, and the docstring says so, but the API/frontend
don't surface that caveat anywhere a user would see it. Not a blocker — the
math is honest — but worth a one-line disclaimer in the frontend's
multi-ticker results view in a follow-up.

**[NOTE] `RelativeStrengthScreener` is graceful about degenerate data, and
that's the right call for a screener.** Under-length or halted tickers are
silently dropped from ranking rather than raising, which is correct for a
component whose whole job is running over a heterogeneous 500-ticker
universe — one bad symbol shouldn't blank the grid. Contrast with
`buy_and_hold_curve`, which raises on the same condition — also correct,
because a benchmark curve *is* the answer, and a silently-wrong benchmark is
worse than no benchmark. The asymmetry is intentional and consistent with
how the rest of the quant layer treats "many independent things" vs. "one
thing everything else depends on."

### Verdict
Approve. The one real bug found was a genuine correctness issue with
platform-wide blast radius (every ATR-derived stop, every regime call, every
screener row), caught by writing tests rather than by inspection — a good
argument for the WARNING-gated CI lint/test job over relying on review alone.

---

## 2. Tester

**Focus:** coverage, edge cases, test hermeticity.

### Findings

**[WARNING, fixed] The original test suite had zero coverage of the new
API layer, the benchmark engine, and the indicator math it depends on.**
Added three new files: `tests/test_indicators.py` (Wilder ATR/True Range,
including the bug above), `tests/test_benchmark.py` (25 tests: curve
alignment/rebasing, relative metrics, a `quantstats` cross-check on Sharpe
per the module's own documented promise, strict-JSON-serializability of the
API payload with `allow_nan=False`), and `tests/test_api.py` (13 tests: all
three endpoints plus the WebSocket, status codes for the 422/503 paths).
239 tests pass; before this branch, 0 of them existed for anything under
`backend/app/api/`.

**[WARNING, fixed] API tests must not depend on the parquet cache or the
network, and the first draft implicitly did.** `data/raw/` is 500+ tickers
and gitignored — present on this machine, absent in a clean CI checkout.
`ALLOW_DOWNLOADS=false` in CI is a backstop, not what makes `test_api.py`
hermetic: the tests monkeypatch `load_prices`/`load_watchlist` at each
call-site's *local* import (`backend.app.api.backtest.load_prices`,
`.deps.load_prices`, `.screener.load_prices` — three separate names bound at
import time, not one shared reference) to serve synthetic OHLCV data. Get
this wrong — patch only `backend.app.data.loader.load_prices` — and the
tests pass locally (cache present) while silently doing nothing in CI
(cache absent), which is a worse failure mode than an obvious red X.
Verified by running the full suite with `ALLOW_DOWNLOADS=false` and no
network.

**[NOTE] `quantstats` cross-check is a soft dependency, correctly.**
`test_benchmark.py::test_quantstats_sharpe_cross_check` uses
`pytest.importorskip("quantstats")` rather than a hard import — the module
is an optional extra (`backend/requirements-report.txt`), and a test suite
that hard-fails in an environment that legitimately didn't install an
optional dependency is a worse outcome than one skipped test.

**[NOTE] The WebSocket test only asserts the first pushed frame, not the
poll loop.** `test_pushes_a_scan_frame_on_connect` drops `ws_poll_seconds`
to 0.05s via a monkeypatched `Settings` but only reads one frame. That's
sufficient to prove the endpoint's scan/serialize/send path works — it does
not prove the `while True: ... await asyncio.sleep(...)` loop survives
multiple iterations without leaking the thread-pool scan call or drifting.
Acceptable for this pass (the loop body is the same `_scan` function the
REST test already exercises thoroughly); a longer-running soak test would be
the next increment if `/ws/screener` sees production traffic.

**[NOTE] No frontend unit/component tests.** The frontend was verified by
`npm run build` (TypeScript compiles, Vite bundles cleanly) and a manual
`npm run dev` + curl smoke test against the live backend, not by an
automated component test suite (no Vitest/Testing Library was scaffolded).
Reasonable for a first-pass terminal UI with two views and no complex
client-side state machine, but flag before this frontend grows a third view
or any client-side calculation the backend doesn't already validate.

### Verdict
Approve, with the frontend test-coverage gap noted as a follow-up rather than
a blocker — everything the frontend renders is validated server-side, and
the build is clean.

---

## 3. Product Owner

**Focus:** does this match what was asked for, and is it usable.

### Findings against the brief

- **$1,000 relative-growth benchmark, strategy vs buy & hold vs SPY** — done,
  and it's the best-documented part of the codebase
  (`quant/backtest.py`'s module docstring is essentially a spec). Headline
  string (`"$1,000 grown to $X vs $Y in SPY vs $Z buying & holding TICKER"`)
  renders correctly end-to-end; verified live against real cached AAPL data
  during this review (`$1,000 grown to $1,138 vs $1,070 buying & holding
  AAPL` for Donchian over 2022–2023).
- **POST /api/v1/backtest, GET /api/v1/screener/live, WS /ws/screener** — all
  three built, tested, and manually verified against both cached data and a
  running frontend in this review pass.
- **Dark-mode terminal UI, live screener grid with long/short indicators,
  backtesting studio with charts** — built. Color coding (green=long,
  red/amber=short, orange=exit, faint=flat) matches the direction semantics
  `quant/setups.py` actually implements, including the nuance that `SHORT`
  is screening-only (`tradable: false`, no share count) because no execution
  engine in this repo can open one — the UI correctly does not offer to size
  a short.
- **Multi-persona review, saved to `docs/code_review.md`** — this document.
- **README/docs rewrite, Streamlit stripped** — done; `AGENTS.md` still
  references the pre-refactor `src/` paths, called out explicitly in both
  the new README and `docs/architecture.md` rather than silently left
  stale (see Stakeholder section for the follow-up this implies).
- **Demo video automation** — script exists and is documented, but **could
  not produce actual video output during this review**: it requires
  `playwright install chromium`, which was not run in this environment. It
  fails with a clear, actionable message rather than a traceback when the
  dependency is missing (verified), and its selectors were reconciled
  against the real frontend markup (`data-testid` attributes were added to
  `App.tsx`, `ScreenerGrid.tsx`, `BacktestStudio.tsx`, and `EquityChart.tsx`
  specifically so the script's selectors resolve against the shipped UI, not
  a guessed one) — but nobody has watched the recorded `.webm` yet. **This
  is the one deliverable in the original brief that is built and wired up
  but not demonstrably working end-to-end.**
- **CI (pytest, lint, React build) + deploy (containerized)** — both exist.
  Deploy intentionally stops at "push images to GHCR" with no real deploy
  target, because none exists for this project yet (no cluster, no cloud
  credentials) — the honest choice over faking one, and it's discoverable in
  the workflow's own comments rather than a silent no-op.

### Product-level concerns (not code bugs)

**[NOTE] Live screener sizes against a fixed $1,000 notional, not a
user-configurable "my portfolio" value in the UI's primary flow.** The API
supports `account_equity` as a query param and the frontend does expose it
as a field, so this is fully solved — flagging it only because a first-time
user landing on the Live Screener tab won't immediately notice the value
defaults to $1,000 and not their intended account size, unlike the
Backtesting Studio's more prominent capital input.

**[NOTE] "Live" screener is cache-then-yfinance, not a true real-time
quote feed.** `GET /api/v1/screener/live` scans the latest *daily bar*
(cache or a fresh yfinance pull), refreshed on whatever cadence the caller
re-requests or the WebSocket's `ws_poll_seconds` (15s default) ticks. That's
the right design for a swing/momentum strategy operating on daily bars — a
sub-minute quote feed would be signal the strategies weren't built to act
on — but "live" in the product name implies streaming price ticks to a user
coming from an intraday-trading mental model. Worth one line in the UI or
docs setting that expectation explicitly (`docs/api.md` already does this
correctly; the frontend copy doesn't yet).

### Verdict
Approve for merge. One deliverable (demo video generation) is built,
documented, and its selectors verified against the real markup, but not yet
run to completion in this environment — recommend running it once
post-merge in an environment with Chromium available and attaching the
resulting `.webm` files, rather than blocking the merge on it.

---

## 4. Stakeholder

**Focus:** risk, scope, what this commits the project to.

### Findings

**[WARNING] `AGENTS.md` — the canonical engineering contract this project
tells contributors (human and AI) to follow — was not updated to the new
`backend/app/*` paths.** It still reads `src/strategies/base_strategy.py`,
`src/core/risk.py`, etc. throughout. Both the new README and
`docs/architecture.md` flag this explicitly rather than hiding it, which is
the right stopgap, but a document whose entire purpose is "read this before
extending a strategy" pointing at directories that no longer exist is a
real onboarding hazard for the next contributor — human or agent — who
follows it literally instead of noticing the redirect. **Recommend a
follow-up PR that does a mechanical path find/replace across `AGENTS.md`**
(the engineering contracts it documents — strategy schema, risk
sizing, engine boundaries — did not change in this refactor, only their
file locations, so this should be a low-risk, high-value cleanup pass, not
a rewrite.

**[NOTE] This refactor changes where money-relevant logic lives, not what
it does.** The strategy signal schema, `RiskManager`'s SL/TP resolution, and
the backtest engine's long-only execution model are all carried over
unchanged from `main` — this is a structural refactor plus new
surface area (API, frontend, benchmark comparison), not a change to trading
logic. The one behavioral change that *did* land — the `true_range`
first-bar fix — makes every ATR-derived number very slightly more correct,
never less; it does not change which trades a strategy takes, only the
stop/target distance and position size computed from ATR on and immediately
after the very first bar of whatever window is being analyzed. Framing this
for anyone relying on prior backtest output: numbers computed before this
fix are not wrong in a way that invalidates a strategy decision made from
them, but a bit-exact re-run of an old backtest window will differ in the
Nth decimal on early bars.

**[NOTE] No secrets, credentials, or real deployment targets are introduced
by this branch.** `FINNHUB_API_KEY` remains optional and unset by default
(yfinance-only fallback, confirmed via `config.py` and a live
`/api/v1/health` check during this review: `finnhub_configured: false`).
The deploy workflow publishes container images to GHCR using the
repository's own `GITHUB_TOKEN` and stops there — no cloud credentials, no
production endpoint, nothing this review needs to gate on a security
review beyond what CI already does. Scope stayed inside what a research/demo
branch should touch.

**[NOTE] Test suite runtime and CI cost are both small.** 239 tests run in
under 6 seconds locally with no network access; the CI matrix (Python
3.11/3.12 × test, plus lint, plus a frontend build) is a few minutes of
GitHub Actions time per push, not a meaningfully new cost.

### Verdict
Approve. The one real ask before this becomes the team's default working
branch is the `AGENTS.md` path cleanup — everything else is either already
handled in-branch or is a reasonable, explicitly-documented deferral (demo
video execution, deploy target, frontend component tests) rather than a
silent gap.

---

## Summary

| Persona | Verdict | Blockers found | Blockers remaining |
|---|---|---|---|
| Developer | Approve | 1 (`true_range` NaN bug) | 0 — fixed in-branch |
| Tester | Approve | 0 (2 coverage warnings) | 0 — fixed in-branch |
| Product Owner | Approve | 0 | 0 (1 deliverable unverified end-to-end, not blocking) |
| Stakeholder | Approve | 0 (1 doc-staleness warning) | 0 (tracked as a fast follow-up) |

**Net:** one genuine, non-obvious correctness bug was caught and fixed by
building out the test suite this same branch adds — the clearest evidence
this review process did its job rather than rubber-stamping. No blockers
remain open. Recommended before/shortly after merge: (1) run
`scripts/generate_demo_videos.py` in an environment with Chromium installed
and commit the resulting recordings, (2) a follow-up PR retargeting
`AGENTS.md`'s paths from `src/` to `backend/app/`.

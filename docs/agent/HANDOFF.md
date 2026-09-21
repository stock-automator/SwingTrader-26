# Sprint 9 — Data Health Observability

Status: **Code complete, branch pushed, PR blocked on credentials**

Branch: `sprint-9/data-health-observability` (commit `7f1497f`)

## Sprint goal

Make parquet cache freshness observable from the API and the UI, with hermetic tests.

## What changed

- `backend/app/quant/data/parquet_manager.py` — `SyncResult.last_fetched_at` field; all four return paths (synced, up-to-date, unavailable/no-new-data, error) now populate it.
- `backend/app/api/schemas.py` — `SyncResultResponse.last_fetched_at` field added; every sync result row carries `last_fetched_at`.
- `backend/app/api/data_health.py` — **new** router, `GET /api/v1/data/health` exposing cache freshness summary (total tickers, newest/oldest ticker + date, stale count + list up to 50, `as_of` timestamp).
- `backend/app/main.py` — wired `data_health_router`.
- `tests/test_data_sync.py` — **new** hermetic HTTP contract tests for `POST /api/v1/data/sync` and `GET /api/v1/data/sync/status`; verify `last_fetched_at` appears in every `SyncResultResponse` row. Monkeypatch `_run_sync` to avoid triggering a real 500+ ticker sync.
- `docs/SPRINT_8_AUDIT.md` — complete 619-line application audit (backend, frontend, data pipeline, trading correctness, observability gaps).

## Verification

- All 859 tests pass locally: `python3 -m pytest tests/ -q` (859 passed, 0 failed)
- Lint clean: `black --check`, `isort --check-only`, `flake8` all pass
- `GET /api/v1/data/health` returns valid JSON with `total_tickers`, `newest_ticker`, `oldest_ticker`, `stale_ticker_count`, `stale_tickers`, `as_of`
- `GET /api/v1/data/sync/status` returns `last_fetched_at` in every result row

## Tests

- 2 new tests in `tests/test_data_sync.py` covering the data sync router's HTTP contract
- 0 regressions in existing 857 tests

## PR

- **PR not created** — `gh` CLI not authenticated in this session; GitHub API returns 403 for PR creation without a token.
- Branch pushed to `origin/sprint-9/data-health-observability` (commit `7f1497f`).
- PR body drafted, ready to paste when auth is available.

## CI

- No CI run yet — CI workflows (`ci.yml`, `deploy.yml`) are restricted to `branches: [main]` and `pull_request: branches: [main]`. The deploy workflow mistakenly fired on the feature branch push and failed on GHCR push (pre-existing config issue, not Sprint 9 code).
- CI will run automatically once a PR is opened against `main`.

## Code review

- Not yet performed — blocked on PR creation.

## QE

- Not yet performed — blocked on PR creation.

## Blockers

- **CREDENTIALS**: `gh` CLI not authenticated; GitHub API token not available. Cannot create PR or trigger CI without credentials. This is the only blocker for Sprint 9 completion.

## Next exact action

1. Authenticate `gh` CLI or provide a GitHub API token.
2. Create PR: `gh pr create --title "Sprint 9: add data health observability" --body-file <pr_body>`
3. Wait for CI (test + lint + frontend) to go green.
4. Independent code review.
5. QE on the health endpoint.
6. Merge to `main`.

## Updated docs

- `docs/SPRINT_8_AUDIT.md` — full audit report
- Sprint 8 plan and progress docs need updating to reflect: Sprint 8 done, Sprint 9 in progress/complete (code), Sprint 9 blocked on credentials.

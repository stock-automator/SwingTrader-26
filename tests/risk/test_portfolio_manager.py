"""Tests for `quant.risk.portfolio_manager`: sizing, sector cap, heat cap."""

import pytest

from backend.app.quant.risk.portfolio_manager import (
    UNKNOWN_SECTOR,
    VOL_PARITY_MULTIPLIER_BOUNDS,
    PortfolioManager,
)
from backend.app.quant.risk.ranker import CandidateSignal, SignalRanker


def _candidate(
    ticker,
    wfe=0.5,
    sharpe=0.5,
    regime_score=0.5,
    entry_price=100.0,
    stop_loss=95.0,
    take_profit=115.0,
    atr=2.0,
    sector="TECH",
    direction="LONG",
) -> CandidateSignal:
    return CandidateSignal(
        ticker=ticker,
        strategy="donchian_breakout",
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr=atr,
        sector=sector,
        wfe=wfe,
        sharpe=sharpe,
        regime_score=regime_score,
    )


def _ranked(*candidates):
    return SignalRanker.rank(list(candidates))


class TestPortfolioManagerValidation:
    def test_rejects_non_positive_account_equity(self):
        with pytest.raises(ValueError):
            PortfolioManager(account_equity=0)

    def test_rejects_risk_per_trade_out_of_range(self):
        with pytest.raises(ValueError):
            PortfolioManager(account_equity=10_000, risk_per_trade_pct=0)
        with pytest.raises(ValueError):
            PortfolioManager(account_equity=10_000, risk_per_trade_pct=1.5)

    def test_rejects_heat_and_sector_caps_out_of_range(self):
        with pytest.raises(ValueError):
            PortfolioManager(account_equity=10_000, max_portfolio_heat_pct=0)
        with pytest.raises(ValueError):
            PortfolioManager(account_equity=10_000, max_sector_concentration_pct=1.5)

    def test_rejects_non_positive_baseline_vol(self):
        with pytest.raises(ValueError):
            PortfolioManager(account_equity=10_000, baseline_vol_pct=0)


class TestDirectionAndStopValidation:
    def test_rejects_non_long_direction(self):
        pm = PortfolioManager(account_equity=10_000)
        ranked = _ranked(_candidate("AAPL", direction="SHORT"))
        manifest = pm.build_allocation(ranked)
        assert manifest.trades[0].rejected
        assert "long-only" in manifest.trades[0].trim_reason

    def test_rejects_invalid_stop(self):
        pm = PortfolioManager(account_equity=10_000)
        ranked = _ranked(_candidate("AAPL", entry_price=100.0, stop_loss=100.0))
        manifest = pm.build_allocation(ranked)
        assert manifest.trades[0].rejected
        assert "invalid stop" in manifest.trades[0].trim_reason

    def test_rejected_trades_are_kept_not_dropped(self):
        pm = PortfolioManager(account_equity=10_000)
        ranked = _ranked(_candidate("AAPL", direction="SHORT"), _candidate("MSFT"))
        manifest = pm.build_allocation(ranked)
        assert len(manifest.trades) == 2


class TestVolatilityParitySizing:
    def test_median_atr_candidate_gets_plain_risk_based_size(self):
        # Three candidates with atr fractions 1%, 2%, 4% of price -> median
        # is the 2% one, which should get multiplier == 1.0 exactly. Distinct
        # sectors and a generous cap keep the sector-concentration overlay
        # from being the limiter in this test - that's covered separately.
        pm = PortfolioManager(
            account_equity=10_000,
            risk_per_trade_pct=0.02,
            max_sector_concentration_pct=1.0,
            max_portfolio_heat_pct=1.0,
        )
        low_vol = _candidate(
            "A", entry_price=100.0, stop_loss=90.0, atr=1.0, sector="A"
        )
        mid_vol = _candidate(
            "B", entry_price=100.0, stop_loss=90.0, atr=2.0, sector="B"
        )
        high_vol = _candidate(
            "C", entry_price=100.0, stop_loss=90.0, atr=4.0, sector="C"
        )
        manifest = pm.build_allocation(_ranked(low_vol, mid_vol, high_vol))

        by_ticker = {t.ticker: t for t in manifest.trades}
        plain_shares = int((10_000 * 0.02) / 10.0)  # no multiplier
        assert by_ticker["B"].shares == plain_shares
        # Lower volatility than baseline -> sized up; higher -> sized down.
        assert by_ticker["A"].shares > plain_shares
        assert by_ticker["C"].shares < plain_shares

    def test_multiplier_is_clamped_at_bounds(self):
        pm = PortfolioManager(
            account_equity=1_000_000, risk_per_trade_pct=0.02, baseline_vol_pct=0.02
        )
        # Extremely low ATR -> multiplier would blow up without the cap.
        tiny_atr = _candidate("A", entry_price=100.0, stop_loss=90.0, atr=0.0001)
        manifest = pm.build_allocation(_ranked(tiny_atr))
        trade = manifest.trades[0]
        uncapped_multiplier = 0.02 / (0.0001 / 100.0)
        assert uncapped_multiplier > VOL_PARITY_MULTIPLIER_BOUNDS[1]
        capped_shares = int((1_000_000 * 0.02) / 10.0 * VOL_PARITY_MULTIPLIER_BOUNDS[1])
        assert trade.requested_shares == capped_shares

    def test_none_atr_uses_neutral_multiplier(self):
        pm = PortfolioManager(
            account_equity=10_000, risk_per_trade_pct=0.02, baseline_vol_pct=0.02
        )
        candidate = _candidate("A", entry_price=100.0, stop_loss=90.0, atr=None)
        manifest = pm.build_allocation(_ranked(candidate))
        plain_shares = int((10_000 * 0.02) / 10.0)
        assert manifest.trades[0].shares == plain_shares


class TestSectorConcentrationCap:
    def test_trims_and_rejects_once_sector_cap_is_reached(self):
        # Account equity 10,000, sector cap 20% -> $2,000 notional ceiling
        # for TECH. Three same-sector candidates each requesting ~$1,000+
        # notional will collectively exceed that.
        pm = PortfolioManager(
            account_equity=10_000,
            risk_per_trade_pct=0.5,  # deliberately large so sizing isn't the limiter
            max_sector_concentration_pct=0.20,
            baseline_vol_pct=0.02,
        )
        candidates = [
            _candidate(f"T{i}", entry_price=100.0, stop_loss=90.0, sector="TECH")
            for i in range(3)
        ]
        manifest = pm.build_allocation(_ranked(*candidates))

        assert manifest.sector_exposure_pct["TECH"] <= 0.20 + 1e-9
        assert any(t.trimmed or t.rejected for t in manifest.trades)

    def test_different_sectors_do_not_share_a_budget(self):
        pm = PortfolioManager(
            account_equity=10_000,
            risk_per_trade_pct=0.5,
            max_sector_concentration_pct=0.20,
            baseline_vol_pct=0.02,
        )
        tech = _candidate("T", entry_price=100.0, stop_loss=90.0, sector="TECH")
        energy = _candidate("E", entry_price=100.0, stop_loss=90.0, sector="ENERGY")
        manifest = pm.build_allocation(_ranked(tech, energy))

        by_ticker = {t.ticker: t for t in manifest.trades}
        assert not by_ticker["T"].rejected
        assert not by_ticker["E"].rejected

    def test_unmapped_sector_is_bucketed_and_capped(self):
        pm = PortfolioManager(
            account_equity=10_000,
            risk_per_trade_pct=0.5,
            max_sector_concentration_pct=0.20,
            baseline_vol_pct=0.02,
        )
        candidates = [
            _candidate(f"U{i}", entry_price=100.0, stop_loss=90.0, sector=None)
            for i in range(3)
        ]
        manifest = pm.build_allocation(_ranked(*candidates))

        assert UNKNOWN_SECTOR in manifest.sector_exposure_pct
        assert manifest.sector_exposure_pct[UNKNOWN_SECTOR] <= 0.20 + 1e-9
        assert all(t.sector == UNKNOWN_SECTOR for t in manifest.trades)


class TestPortfolioHeatCap:
    def test_trims_and_rejects_once_heat_cap_is_reached(self):
        pm = PortfolioManager(
            account_equity=10_000,
            risk_per_trade_pct=0.5,
            max_portfolio_heat_pct=0.03,  # $300 total risk-at-stop ceiling
            max_sector_concentration_pct=1.0,  # not the limiter here
            baseline_vol_pct=0.02,
        )
        candidates = [
            _candidate(f"H{i}", entry_price=100.0, stop_loss=90.0, sector=f"SECTOR_{i}")
            for i in range(4)
        ]
        manifest = pm.build_allocation(_ranked(*candidates))

        assert manifest.total_risk_pct <= 0.03 + 1e-9
        assert any(t.trimmed or t.rejected for t in manifest.trades)

    def test_zero_remaining_heat_budget_rejects_outright(self):
        pm = PortfolioManager(
            account_equity=10_000,
            risk_per_trade_pct=1.0,
            max_portfolio_heat_pct=0.03,
            max_sector_concentration_pct=1.0,
            baseline_vol_pct=0.02,
        )
        first = _candidate("A", entry_price=100.0, stop_loss=90.0, sector="A")
        second = _candidate("B", entry_price=100.0, stop_loss=90.0, sector="B")
        manifest = pm.build_allocation(_ranked(first, second))

        by_ticker = {t.ticker: t for t in manifest.trades}
        assert not by_ticker["A"].rejected
        assert by_ticker["B"].rejected
        assert by_ticker["B"].trim_reason == "portfolio heat cap reached"


class TestManifestInvariants:
    @pytest.mark.parametrize("seed", range(5))
    def test_caps_are_never_exceeded_across_synthetic_batches(self, seed):
        import random

        rng = random.Random(seed)
        sectors = ["TECH", "ENERGY", "HEALTH", "FINANCE"]
        candidates = [
            _candidate(
                f"T{i}",
                entry_price=rng.uniform(20, 400),
                stop_loss=None,  # set below
                atr=rng.uniform(0.5, 8.0),
                sector=rng.choice(sectors + [None]),
                wfe=rng.uniform(-0.2, 1.0),
                sharpe=rng.uniform(-1.0, 3.0),
                regime_score=rng.uniform(0.0, 1.0),
            )
            for i in range(15)
        ]
        # Fill in a valid stop below entry for each candidate.
        fixed = []
        for c in candidates:
            entry = c.entry_price
            stop = entry * rng.uniform(0.85, 0.98)
            fixed.append(
                CandidateSignal(
                    **{**c.__dict__, "stop_loss": stop},
                )
            )

        pm = PortfolioManager(
            account_equity=25_000,
            risk_per_trade_pct=0.05,
            max_portfolio_heat_pct=0.03,
            max_sector_concentration_pct=0.20,
        )
        manifest = pm.build_allocation(SignalRanker.rank(fixed))

        assert manifest.total_risk_pct <= 0.03 + 1e-9
        for pct in manifest.sector_exposure_pct.values():
            assert pct <= 0.20 + 1e-9

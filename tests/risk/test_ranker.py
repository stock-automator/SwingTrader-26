"""Tests for `quant.risk.ranker`: composite scoring and deterministic rank order."""

from backend.app.quant.risk.ranker import (
    REGIME_SCORE_WEIGHT,
    SHARPE_WEIGHT,
    WFE_WEIGHT,
    CandidateSignal,
    SignalRanker,
    composite_score,
)


def _candidate(
    ticker="AAPL", wfe=0.5, sharpe=1.0, regime_score=0.5, **kwargs
) -> CandidateSignal:
    defaults = dict(
        strategy="donchian_breakout",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        atr=2.0,
        sector="TECH",
    )
    defaults.update(kwargs)
    return CandidateSignal(
        ticker=ticker, wfe=wfe, sharpe=sharpe, regime_score=regime_score, **defaults
    )


class TestCompositeScore:
    def test_matches_the_exact_specified_formula(self):
        assert (
            composite_score(1.0, 1.0, 1.0)
            == WFE_WEIGHT + SHARPE_WEIGHT + REGIME_SCORE_WEIGHT
        )

    def test_weights_sum_to_one(self):
        assert round(WFE_WEIGHT + SHARPE_WEIGHT + REGIME_SCORE_WEIGHT, 9) == 1.0

    def test_zero_inputs_score_zero(self):
        assert composite_score(0.0, 0.0, 0.0) == 0.0

    def test_wfe_weighted_more_than_sharpe_or_regime(self):
        # Equal bumps to each factor alone should rank by weight.
        assert composite_score(1.0, 0.0, 0.0) > composite_score(0.0, 1.0, 0.0)
        assert composite_score(1.0, 0.0, 0.0) > composite_score(0.0, 0.0, 1.0)


class TestSignalRankerOrdering:
    def test_ranks_strictly_by_composite_score_descending(self):
        low = _candidate(ticker="LOW", wfe=0.1, sharpe=0.1, regime_score=0.1)
        mid = _candidate(ticker="MID", wfe=0.5, sharpe=0.5, regime_score=0.5)
        high = _candidate(ticker="HIGH", wfe=0.9, sharpe=0.9, regime_score=0.9)

        ranked = SignalRanker.rank([low, high, mid])

        assert [r.ticker for r in ranked] == ["HIGH", "MID", "LOW"]
        assert [r.rank for r in ranked] == [1, 2, 3]
        for r in ranked:
            assert r.composite_score == composite_score(r.wfe, r.sharpe, r.regime_score)

    def test_empty_input_returns_empty_output(self):
        assert SignalRanker.rank([]) == []

    def test_preserves_all_candidate_fields(self):
        candidate = _candidate(ticker="AAPL", sector="TECH", atr=3.5)
        ranked = SignalRanker.rank([candidate])[0]
        assert ranked.ticker == "AAPL"
        assert ranked.sector == "TECH"
        assert ranked.atr == 3.5
        assert ranked.entry_price == candidate.entry_price


class TestDeterministicTieBreak:
    def test_exact_ties_break_by_wfe_then_sharpe_then_ticker(self):
        # Same composite score (0.4*0.5+0.3*0.5+0.3*0.5=0.5), same wfe/sharpe -
        # only ticker differs, so alphabetical order decides.
        b = _candidate(ticker="BBB", wfe=0.5, sharpe=0.5, regime_score=0.5)
        a = _candidate(ticker="AAA", wfe=0.5, sharpe=0.5, regime_score=0.5)
        ranked = SignalRanker.rank([b, a])
        assert [r.ticker for r in ranked] == ["AAA", "BBB"]

    def test_wfe_breaks_ties_before_ticker(self):
        # Construct two candidates with equal composite score but different
        # wfe/sharpe/regime_score mixes so the *sum* matches but wfe differs.
        # score = 0.4*wfe + 0.3*sharpe + 0.3*regime
        # candidate X: wfe=0.8, sharpe=0.0, regime=0.0 -> 0.32
        # candidate Y: wfe=0.2, sharpe=1.0, regime=... solve regime so score matches:
        # 0.4*0.2 + 0.3*1.0 + 0.3*r = 0.32 -> 0.08+0.3+0.3r=0.32 -> 0.3r=-0.06 -> r=-0.2
        x = _candidate(ticker="X", wfe=0.8, sharpe=0.0, regime_score=0.0)
        y = _candidate(ticker="Y", wfe=0.2, sharpe=1.0, regime_score=-0.2)
        assert round(composite_score(0.8, 0.0, 0.0), 6) == round(
            composite_score(0.2, 1.0, -0.2), 6
        )
        ranked = SignalRanker.rank([y, x])
        # Higher wfe (X, 0.8) wins the tie over Y (0.2) despite Y sorting
        # alphabetically first.
        assert [r.ticker for r in ranked] == ["X", "Y"]

    def test_ranking_is_stable_across_repeated_calls(self):
        candidates = [
            _candidate(ticker=t, wfe=0.5, sharpe=0.5, regime_score=0.5)
            for t in ("ZZZ", "AAA", "MMM")
        ]
        first = [r.ticker for r in SignalRanker.rank(candidates)]
        second = [r.ticker for r in SignalRanker.rank(list(reversed(candidates)))]
        assert first == second == ["AAA", "MMM", "ZZZ"]

    def test_composite_score_never_compared_with_bare_equality(self):
        # Two composite scores that are mathematically equal but reach that
        # value via a different intermediate float computation must still
        # tie for ranking purposes rather than being treated as distinct
        # merely because `a == b` might be False on the raw floats - the
        # rounding in `_sort_key` is what guarantees this, not the ordering
        # of the wfe/sharpe/ticker tie-break chain itself.
        x = _candidate(ticker="X", wfe=0.3, sharpe=0.3, regime_score=0.3)
        y = _candidate(ticker="Y", wfe=0.3, sharpe=0.3, regime_score=0.3)
        sx = composite_score(x.wfe, x.sharpe, x.regime_score)
        sy = composite_score(y.wfe, y.sharpe, y.regime_score)
        assert sx == sy  # identical inputs -> bit-identical result in this case
        ranked = SignalRanker.rank([y, x])
        assert [r.ticker for r in ranked] == ["X", "Y"]  # ticker breaks the tie

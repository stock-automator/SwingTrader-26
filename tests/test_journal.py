"""
Tests for Trade Journal
"""

import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.journal.executor import TradeJournal


@pytest.fixture
def temp_dir():
    """Create temp directory for journal files"""
    
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir)


@pytest.fixture
def journal(temp_dir):
    """Create journal instance with temp directory"""
    
    journal_file = str(Path(temp_dir) / "test_journal.csv")
    return TradeJournal(journal_file=journal_file)


class TestTradeJournalBasics:
    """Test basic journal functionality"""
    
    def test_initialization(self, journal):
        """Test journal initialization"""
        
        assert len(journal.df) == 0
        assert 'ticker' in journal.df.columns
        assert 'entry_date' in journal.df.columns
        assert 'pnl' in journal.df.columns
    
    def test_log_signal(self, journal):
        """Test logging a signal"""
        
        trade_id = journal.log_signal(
            ticker='AAPL',
            entry_date=datetime(2024, 1, 1),
            entry_price=150.0,
            thesis='Breakout above 20-day high',
            signal_strength=75.0,
            stop_loss=148.0,
            target_1=155.0,
            target_2=160.0
        )
        
        assert trade_id == 1
        assert len(journal.df) == 1
        assert journal.df.iloc[0]['entry_status'] == 'PENDING'
        assert journal.df.iloc[0]['ticker'] == 'AAPL'
    
    def test_multiple_signals(self, journal):
        """Test logging multiple signals"""
        
        for i in range(5):
            journal.log_signal(
                ticker=f'STOCK{i}',
                entry_date=datetime(2024, 1, 1),
                entry_price=100.0 + i,
                thesis='Test signal',
                signal_strength=50.0,
                stop_loss=98.0,
                target_1=105.0,
                target_2=110.0
            )
        
        assert len(journal.df) == 5


class TestTradeExecution:
    """Test trade entry and exit logging"""
    
    def test_log_entry_exit_sequence(self, journal):
        """Test complete entry->exit sequence"""
        
        # Log signal
        trade_id = journal.log_signal(
            ticker='MSFT',
            entry_date=datetime(2024, 1, 1),
            entry_price=300.0,
            thesis='Test',
            signal_strength=70.0,
            stop_loss=295.0,
            target_1=310.0,
            target_2=320.0
        )
        
        # Log entry
        journal.log_entry(
            trade_id=trade_id,
            actual_entry_price=300.5,
            actual_entry_date=datetime(2024, 1, 2)
        )
        
        assert journal.df.loc[journal.df['id'] == trade_id, 'entry_status'].iloc[0] == 'TAKEN'
        assert journal.df.loc[journal.df['id'] == trade_id, 'actual_entry_price'].iloc[0] == 300.5
        
        # Log exit (profit)
        journal.log_exit(
            trade_id=trade_id,
            exit_date=datetime(2024, 1, 5),
            exit_price=312.0,
            exit_reason='TP1'
        )
        
        trade = journal.df.loc[journal.df['id'] == trade_id].iloc[0]
        assert trade['pnl'] == pytest.approx(12.0 - 0.5)  # 312 - 300.5
        assert trade['pnl_pct'] == pytest.approx((312.0 - 300.5) / 300.5)
    
    def test_log_skip(self, journal):
        """Test logging skipped signals"""
        
        trade_id = journal.log_signal(
            ticker='GOOG',
            entry_date=datetime(2024, 1, 1),
            entry_price=140.0,
            thesis='Test',
            signal_strength=40.0,
            stop_loss=138.0,
            target_1=145.0,
            target_2=150.0
        )
        
        journal.log_skip(trade_id=trade_id, skip_reason='Missed entry, market moved too fast')
        
        trade = journal.df.loc[journal.df['id'] == trade_id].iloc[0]
        assert trade['entry_status'] == 'SKIPPED'
        assert 'too fast' in trade['skip_reason']
    
    def test_r_multiple_calculation(self, journal):
        """Test R-multiple (risk-multiple) calculation"""
        
        trade_id = journal.log_signal(
            ticker='TSLA',
            entry_date=datetime(2024, 1, 1),
            entry_price=100.0,
            thesis='Test',
            signal_strength=60.0,
            stop_loss=95.0,  # 5 point risk
            target_1=110.0,
            target_2=120.0
        )
        
        journal.log_entry(
            trade_id=trade_id,
            actual_entry_price=100.0,
            actual_entry_date=datetime(2024, 1, 2)
        )
        
        # Exit at +10 (2R)
        journal.log_exit(
            trade_id=trade_id,
            exit_date=datetime(2024, 1, 5),
            exit_price=110.0,
            exit_reason='TP1'
        )
        
        trade = journal.df.loc[journal.df['id'] == trade_id].iloc[0]
        expected_r = 10.0 / 5.0  # 2.0
        assert trade['r_multiple'] == pytest.approx(expected_r)


class TestAnalytics:
    """Test trade analysis functions"""
    
    def test_analyze_all_trades(self, journal):
        """Test comprehensive trade analysis"""
        
        # Log and execute some trades
        for i in range(10):
            trade_id = journal.log_signal(
                ticker=f'STOCK{i}',
                entry_date=datetime(2024, 1, 1),
                entry_price=100.0,
                thesis='Test',
                signal_strength=50.0,
                stop_loss=98.0,
                target_1=105.0,
                target_2=110.0
            )
            
            journal.log_entry(
                trade_id=trade_id,
                actual_entry_price=100.0,
                actual_entry_date=datetime(2024, 1, 2)
            )
            
            # Half are winners, half are losers
            exit_price = 104.0 if i % 2 == 0 else 97.0
            
            journal.log_exit(
                trade_id=trade_id,
                exit_date=datetime(2024, 1, 5),
                exit_price=exit_price,
                exit_reason='TP1' if i % 2 == 0 else 'SL'
            )
        
        analysis = journal.analyze_all_trades()
        
        assert analysis['total_trades'] == 10
        assert analysis['completed_trades'] == 10
        assert analysis['win_rate'] == pytest.approx(0.5)
        assert analysis['avg_winner'] > 0
        assert analysis['avg_loser'] < 0
    
    def test_analyze_by_signal_strength(self, journal):
        """Test cohort analysis by signal strength"""
        
        # Log trades with varying signal strengths
        for strength in [30, 50, 70, 90]:
            trade_id = journal.log_signal(
                ticker='TEST',
                entry_date=datetime(2024, 1, 1),
                entry_price=100.0,
                thesis='Test',
                signal_strength=strength,
                stop_loss=98.0,
                target_1=105.0,
                target_2=110.0
            )
            
            journal.log_entry(
                trade_id=trade_id,
                actual_entry_price=100.0,
                actual_entry_date=datetime(2024, 1, 2)
            )
            
            # Higher strength = more likely to win (artificial)
            exit_price = 105.0 if strength > 60 else 97.0
            
            journal.log_exit(
                trade_id=trade_id,
                exit_date=datetime(2024, 1, 5),
                exit_price=exit_price,
                exit_reason='TP1' if strength > 60 else 'SL'
            )
        
        cohorts = journal.analyze_by_signal_strength()
        
        # Should have multiple cohorts
        assert len(cohorts) > 0
        
        # Higher strength cohorts should have better win rates
        # (this is artificial data, so just check structure)
        for cohort in cohorts:
            assert 'strength_range' in cohort
            assert 'win_rate' in cohort
            assert 'num_trades' in cohort
    
    def test_analyze_by_exit_reason(self, journal):
        """Test analysis by exit reason"""
        
        # Log exits with different reasons
        for reason in ['SL', 'TP1', 'TP2', 'TIMEOUT']:
            trade_id = journal.log_signal(
                ticker='TEST',
                entry_date=datetime(2024, 1, 1),
                entry_price=100.0,
                thesis='Test',
                signal_strength=50.0,
                stop_loss=98.0,
                target_1=105.0,
                target_2=110.0
            )
            
            journal.log_entry(
                trade_id=trade_id,
                actual_entry_price=100.0,
                actual_entry_date=datetime(2024, 1, 2)
            )
            
            journal.log_exit(
                trade_id=trade_id,
                exit_date=datetime(2024, 1, 5),
                exit_price=103.0,
                exit_reason=reason
            )
        
        by_reason = journal.analyze_by_exit_reason()
        
        assert 'SL' in by_reason
        assert 'TP1' in by_reason
        assert len(by_reason) == 4


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

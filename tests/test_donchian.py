"""
Tests for Donchian Breakout Strategy
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategies.donchian_breakout import DonchianBreakout


@pytest.fixture
def sample_data():
    """Create sample OHLCV data for testing"""
    
    dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
    
    # Create synthetic uptrend with breakouts
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(100) * 0.5)
    high = close + np.abs(np.random.randn(100) * 0.3)
    low = close - np.abs(np.random.randn(100) * 0.3)
    volume = np.random.randint(1000000, 5000000, 100)
    
    df = pd.DataFrame({
        'Date': dates,
        'Open': close - np.random.randn(100) * 0.2,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': volume,
    })
    
    df.set_index('Date', inplace=True)
    df['ticker'] = 'TEST'
    
    return df


class TestDonchianBreakout:
    """Test suite for DonchianBreakout strategy"""
    
    def test_initialization(self):
        """Test strategy initialization"""
        
        strategy = DonchianBreakout()
        
        assert strategy.name == "Donchian 20-day Breakout"
        assert strategy.breakout_period == 20
        assert strategy.ema_period == 50
        assert strategy.momentum_period == 63
    
    def test_custom_parameters(self):
        """Test strategy with custom parameters"""
        
        strategy = DonchianBreakout(
            breakout_period=10,
            ema_period=30,
            momentum_period=30
        )
        
        assert strategy.breakout_period == 10
        assert strategy.ema_period == 30
        assert strategy.momentum_period == 30
    
    def test_calculate_indicators(self, sample_data):
        """Test indicator calculation"""
        
        strategy = DonchianBreakout()
        df = strategy.calculate_indicators(sample_data.copy())
        
        # Check that required columns exist
        assert 'donchian_high' in df.columns
        assert 'donchian_low' in df.columns
        assert 'ema_50' in df.columns
        assert 'atr' in df.columns
        assert 'volume_spike' in df.columns
        
        # Check for NaN values (should have some due to lookback)
        assert df['donchian_high'].notna().sum() > 0
        assert df['atr'].notna().sum() > 0
    
    def test_generate_signals(self, sample_data):
        """Test signal generation"""
        
        strategy = DonchianBreakout()
        df = strategy.calculate_indicators(sample_data.copy())
        signals = strategy.generate_signals(df)
        
        # Signals is a list
        assert isinstance(signals, list)
        
        # If we have signals, they should be Signal objects
        if len(signals) > 0:
            signal = signals[0]
            assert hasattr(signal, 'ticker')
            assert hasattr(signal, 'price')
            assert hasattr(signal, 'signal_type')
            assert signal.signal_type == 'BUY'
            assert 0 <= signal.strength <= 100
    
    def test_signal_properties(self, sample_data):
        """Test signal properties are correct"""
        
        strategy = DonchianBreakout()
        df = strategy.calculate_indicators(sample_data.copy())
        signals = strategy.generate_signals(df)
        
        if len(signals) > 0:
            signal = signals[0]
            
            # Stop loss must be below entry
            assert signal.stop_loss < signal.price
            
            # Targets must be above entry
            assert signal.target_1 > signal.price
            assert signal.target_2 > signal.target_1
            
            # Thesis should be non-empty
            assert len(signal.thesis) > 0
            
            # Metadata should have required fields
            assert 'atr' in signal.metadata
            assert 'ema_50' in signal.metadata
    
    def test_no_signals_in_downtrend(self):
        """Test that strategy generates fewer signals in downtrend"""
        
        # Create downtrend data
        dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
        close = 100 - np.arange(100) * 0.5  # Downtrend
        
        df = pd.DataFrame({
            'High': close + np.abs(np.random.randn(100) * 0.3),
            'Low': close - np.abs(np.random.randn(100) * 0.3),
            'Close': close,
            'Volume': np.random.randint(1000000, 5000000, 100),
        }, index=dates)
        
        df['ticker'] = 'TEST'
        
        strategy = DonchianBreakout()
        df_calc = strategy.calculate_indicators(df.copy())
        signals = strategy.generate_signals(df_calc)
        
        # In downtrend, should have very few (ideally 0) buy signals
        assert len(signals) < 5  # Being generous due to random data


class TestPositionSizing:
    """Test position sizing logic"""
    
    def test_position_size_calculation(self, sample_data):
        """Test ATR-based position sizing"""
        
        strategy = DonchianBreakout()
        
        entry_price = 100
        stop_loss = 98
        account_equity = 5000
        
        size = strategy.position_size(
            account_equity=account_equity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_pct=0.01
        )
        
        # Size should be positive
        assert size > 0
        
        # Risk = size * (entry - stop)
        actual_risk = size * (entry_price - stop_loss)
        expected_risk = account_equity * 0.01
        
        # Should match expected risk (within rounding)
        assert abs(actual_risk - expected_risk) < 1


class TestSignalValidation:
    """Test signal validation"""
    
    def test_validate_signal_strength(self, sample_data):
        """Test signal strength validation"""
        
        from src.strategies.base_strategy import Signal
        
        strategy = DonchianBreakout()
        
        # Valid signal
        valid = Signal(
            ticker='TEST',
            date=datetime.now(),
            price=100,
            signal_type='BUY',
            strength=75,
            thesis='Test',
            stop_loss=98,
            target_1=105,
            target_2=110
        )
        
        is_valid, reason = strategy.validate_signal(valid)
        assert is_valid
        
        # Weak signal
        weak = Signal(
            ticker='TEST',
            date=datetime.now(),
            price=100,
            signal_type='BUY',
            strength=20,
            thesis='Test',
            stop_loss=98,
            target_1=105,
            target_2=110
        )
        
        is_valid, reason = strategy.validate_signal(weak)
        assert not is_valid


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

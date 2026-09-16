"""
Donchian 20-day Momentum Breakout Strategy
- 20-day breakout
- 50 EMA trend filter
- 3-month momentum
- Volume confirmation
- ATR-based stop
"""

import pandas as pd
import numpy as np
from typing import List
from .base_strategy import BaseStrategy, Signal
from datetime import datetime


class DonchianBreakout(BaseStrategy):
    """
    Breakout + trend + momentum + volume confirmation
    """
    
    def __init__(self, 
                 breakout_period: int = 20,
                 ema_period: int = 50,
                 momentum_period: int = 63,  # 3 months
                 volume_period: int = 20,
                 atr_period: int = 14,
                 atr_multiplier: float = 2.5):
        
        super().__init__(name="Donchian 20-day Breakout")
        
        self.breakout_period = breakout_period
        self.ema_period = ema_period
        self.momentum_period = momentum_period
        self.volume_period = volume_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all indicators needed for strategy"""
        
        df = df.copy()
        
        # ========== DONCHIAN BREAKOUT ==========
        df['donchian_high'] = df['High'].rolling(window=self.breakout_period).max()
        df['donchian_low'] = df['Low'].rolling(window=self.breakout_period).min()
        
        # ========== EMA TREND FILTER ==========
        df['ema_50'] = df['Close'].ewm(span=self.ema_period, adjust=False).mean()
        
        # ========== MOMENTUM ==========
        df['returns_3m'] = df['Close'].pct_change(periods=self.momentum_period)
        df['momentum_positive'] = df['returns_3m'] > 0
        
        # ========== VOLUME CONFIRMATION ==========
        df['volume_ma'] = df['Volume'].rolling(window=self.volume_period).mean()
        df['volume_spike'] = df['Volume'] > df['volume_ma']
        
        # ========== ATR FOR POSITION SIZING & STOPS ==========
        df['tr'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Close'].shift()),
                abs(df['Low'] - df['Close'].shift())
            )
        )
        df['atr'] = df['tr'].rolling(window=self.atr_period).mean()
        
        # ========== BREAKOUT DETECTION ==========
        # Signal when price breaks above Donchian high
        df['breakout_signal'] = (
            (df['Close'] > df['donchian_high'].shift(1)) &
            (df['Close'] > df['ema_50'])  # Above trend
        )
        
        return df
    
    def generate_signals(self, df: pd.DataFrame) -> List[Signal]:
        """Generate BUY signals when breakout conditions are met"""
        
        signals = []
        
        for idx in range(len(df)):
            if idx < max(self.breakout_period, self.momentum_period):
                continue
            
            row = df.iloc[idx]
            prev_row = df.iloc[idx - 1]
            
            # ========== ENTRY CONDITIONS ==========
            
            # 1. Breakout above Donchian high
            donchian_break = row['Close'] > row['donchian_high'] and \
                            prev_row['Close'] <= prev_row['donchian_high']
            
            if not donchian_break:
                continue
            
            # 2. Price above 50 EMA (trend filter)
            above_ema = row['Close'] > row['ema_50']
            
            if not above_ema:
                continue
            
            # 3. Positive 3-month momentum
            positive_momentum = row['momentum_positive']
            
            if not positive_momentum:
                continue
            
            # 4. Volume confirmation
            volume_confirmed = row['Volume'] > row['volume_ma']
            
            if not volume_confirmed:
                continue
            
            # ========== ALL CONDITIONS MET - GENERATE SIGNAL ==========
            
            # Calculate stops and targets
            entry_price = row['Close']
            atr_value = row['atr'] if row['atr'] > 0 else entry_price * 0.02
            
            stop_loss = entry_price - (self.atr_multiplier * atr_value)
            target_1 = entry_price + (self.atr_multiplier * atr_value)
            target_2 = entry_price + (self.atr_multiplier * atr_value * 1.5)
            
            # Calculate signal strength (0-100)
            strength = 50  # Base score
            
            # Add points for alignment
            strength += 10 if volume_confirmed else 0  # Volume spike
            strength += 10 if row['Close'] > row['ema_50'] * 1.02 else 0  # Distance from EMA
            strength += 10 if row['momentum_positive'] else 0  # Momentum
            
            # Cap at 100
            strength = min(strength, 100)
            
            signal = Signal(
                ticker=row.get('ticker', 'UNKNOWN'),
                date=row.name if hasattr(row.name, '__str__') else datetime.now(),
                price=entry_price,
                signal_type='BUY',
                strength=strength,
                thesis=f"20-day breakout above {row['donchian_high']:.2f} | "
                       f"EMA trend confirmed | 3-month momentum positive | Volume spike",
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                metadata={
                    'atr': atr_value,
                    'ema_50': row['ema_50'],
                    'donchian_high': row['donchian_high'],
                    'volume_ma': row['volume_ma'],
                    'returns_3m': row['returns_3m'],
                }
            )
            
            signals.append(signal)
        
        self.signals = signals
        return signals
    
    def __repr__(self):
        return (f"DonchianBreakout(breakout={self.breakout_period}, "
                f"ema={self.ema_period}, momentum={self.momentum_period})")

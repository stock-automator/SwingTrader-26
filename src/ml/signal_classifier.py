"""
Signal Quality Classifier
Machine learning model to predict win probability of each signal
Trains on closed trades, ranks new signals by predicted quality
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
import json
import pickle

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.metrics import classification_report, confusion_matrix


class SignalQualityClassifier:
    """
    Train ML model on backtest/real trades.
    Predict win probability for new signals.
    """
    
    def __init__(self, model_type: str = 'random_forest'):
        self.model_type = model_type
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = None
        self.is_trained = False
    
    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Extract predictive features from trade/signal data.
        
        Assumes df has columns: rsi, atr, momentum_3m, volume_spike,
                                signal_strength, price, ma_50, etc.
        
        Returns:
            DataFrame with engineered features
        """
        
        features = pd.DataFrame()
        
        # ========== SIGNAL PROPERTIES ==========
        if 'signal_strength' in df.columns:
            features['signal_strength_norm'] = df['signal_strength'] / 100.0
        
        if 'rsi' in df.columns:
            # How extreme is RSI? (distance from extremes)
            features['rsi_extremeness'] = np.where(
                df['rsi'] < 50,
                (50 - df['rsi']) / 50,  # Oversold
                (df['rsi'] - 50) / 50   # Overbought
            )
        
        # ========== VOLATILITY REGIME ==========
        if 'atr' in df.columns and 'atr_ma20' in df.columns:
            features['atr_expansion'] = df['atr'] / df['atr_ma20']
        elif 'atr' in df.columns:
            features['atr'] = df['atr']
        
        # ========== MOMENTUM ==========
        if 'returns_3m' in df.columns:
            features['momentum_3m_sign'] = np.sign(df['returns_3m'])
            features['momentum_3m_strength'] = np.abs(df['returns_3m'])
        
        # ========== VOLUME ==========
        if 'volume_spike' in df.columns:
            features['has_volume_confirmation'] = df['volume_spike'].astype(int)
        
        if 'volume' in df.columns and 'volume_ma20' in df.columns:
            features['volume_ratio'] = df['volume'] / df['volume_ma20']
        
        # ========== PRICE STRUCTURE ==========
        if 'price' in df.columns and 'ma_50' in df.columns:
            features['price_above_ema'] = ((df['price'] - df['ma_50']) / df['ma_50']).fillna(0)
        
        if 'price' in df.columns and 'ma_200' in df.columns:
            features['price_above_sma200'] = ((df['price'] - df['ma_200']) / df['ma_200']).fillna(0)
        
        # ========== SIGNAL FRESHNESS ==========
        if 'bars_since_signal' in df.columns:
            features['signal_age'] = df['bars_since_signal']
            features['signal_fresh'] = (df['bars_since_signal'] <= 5).astype(int)
        
        # ========== SECTOR STRENGTH ==========
        if 'sector_rs' in df.columns:
            features['sector_rs'] = df['sector_rs']
        
        # ========== FILL NAN ==========
        features = features.fillna(0)
        
        self.feature_names = list(features.columns)
        
        return features
    
    def fit(self, trades_df: pd.DataFrame, val_test_split: float = 0.2) -> Dict:
        """
        Train classifier on historical trades.
        
        Args:
            trades_df: DataFrame with: entry features + 'pnl' or 'won' column
            val_test_split: Fraction to use for validation/test
        
        Returns:
            Training metrics dict
        """
        
        # Engineer features
        X = self.engineer_features(trades_df.copy())
        
        # Create target: 1 if trade won, 0 if lost
        if 'pnl' in trades_df.columns:
            y = (trades_df['pnl'] > 0).astype(int)
        elif 'won' in trades_df.columns:
            y = trades_df['won'].astype(int)
        else:
            raise ValueError("trades_df must have 'pnl' or 'won' column")
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=val_test_split, random_state=42
        )
        
        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        # Train model
        if self.model_type == 'random_forest':
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                min_samples_split=5,
                random_state=42,
                n_jobs=-1
            )
        else:
            self.model = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=5,
                random_state=42
            )
        
        self.model.fit(X_train_scaled, y_train)
        self.is_trained = True
        
        # Evaluate
        test_score = self.model.score(X_test_scaled, y_test)
        y_pred = self.model.predict(X_test_scaled)
        
        metrics = {
            'test_accuracy': test_score,
            'n_features': len(self.feature_names),
            'n_train_samples': len(X_train),
            'n_test_samples': len(X_test),
            'class_distribution': {
                'wins': int((y == 1).sum()),
                'losses': int((y == 0).sum()),
            }
        }
        
        # Feature importance
        importances = {}
        for name, imp in zip(self.feature_names, self.model.feature_importances_):
            importances[name] = float(imp)
        
        metrics['feature_importance'] = sorted(
            importances.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        return metrics
    
    def predict_signal_quality(self, signal_df: pd.DataFrame) -> List[float]:
        """
        Predict win probability for new signals.
        
        Args:
            signal_df: DataFrame with signal features (one row or multiple)
        
        Returns:
            List of win probabilities (0-1)
        """
        
        if not self.is_trained:
            raise RuntimeError("Model not trained. Call fit() first.")
        
        X = self.engineer_features(signal_df.copy())
        X_scaled = self.scaler.transform(X)
        
        # Get probability of class 1 (win)
        probabilities = self.model.predict_proba(X_scaled)[:, 1]
        
        return list(probabilities)
    
    def rank_signals(self, signals_df: pd.DataFrame) -> pd.DataFrame:
        """
        Rank multiple signals by predicted win probability.
        
        Args:
            signals_df: DataFrame with multiple signals
        
        Returns:
            Same DataFrame with added 'predicted_win_prob' column, sorted descending
        """
        
        signals = signals_df.copy()
        signals['predicted_win_prob'] = self.predict_signal_quality(signals)
        
        return signals.sort_values('predicted_win_prob', ascending=False)
    
    def filter_signals(self, signals_df: pd.DataFrame,
                      min_probability: float = 0.55) -> pd.DataFrame:
        """
        Filter signals to only those above confidence threshold.
        
        Args:
            signals_df: DataFrame with signals
            min_probability: Minimum predicted win probability (0-1)
        
        Returns:
            Filtered DataFrame
        """
        
        signals = self.rank_signals(signals_df)
        return signals[signals['predicted_win_prob'] >= min_probability]
    
    def save(self, path: str):
        """Save trained model to disk"""
        
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'scaler': self.scaler,
                'feature_names': self.feature_names,
                'is_trained': self.is_trained,
                'model_type': self.model_type,
            }, f)
    
    def load(self, path: str):
        """Load trained model from disk"""
        
        with open(path, 'rb') as f:
            data = pickle.load(f)
        
        self.model = data['model']
        self.scaler = data['scaler']
        self.feature_names = data['feature_names']
        self.is_trained = data['is_trained']
        self.model_type = data['model_type']
    
    def export_feature_importance(self, output_file: str = "results/feature_importance.json"):
        """Export feature importance for analysis"""
        
        if not self.is_trained or self.model is None:
            raise RuntimeError("Model not trained")
        
        importances = {}
        for name, imp in zip(self.feature_names, self.model.feature_importances_):
            importances[name] = float(imp)
        
        with open(output_file, 'w') as f:
            json.dump(importances, f, indent=2)

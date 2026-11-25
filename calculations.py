"""
Trading Journal Dashboard - Core Calculations Engine
Complete performance metrics and analytics calculations
Ported from Dash to Flask implementation
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, List, Tuple, Union
import math
from dataclasses import dataclass
from collections import defaultdict


def sort_by_close(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Trades in the order they closed (close date, then last exit time, then
    id), so cumulative P&L follows when profits and losses were realized."""
    keys = [k for k in ('closed_date', 'exit_time', 'trade_id') if k in trades_df.columns]
    if not keys:
        keys = [k for k in ('date', 'time') if k in trades_df.columns]
    return trades_df.sort_values(keys, na_position='first').reset_index(drop=True)


@dataclass
class PerformanceMetrics:
    """Container for comprehensive performance metrics"""

    # Basic metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0

    # Performance ratios
    win_rate: float = 0.0
    loss_rate: float = 0.0
    profit_factor: float = 0.0

    # P&L metrics
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0

    # Trade size metrics
    average_win: float = 0.0
    average_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    average_trade: float = 0.0

    # Risk metrics
    average_r_multiple: float = 0.0
    median_r_multiple: float = 0.0
    expectancy: float = 0.0
    kelly_criterion: float = 0.0

    # Streak metrics
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    current_streak: int = 0
    current_streak_type: str = "None"

    # Drawdown metrics
    max_drawdown_amount: float = 0.0
    max_drawdown_percent: float = 0.0
    current_drawdown_amount: float = 0.0
    current_drawdown_percent: float = 0.0

    # Time-based metrics
    avg_intraday_duration_hours: float = 0.0
    avg_swing_duration_days: float = 0.0
    avg_trades_per_day: float = 0.0
    avg_trades_per_week: float = 0.0
    avg_trades_per_month: float = 0.0

    # Advanced metrics
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    recovery_factor: float = 0.0

    # Distribution metrics
    win_loss_ratio: float = 0.0
    standard_deviation: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0


class PerformanceCalculator:
    """Comprehensive trading performance calculator"""

    def __init__(self, trades_df: pd.DataFrame, starting_balance: float = 10000.0):
        """
        Initialize calculator with trades DataFrame

        Args:
            trades_df: DataFrame with trade data
            starting_balance: Total capital deployed (for ROI calculations)
                             Note: Despite the parameter name, this represents total capital deployed,
                             not the locked starting balance. Used as denominator for ROI calculations.
        """
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()
        self.starting_balance = starting_balance

        if not self.trades_df.empty:
            # Ensure proper data types; trades in the order they CLOSED (when
            # their P&L was realized) for streaks and drawdown
            self.trades_df['date'] = pd.to_datetime(self.trades_df['date'])
            self.trades_df = sort_by_close(self.trades_df)

            # Convert to numeric first, then fill missing values (avoids downcasting warning)
            self.trades_df['net_pnl'] = pd.to_numeric(self.trades_df['net_pnl'], errors='coerce').fillna(0)
            # No stop-loss means no R-multiple: leave it empty (not 0R)
            self.trades_df['r_multiple'] = pd.to_numeric(self.trades_df['r_multiple'], errors='coerce')

            # Handle duration columns (both hours and days)
            if 'duration_hours' in self.trades_df.columns:
                self.trades_df['duration_hours'] = pd.to_numeric(self.trades_df['duration_hours'], errors='coerce').fillna(0)
            if 'duration_days' in self.trades_df.columns:
                self.trades_df['duration_days'] = pd.to_numeric(self.trades_df['duration_days'], errors='coerce').fillna(0)

    def calculate_all_metrics(self) -> PerformanceMetrics:
        """Calculate comprehensive performance metrics"""

        if self.trades_df.empty:
            return PerformanceMetrics()

        metrics = PerformanceMetrics()

        # Basic counts
        metrics.total_trades = len(self.trades_df)
        metrics.winning_trades = (self.trades_df['net_pnl'] > 0).sum()
        metrics.losing_trades = (self.trades_df['net_pnl'] < 0).sum()
        metrics.breakeven_trades = (self.trades_df['net_pnl'] == 0).sum()

        # Performance ratios
        if metrics.total_trades > 0:
            metrics.win_rate = (metrics.winning_trades / metrics.total_trades) * 100
            metrics.loss_rate = (metrics.losing_trades / metrics.total_trades) * 100

        # P&L metrics
        metrics.total_pnl = self.trades_df['net_pnl'].sum()
        metrics.gross_profit = self.trades_df[self.trades_df['net_pnl'] > 0]['net_pnl'].sum()
        metrics.gross_loss = abs(self.trades_df[self.trades_df['net_pnl'] < 0]['net_pnl'].sum())
        metrics.net_profit = metrics.total_pnl

        # Profit factor
        if metrics.gross_loss > 0:
            metrics.profit_factor = metrics.gross_profit / metrics.gross_loss
        elif metrics.gross_profit > 0:
            metrics.profit_factor = float('inf')

        # Trade size metrics
        winning_trades = self.trades_df[self.trades_df['net_pnl'] > 0]['net_pnl']
        losing_trades = self.trades_df[self.trades_df['net_pnl'] < 0]['net_pnl']

        if not winning_trades.empty:
            metrics.average_win = winning_trades.mean()
            metrics.largest_win = winning_trades.max()

        if not losing_trades.empty:
            metrics.average_loss = losing_trades.mean()
            metrics.largest_loss = losing_trades.min()

        metrics.average_trade = self.trades_df['net_pnl'].mean()

        # Risk metrics
        r_multiples = self.trades_df['r_multiple'].dropna()
        if not r_multiples.empty:
            metrics.average_r_multiple = r_multiples.mean()
            metrics.median_r_multiple = r_multiples.median()

        # Expectancy calculation
        if metrics.total_trades > 0:
            win_rate_decimal = metrics.win_rate / 100
            loss_rate_decimal = metrics.loss_rate / 100

            if metrics.average_loss != 0:
                metrics.expectancy = (win_rate_decimal * metrics.average_win) + (loss_rate_decimal * metrics.average_loss)
            else:
                metrics.expectancy = win_rate_decimal * metrics.average_win

        # Kelly Criterion
        if metrics.average_loss != 0 and metrics.win_rate > 0:
            win_loss_ratio = abs(metrics.average_win / metrics.average_loss)
            win_rate_decimal = metrics.win_rate / 100
            metrics.kelly_criterion = win_rate_decimal - ((1 - win_rate_decimal) / win_loss_ratio)

        # Streak calculations
        streaks = self._calculate_streaks()
        metrics.max_consecutive_wins = streaks['max_win_streak']
        metrics.max_consecutive_losses = streaks['max_loss_streak']
        metrics.current_streak = streaks['current_streak']
        metrics.current_streak_type = streaks['current_streak_type']

        # Drawdown calculations
        drawdown_data = self._calculate_drawdowns()
        metrics.max_drawdown_amount = drawdown_data['max_dd_amount']
        metrics.max_drawdown_percent = drawdown_data['max_dd_percent']
        metrics.current_drawdown_amount = drawdown_data['current_dd_amount']
        metrics.current_drawdown_percent = drawdown_data['current_dd_percent']

        # Time-based metrics
        time_metrics = self._calculate_time_metrics()
        metrics.avg_intraday_duration_hours = time_metrics['avg_intraday_duration_hours']
        metrics.avg_swing_duration_days = time_metrics['avg_swing_duration_days']
        metrics.avg_trades_per_day = time_metrics['trades_per_day']
        metrics.avg_trades_per_week = time_metrics['trades_per_week']
        metrics.avg_trades_per_month = time_metrics['trades_per_month']

        # Advanced metrics
        advanced = self._calculate_advanced_metrics()
        metrics.sharpe_ratio = advanced['sharpe_ratio']
        metrics.sortino_ratio = advanced['sortino_ratio']
        metrics.calmar_ratio = advanced['calmar_ratio']
        metrics.recovery_factor = advanced['recovery_factor']

        # Distribution metrics
        if metrics.average_loss != 0:
            metrics.win_loss_ratio = abs(metrics.average_win / metrics.average_loss)

        pnl_series = self.trades_df['net_pnl']
        # std() returns NaN for single value, need at least 2 data points
        metrics.standard_deviation = pnl_series.std() if len(pnl_series) >= 2 else 0.0

        if len(pnl_series) > 2:
            metrics.skewness = pnl_series.skew()
            metrics.kurtosis = pnl_series.kurtosis()

        return metrics

    def _calculate_streaks(self) -> Dict[str, Any]:
        """Calculate winning and losing streaks"""

        if self.trades_df.empty:
            return {
                'max_win_streak': 0,
                'max_loss_streak': 0,
                'current_streak': 0,
                'current_streak_type': 'None'
            }

        # Convert P&L to win/loss indicators
        results = []
        for pnl in self.trades_df['net_pnl']:
            if pnl > 0:
                results.append('W')
            elif pnl < 0:
                results.append('L')
            else:
                results.append('B')  # Breakeven

        # Calculate streaks
        max_win_streak = 0
        max_loss_streak = 0
        current_win_streak = 0
        current_loss_streak = 0

        for result in results:
            if result == 'W':
                current_win_streak += 1
                current_loss_streak = 0
                max_win_streak = max(max_win_streak, current_win_streak)
            elif result == 'L':
                current_loss_streak += 1
                current_win_streak = 0
                max_loss_streak = max(max_loss_streak, current_loss_streak)
            else:  # Breakeven
                current_win_streak = 0
                current_loss_streak = 0

        # Current streak
        current_streak = 0
        current_streak_type = 'None'

        if results:
            last_result = results[-1]
            if last_result == 'W':
                current_streak = current_win_streak
                current_streak_type = 'Wins'
            elif last_result == 'L':
                current_streak = current_loss_streak
                current_streak_type = 'Losses'

        return {
            'max_win_streak': max_win_streak,
            'max_loss_streak': max_loss_streak,
            'current_streak': current_streak,
            'current_streak_type': current_streak_type
        }

    def _calculate_drawdowns(self) -> Dict[str, float]:
        """Calculate drawdown metrics"""

        if self.trades_df.empty:
            return {
                'max_dd_amount': 0.0,
                'max_dd_percent': 0.0,
                'current_dd_amount': 0.0,
                'current_dd_percent': 0.0
            }

        # Calculate running balance
        cumulative_pnl = self.trades_df['net_pnl'].cumsum()
        running_balance = self.starting_balance + cumulative_pnl

        # Calculate running maximum (peak)
        running_peak = running_balance.expanding().max()

        # Calculate drawdown
        drawdown_amount = running_peak - running_balance
        drawdown_percent = np.where(running_peak > 0, (drawdown_amount / running_peak) * 100, 0.0)

        # Maximum drawdown
        max_dd_amount = drawdown_amount.max()
        max_dd_percent = drawdown_percent.max()

        # Current drawdown
        current_dd_amount = drawdown_amount.iloc[-1] if len(drawdown_amount) > 0 else 0.0
        current_dd_percent = drawdown_percent[-1] if len(drawdown_percent) > 0 else 0.0

        return {
            'max_dd_amount': max_dd_amount,
            'max_dd_percent': max_dd_percent,
            'current_dd_amount': current_dd_amount,
            'current_dd_percent': current_dd_percent
        }

    def _calculate_time_metrics(self) -> Dict[str, float]:
        """Calculate time-based performance metrics"""

        if self.trades_df.empty:
            return {
                'avg_intraday_duration_hours': 0.0,
                'avg_swing_duration_days': 0.0,
                'trades_per_day': 0.0,
                'trades_per_week': 0.0,
                'trades_per_month': 0.0
            }

        # Average trade duration - separate calculations for Intraday vs Swing/Investment
        avg_intraday_hours = 0.0
        avg_swing_days = 0.0

        # Calculate Intraday average (in hours)
        if 'position_type' in self.trades_df.columns and 'duration_hours' in self.trades_df.columns:
            intraday_trades = self.trades_df[self.trades_df['position_type'] == 'Intraday']
            if not intraday_trades.empty:
                intraday_durations = intraday_trades['duration_hours'].dropna()
                avg_intraday_hours = intraday_durations.mean() if not intraday_durations.empty else 0.0

        # Calculate Swing/Investment average (in days)
        if 'position_type' in self.trades_df.columns and 'duration_days' in self.trades_df.columns:
            swing_trades = self.trades_df[self.trades_df['position_type'].isin(['Swing', 'Investment'])]
            if not swing_trades.empty:
                swing_durations = swing_trades['duration_days'].dropna()
                avg_swing_days = swing_durations.mean() if not swing_durations.empty else 0.0

        # Trading frequency calculations
        date_range = (self.trades_df['date'].max() - self.trades_df['date'].min()).days

        if date_range > 0:
            total_trades = len(self.trades_df)
            trades_per_day = total_trades / date_range
            trades_per_week = trades_per_day * 7
            trades_per_month = trades_per_day * 30
        else:
            trades_per_day = trades_per_week = trades_per_month = 0.0

        return {
            'avg_intraday_duration_hours': avg_intraday_hours,
            'avg_swing_duration_days': avg_swing_days,
            'trades_per_day': trades_per_day,
            'trades_per_week': trades_per_week,
            'trades_per_month': trades_per_month
        }

    def _calculate_advanced_metrics(self) -> Dict[str, float]:
        """Calculate advanced risk-adjusted performance metrics"""

        if self.trades_df.empty:
            return {
                'sharpe_ratio': 0.0,
                'sortino_ratio': 0.0,
                'calmar_ratio': 0.0,
                'recovery_factor': 0.0
            }

        # Use ROI per trade (percentage returns) instead of absolute PnL
        roi_per_trade = self.trades_df['net_pnl'] / self.starting_balance

        # Sharpe Ratio (per-trade, no annualization since we don't have daily data)
        # Using simple Sharpe without risk-free rate since trade frequency varies
        if roi_per_trade.std() > 0:
            sharpe_ratio = roi_per_trade.mean() / roi_per_trade.std()
        else:
            sharpe_ratio = 0.0

        # Sortino Ratio (downside deviation on ROI)
        negative_roi = roi_per_trade[roi_per_trade < 0]
        if len(negative_roi) > 0:
            downside_deviation = negative_roi.std()
            if downside_deviation > 0:
                sortino_ratio = roi_per_trade.mean() / downside_deviation
            else:
                sortino_ratio = 0.0
        else:
            sortino_ratio = float('inf') if roi_per_trade.mean() > 0 else 0.0

        # Calmar Ratio
        drawdown_data = self._calculate_drawdowns()
        max_dd_percent = drawdown_data['max_dd_percent']

        if max_dd_percent > 0:
            annual_return = (self.trades_df['net_pnl'].sum() / self.starting_balance) * 100
            calmar_ratio = annual_return / max_dd_percent
        else:
            calmar_ratio = 0.0

        # Recovery Factor
        if drawdown_data['max_dd_amount'] > 0:
            recovery_factor = self.trades_df['net_pnl'].sum() / drawdown_data['max_dd_amount']
        else:
            recovery_factor = 0.0

        return {
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'calmar_ratio': calmar_ratio,
            'recovery_factor': recovery_factor
        }


class EquityCurveCalculator:
    """Calculate and analyze equity curve data"""

    def __init__(self, trades_df: pd.DataFrame, starting_balance: float = 10000.0):
        """
        Initialize equity curve calculator

        Args:
            trades_df: DataFrame with trade data
            starting_balance: Total capital deployed (for ROI calculations)
                             Note: Despite the parameter name, this represents total capital deployed,
                             not the locked starting balance. Used as denominator for ROI calculations.
        """
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()
        self.starting_balance = starting_balance

        if not self.trades_df.empty:
            self.trades_df['date'] = pd.to_datetime(self.trades_df['date'])
            self.trades_df = sort_by_close(self.trades_df)

    def calculate_equity_curve(self) -> pd.DataFrame:
        """Calculate complete equity curve with all metrics"""

        if self.trades_df.empty:
            return pd.DataFrame({
                'date': [datetime.now()],
                'trade_number': [0],
                'pnl': [0.0],
                'cumulative_pnl': [0.0],
                'balance': [self.starting_balance],
                'running_peak': [self.starting_balance],
                'drawdown_amount': [0.0],
                'drawdown_percent': [0.0],
                'underwater_curve': [0.0],
                'returns': [0.0],
                'log_returns': [0.0]
            })

        equity_data = []
        running_balance = self.starting_balance
        peak_balance = self.starting_balance
        cumulative_pnl = 0.0

        for idx, trade in self.trades_df.iterrows():
            # Update running totals
            cumulative_pnl += trade['net_pnl']
            running_balance = self.starting_balance + cumulative_pnl

            # Update peak balance
            peak_balance = max(peak_balance, running_balance)

            # Calculate drawdown
            drawdown_amount = peak_balance - running_balance
            drawdown_percent = (drawdown_amount / peak_balance) * 100 if peak_balance > 0 else 0.0

            # Calculate returns
            prev_balance = self.starting_balance + cumulative_pnl - trade['net_pnl']
            returns = (trade['net_pnl'] / prev_balance) * 100 if prev_balance > 0 else 0.0
            log_returns = np.log(running_balance / prev_balance) if prev_balance > 0 else 0.0

            # Underwater curve (time in drawdown)
            underwater = -drawdown_percent if drawdown_percent > 0 else 0.0

            equity_data.append({
                # Dated when the P&L was realized (close), falling back to open
                'date': pd.to_datetime(trade['closed_date']) if pd.notna(trade.get('closed_date')) else trade['date'],
                'trade_number': len(equity_data) + 1,
                'symbol': trade['symbol'],
                'pnl': trade['net_pnl'],
                'cumulative_pnl': cumulative_pnl,
                'balance': running_balance,
                'running_peak': peak_balance,
                'drawdown_amount': drawdown_amount,
                'drawdown_percent': drawdown_percent,
                'underwater_curve': underwater,
                'returns': returns,
                'log_returns': log_returns
            })

        return pd.DataFrame(equity_data)


class TimeBasedAnalyzer:
    """Analyze performance by time periods"""

    def __init__(self, trades_df: pd.DataFrame):
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()

        if not self.trades_df.empty:
            self.trades_df['date'] = pd.to_datetime(self.trades_df['date'])
            self.trades_df['year'] = self.trades_df['date'].dt.year
            self.trades_df['month'] = self.trades_df['date'].dt.month
            self.trades_df['quarter'] = self.trades_df['date'].dt.quarter
            self.trades_df['day_of_week'] = self.trades_df['date'].dt.day_name()
            self.trades_df['hour'] = pd.to_datetime(self.trades_df['time'], format='%H:%M', errors='coerce').dt.hour

    def analyze_by_day_of_week(self) -> pd.DataFrame:
        """Analyze performance by day of week"""

        if self.trades_df.empty:
            return pd.DataFrame()

        day_stats = self.trades_df.groupby('day_of_week').agg({
            'net_pnl': ['count', 'sum', 'mean', 'std'],
            'r_multiple': ['mean', 'std']
        }).round(2)

        day_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'PnL_StdDev', 'Avg_R_Multiple', 'R_StdDev']

        # Add win rate
        win_rates = self.trades_df.groupby('day_of_week').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        day_stats['Win_Rate'] = win_rates

        # Add best and worst trades
        best_trades = self.trades_df.groupby('day_of_week')['net_pnl'].max()
        worst_trades = self.trades_df.groupby('day_of_week')['net_pnl'].min()
        day_stats['Best_Trade'] = best_trades
        day_stats['Worst_Trade'] = worst_trades

        return day_stats.reset_index()

    def analyze_by_month(self) -> pd.DataFrame:
        """Analyze performance by month"""

        if self.trades_df.empty:
            return pd.DataFrame()

        # Month each trade closed in (when its P&L was realized)
        self.trades_df['year_month'] = pd.to_datetime(self.trades_df['closed_date']).dt.to_period('M')

        monthly_stats = self.trades_df.groupby('year_month').agg({
            'net_pnl': ['count', 'sum', 'mean'],
            'r_multiple': 'mean'
        }).round(2)

        monthly_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL_per_Trade', 'Avg_R_Multiple']

        # Add win rate and other metrics
        win_rates = self.trades_df.groupby('year_month').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        monthly_stats['Win_Rate'] = win_rates

        # Add profit factor
        profit_factors = self.trades_df.groupby('year_month').apply(
            lambda x: (x[x['net_pnl'] > 0]['net_pnl'].sum() /
                      abs(x[x['net_pnl'] < 0]['net_pnl'].sum())) if x[x['net_pnl'] < 0]['net_pnl'].sum() != 0 else float('inf'),
            include_groups=False
        ).round(2)
        monthly_stats['Profit_Factor'] = profit_factors

        monthly_stats = monthly_stats.reset_index()
        monthly_stats['year_month'] = monthly_stats['year_month'].astype(str)

        return monthly_stats

    def analyze_by_hour(self) -> pd.DataFrame:
        """Analyze performance by hour of day"""

        if self.trades_df.empty or self.trades_df['hour'].isna().all():
            return pd.DataFrame()

        # Filter out NaT values
        hourly_data = self.trades_df.dropna(subset=['hour'])

        if hourly_data.empty:
            return pd.DataFrame()

        hourly_stats = hourly_data.groupby('hour').agg({
            'net_pnl': ['count', 'sum', 'mean'],
            'r_multiple': 'mean'
        }).round(2)

        hourly_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'Avg_R_Multiple']

        # Add win rate
        win_rates = hourly_data.groupby('hour').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        hourly_stats['Win_Rate'] = win_rates

        return hourly_stats.reset_index()

    def analyze_current_week(self) -> pd.DataFrame:
        """Analyze performance for the current calendar week (Monday-Friday)"""
        from datetime import datetime, timedelta

        if self.trades_df.empty:
            return pd.DataFrame()

        # Get current date and calculate Monday of this week
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())  # weekday() returns 0 for Monday
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

        # Trades that closed this week, by the day they closed
        closed = pd.to_datetime(self.trades_df['closed_date'])
        current_week_trades = self.trades_df[closed >= monday].copy()

        if current_week_trades.empty:
            return pd.DataFrame()

        current_week_trades['day_of_week'] = closed[closed >= monday].dt.day_name()

        # Group by day of week
        week_stats = current_week_trades.groupby('day_of_week').agg({
            'net_pnl': ['count', 'sum', 'mean'],
            'r_multiple': 'mean'
        }).round(2)

        week_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'Avg_R_Multiple']

        # Add win rate
        win_rates = current_week_trades.groupby('day_of_week').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        week_stats['Win_Rate'] = win_rates

        return week_stats.reset_index()


class StrategyAnalyzer:
    """Analyze performance by strategy"""

    def __init__(self, trades_df: pd.DataFrame):
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()

        # Filter out trades without strategy
        if not self.trades_df.empty:
            self.trades_df = self.trades_df.dropna(subset=['strategy'])

    def analyze_strategy_performance(self) -> pd.DataFrame:
        """Comprehensive strategy performance analysis"""

        if self.trades_df.empty:
            return pd.DataFrame()

        strategy_stats = self.trades_df.groupby('strategy').agg({
            'net_pnl': ['count', 'sum', 'mean', 'std'],
            'r_multiple': ['mean', 'std']
        }).round(2)

        strategy_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'PnL_StdDev',
                                 'Avg_R_Multiple', 'R_StdDev']

        # Add win rate
        win_rates = self.trades_df.groupby('strategy').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        strategy_stats['Win_Rate'] = win_rates

        # Add profit factor
        profit_factors = self.trades_df.groupby('strategy').apply(
            lambda x: (x[x['net_pnl'] > 0]['net_pnl'].sum() /
                      abs(x[x['net_pnl'] < 0]['net_pnl'].sum())) if x[x['net_pnl'] < 0]['net_pnl'].sum() != 0 else float('inf'),
            include_groups=False
        ).round(2)
        strategy_stats['Profit_Factor'] = profit_factors

        # Add best and worst trades
        best_trades = self.trades_df.groupby('strategy')['net_pnl'].max()
        worst_trades = self.trades_df.groupby('strategy')['net_pnl'].min()
        strategy_stats['Best_Trade'] = best_trades
        strategy_stats['Worst_Trade'] = worst_trades

        # Add Sharpe ratio per strategy
        sharpe_ratios = self.trades_df.groupby('strategy').apply(
            lambda x: (x['net_pnl'].mean() / x['net_pnl'].std()) * np.sqrt(252) if x['net_pnl'].std() > 0 else 0,
            include_groups=False
        ).round(2)
        strategy_stats['Sharpe_Ratio'] = sharpe_ratios

        # Add expectancy per strategy
        expectancies = self.trades_df.groupby('strategy').apply(
            lambda x: self._calculate_expectancy(x),
            include_groups=False
        ).round(2)
        strategy_stats['Expectancy'] = expectancies

        return strategy_stats.reset_index()

    def _calculate_expectancy(self, group_df: pd.DataFrame) -> float:
        """Calculate expectancy for a group of trades"""

        if group_df.empty:
            return 0.0

        wins = group_df[group_df['net_pnl'] > 0]
        losses = group_df[group_df['net_pnl'] < 0]

        total_trades = len(group_df)

        if total_trades == 0:
            return 0.0

        win_rate = len(wins) / total_trades
        loss_rate = len(losses) / total_trades

        avg_win = wins['net_pnl'].mean() if not wins.empty else 0
        avg_loss = losses['net_pnl'].mean() if not losses.empty else 0

        return (win_rate * avg_win) + (loss_rate * avg_loss)


class MarketAnalyzer:
    """Analyze performance by market type"""

    def __init__(self, trades_df: pd.DataFrame):
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()

        # Filter out trades without market data
        if not self.trades_df.empty:
            self.trades_df = self.trades_df.dropna(subset=['market'])

    def analyze_market_performance(self) -> pd.DataFrame:
        """Analyze performance by market type (Spot, Options, Futures)"""

        if self.trades_df.empty:
            return pd.DataFrame()

        market_stats = self.trades_df.groupby('market').agg({
            'net_pnl': ['count', 'sum', 'mean', 'std'],
            'r_multiple': ['mean', 'std']
        }).round(2)

        market_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'PnL_StdDev',
                               'Avg_R_Multiple', 'R_StdDev']

        # Add win rate
        win_rates = self.trades_df.groupby('market').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        market_stats['Win_Rate'] = win_rates

        # Add profit factor
        profit_factors = self.trades_df.groupby('market').apply(
            lambda x: (x[x['net_pnl'] > 0]['net_pnl'].sum() /
                      abs(x[x['net_pnl'] < 0]['net_pnl'].sum())) if x[x['net_pnl'] < 0]['net_pnl'].sum() != 0 else float('inf'),
            include_groups=False
        ).round(2)
        market_stats['Profit_Factor'] = profit_factors

        # Add volatility (coefficient of variation)
        volatilities = self.trades_df.groupby('market').apply(
            lambda x: (x['net_pnl'].std() / abs(x['net_pnl'].mean())) * 100 if x['net_pnl'].mean() != 0 else 0,
            include_groups=False
        ).round(2)
        market_stats['Volatility_CoV'] = volatilities

        return market_stats.reset_index()

    def analyze_by_market_conditions(self) -> pd.DataFrame:
        """Analyze performance by market conditions (Bullish, Bearish, Neutral, Volatile)"""

        if self.trades_df.empty:
            return pd.DataFrame()

        # Filter out trades without market_conditions data
        df_with_conditions = self.trades_df.dropna(subset=['market_conditions'])

        if df_with_conditions.empty:
            return pd.DataFrame()

        condition_stats = df_with_conditions.groupby('market_conditions').agg({
            'net_pnl': ['count', 'sum', 'mean', 'std'],
            'r_multiple': ['mean', 'std']
        }).round(2)

        condition_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'PnL_StdDev',
                                   'Avg_R_Multiple', 'R_StdDev']

        # Add win rate
        win_rates = df_with_conditions.groupby('market_conditions').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        condition_stats['Win_Rate'] = win_rates

        # Add profit factor
        profit_factors = df_with_conditions.groupby('market_conditions').apply(
            lambda x: (x[x['net_pnl'] > 0]['net_pnl'].sum() /
                      abs(x[x['net_pnl'] < 0]['net_pnl'].sum())) if x[x['net_pnl'] < 0]['net_pnl'].sum() != 0 else float('inf'),
            include_groups=False
        ).round(2)
        condition_stats['Profit_Factor'] = profit_factors

        return condition_stats.reset_index()

    def analyze_direction_performance(self) -> pd.DataFrame:
        """Analyze performance by trade direction (Long vs Short)"""

        if self.trades_df.empty:
            return pd.DataFrame()

        direction_stats = self.trades_df.groupby('direction').agg({
            'net_pnl': ['count', 'sum', 'mean', 'std'],
            'r_multiple': ['mean', 'std']
        }).round(2)

        direction_stats.columns = ['Total_Trades', 'Net_PnL', 'Avg_PnL', 'PnL_StdDev',
                                  'Avg_R_Multiple', 'R_StdDev']

        # Add win rate
        win_rates = self.trades_df.groupby('direction').apply(
            lambda x: (x['net_pnl'] > 0).mean() * 100,
            include_groups=False
        ).round(2)
        direction_stats['Win_Rate'] = win_rates

        # Add profit factor
        profit_factors = self.trades_df.groupby('direction').apply(
            lambda x: (x[x['net_pnl'] > 0]['net_pnl'].sum() /
                      abs(x[x['net_pnl'] < 0]['net_pnl'].sum())) if x[x['net_pnl'] < 0]['net_pnl'].sum() != 0 else float('inf'),
            include_groups=False
        ).round(2)
        direction_stats['Profit_Factor'] = profit_factors

        return direction_stats.reset_index()


class RiskAnalyzer:
    """Value at Risk of single-trade results"""

    def __init__(self, trades_df: pd.DataFrame):
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()

    def calculate_var(self) -> Dict[str, float]:
        """Value at Risk of one trade's net P&L: var_95 is the 5th percentile
        (1 trade in 20 does this badly or worse), var_99 the 1st; cvar_* is the
        average of the trades at or below each."""

        if self.trades_df.empty:
            return {'var_95': 0.0, 'var_99': 0.0, 'cvar_95': 0.0, 'cvar_99': 0.0}

        returns = self.trades_df['net_pnl']

        # Calculate VaR at 95% and 99% confidence levels
        var_95 = np.percentile(returns, 5)  # 5th percentile
        var_99 = np.percentile(returns, 1)  # 1st percentile

        # Calculate Conditional VaR (Expected Shortfall)
        cvar_95 = returns[returns <= var_95].mean()
        cvar_99 = returns[returns <= var_99].mean()

        return {
            'var_95': var_95,
            'var_99': var_99,
            'cvar_95': cvar_95 if not np.isnan(cvar_95) else 0.0,
            'cvar_99': cvar_99 if not np.isnan(cvar_99) else 0.0
        }


class TradeStyleAnalyzer:
    """Analyze performance by trade style (Intraday/Swing/Investment)"""

    def __init__(self, trades_df: pd.DataFrame):
        self.trades_df = trades_df.copy() if not trades_df.empty else pd.DataFrame()

        # Filter out trades without position_type
        if not self.trades_df.empty:
            self.trades_df = self.trades_df.dropna(subset=['position_type'])

    def analyze_by_trade_style(self) -> pd.DataFrame:
        """Comprehensive trade style performance analysis"""

        if self.trades_df.empty:
            return pd.DataFrame()

        df = self.trades_df
        g = df.groupby('position_type')['net_pnl']

        base = pd.DataFrame({
            'Total_Trades':   g.count(),
            'Total_PnL':      g.sum(),
            'Avg_Trade':      g.mean(),
            'Gross_Profit':   g.apply(lambda x: x[x > 0].sum()),
            'Gross_Loss':     g.apply(lambda x: abs(x[x < 0].sum())),
            'Winning_Trades': g.apply(lambda x: (x > 0).sum()),
            'Losing_Trades':  g.apply(lambda x: (x < 0).sum()),
        })

        base['Win_Rate'] = base['Winning_Trades'] / base['Total_Trades'] * 100
        base['Profit_Factor'] = base.apply(
            lambda r: (r['Gross_Profit'] / r['Gross_Loss']) if r['Gross_Loss'] > 0
                      else (float('inf') if r['Gross_Profit'] > 0 else 0),
            axis=1
        )
        base['Avg_Win'] = (base['Gross_Profit'] / base['Winning_Trades'].replace(0, float('nan'))).fillna(0)
        base['Avg_Loss'] = (base['Gross_Loss'] / base['Losing_Trades'].replace(0, float('nan'))).fillna(0)

        # Duration: Intraday uses duration_hours; Swing/Investment use duration_days
        dur_map = {}
        for style in base.index:
            col = 'duration_hours' if style == 'Intraday' else 'duration_days'
            dur_map[style] = df.loc[df['position_type'] == style, col].mean() if col in df.columns else 0
        base['Avg_Duration'] = pd.Series(dur_map)

        return (base.reset_index()
                    .rename(columns={'position_type': 'Style'})
                    .sort_values('Total_Trades', ascending=False))


"""
Trading Journal Dashboard - Database Foundation
Refactored: positions + executions model (no stored aggregates, direction-aware P&L in Python)
"""

import sqlite3
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
import os
import re
import json
import logging

logger = logging.getLogger(__name__)

VALID_MARKETS = [
    'Index Futures', 'Index Options',
    'Equity', 'Equity Futures', 'Equity Options',
    'Commodity Futures', 'Commodity Options',
]


def _parse_date_time(date_str: Optional[str], time_str: Optional[str]) -> Optional[datetime]:
    """'YYYY-MM-DD' plus an optional 'HH:MM' or 'HH:MM:SS' into a datetime."""
    if not date_str:
        return None
    try:
        day = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
    except ValueError:
        return None
    if time_str:
        for fmt in ('%H:%M', '%H:%M:%S'):
            try:
                t = datetime.strptime(str(time_str), fmt)
                return day.replace(hour=t.hour, minute=t.minute, second=t.second)
            except ValueError:
                continue
    return day


def _holding_time(entry_date, entry_time, exit_date, exit_time) -> Tuple[Optional[int], Optional[float]]:
    """(days, hours) from first entry to last exit. Hours come from the times
    when both are known; otherwise they are the days x 24."""
    start = _parse_date_time(entry_date, None)
    end = _parse_date_time(exit_date, None)
    if start is None or end is None:
        return None, None
    days = (end - start).days
    hours = days * 24
    if entry_time and exit_time:
        start_t = _parse_date_time(entry_date, entry_time)
        end_t = _parse_date_time(exit_date, exit_time)
        if start_t and end_t:
            hours = (end_t - start_t).total_seconds() / 3600
    return days, hours


def normalize_column_name(name: str) -> str:
    """CSV header -> lower-case snake_case ('Entry Price' -> 'entry_price')."""
    return re.sub(r'[^0-9a-z]+', '_', str(name).strip().lower()).strip('_')


class Execution:
    """Single buy or sell leg — the single source of truth for price/quantity data."""

    def __init__(self,
                 position_id: int,
                 date: str,
                 type: str,          # 'entry' or 'exit'
                 price: float,
                 quantity: float,
                 execution_id: Optional[int] = None,
                 time: Optional[str] = None,
                 charges: float = 0.0,
                 leverage: float = 1.0,
                 stop_loss: Optional[float] = None,
                 take_profit: Optional[float] = None,
                 risk_amount: Optional[float] = None,
                 setup_type: Optional[str] = None,
                 market_conditions: Optional[str] = None,
                 emotions: Optional[str] = None,
                 confluence_score: Optional[int] = None,
                 trade_grade: Optional[str] = None,
                 notes: Optional[str] = None,
                 mistakes: Optional[str] = None,
                 created_at: Optional[str] = None):

        self.execution_id = execution_id
        self.position_id = position_id
        self.date = date
        self.time = time
        self.type = type          # 'entry' or 'exit'
        self.price = price
        self.quantity = quantity
        self.charges = charges
        self.leverage = leverage
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.risk_amount = risk_amount
        self.setup_type = setup_type
        self.market_conditions = market_conditions
        self.emotions = emotions
        self.confluence_score = confluence_score
        self.trade_grade = trade_grade
        self.notes = notes
        self.mistakes = mistakes
        self.created_at = created_at


class CashTransaction:
    """Cash transaction model for deposits, withdrawals, dividends"""

    def __init__(self,
                 date: str,
                 type: str,
                 amount: float,
                 transaction_id: Optional[int] = None,
                 time: Optional[str] = None,
                 description: Optional[str] = None,
                 created_at: Optional[str] = None):

        self.transaction_id = transaction_id
        self.date = date
        self.time = time
        self.type = type
        self.amount = amount
        self.description = description
        self.created_at = created_at


class PortfolioSummary:
    """Portfolio summary model"""

    def __init__(self,
                 cash_balance: float,
                 starting_balance_locked: float,
                 total_capital_deployed: float,
                 additional_capital: float,
                 total_equity: float = 0,
                 lifetime_pnl: float = 0,
                 open_positions_value: float = 0,
                 open_positions_count: int = 0,
                 unrealized_pnl: float = 0,
                 realized_pnl: float = 0):

        self.cash_balance = cash_balance
        self.starting_balance_locked = starting_balance_locked
        self.total_capital_deployed = total_capital_deployed
        self.additional_capital = additional_capital
        self.total_equity = total_equity
        self.lifetime_pnl = lifetime_pnl
        self.open_positions_value = open_positions_value
        self.open_positions_count = open_positions_count
        self.unrealized_pnl = unrealized_pnl
        self.realized_pnl = realized_pnl


class DatabaseManager:
    """Complete database management for trading journal"""

    def __init__(self, db_path: str = "trading_journal.db"):
        self.db_path = db_path
        self.initialize_database()

    @contextmanager
    def get_connection(self):
        """
        Context manager: opens a connection, commits on clean exit, rolls back on
        exception, and ALWAYS closes the connection in finally. This ensures that
        every commit is followed by an explicit conn.close() which flushes OS-level
        buffers — critical for Docker Desktop / VirtioFS on macOS where relying on
        GC-based close can lose recently-committed data if the container is stopped
        before the destructor runs.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize_database(self):
        """Initialize database with complete schema"""
        with self.get_connection() as conn:
            self._create_tables(conn)
            self._create_indexes(conn)
            self._create_triggers(conn)
            self._insert_default_settings(conn)
        self.optimize_performance()

    def _create_tables(self, conn: sqlite3.Connection):
        """Create all tables"""

        # Portfolio Summary (single-row singleton)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_summary (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash_balance REAL NOT NULL DEFAULT 0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO portfolio_summary (id, cash_balance) VALUES (1, 0)
        """)

        # Cash Transactions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL,
                time TIME,
                type TEXT NOT NULL CHECK (type IN ('deposit', 'withdrawal', 'dividend', 'interest', 'fees')),
                amount REAL NOT NULL,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Positions — header only, NO stored aggregates
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                position_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol               TEXT NOT NULL,
                market               TEXT CHECK(market IN ('Index Futures', 'Index Options', 'Equity', 'Equity Futures', 'Equity Options', 'Commodity Futures', 'Commodity Options')),
                option_type          TEXT CHECK(option_type IS NULL OR option_type IN ('Call', 'Put')),
                strike_price         REAL,
                expiry_date          DATE,
                direction            TEXT NOT NULL CHECK (direction IN ('Long', 'Short')),
                position_type        TEXT CHECK (position_type IN ('Intraday', 'Swing', 'Investment')),
                status               TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
                opened_date          DATE NOT NULL,
                closed_date          DATE,
                strategy             TEXT,
                notes                TEXT,
                tags                 TEXT,
                current_market_price REAL,
                price_updated_at     TIMESTAMP,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Executions — single source of truth for all price/qty data
        conn.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                execution_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id      INTEGER NOT NULL REFERENCES positions(position_id) ON DELETE CASCADE,
                type             TEXT NOT NULL CHECK (type IN ('entry', 'exit')),
                date             DATE NOT NULL,
                time             TIME,
                price            REAL NOT NULL CHECK (price > 0),
                quantity         REAL NOT NULL CHECK (quantity > 0),
                charges          REAL NOT NULL DEFAULT 0,
                leverage         REAL NOT NULL DEFAULT 1.0 CHECK (leverage >= 1.0),
                stop_loss        REAL,
                take_profit      REAL,
                risk_amount      REAL,
                setup_type       TEXT,
                market_conditions TEXT CHECK(market_conditions IS NULL OR market_conditions IN ('Bullish Range', 'Bearish Range', 'Bullish Trending', 'Bearish Trending', 'Undecided', 'Volatile')),
                emotions         TEXT CHECK(emotions IS NULL OR emotions IN ('Confident', 'Calm', 'Anxious', 'Frustrated', 'Excited', 'Patient', 'Focused')),
                confluence_score INTEGER CHECK (confluence_score IS NULL OR (confluence_score BETWEEN 1 AND 5)),
                trade_grade      TEXT CHECK (trade_grade IS NULL OR trade_grade IN ('A', 'B', 'C', 'D', 'F')),
                notes            TEXT,
                mistakes         TEXT,
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Screenshot attachments — one-to-many on executions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_screenshots (
                screenshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id    INTEGER NOT NULL
                                REFERENCES executions(execution_id) ON DELETE CASCADE,
                filename        TEXT NOT NULL,
                original_name   TEXT,
                uploaded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Strategies
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT UNIQUE NOT NULL,
                description TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Settings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_name TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                setting_type TEXT DEFAULT 'string' CHECK(setting_type IN ('string', 'number', 'boolean', 'json')),
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Import History
        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                original_filename TEXT,
                import_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                records_processed INTEGER DEFAULT 0,
                records_imported INTEGER DEFAULT 0,
                records_failed INTEGER DEFAULT 0,
                status TEXT CHECK(status IN ('Processing', 'Success', 'Partial', 'Failed')) DEFAULT 'Processing',
                error_message TEXT,
                column_mapping TEXT,
                import_summary TEXT
            )
        """)

    def _create_indexes(self, conn: sqlite3.Connection):
        """Create indexes for optimal query performance"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)",
            "CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)",
            "CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy)",
            "CREATE INDEX IF NOT EXISTS idx_positions_opened_date ON positions(opened_date)",
            "CREATE INDEX IF NOT EXISTS idx_positions_closed_date ON positions(closed_date)",
            "CREATE INDEX IF NOT EXISTS idx_executions_position ON executions(position_id)",
            "CREATE INDEX IF NOT EXISTS idx_executions_date ON executions(date)",
            "CREATE INDEX IF NOT EXISTS idx_executions_type ON executions(type)",
            "CREATE INDEX IF NOT EXISTS idx_executions_date_type ON executions(date, type)",
            "CREATE INDEX IF NOT EXISTS idx_screenshots_execution ON execution_screenshots(execution_id)",
            "CREATE INDEX IF NOT EXISTS idx_cash_date ON cash_transactions(date)",
            "CREATE INDEX IF NOT EXISTS idx_import_history_date ON import_history(import_date)",
            "CREATE INDEX IF NOT EXISTS idx_settings_name ON settings(setting_name)",
        ]
        for index_sql in indexes:
            conn.execute(index_sql)

    def _create_triggers(self, conn: sqlite3.Connection):
        """Create triggers for automated timestamp updates"""
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS update_strategies_timestamp
            AFTER UPDATE ON strategies
            FOR EACH ROW
            BEGIN
                UPDATE strategies SET updated_at = CURRENT_TIMESTAMP
                WHERE id = NEW.id;
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS update_settings_timestamp
            AFTER UPDATE ON settings
            FOR EACH ROW
            BEGIN
                UPDATE settings SET updated_at = CURRENT_TIMESTAMP
                WHERE id = NEW.id;
            END
        """)

    def _insert_default_settings(self, conn: sqlite3.Connection):
        """Insert default application settings"""
        default_settings = [
            ('base_currency', 'INR', 'string', 'Base currency for P&L calculations'),
            ('risk_per_trade_percent', '2', 'number', 'Default risk percentage per trade'),
            ('timezone', 'Asia/Kolkata', 'string', 'User timezone for date/time display'),
            ('date_format', 'DD/MM/YYYY', 'string', 'Preferred date format'),
            ('theme', 'dark', 'string', 'UI theme preference'),
            ('auto_calculate_duration', 'true', 'boolean', 'Auto-calculate trade duration'),
            ('default_market', 'Equity', 'string', 'Default market type for new trades'),
            ('export_format', 'CSV', 'string', 'Default export format'),
            ('backup_frequency_days', '7', 'number', 'Automatic backup frequency in days'),
        ]
        for setting_name, setting_value, setting_type, description in default_settings:
            conn.execute("""
                INSERT OR IGNORE INTO settings (setting_name, setting_value, setting_type, description)
                VALUES (?, ?, ?, ?)
            """, (setting_name, setting_value, setting_type, description))

    # ============================================================
    # Core Computation (read-only — never writes to DB)
    # ============================================================

    def _compute_position_metrics(self, position_id: int, conn: sqlite3.Connection,
                                   direction: str,
                                   current_market_price: Optional[float] = None) -> dict:
        """
        Pure read — compute all position metrics from executions.
        Returns dict with canonical keys AND template-compat aliases.
        Never writes to the database.
        """
        cursor = conn.execute("""
            SELECT type, price, quantity, charges, leverage
            FROM executions
            WHERE position_id = ?
            ORDER BY execution_id
        """, (position_id,))
        rows = cursor.fetchall()

        entries = [(r['price'], r['quantity'], r['charges'], r['leverage'])
                   for r in rows if r['type'] == 'entry']
        exits = [(r['price'], r['quantity'])
                 for r in rows if r['type'] == 'exit']

        total_entered = sum(q for _, q, _, _ in entries)
        total_exited = sum(q for _, q in exits)
        current_qty = total_entered - total_exited

        avg_exit = (sum(p * q for p, q in exits) / total_exited
                    ) if total_exited > 0 else 0.0
        avg_leverage = (sum(lev * q for _, q, _, lev in entries) / total_entered
                        ) if total_entered > 0 else 1.0

        total_entry_value = sum(p * q for p, q, _, _ in entries)
        total_exit_value = sum(p * q for p, q in exits)

        # Sales measured against the average cost of what was held at the time;
        # charges are entry and exit charges together
        walk = self._walk_executions(direction, rows)
        total_charges = walk['charges']
        realized_pnl = walk['gross_realized'] - total_charges

        # Average entry: of what is still held while open; of every entry once closed
        if current_qty > 0:
            avg_entry = walk['held_avg']
        else:
            avg_entry = total_entry_value / total_entered if total_entered > 0 else 0.0

        # Unrealized P&L (only if market price known and qty open)
        unrealized_pnl = None
        if current_market_price is not None and current_qty > 0:
            if direction == 'Long':
                unrealized_pnl = (current_market_price - avg_entry) * current_qty
            else:  # Short
                unrealized_pnl = (avg_entry - current_market_price) * current_qty

        # Status and closed_date
        status = 'closed' if current_qty <= 0 and total_entered > 0 else 'open'
        closed_date = None
        if status == 'closed' and exits:
            cursor2 = conn.execute("""
                SELECT MAX(date) FROM executions WHERE position_id = ? AND type = 'exit'
            """, (position_id,))
            row2 = cursor2.fetchone()
            closed_date = row2[0] if row2 else None

        # Duration
        duration_days = None
        duration_hours = None
        if status == 'closed' and closed_date:
            first_entry = conn.execute("""
                SELECT date, time FROM executions WHERE position_id = ? AND type = 'entry'
                ORDER BY date, time, execution_id LIMIT 1
            """, (position_id,)).fetchone()
            last_exit = conn.execute("""
                SELECT date, time FROM executions WHERE position_id = ? AND type = 'exit'
                ORDER BY date DESC, time DESC, execution_id DESC LIMIT 1
            """, (position_id,)).fetchone()
            if first_entry and last_exit:
                duration_days, duration_hours = _holding_time(
                    first_entry['date'], first_entry['time'], last_exit['date'], last_exit['time'])

        return {
            # Canonical
            'avg_entry': avg_entry,
            'avg_exit': avg_exit,
            'current_qty': current_qty,
            'total_entered': total_entered,
            'total_exited': total_exited,
            'total_entry_value': total_entry_value,
            'total_exit_value': total_exit_value,
            # Cost of the units still held (average cost x quantity held)
            'held_cost': walk['held_cost'] if current_qty > 0 else 0.0,
            'total_charges': total_charges,
            'avg_leverage': avg_leverage,
            'realized_pnl': realized_pnl,
            'unrealized_pnl': unrealized_pnl,
            'status': status,
            'closed_date': closed_date,
            'duration_days': duration_days,
            'duration_hours': duration_hours,
            # Template-compat aliases
            'average_entry_price': avg_entry,
            'average_exit_price': avg_exit,
            'current_quantity': current_qty,
            'total_quantity_entered': total_entered,
            'total_quantity_exited': total_exited,
        }

    def recalculate_position(self, position_id: int) -> dict:
        """
        Compute metrics then WRITE status and closed_date to positions table.
        Call only from POST routes after inserting executions.
        Returns the metrics dict.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT direction, current_market_price FROM positions WHERE position_id = ?",
                (position_id,)
            )
            row = cursor.fetchone()
            if not row:
                return {}
            direction = row['direction']
            cmp = row['current_market_price']

            metrics = self._compute_position_metrics(position_id, conn, direction, cmp)

            conn.execute("""
                UPDATE positions SET status = ?, closed_date = ? WHERE position_id = ?
            """, (metrics['status'], metrics['closed_date'], position_id))

        return metrics

    # ============================================================
    # Position Methods
    # ============================================================

    def find_or_create_position(self, symbol: str, market: Optional[str],
                                option_type: Optional[str], strike_price: Optional[float],
                                expiry_date: Optional[str], direction: str,
                                position_type: Optional[str], opened_date: str,
                                strategy: Optional[str], notes: Optional[str],
                                tags: Optional[str]) -> int:
        """Find existing open position or create new one. Returns position_id."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT position_id FROM positions
                    WHERE symbol = ? AND direction = ?
                    AND (market IS ? OR (market IS NULL AND ? IS NULL))
                    AND (option_type IS ? OR (option_type IS NULL AND ? IS NULL))
                    AND (strike_price IS ? OR (strike_price IS NULL AND ? IS NULL))
                    AND (expiry_date IS ? OR (expiry_date IS NULL AND ? IS NULL))
                    AND status = 'open'
                """, (symbol, direction,
                      market, market,
                      option_type, option_type,
                      strike_price, strike_price,
                      expiry_date, expiry_date))
                result = cursor.fetchone()
                if result:
                    return result[0]

                cursor = conn.execute("""
                    INSERT INTO positions (
                        symbol, market, option_type, strike_price, expiry_date, direction,
                        position_type, status, opened_date, strategy, notes, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                """, (symbol, market, option_type, strike_price, expiry_date, direction,
                      position_type, opened_date, strategy, notes, tags))
                return cursor.lastrowid
        except sqlite3.Error as e:
            raise Exception(f"Database error in find_or_create_position: {str(e)}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Returns all open positions with computed metrics."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT * FROM positions WHERE status = 'open' ORDER BY opened_date DESC
                """)
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    p = dict(row)
                    metrics = self._compute_position_metrics(
                        p['position_id'], conn, p['direction'], p.get('current_market_price')
                    )
                    p.update(metrics)
                    result.append(p)
                return result
        except sqlite3.Error:
            return []

    def get_closed_positions(self, start_date: Optional[str] = None,
                             end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns closed positions with computed metrics."""
        try:
            with self.get_connection() as conn:
                if start_date and end_date:
                    cursor = conn.execute("""
                        SELECT * FROM positions WHERE status = 'closed'
                        AND closed_date BETWEEN ? AND ?
                        ORDER BY closed_date DESC
                    """, (start_date, end_date))
                else:
                    cursor = conn.execute("""
                        SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_date DESC
                    """)
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    p = dict(row)
                    metrics = self._compute_position_metrics(
                        p['position_id'], conn, p['direction'], p.get('current_market_price')
                    )
                    p.update(metrics)
                    result.append(p)
                return result
        except sqlite3.Error:
            return []

    def get_position_by_id(self, position_id: int) -> Optional[Dict[str, Any]]:
        """Returns single position with computed metrics."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM positions WHERE position_id = ?", (position_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                p = dict(row)
                metrics = self._compute_position_metrics(
                    p['position_id'], conn, p['direction'], p.get('current_market_price')
                )
                p.update(metrics)
                return p
        except sqlite3.Error:
            return None

    def update_position_market_price(self, position_id: int, market_price: float):
        """Update current market price only (unrealized P&L computed on read)."""
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    UPDATE positions SET current_market_price = ?, price_updated_at = CURRENT_TIMESTAMP
                    WHERE position_id = ?
                """, (market_price, position_id))
        except sqlite3.Error as e:
            raise Exception(f"Database error in update_position_market_price: {str(e)}")

    # ============================================================
    # Execution Methods
    # ============================================================

    def insert_execution(self, execution: Execution) -> Tuple[bool, str, Optional[int]]:
        """Insert one execution row. Caller must call recalculate_position() after."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO executions (
                        position_id, type, date, time, price, quantity, charges, leverage,
                        stop_loss, take_profit, risk_amount,
                        setup_type, market_conditions, emotions, confluence_score, trade_grade,
                        notes, mistakes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    execution.position_id, execution.type, execution.date, execution.time,
                    execution.price, execution.quantity, execution.charges, execution.leverage,
                    execution.stop_loss, execution.take_profit, execution.risk_amount,
                    execution.setup_type, execution.market_conditions, execution.emotions,
                    execution.confluence_score, execution.trade_grade,
                    execution.notes, execution.mistakes
                ))
                return True, "Execution inserted successfully", cursor.lastrowid
        except sqlite3.IntegrityError as e:
            return False, f"Database integrity error: {str(e)}", None
        except sqlite3.Error as e:
            return False, f"Database error: {str(e)}", None

    def get_executions_by_position(self, position_id: int) -> List[Dict[str, Any]]:
        """Get all executions for a position, ordered by date/time."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT * FROM executions WHERE position_id = ?
                    ORDER BY date, time, execution_id
                """, (position_id,))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    # ============================================================
    # Screenshot Methods
    # ============================================================

    def insert_screenshot(self, execution_id: int, filename: str,
                          original_name: Optional[str] = None) -> Tuple[bool, str]:
        """Insert a screenshot record linked to an execution."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    """INSERT INTO execution_screenshots
                       (execution_id, filename, original_name)
                       VALUES (?, ?, ?)""",
                    (execution_id, filename, original_name)
                )
            return True, "ok"
        except sqlite3.Error as e:
            logger.error(f"insert_screenshot error: {e}")
            return False, str(e)

    def get_screenshots_by_execution(self, execution_id: int) -> List[Dict[str, Any]]:
        """Return all screenshots for one execution, oldest first."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    """SELECT * FROM execution_screenshots
                       WHERE execution_id = ?
                       ORDER BY uploaded_at""",
                    (execution_id,)
                )
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def get_screenshots_by_position(self, position_id: int) -> Dict[int, List[Dict[str, Any]]]:
        """
        Return all screenshots for every execution of a position.
        Result is keyed by execution_id (int) for O(1) template lookup.
        e.g. {12: [{screenshot_id, filename, ...}, ...], 15: [...]}
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    """SELECT s.*
                       FROM execution_screenshots s
                       JOIN executions e ON s.execution_id = e.execution_id
                       WHERE e.position_id = ?
                       ORDER BY s.execution_id, s.uploaded_at""",
                    (position_id,)
                )
                result: Dict[int, List[Dict[str, Any]]] = {}
                for row in cursor.fetchall():
                    d = dict(row)
                    eid = d['execution_id']
                    result.setdefault(eid, []).append(d)
                return result
        except sqlite3.Error:
            return {}

    def delete_screenshot(self, screenshot_id: int) -> Tuple[bool, str, Optional[str]]:
        """
        Delete a screenshot record and return its filename so the caller can
        also remove the physical file.
        Returns (success, message, filename_or_None).
        """
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT filename FROM execution_screenshots WHERE screenshot_id = ?",
                    (screenshot_id,)
                ).fetchone()
                if not row:
                    return False, "Screenshot not found", None
                filename = row['filename']
                conn.execute(
                    "DELETE FROM execution_screenshots WHERE screenshot_id = ?",
                    (screenshot_id,)
                )
            return True, "ok", filename
        except sqlite3.Error as e:
            logger.error(f"delete_screenshot error: {e}")
            return False, str(e), None

    # ============================================================
    # Analytics-Compatible Trade Methods
    # ============================================================

    def get_all_trades(self, limit: Optional[int] = None, offset: int = 0) -> pd.DataFrame:
        """
        Returns one row per CLOSED position with all analytics columns.
        Column names maintain compatibility with PerformanceCalculator.
        net_pnl is computed in Python with direction awareness.
        """
        try:
            with self.get_connection() as conn:
                query = """
                    SELECT
                        p.position_id AS trade_id,
                        p.opened_date AS date,
                        p.closed_date,
                        p.symbol,
                        p.market,
                        p.option_type,
                        p.direction,
                        p.position_type,
                        p.strategy,
                        p.tags,
                        p.notes,
                        -- VWAP entry price
                        SUM(CASE WHEN e.type='entry' THEN e.price * e.quantity ELSE 0 END) /
                            NULLIF(SUM(CASE WHEN e.type='entry' THEN e.quantity ELSE 0 END), 0)
                            AS entry_price,
                        -- VWAP exit price
                        SUM(CASE WHEN e.type='exit' THEN e.price * e.quantity ELSE 0 END) /
                            NULLIF(SUM(CASE WHEN e.type='exit' THEN e.quantity ELSE 0 END), 0)
                            AS exit_price,
                        -- Total entered quantity
                        SUM(CASE WHEN e.type='entry' THEN e.quantity ELSE 0 END) AS quantity,
                        -- All charges, entry and exit
                        SUM(e.charges) AS charges,
                        -- Weighted avg leverage
                        COALESCE(
                            SUM(CASE WHEN e.type='entry' THEN e.leverage * e.quantity ELSE 0 END) /
                            NULLIF(SUM(CASE WHEN e.type='entry' THEN e.quantity ELSE 0 END), 0),
                            1.0
                        ) AS leverage,
                        -- Qualitative fields from first entry execution
                        fe.time,
                        fe.setup_type,
                        fe.market_conditions,
                        fe.emotions,
                        fe.confluence_score,
                        fe.trade_grade,
                        fe.stop_loss,
                        fe.take_profit,
                        fe.risk_amount,
                        fe.mistakes,
                        -- Time of the last exit (holding time and ordering)
                        le.time AS exit_time
                    FROM positions p
                    JOIN executions e ON p.position_id = e.position_id
                    LEFT JOIN (
                        SELECT e2.position_id,
                               e2.time, e2.setup_type, e2.market_conditions, e2.emotions,
                               e2.confluence_score, e2.trade_grade, e2.stop_loss,
                               e2.take_profit, e2.risk_amount, e2.mistakes
                        FROM executions e2
                        WHERE e2.type = 'entry'
                          AND e2.execution_id = (
                              SELECT MIN(e3.execution_id) FROM executions e3
                              WHERE e3.position_id = e2.position_id AND e3.type = 'entry'
                          )
                    ) fe ON p.position_id = fe.position_id
                    LEFT JOIN (
                        SELECT e4.position_id, e4.time
                        FROM executions e4
                        WHERE e4.type = 'exit'
                          AND e4.execution_id = (
                              SELECT e5.execution_id FROM executions e5
                              WHERE e5.position_id = e4.position_id AND e5.type = 'exit'
                              ORDER BY e5.date DESC, e5.time DESC, e5.execution_id DESC
                              LIMIT 1
                          )
                    ) le ON p.position_id = le.position_id
                    WHERE p.status = 'closed'
                    GROUP BY p.position_id
                    ORDER BY p.closed_date DESC
                """
                if limit:
                    query += f" LIMIT {limit} OFFSET {offset}"

                df = pd.read_sql_query(query, conn, parse_dates=['date', 'closed_date'])

                if df.empty:
                    return df

                # Direction-aware net_pnl in Python
                def compute_pnl(row):
                    entry = row['entry_price']
                    exit_ = row['exit_price']
                    qty = row['quantity']
                    charges = row['charges']
                    if pd.isna(exit_) or pd.isna(entry) or qty == 0:
                        return -charges
                    if row['direction'] == 'Long':
                        return (exit_ - entry) * qty - charges
                    else:
                        return (entry - exit_) * qty - charges

                df['net_pnl'] = df.apply(compute_pnl, axis=1)

                # R-multiple
                def compute_r(row):
                    if pd.notna(row.get('risk_amount')) and row['risk_amount'] > 0:
                        return row['net_pnl'] / row['risk_amount']
                    return None

                df['r_multiple'] = df.apply(compute_r, axis=1)

                # Holding time: first entry to last exit; hours from the times
                # when both are recorded, otherwise days x 24.
                def _day(value):
                    return value.strftime('%Y-%m-%d') if pd.notna(value) else None

                def _clock(value):
                    return value if isinstance(value, str) and value else None

                durations = [
                    _holding_time(_day(opened), _clock(t), _day(closed), _clock(xt))
                    for opened, t, closed, xt in zip(df['date'], df['time'],
                                                     df['closed_date'], df['exit_time'])
                ]
                df['duration_days'] = [d for d, _ in durations]
                df['duration_hours'] = [h for _, h in durations]

                # Alias
                df['avg_leverage'] = df['leverage']

                return df

        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            return pd.DataFrame()

    def get_strategies_list(self) -> List[str]:
        """Get unique strategy names from closed positions."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT DISTINCT strategy FROM positions
                    WHERE status = 'closed' AND strategy IS NOT NULL
                    ORDER BY strategy
                """)
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def get_filtered_trades(self, strategy: Optional[str] = None,
                            filters: Optional[Dict] = None) -> pd.DataFrame:
        """Get completed positions with optional filters applied."""
        df = self.get_all_trades()
        if df.empty:
            return df

        if strategy:
            df = df[df['strategy'] == strategy]

        if filters:
            if filters.get('trade_style'):
                df = df[df['position_type'] == filters['trade_style']]

            if filters.get('day_of_week') is not None:
                df['date'] = pd.to_datetime(df['date'])
                df = df[df['date'].dt.dayofweek == filters['day_of_week']]

            if filters.get('hour') is not None:
                df = df[pd.to_numeric(df['time'].str[:2], errors='coerce') == filters['hour']]

            if filters.get('market_condition'):
                df = df[df['market_conditions'] == filters['market_condition']]

            if filters.get('market'):
                df = df[df['market'] == filters['market']]

            if filters.get('setup_type'):
                df = df[df['setup_type'] == filters['setup_type']]

            if filters.get('tags'):
                df = df[df['tags'].str.contains(filters['tags'], case=False, na=False)]

            if filters.get('date_from'):
                df['date'] = pd.to_datetime(df['date'])
                df = df[df['date'] >= filters['date_from']]

            if filters.get('date_to'):
                df['date'] = pd.to_datetime(df['date'])
                df = df[df['date'] <= filters['date_to']]

        return df

    def get_trade_count(self) -> int:
        """Get total number of closed positions."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM positions WHERE status='closed'")
                return cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    # ============================================================
    # Portfolio Methods
    # ============================================================

    def get_first_trade_date(self) -> Optional[str]:
        """Get the date of the first execution ever made."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("SELECT MIN(date) FROM executions")
                result = cursor.fetchone()
                return result[0] if result and result[0] else None
        except sqlite3.Error:
            return None

    def get_starting_balance(self) -> float:
        """Sum of cash transactions BEFORE first execution date."""
        try:
            first_date = self.get_first_trade_date()
            if not first_date:
                return 0.0
            with self.get_connection() as conn:
                # Amounts are stored signed: deposits/dividends/interest positive,
                # withdrawals/fees negative (see add_cash_transaction in app.py).
                cursor = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM cash_transactions WHERE date < ?
                """, (first_date,))
                result = cursor.fetchone()
                return result[0] if result else 0.0
        except sqlite3.Error as e:
            logger.error(f"Database error in get_starting_balance: {e}")
            return 0.0

    def get_total_capital_deployed(self) -> float:
        """Sum of ALL cash transactions (deposits minus withdrawals). Amounts are
        stored signed, so this is a plain sum."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM cash_transactions
                """)
                result = cursor.fetchone()
                return result[0] if result else 0.0
        except sqlite3.Error as e:
            logger.error(f"Database error in get_total_capital_deployed: {e}")
            return 0.0

    def get_additional_capital(self) -> float:
        return self.get_total_capital_deployed() - self.get_starting_balance()

    def get_portfolio_summary(self) -> PortfolioSummary:
        """Returns PortfolioSummary with all portfolio metrics."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("SELECT cash_balance FROM portfolio_summary WHERE id = 1")
                row = cursor.fetchone()
                cash_balance = row[0] if row else 0

                starting_balance_locked = self.get_starting_balance()
                total_capital_deployed = self.get_total_capital_deployed()
                additional_capital = self.get_additional_capital()

                # Open positions
                cursor = conn.execute(
                    "SELECT position_id, direction, current_market_price FROM positions WHERE status = 'open'"
                )
                open_rows = cursor.fetchall()
                open_positions_count = len(open_rows)

                # Each open position is worth the margin still set aside for it
                # plus its unrealized P&L (none until a market price is entered,
                # so an unpriced position counts at its margin, i.e. its cost).
                unrealized_pnl = 0.0
                open_positions_value = 0.0
                for row in open_rows:
                    pos_id = row['position_id']
                    direction = row['direction']
                    cmp = row['current_market_price']
                    metrics = self._compute_position_metrics(pos_id, conn, direction, cmp)
                    position_unrealized = metrics.get('unrealized_pnl') or 0.0
                    unrealized_pnl += position_unrealized
                    locked = self._replay_position_cash(conn, pos_id, direction)['locked_margin']
                    open_positions_value += locked + position_unrealized

                # Realized P&L from closed positions
                df = self.get_all_trades()
                realized_pnl = float(df['net_pnl'].sum()) if not df.empty else 0.0

                total_equity = cash_balance + open_positions_value
                lifetime_pnl = total_equity - total_capital_deployed

                return PortfolioSummary(
                    cash_balance=cash_balance,
                    starting_balance_locked=starting_balance_locked,
                    total_capital_deployed=total_capital_deployed,
                    additional_capital=additional_capital,
                    total_equity=total_equity,
                    lifetime_pnl=lifetime_pnl,
                    open_positions_value=open_positions_value,
                    open_positions_count=open_positions_count,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl,
                )
        except sqlite3.Error as e:
            logger.error(f"Database error in get_portfolio_summary: {e}")
            return PortfolioSummary(
                cash_balance=0, starting_balance_locked=0,
                total_capital_deployed=0, additional_capital=0
            )

    def update_cash_balance(self, amount: float):
        """Add or subtract from cash balance."""
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    UPDATE portfolio_summary
                    SET cash_balance = cash_balance + ?,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (amount,))
        except sqlite3.Error as e:
            raise Exception(f"Database error in update_cash_balance: {str(e)}")

    # ============================================================
    # Cash and realized P&L for trades — margin model, average cost
    # ============================================================
    # Opening or adding to a position, long or short, sets aside its margin
    # (value / leverage) plus its charges. A sale is measured against the
    # average cost of what is held at that moment: it releases that share of
    # the margin set aside, plus its profit (or minus its loss), less its
    # charges. At leverage 1 a long works like a cash purchase.

    @staticmethod
    def _walk_executions(direction: str, rows) -> Dict[str, Any]:
        """Walk a position's executions in the order they were recorded
        (rows: type, price, quantity, charges, leverage). Returns the cash each
        one moved, the profit realized by sales before charges, all charges,
        and what is still held: quantity, average cost and margin set aside."""
        held_qty = 0.0
        held_cost = 0.0      # price x quantity of what is held, at average cost
        held_margin = 0.0
        gross_realized = 0.0
        charges_total = 0.0
        cash = []
        for r in rows:
            price, qty = r['price'], r['quantity']
            charges = r['charges'] or 0.0
            charges_total += charges
            if r['type'] == 'entry':
                margin = price * qty / (r['leverage'] or 1.0)
                held_qty += qty
                held_cost += price * qty
                held_margin += margin
                cash.append(-(margin + charges))
            elif held_qty > 0:
                sold = min(qty, held_qty)
                avg_cost = held_cost / held_qty
                released = held_margin * sold / held_qty
                pnl = (price - avg_cost) * sold if direction == 'Long' else (avg_cost - price) * sold
                gross_realized += pnl
                held_qty -= sold
                held_cost -= avg_cost * sold
                held_margin -= released
                cash.append(released + pnl - charges)
            else:
                cash.append(-charges)
        return {
            'cash': cash,
            'gross_realized': gross_realized,
            'charges': charges_total,
            'held_qty': held_qty,
            'held_avg': held_cost / held_qty if held_qty > 0 else 0.0,
            'held_cost': max(held_cost, 0.0) if held_qty > 0 else 0.0,
            'held_margin': max(held_margin, 0.0),
        }

    def _position_rows(self, conn: sqlite3.Connection, position_id: int):
        return conn.execute("""
            SELECT type, price, quantity, charges, leverage FROM executions
            WHERE position_id = ? ORDER BY execution_id
        """, (position_id,)).fetchall()

    def _replay_position_cash(self, conn: sqlite3.Connection, position_id: int,
                              direction: str) -> Dict[str, float]:
        """Net cash a position's executions moved, and the margin still set aside."""
        walk = self._walk_executions(direction, self._position_rows(conn, position_id))
        return {'net_cash': sum(walk['cash']), 'locked_margin': walk['held_margin']}

    def apply_entry_cash(self, price: float, quantity: float, leverage: float,
                         charges: float = 0.0):
        """Set aside the margin and charges of an entry."""
        self.update_cash_balance(-(price * quantity / (leverage or 1.0) + (charges or 0.0)))

    def apply_exit_cash(self, position_id: int):
        """Move the cash of the exit just recorded for this position (its most
        recent execution). Call right after inserting the exit."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT direction FROM positions WHERE position_id = ?",
                               (position_id,)).fetchone()
            rows = self._position_rows(conn, position_id)
            if not row or not rows or rows[-1]['type'] != 'exit':
                return
            amount = self._walk_executions(row['direction'], rows)['cash'][-1]
            conn.execute("""
                UPDATE portfolio_summary
                SET cash_balance = cash_balance + ?, last_updated = CURRENT_TIMESTAMP
                WHERE id = 1
            """, (amount,))

    def delete_position(self, position_id: int):
        """Delete a position with its executions and screenshot rows, giving back
        the cash its executions moved."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT direction FROM positions WHERE position_id = ?",
                               (position_id,)).fetchone()
            if not row:
                return
            net_cash = self._replay_position_cash(conn, position_id, row['direction'])['net_cash']
            conn.execute("""
                UPDATE portfolio_summary
                SET cash_balance = cash_balance - ?, last_updated = CURRENT_TIMESTAMP
                WHERE id = 1
            """, (net_cash,))
            conn.execute("DELETE FROM positions WHERE position_id = ?", (position_id,))

    # ============================================================
    # Cash Transaction Methods
    # ============================================================

    def insert_cash_transaction(self, transaction: CashTransaction) -> Tuple[bool, str, Optional[int]]:
        """Record cash deposit/withdrawal/dividend.

        Returns a warning message if the transaction date precedes the first trade,
        as this would retroactively alter the starting balance and break ROI calculations.
        """
        warning = ''
        first_trade_date = self.get_first_trade_date()
        if first_trade_date and transaction.date < first_trade_date:
            warning = (f" Warning: transaction date ({transaction.date}) is before the first "
                       f"trade ({first_trade_date}). This will retroactively alter starting "
                       f"balance and may distort ROI calculations.")

        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO cash_transactions (date, time, type, amount, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (transaction.date, transaction.time, transaction.type,
                      transaction.amount, transaction.description))
                msg = "Cash transaction recorded" + warning
                return True, msg, cursor.lastrowid
        except sqlite3.Error as e:
            return False, f"Database error: {str(e)}", None

    def get_cash_transactions(self, start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get cash transaction history."""
        try:
            with self.get_connection() as conn:
                if start_date and end_date:
                    cursor = conn.execute("""
                        SELECT * FROM cash_transactions
                        WHERE date BETWEEN ? AND ?
                        ORDER BY date DESC, time DESC
                    """, (start_date, end_date))
                else:
                    cursor = conn.execute("""
                        SELECT * FROM cash_transactions ORDER BY date DESC, time DESC
                    """)
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    # ============================================================
    # Settings Management
    # ============================================================

    def get_setting(self, setting_name: str, default_value: Any = None) -> Any:
        """Get a setting value with optional default."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT setting_value, setting_type FROM settings WHERE setting_name = ?
                """, (setting_name,))
                row = cursor.fetchone()
                if row:
                    value, value_type = row[0], row[1]
                    if value_type == 'boolean':
                        return value.lower() in ('true', '1', 'yes')
                    elif value_type == 'number':
                        return float(value) if '.' in value else int(value)
                    elif value_type == 'json':
                        return json.loads(value)
                    else:
                        return value
                return default_value
        except (sqlite3.Error, json.JSONDecodeError):
            return default_value

    def set_setting(self, setting_name: str, setting_value: Any,
                    setting_type: str = 'string', description: str = '') -> bool:
        """Set a setting value."""
        try:
            if setting_type == 'boolean':
                str_value = 'true' if setting_value else 'false'
            elif setting_type == 'json':
                str_value = json.dumps(setting_value)
            else:
                str_value = str(setting_value)
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO settings (setting_name, setting_value, setting_type, description)
                    VALUES (?, ?, ?, ?)
                """, (setting_name, str_value, setting_type, description))
                return True
        except sqlite3.Error:
            return False

    def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings as a dictionary."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT setting_name, setting_value, setting_type FROM settings"
                )
                settings = {}
                for row in cursor.fetchall():
                    name, value, value_type = row
                    if value_type == 'boolean':
                        settings[name] = value.lower() in ('true', '1', 'yes')
                    elif value_type == 'number':
                        settings[name] = float(value) if '.' in value else int(value)
                    elif value_type == 'json':
                        settings[name] = json.loads(value)
                    else:
                        settings[name] = value
                return settings
        except (sqlite3.Error, json.JSONDecodeError):
            return {}

    # ============================================================
    # Strategy Management
    # ============================================================

    def add_strategy(self, strategy_name: str, description: str = '') -> Tuple[bool, str]:
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO strategies (strategy_name, description) VALUES (?, ?)
                """, (strategy_name, description))
                return True, "Strategy added successfully"
        except sqlite3.IntegrityError:
            return False, "Strategy already exists"
        except sqlite3.Error as e:
            return False, f"Database error: {str(e)}"

    def get_all_strategies(self) -> List[Dict[str, Any]]:
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id, strategy_name, description, is_active, created_at, updated_at
                    FROM strategies ORDER BY strategy_name
                """)
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def get_active_strategies(self) -> List[str]:
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT strategy_name FROM strategies WHERE is_active = 1 ORDER BY strategy_name
                """)
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def update_strategy(self, strategy_id: int, strategy_name: str,
                        description: str = '') -> Tuple[bool, str]:
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    UPDATE strategies SET strategy_name = ?, description = ? WHERE id = ?
                """, (strategy_name, description, strategy_id))
                if cursor.rowcount > 0:
                    return True, "Strategy updated successfully"
                return False, "Strategy not found"
        except sqlite3.IntegrityError:
            return False, "Strategy name already exists"
        except sqlite3.Error as e:
            return False, f"Database error: {str(e)}"

    def delete_strategy(self, strategy_id: int) -> Tuple[bool, str]:
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
                if cursor.rowcount > 0:
                    return True, "Strategy deleted successfully"
                return False, "Strategy not found"
        except sqlite3.Error as e:
            return False, f"Database error: {str(e)}"

    def toggle_strategy_status(self, strategy_id: int) -> Tuple[bool, str, bool]:
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT is_active FROM strategies WHERE id = ?", (strategy_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return False, "Strategy not found", False
                new_status = not bool(row[0])
                conn.execute(
                    "UPDATE strategies SET is_active = ? WHERE id = ?", (new_status, strategy_id)
                )
                status_text = "activated" if new_status else "deactivated"
                return True, f"Strategy {status_text} successfully", new_status
        except sqlite3.Error as e:
            return False, f"Database error: {str(e)}", False

    # ============================================================
    # Import History Management
    # ============================================================

    def create_import_record(self, filename: str, original_filename: str) -> int:
        try:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO import_history (filename, original_filename) VALUES (?, ?)
                """, (filename, original_filename))
                return cursor.lastrowid
        except sqlite3.Error:
            return 0

    def update_import_record(self, import_id: int, records_processed: int,
                              records_imported: int, records_failed: int, status: str,
                              error_message: str = '', column_mapping: Dict = None,
                              import_summary: Dict = None) -> bool:
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    UPDATE import_history SET
                        records_processed = ?, records_imported = ?, records_failed = ?,
                        status = ?, error_message = ?, column_mapping = ?, import_summary = ?
                    WHERE id = ?
                """, (records_processed, records_imported, records_failed, status, error_message,
                      json.dumps(column_mapping) if column_mapping else None,
                      json.dumps(import_summary) if import_summary else None,
                      import_id))
                return True
        except sqlite3.Error:
            return False

    # ============================================================
    # CSV Import
    # ============================================================

    def import_trades_from_csv(self, csv_path: str, column_mapping: Dict[str, str],
                                skip_duplicates: bool = True,
                                validate_data: bool = True) -> Tuple[bool, str, Dict[str, int]]:
        """CSV import using the executions model. column_mapping maps each
        field name to its (normalized) CSV column name; see normalize_column_name."""
        try:
            try:
                df = pd.read_csv(csv_path, encoding='utf-8')
            except UnicodeDecodeError:
                for encoding in ['latin-1', 'iso-8859-1', 'cp1252']:
                    try:
                        df = pd.read_csv(csv_path, encoding=encoding)
                        break
                    except (UnicodeDecodeError, Exception):
                        continue
                else:
                    return False, "Unable to read CSV file - encoding not supported", \
                           {'processed': 0, 'imported': 0, 'failed': 0}

            if df.empty:
                return False, "CSV file is empty", {'processed': 0, 'imported': 0, 'failed': 0}

            df.columns = [normalize_column_name(c) for c in df.columns]
            import_id = self.create_import_record(csv_path, os.path.basename(csv_path))

            imported = 0
            failed = 0
            skipped = 0
            errors = []

            processed_data = self._preprocess_csv_data(df, column_mapping)
            df = processed_data['dataframe']
            # Rows whose dates could not be read were removed; report them as failed
            failed += len(processed_data['bad_rows'])
            errors.extend(processed_data['bad_rows'])
            errors.extend(processed_data['warnings'])

            for index, row in df.iterrows():
                try:
                    trade_data = {}
                    for db_field, csv_col in column_mapping.items():
                        if csv_col and csv_col in df.columns and pd.notna(row[csv_col]):
                            if db_field in ['entry_price', 'exit_price', 'quantity', 'charges',
                                            'leverage', 'stop_loss', 'take_profit', 'risk_amount']:
                                try:
                                    clean_value = str(row[csv_col]).replace('$', '').replace(',', '').strip()
                                    trade_data[db_field] = float(clean_value)
                                except (ValueError, TypeError):
                                    errors.append(f"Row {index + 1}: Invalid numeric value for {db_field}: {row[csv_col]}")
                                    continue
                            elif db_field == 'confluence_score':
                                try:
                                    trade_data[db_field] = int(float(row[csv_col]))
                                except (ValueError, TypeError):
                                    errors.append(f"Row {index + 1}: Invalid integer value for {db_field}: {row[csv_col]}")
                                    continue
                            else:
                                # Text: a cell holding only spaces counts as blank
                                text = str(row[csv_col]).strip()
                                if text:
                                    trade_data[db_field] = text

                    required_fields = ['date', 'symbol', 'direction', 'entry_price', 'exit_price', 'quantity']
                    missing_fields = [f for f in required_fields if f not in trade_data]
                    if missing_fields:
                        failed += 1
                        errors.append(f"Row {index + 1}: Missing required fields: {', '.join(missing_fields)}")
                        continue

                    if validate_data:
                        validation_errors = self._validate_trade_data(trade_data, index + 1)
                        if validation_errors:
                            failed += 1
                            errors.extend(validation_errors)
                            continue

                    entry_date = trade_data['date']
                    entry_time = trade_data.get('time') or trade_data.get('entry_time')
                    exit_date = trade_data.get('exit_date', entry_date)
                    exit_time = trade_data.get('exit_time')
                    symbol = trade_data['symbol']
                    direction = trade_data['direction']
                    entry_price = trade_data['entry_price']
                    exit_price = trade_data['exit_price']
                    quantity = trade_data['quantity']
                    charges = trade_data.get('charges', 0.0)
                    leverage = trade_data.get('leverage', 1.0)
                    trade_style = trade_data.get('trade_style') or trade_data.get('position_type') or 'Swing'

                    if skip_duplicates:
                        with self.get_connection() as conn:
                            existing = conn.execute("""
                                SELECT COUNT(*) FROM executions e
                                JOIN positions p ON e.position_id = p.position_id
                                WHERE p.symbol = ? AND e.date = ? AND e.price = ? AND e.quantity = ?
                                AND e.type = 'entry'
                            """, (symbol, entry_date, entry_price, quantity)).fetchone()[0]
                        if existing > 0:
                            skipped += 1
                            errors.append(f"Row {index + 1}: Potential duplicate trade skipped")
                            continue

                    position_id = self.find_or_create_position(
                        symbol=symbol,
                        market=trade_data.get('market'),
                        option_type=trade_data.get('option_type'),
                        strike_price=None,
                        expiry_date=None,
                        direction=direction,
                        position_type=trade_style,
                        opened_date=entry_date,
                        strategy=trade_data.get('strategy'),
                        notes=trade_data.get('notes'),
                        tags=trade_data.get('tags'),
                    )

                    entry_exec = Execution(
                        position_id=position_id,
                        date=entry_date,
                        time=entry_time,
                        type='entry',
                        price=entry_price,
                        quantity=quantity,
                        charges=charges,
                        leverage=leverage,
                        stop_loss=trade_data.get('stop_loss'),
                        take_profit=trade_data.get('take_profit'),
                        risk_amount=trade_data.get('risk_amount'),
                        market_conditions=trade_data.get('market_conditions'),
                        emotions=trade_data.get('emotions'),
                        confluence_score=trade_data.get('confluence_score'),
                        trade_grade=trade_data.get('trade_grade'),
                        setup_type=trade_data.get('setup_type'),
                        notes=trade_data.get('notes'),
                        mistakes=trade_data.get('mistakes'),
                    )
                    success, message, _ = self.insert_execution(entry_exec)
                    if not success:
                        failed += 1
                        errors.append(f"Row {index + 1}: Entry failed - {message}")
                        continue

                    self.apply_entry_cash(entry_price, quantity, leverage, charges)

                    exit_exec = Execution(
                        position_id=position_id,
                        date=exit_date,
                        time=exit_time,
                        type='exit',
                        price=exit_price,
                        quantity=quantity,
                        charges=0,
                        leverage=leverage,
                    )
                    success, message, _ = self.insert_execution(exit_exec)
                    if not success:
                        failed += 1
                        errors.append(f"Row {index + 1}: Exit failed - {message}")
                        continue

                    self.apply_exit_cash(position_id)
                    self.recalculate_position(position_id)
                    imported += 1

                except Exception as e:
                    failed += 1
                    errors.append(f"Row {index + 1}: {str(e)}")

            status = 'Success' if failed == 0 else ('Partial' if imported > 0 else 'Failed')
            error_message = '; '.join(errors[:10])
            import_summary = {
                'total_rows': len(df),
                'imported': imported,
                'failed': failed,
                'errors': errors[:20],
            }
            self.update_import_record(import_id, len(df), imported, failed, status,
                                      error_message, column_mapping, import_summary)

            stats = {'processed': len(df), 'imported': imported, 'failed': failed,
                     'skipped': skipped, 'errors': errors}
            if imported > 0:
                return True, f"Import completed: {imported} trades imported, {failed} failed", stats
            else:
                return False, f"Import failed: No trades imported, {failed} errors", stats

        except Exception as e:
            return False, f"Import error: {str(e)}", {'processed': 0, 'imported': 0, 'failed': 0}

    def _preprocess_csv_data(self, df: pd.DataFrame, column_mapping: Dict[str, str]) -> Dict[str, Any]:
        """Normalise the CSV before import. Dates are read as YYYY-MM-DD or
        DD/MM/YYYY (never guessed month-first) and stored as YYYY-MM-DD. Rows
        whose entry date, or a given exit date, cannot be read are removed and
        returned in bad_rows."""
        warnings = []
        bad_rows = []
        processed_df = df.copy()

        for field in ('date', 'exit_date'):
            col = column_mapping.get(field)
            if not col or col not in processed_df.columns:
                continue
            raw = processed_df[col]
            text = raw.astype(str).str.strip()
            blank = raw.isna() | (text == '')
            parsed = pd.to_datetime(text, format='%Y-%m-%d', errors='coerce').fillna(
                pd.to_datetime(text, format='%d/%m/%Y', errors='coerce'))
            # The entry date is required; an exit date may be left blank
            unreadable = parsed.isna() if field == 'date' else (parsed.isna() & ~blank)
            for idx in processed_df.index[unreadable]:
                value = '' if blank[idx] else raw[idx]
                bad_rows.append(f"Row {idx + 1}: missing or unreadable {field} '{value}' "
                                f"(use YYYY-MM-DD or DD/MM/YYYY)")
            processed_df = processed_df[~unreadable].copy()
            processed_df[col] = parsed[~unreadable].dt.strftime('%Y-%m-%d')

        if 'time' in column_mapping and column_mapping['time'] in df.columns:
            time_col = column_mapping['time']
            try:
                processed_df[time_col] = pd.to_datetime(
                    processed_df[time_col], format='%H:%M', errors='coerce'
                ).dt.strftime('%H:%M')
            except (ValueError, TypeError):
                warnings.append("Time formatting issue - using original values")

        numeric_fields = ['entry_price', 'exit_price', 'quantity', 'charges',
                          'stop_loss', 'take_profit', 'risk_amount']
        for field in numeric_fields:
            if field in column_mapping and column_mapping[field] in df.columns:
                col = column_mapping[field]
                processed_df[col] = processed_df[col].astype(str).str.replace(
                    r'[$,€£¥]', '', regex=True
                )
                processed_df[col] = pd.to_numeric(processed_df[col], errors='coerce')

        # Trim text; a blank cell stays empty (never the text 'nan')
        string_fields = ['symbol', 'direction', 'market', 'strategy', 'notes', 'tags']
        for field in string_fields:
            if field in column_mapping and column_mapping[field] in df.columns:
                col = column_mapping[field]
                processed_df[col] = processed_df[col].map(
                    lambda v: (str(v).strip() or None) if pd.notna(v) else None)

        return {'dataframe': processed_df, 'warnings': warnings, 'bad_rows': bad_rows}

    def _validate_trade_data(self, trade_data: Dict[str, Any], row_number: int) -> List[str]:
        """Validate trade data before creating Execution objects."""
        errors = []
        if 'date' in trade_data:
            try:
                pd.to_datetime(trade_data['date'])
            except (ValueError, TypeError):
                errors.append(f"Row {row_number}: Invalid date format: {trade_data['date']}")

        for field in ['entry_price', 'exit_price']:
            if field in trade_data and trade_data[field] <= 0:
                errors.append(f"Row {row_number}: {field} must be greater than 0: {trade_data[field]}")

        if 'quantity' in trade_data and trade_data['quantity'] <= 0:
            errors.append(f"Row {row_number}: Quantity must be greater than 0: {trade_data['quantity']}")

        if 'direction' in trade_data and trade_data['direction'] not in ['Long', 'Short']:
            errors.append(f"Row {row_number}: Direction must be 'Long' or 'Short': {trade_data['direction']}")

        if 'market' in trade_data:
            if trade_data['market'] not in VALID_MARKETS:
                errors.append(f"Row {row_number}: Invalid market type: {trade_data['market']}")

        return errors

    # ============================================================
    # Utility Methods
    # ============================================================

    def get_database_stats(self) -> Dict[str, Any]:
        """Get comprehensive database statistics."""
        try:
            with self.get_connection() as conn:
                stats = {}

                df = self.get_all_trades()
                if not df.empty:
                    stats['total_trades'] = len(df)
                    stats['winning_trades'] = int((df['net_pnl'] > 0).sum())
                    stats['losing_trades'] = int((df['net_pnl'] < 0).sum())
                    stats['breakeven_trades'] = int((df['net_pnl'] == 0).sum())
                    stats['total_pnl'] = float(df['net_pnl'].sum())
                    stats['avg_pnl'] = float(df['net_pnl'].mean())
                    stats['best_trade'] = float(df['net_pnl'].max())
                    stats['worst_trade'] = float(df['net_pnl'].min())
                    stats['unique_symbols'] = int(df['symbol'].nunique())
                    stats['unique_strategies'] = int(df['strategy'].dropna().nunique())
                    stats['earliest_trade'] = str(df['date'].min())
                    stats['latest_trade'] = str(df['date'].max())
                else:
                    stats.update({
                        'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                        'breakeven_trades': 0, 'total_pnl': 0, 'avg_pnl': 0,
                        'best_trade': 0, 'worst_trade': 0, 'unique_symbols': 0,
                        'unique_strategies': 0, 'earliest_trade': None, 'latest_trade': None,
                    })

                # Monthly summary
                if not df.empty:
                    df['month'] = pd.to_datetime(df['date']).dt.to_period('M').astype(str)
                    monthly = df.groupby('month').agg(
                        trade_count=('net_pnl', 'count'),
                        monthly_pnl=('net_pnl', 'sum'),
                    ).reset_index().tail(12)
                    stats['monthly_summary'] = monthly.to_dict('records')
                else:
                    stats['monthly_summary'] = []

                # Top strategies
                if not df.empty and 'strategy' in df.columns:
                    strat = df[df['strategy'].notna()].groupby('strategy').agg(
                        trade_count=('net_pnl', 'count'),
                        total_pnl=('net_pnl', 'sum'),
                        avg_pnl=('net_pnl', 'mean'),
                    ).reset_index().sort_values('total_pnl', ascending=False).head(10)
                    stats['top_strategies'] = strat.to_dict('records')
                else:
                    stats['top_strategies'] = []

                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                stats['tables'] = [row[0] for row in cursor.fetchall()]

                try:
                    stats['db_size_bytes'] = os.path.getsize(self.db_path)
                    stats['db_size_mb'] = round(stats['db_size_bytes'] / (1024 * 1024), 2)
                except OSError:
                    stats['db_size_bytes'] = 0
                    stats['db_size_mb'] = 0

                return stats
        except sqlite3.Error as e:
            return {'error': str(e)}

    def optimize_performance(self) -> bool:
        """Optimize database performance for better speed."""
        try:
            with self.get_connection() as conn:
                for sql in [
                    "PRAGMA journal_mode=WAL",
                    "PRAGMA synchronous=NORMAL",
                    "PRAGMA cache_size=10000",
                    "PRAGMA temp_store=memory",
                ]:
                    conn.execute(sql)
            return True
        except Exception as e:
            logger.warning(f"Performance optimization failed: {e}")
            return False

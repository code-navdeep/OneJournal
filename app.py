"""
Trading Journal Application - Flask Backend

FRONTEND LIBRARIES (self-hosted for offline use):
-------------------------------------------------
HTMX, Plotly.js, and Flatpickr are all served locally from static/ — there is no
runtime CDN dependency, so the app works fully offline. See templates/base.html.

  - static/js/htmx-1.9.10.min.js
  - static/js/plotly-2.27.0.min.js
  - static/js/flatpickr-4.6.13.min.js
  - static/css/flatpickr-4.6.13.min.css

To upgrade a library: download the new version into static/, then update the matching
url_for('static', ...) tag in templates/base.html.
"""

from flask import Flask, render_template, render_template_string, request, jsonify, send_from_directory, make_response, session, g
from markupsafe import escape
from database import DatabaseManager, Execution, CashTransaction, normalize_column_name
import pandas as pd
from datetime import datetime, date
import io
import json
import os
import math
import tempfile
import shutil
import logging
import uuid
import hmac
import secrets
import numpy as np
from pathlib import Path
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from calculations import (
    PerformanceCalculator,
    EquityCurveCalculator,
    TimeBasedAnalyzer,
    StrategyAnalyzer,
    MarketAnalyzer,
    TradeStyleAnalyzer,
    RiskAnalyzer,
    sort_by_close,
)

# Load credentials from .env (host-mounted; injected via docker-compose env_file in prod)
load_dotenv()

# Quiet Werkzeug's per-request access log (noise; inside Docker it only ever shows
# the bridge gateway IP, not real clients). Genuine warnings/errors still surface.
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__)

# Session signing key — generated fresh in memory on every startup, never written to
# disk. It only signs the 'current_db' session cookie; auth is stateless and does not
# use it. Rotating on restart matches the app's existing session-reset behavior.
app.secret_key = secrets.token_hex(32)

# Credentials for HTTP Basic auth (plaintext in .env, host-only, chmod 600).
# Fail closed: refuse to start if either is missing or empty — never run unauthenticated.
AUTH_USERNAME = os.environ.get('AUTH_USERNAME', '')
AUTH_PASSWORD = os.environ.get('AUTH_PASSWORD', '')
if not AUTH_USERNAME or not AUTH_PASSWORD:
    raise SystemExit(
        'FATAL: AUTH_USERNAME and AUTH_PASSWORD must be set (and non-empty) in .env. '
        'Refusing to start without authentication credentials.'
    )

# Cap request body size to bound upload DoS (50 MB)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Data directory for databases
DATA_DIR = Path(__file__).resolve().parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
DEFAULT_DB = 'trading_journal.db'

# Screenshots storage
SCREENSHOTS_DIR = DATA_DIR / 'screenshots'
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

# Initialize default database if it doesn't exist
default_db_path = DATA_DIR / DEFAULT_DB
if not default_db_path.exists():
    temp_db = DatabaseManager(str(default_db_path))
    temp_db.initialize_database()
    app.logger.info(f"Created default database: {default_db_path}")

# Session initialization marker - unique per app restart
APP_START_TIME = datetime.now().isoformat()

@app.before_request
def require_auth_and_set_nonce():
    """Generate a per-request CSP nonce, then enforce HTTP Basic auth.

    Registered before ensure_default_database so unauthorized requests are
    rejected before any database work happens.
    """
    # Per-request nonce consumed by inline <script> tags and the CSP header.
    g.csp_nonce = secrets.token_urlsafe(16)

    auth = request.authorization
    if not (auth and auth.type == 'basic'
            and hmac.compare_digest(auth.username or '', AUTH_USERNAME)
            and hmac.compare_digest(auth.password or '', AUTH_PASSWORD)):
        resp = make_response('Authentication required', 401)
        resp.headers['WWW-Authenticate'] = 'Basic realm="OneJournal"'
        return resp

@app.before_request
def ensure_default_database():
    """
    CRITICAL: Ensure every NEW session starts with trading_journal.db
    Session-based database switching is allowed ONLY within a single session
    """
    # Check if this is a new session (first request after app restart or new browser session)
    if 'app_start_time' not in session or session.get('app_start_time') != APP_START_TIME:
        # This is a new session - FORCE default database
        session['current_db'] = DEFAULT_DB
        session['app_start_time'] = APP_START_TIME
        session.permanent = False  # Session expires when browser closes

    # Ensure default database exists
    default_db_path = DATA_DIR / DEFAULT_DB
    if not default_db_path.exists():
        temp_db = DatabaseManager(str(default_db_path))
        temp_db.initialize_database()

def get_db():
    """Get database manager for current session — cached per request via Flask g"""
    if 'db' not in g:
        db_name = session.get('current_db', DEFAULT_DB)
        db_path = DATA_DIR / db_name
        if not db_path.exists():
            session['current_db'] = DEFAULT_DB
            db_path = DATA_DIR / DEFAULT_DB
        g.db = DatabaseManager(str(db_path))
    return g.db

@app.context_processor
def inject_csp_nonce():
    """Expose the per-request CSP nonce to every template (as csp_nonce)."""
    return {'csp_nonce': g.get('csp_nonce', '')}

# Origins permitted to read responses (this device only)
ALLOWED_ORIGINS = {'http://localhost:8090', 'http://127.0.0.1:8090'}

@app.after_request
def set_security_headers(response):
    """Attach security/CSP/CORS headers.

    The Server banner is scrubbed at the request-handler layer (see app.run);
    the dev server emits Server itself, so setting it here would only duplicate.
    """
    # Browser hardening headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Permissions-Policy'] = (
        'geolocation=(), microphone=(), camera=(), usb=(), payment=()'
    )
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'

    # Strict, nonce-based Content-Security-Policy.
    # style-src keeps 'unsafe-inline' because Plotly injects inline styles at
    # runtime (revisited in FINAL CHECK 1).
    nonce = g.get('csp_nonce', '')
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )

    # CORS — reflect only this device's own origins.
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers.add('Vary', 'Origin')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'

    return response

# Helper functions for Indian formatting
def format_indian_number(num):
    """Format number in Indian numbering system (lakhs, crores)"""
    if num is None:
        return "0"

    num = float(num)
    is_negative = num < 0
    num = abs(num)

    # Format based on magnitude
    if num >= 10000000:  # 1 crore or more
        formatted = f"{num/10000000:.2f} Cr"
    elif num >= 100000:  # 1 lakh or more
        formatted = f"{num/100000:.2f} L"
    elif num >= 1000:  # 1 thousand or more
        formatted = f"{num/1000:.2f} K"
    else:
        # For smaller numbers, use Indian comma format
        s = f"{num:.2f}"
        parts = s.split('.')
        integer_part = parts[0]
        decimal_part = parts[1] if len(parts) > 1 else '00'

        # Add commas in Indian format (last 3, then every 2)
        if len(integer_part) > 3:
            last_three = integer_part[-3:]
            remaining = integer_part[:-3]
            # Add comma every 2 digits from right
            groups = []
            while remaining:
                groups.insert(0, remaining[-2:])
                remaining = remaining[:-2]
            formatted = ','.join(groups) + ',' + last_three + '.' + decimal_part
        else:
            formatted = integer_part + '.' + decimal_part

    return '-' + formatted if is_negative else formatted

def format_date_indian(date_str):
    """Convert YYYY-MM-DD to DD/MM/YYYY"""
    if not date_str:
        return ""
    try:
        # Handle both string and datetime objects
        if isinstance(date_str, str):
            dt = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            dt = date_str
        return dt.strftime('%d/%m/%Y')
    except (ValueError, TypeError, AttributeError):
        return date_str

def parse_indian_date(date_str):
    """Convert DD/MM/YYYY to YYYY-MM-DD for database"""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, '%d/%m/%Y')
        return dt.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return date_str

# Register template filters
app.jinja_env.filters['indian_number'] = format_indian_number
app.jinja_env.filters['indian_date'] = format_date_indian
app.jinja_env.filters['time_ago'] = lambda v: get_time_ago(v) if v else None

def alert_html(message, variant='info', title=None):
    """A message in the app's own alert style (templates/components/alert.html):
    variant is info, success, warning or danger. `message` is HTML, so escape
    any user-supplied text before putting it in."""
    return render_template_string(
        "{% from 'components/alert.html' import alert %}"
        "{{ alert(message, variant=variant, title=title) }}",
        message=message, variant=variant, title=title)

# Form parsing helpers — used in multiple route handlers
def get_float(key, default=None):
    val = request.form.get(key, '').strip()
    return float(val) if val else default

def get_int(key, default=None):
    val = request.form.get(key, '').strip()
    return int(val) if val else default

def get_str(key, default=None):
    val = request.form.get(key, '').strip()
    return val if val else default

def save_screenshot_file(file):
    """
    Validate and save an uploaded image file to SCREENSHOTS_DIR.
    Returns (filename, original_name) on success; raises ValueError on bad input.
    """
    original_name = file.filename or ''
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{ext}'. Allowed: jpg, jpeg, png, webp, gif")
    filename = f"{uuid.uuid4().hex}{ext}"
    file.save(str(SCREENSHOTS_DIR / filename))
    return filename, original_name


# Main routes
def get_time_ago(trade_date):
    """Calculate human-readable time ago string"""
    now = datetime.now()
    trade_dt = pd.to_datetime(trade_date)

    # Make timezone-naive for comparison
    if trade_dt.tzinfo is not None:
        trade_dt = trade_dt.replace(tzinfo=None)

    diff = now - trade_dt

    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days}d ago"
    elif seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks}w ago"
    else:
        months = int(seconds / 2592000)
        return f"{months}mo ago"

def generate_alerts(trades_df, metrics):
    """Generate alerts based on trading conditions"""
    alerts = []

    if trades_df.empty:
        return alerts

    # Check for high drawdown
    if metrics.max_drawdown_percent > 10:
        alerts.append({
            'type': 'danger',
            'message': f'High drawdown alert: {metrics.max_drawdown_percent:.1f}% - Consider reducing position sizes'
        })
    elif metrics.max_drawdown_percent > 5:
        alerts.append({
            'type': 'warning',
            'message': f'Moderate drawdown: {metrics.max_drawdown_percent:.1f}% - Monitor risk carefully'
        })

    # Check for consecutive losses
    if metrics.current_streak_type == 'Loss' and metrics.current_streak >= 3:
        alerts.append({
            'type': 'warning',
            'message': f'Current losing streak: {metrics.current_streak} trades - Consider taking a break'
        })

    # Check for new equity high (cumulative P&L in the order trades closed)
    trades_df_sorted = sort_by_close(trades_df)
    cumulative_pnl = trades_df_sorted['net_pnl'].cumsum()
    if len(cumulative_pnl) > 0 and cumulative_pnl.iloc[-1] == cumulative_pnl.max():
        alerts.append({
            'type': 'success',
            'message': 'New equity high achieved! Stay disciplined.'
        })

    # Check for low win rate
    if metrics.total_trades >= 10 and metrics.win_rate < 40:
        alerts.append({
            'type': 'warning',
            'message': f'Low win rate: {metrics.win_rate:.1f}% - Review your strategy'
        })

    # Check for negative expectancy
    if metrics.expectancy < 0:
        alerts.append({
            'type': 'danger',
            'message': f'Negative expectancy: ₹{metrics.expectancy:.2f} - Strategy needs improvement'
        })

    return alerts

def calculate_period_stats(trades_df, start_date):
    """Calculate statistics for a specific time period"""
    if trades_df.empty:
        return {
            'total_trades': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0
        }

    # Trades that closed in the period (when their P&L was realized)
    trades_df = trades_df.copy()
    closed = pd.to_datetime(trades_df['closed_date'])
    period_trades = trades_df[closed >= start_date]

    if period_trades.empty:
        return {
            'total_trades': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0
        }

    winning_trades = len(period_trades[period_trades['net_pnl'] > 0])
    total_trades = len(period_trades)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = period_trades['net_pnl'].sum()

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'total_pnl': total_pnl
    }

@app.route('/')
def dashboard():
    """Enhanced dashboard with comprehensive metrics"""

    # Get trades
    trades_df = get_db().get_all_trades()

    if not trades_df.empty:
        # Calculate comprehensive metrics using calculations module
        total_capital_deployed = get_db().get_total_capital_deployed()
        calc = PerformanceCalculator(trades_df, starting_balance=total_capital_deployed)
        metrics = calc.calculate_all_metrics()

        # Convert dataclass to dict for template
        stats = {
            # Primary metrics
            'total_trades': metrics.total_trades,
            'win_rate': metrics.win_rate,
            'profit_factor': metrics.profit_factor,
            'profit_factor_display': '∞' if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.2f}",
            'total_pnl': metrics.total_pnl,

            # Profitability metrics
            'gross_profit': metrics.gross_profit,
            'gross_loss': metrics.gross_loss,
            'average_win': metrics.average_win,
            'average_loss': metrics.average_loss,
            'largest_win': metrics.largest_win,
            'largest_loss': metrics.largest_loss,
            'win_loss_ratio': metrics.win_loss_ratio,
            'expectancy': metrics.expectancy,
            'average_trade': metrics.average_trade,

            # Risk metrics
            'average_r_multiple': metrics.average_r_multiple,
            'median_r_multiple': metrics.median_r_multiple,
            'max_drawdown_percent': metrics.max_drawdown_percent,
            'max_drawdown_amount': metrics.max_drawdown_amount,
            'sharpe_ratio': metrics.sharpe_ratio,
            'sortino_ratio': metrics.sortino_ratio,
            'calmar_ratio': metrics.calmar_ratio,
            'recovery_factor': metrics.recovery_factor,
            'kelly_criterion': metrics.kelly_criterion,
            # Clamp Kelly to [0, 0.25] for display (fractional Kelly convention); negative = no edge
            'kelly_percentage': max(0.0, min(metrics.kelly_criterion, 0.25)) * 100,
            'kelly_negative': metrics.kelly_criterion < 0,

            # Streak analysis
            'current_streak': metrics.current_streak,
            'current_streak_type': metrics.current_streak_type,
            'max_consecutive_wins': metrics.max_consecutive_wins,
            'max_consecutive_losses': metrics.max_consecutive_losses,

            # Time-based metrics
            'avg_intraday_duration_hours': metrics.avg_intraday_duration_hours,
            'avg_swing_duration_days': metrics.avg_swing_duration_days,
            'avg_trades_per_day': metrics.avg_trades_per_day,
            'avg_trades_per_week': metrics.avg_trades_per_week,
            'avg_trades_per_month': metrics.avg_trades_per_month,

            # Statistical metrics
            'std_deviation': metrics.standard_deviation,
            'standard_deviation': metrics.standard_deviation,
            'skewness': metrics.skewness,
            'kurtosis': metrics.kurtosis,

            # Calculated metrics
            'median_pnl': trades_df['net_pnl'].median() if not trades_df.empty else 0.0,
            'avg_daily_pnl': metrics.total_pnl / max(pd.to_datetime(trades_df['closed_date']).dt.date.nunique(), 1) if not trades_df.empty else 0.0,  # per day a trade closed
            'avg_roi_per_trade': (metrics.average_trade / total_capital_deployed * 100) if metrics.average_trade != 0 and total_capital_deployed > 0 else 0.0,

            # Counts
            'winning_trades': metrics.winning_trades,
            'losing_trades': metrics.losing_trades,
            'breakeven_trades': metrics.breakeven_trades,

            # Capital metrics
            'total_capital_deployed': total_capital_deployed
        }

        # Recent activity - the 10 most recently closed trades
        recent_df = sort_by_close(trades_df).iloc[::-1].head(10)
        recent_trades = []
        for _, trade in recent_df.iterrows():
            recent_trades.append({
                'trade_id': trade['trade_id'],
                'symbol': trade['symbol'],
                'strategy': trade['strategy'],
                'direction': trade['direction'],
                'net_pnl': trade['net_pnl'],
                'time_ago': get_time_ago(trade['closed_date'])
            })

        # Generate alerts
        alerts = generate_alerts(trades_df, metrics)

        # Calculate period statistics
        now = datetime.now()

        # This week (Monday to Sunday)
        week_start = now - pd.Timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_stats = calculate_period_stats(trades_df, week_start)

        # This month
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_stats = calculate_period_stats(trades_df, month_start)

        # This year
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        year_stats = calculate_period_stats(trades_df, year_start)

    else:
        # Empty state
        stats = {
            'total_trades': 0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'profit_factor_display': 0.0,
            'total_pnl': 0.0,
            'average_r_multiple': 0.0,
            'max_drawdown_percent': 0.0,
            'max_drawdown_amount': 0.0,
            'sharpe_ratio': 0.0,
            'expectancy': 0.0,
            'average_win': 0.0,
            'average_loss': 0.0,
            'largest_win': 0.0,
            'largest_loss': 0.0,
            'win_loss_ratio': 0.0,
            'current_streak': 0,
            'current_streak_type': 'None',
            'max_consecutive_wins': 0,
            'max_consecutive_losses': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'breakeven_trades': 0,
            'total_capital_deployed': 0.0
        }
        recent_trades = []
        alerts = []
        week_stats = {'total_trades': 0, 'win_rate': 0.0, 'total_pnl': 0.0}
        month_stats = {'total_trades': 0, 'win_rate': 0.0, 'total_pnl': 0.0}
        year_stats = {'total_trades': 0, 'win_rate': 0.0, 'total_pnl': 0.0}

    # Get open positions breakdown by trade style
    db = get_db()
    open_positions = db.get_open_positions()

    position_breakdown = {
        'Intraday': 0,
        'Swing': 0,
        'Investment': 0
    }

    for position in open_positions:
        pos_type = position.get('position_type')
        if pos_type in position_breakdown:
            position_breakdown[pos_type] += 1

    # ── Inline chart data (avoids 6 AJAX round-trips after page load) ──────────
    if not trades_df.empty:
        _df = trades_df.copy()
        _df['date'] = pd.to_datetime(_df['date'])
        # In the order trades closed, when their P&L was realized
        _df_sorted = sort_by_close(_df)

        # Equity curve
        _df_sorted['equity'] = total_capital_deployed + _df_sorted['net_pnl'].cumsum()
        _cash = get_db().get_cash_transactions()
        _deposits = [t['date'] for t in _cash if t['type'] in ('deposit', 'dividend', 'interest')]
        _withdrawals = [t['date'] for t in _cash if t['type'] in ('withdrawal', 'fees')]

        # Monthly performance (last 12 months)
        _today = pd.Timestamp.now()
        _end_mo = _today + pd.offsets.MonthEnd(0)
        _start_mo = _end_mo - pd.DateOffset(months=11)
        _12mo = [str(p) for p in pd.period_range(start=_start_mo, end=_end_mo, freq='M')]
        _df_sorted['month'] = pd.to_datetime(_df_sorted['closed_date']).dt.to_period('M').astype(str)
        _mo_pnl = dict(zip(*(_df_sorted.groupby('month')['net_pnl'].sum().reset_index()
                             .values.T.tolist())) if not _df_sorted.empty else [{}, {}])
        _mo_labels = [datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in _12mo]

        # Strategy performance
        _strat_df = _df[_df['strategy'].notna()]
        _strat_grp = _strat_df.groupby('strategy').agg(pnl=('net_pnl', 'sum'), trades=('net_pnl', 'count')).reset_index()

        # Drawdown
        _eq_calc = EquityCurveCalculator(trades_df, starting_balance=total_capital_deployed)
        _eq_curve = _eq_calc.calculate_equity_curve()
        _dd_dates = _eq_curve['date'].dt.strftime('%Y-%m-%d').tolist() if not _eq_curve.empty else []
        _dd_pct = _eq_curve['drawdown_percent'].tolist() if not _eq_curve.empty else []

        # R-multiple distribution
        _r = _df['r_multiple'].dropna()
        if not _r.empty:
            _bins = np.arange(-5, 10, 0.5)
            _counts, _edges = np.histogram(_r, bins=_bins)
            _centers = (((_edges[:-1] + _edges[1:]) / 2)).tolist()
            _r_counts = _counts.tolist()
        else:
            _centers, _r_counts = [], []

        # Win/loss pie
        _wins = int((_df['net_pnl'] > 0).sum())
        _losses = int((_df['net_pnl'] < 0).sum())
        _be = int((_df['net_pnl'] == 0).sum())

        chart_data = {
            'equity': {
                'dates': [str(d) for d in _df_sorted['closed_date'].tolist()],
                'equity': _df_sorted['equity'].tolist(),
                'starting_balance': total_capital_deployed,
                'cash_deposits': _deposits,
                'cash_withdrawals': _withdrawals,
            },
            'monthly': {
                'months': _mo_labels,
                'pnl': [_mo_pnl.get(m, 0) for m in _12mo],
            },
            'strategy': {
                'strategies': _strat_grp['strategy'].tolist(),
                'pnl': _strat_grp['pnl'].tolist(),
                'trades': _strat_grp['trades'].tolist(),
            },
            'drawdown': {
                'dates': _dd_dates,
                'drawdown_pct': _dd_pct,
            },
            'r_multiple': {
                'r_multiples': _centers,
                'counts': _r_counts,
            },
            'win_loss': {
                'labels': ['Wins', 'Losses', 'Breakeven'],
                'values': [_wins, _losses, _be],
                'colors': ['#28a745', '#dc3545', '#6c757d'],
            },
        }
    else:
        chart_data = {}

    return render_template('dashboard.html',
                         stats=stats,
                         recent_trades=recent_trades,
                         alerts=alerts,
                         week_stats=week_stats,
                         month_stats=month_stats,
                         year_stats=year_stats,
                         position_breakdown=position_breakdown,
                         chart_data=chart_data)

@app.route('/api/charts/equity-curve')
def equity_curve_data():
    """Return equity curve chart data as JSON"""
    import json
    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'dates': [], 'equity': [], 'cash_deposits': [], 'cash_withdrawals': []})

    # In the order trades closed, when their P&L was realized
    trades_df = sort_by_close(trades_df)

    # Calculate cumulative P&L
    total_capital_deployed = get_db().get_total_capital_deployed()
    trades_df['cumulative_pnl'] = trades_df['net_pnl'].cumsum()
    trades_df['equity'] = total_capital_deployed + trades_df['cumulative_pnl']

    # Get cash transactions for vertical lines
    cash_transactions = get_db().get_cash_transactions()
    deposits = []
    withdrawals = []

    for txn in cash_transactions:
        if txn['type'] in ['deposit', 'dividend', 'interest']:
            deposits.append(txn['date'])
        elif txn['type'] in ['withdrawal', 'fees']:
            withdrawals.append(txn['date'])

    return jsonify({
        'dates': trades_df['closed_date'].dt.strftime('%Y-%m-%d').tolist(),
        'equity': trades_df['equity'].tolist(),
        'starting_balance': total_capital_deployed,
        'cash_deposits': deposits,
        'cash_withdrawals': withdrawals
    })

@app.route('/api/charts/monthly-performance')
def monthly_performance_data():
    """Return monthly performance data for last 12 months as JSON"""

    trades_df = get_db().get_all_trades()

    # Generate last 12 months using pandas date_range
    today = pd.Timestamp.now()
    end_of_current_month = today + pd.offsets.MonthEnd(0)
    start_of_period = end_of_current_month - pd.DateOffset(months=11)

    # Generate monthly periods for last 12 months
    last_12_months_periods = pd.period_range(start=start_of_period, end=end_of_current_month, freq='M')
    last_12_months = [str(p) for p in last_12_months_periods]

    if trades_df.empty:
        # Return last 12 months with 0 P&L
        month_labels = [datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in last_12_months]
        return jsonify({'months': month_labels, 'pnl': [0] * 12})

    # Month each trade closed in (when its P&L was realized)
    trades_df['month'] = pd.to_datetime(trades_df['closed_date']).dt.to_period('M').astype(str)

    # Group by month
    monthly = trades_df.groupby('month')['net_pnl'].sum().reset_index()

    # Create complete dataset for last 12 months
    pnl_dict = dict(zip(monthly['month'], monthly['net_pnl']))
    pnls = [pnl_dict.get(m, 0) for m in last_12_months]
    month_labels = [datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in last_12_months]

    return jsonify({
        'months': month_labels,
        'pnl': pnls
    })

@app.route('/api/charts/strategy-performance')
def strategy_performance_data():
    """Return strategy performance data as JSON"""
    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'strategies': [], 'pnl': [], 'trades': []})

    # Filter out null strategies
    trades_df = trades_df[trades_df['strategy'].notna()]

    # Group by strategy
    strategy_stats = trades_df.groupby('strategy').agg({
        'net_pnl': 'sum'
    }).reset_index()

    # Count trades per strategy separately
    trade_counts = trades_df.groupby('strategy').size().reset_index(name='trades')

    # Merge together
    strategy_stats = strategy_stats.merge(trade_counts, on='strategy')
    strategy_stats.columns = ['strategy', 'pnl', 'trades']

    return jsonify({
        'strategies': strategy_stats['strategy'].tolist(),
        'pnl': strategy_stats['pnl'].tolist(),
        'trades': strategy_stats['trades'].tolist()
    })

@app.route('/api/charts/drawdown')
def drawdown_data():
    """Return drawdown underwater curve data as JSON"""

    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'dates': [], 'drawdown_pct': [], 'drawdown_amount': []})

    # Calculate equity curve and drawdown (already includes drawdown calculations)
    total_capital_deployed = get_db().get_total_capital_deployed()
    equity_calc = EquityCurveCalculator(trades_df, starting_balance=total_capital_deployed)
    equity_curve = equity_calc.calculate_equity_curve()

    if equity_curve.empty:
        return jsonify({'dates': [], 'drawdown_pct': [], 'drawdown_amount': []})

    # EquityCurveCalculator already calculates drawdown_percent and drawdown_amount
    # Convert date to string format for JSON
    dates = equity_curve['date'].dt.strftime('%Y-%m-%d').tolist() if 'date' in equity_curve else []

    return jsonify({
        'dates': dates,
        'drawdown_pct': equity_curve['drawdown_percent'].tolist(),
        'drawdown_amount': equity_curve['drawdown_amount'].tolist()
    })

@app.route('/api/charts/r-multiple-distribution')
def r_multiple_distribution_data():
    """Return R-multiple distribution data as JSON"""
    trades_df = get_db().get_all_trades()

    if trades_df.empty or 'r_multiple' not in trades_df.columns:
        return jsonify({'r_multiples': [], 'counts': []})

    # Filter out NaN and create bins
    r_multiples = trades_df['r_multiple'].dropna()

    if r_multiples.empty:
        return jsonify({'r_multiples': [], 'counts': []})

    # Create histogram bins
    bins = np.arange(-5, 10, 0.5)  # -5R to +10R in 0.5R increments
    counts, bin_edges = np.histogram(r_multiples, bins=bins)

    # Calculate bin centers for display
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    return jsonify({
        'r_multiples': bin_centers.tolist(),
        'counts': counts.tolist(),
        'bin_edges': bin_edges.tolist()
    })

@app.route('/api/charts/win-loss-distribution')
def win_loss_distribution_data():
    """Return win/loss distribution data as JSON"""
    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'labels': [], 'values': [], 'colors': []})

    # Count wins, losses, breakeven
    wins = (trades_df['net_pnl'] > 0).sum()
    losses = (trades_df['net_pnl'] < 0).sum()
    breakeven = (trades_df['net_pnl'] == 0).sum()

    return jsonify({
        'labels': ['Wins', 'Losses', 'Breakeven'],
        'values': [int(wins), int(losses), int(breakeven)],
        'colors': ['#28a745', '#dc3545', '#6c757d']
    })

@app.route('/manual-entry')
def manual_entry():
    """Manual trade entry page"""
    strategies = get_db().get_active_strategies()
    today = date.today().strftime('%d/%m/%Y')
    return render_template('manual_entry.html', strategies=strategies, today=today)

@app.route('/positions')
def positions():
    """Positions page - view open and closed positions"""
    db = get_db()
    open_positions = db.get_open_positions()
    closed_positions = db.get_closed_positions()

    return render_template('positions.html',
                         open_positions=open_positions,
                         closed_positions=closed_positions,
                         open_count=len(open_positions),
                         closed_count=len(closed_positions))

@app.route('/positions/<int:position_id>/details')
def position_details(position_id):
    """HTMX endpoint - load position details with all executions"""
    db = get_db()
    position = db.get_position_by_id(position_id)
    executions = db.get_executions_by_position(position_id)

    if not position:
        return alert_html('Position not found', 'danger')

    screenshots = db.get_screenshots_by_position(position_id)
    return render_template('partials/position_details.html',
                         position=position,
                         trades=executions,
                         screenshots=screenshots)

@app.route('/positions/update-price', methods=['POST'])
def update_position_price():
    """HTMX endpoint - update market price and recalculate unrealized P&L"""
    try:
        position_id = int(request.form.get('position_id'))
        market_price = float(request.form.get('market_price'))

        db = get_db()
        db.update_position_market_price(position_id, market_price)
        position = db.get_position_by_id(position_id)

        return render_template('partials/position_pnl.html', position=position)
    except Exception as e:
        return f'<span class="text-loss">Error: {escape(str(e))}</span>'

@app.route('/positions/update-type', methods=['POST'])
def update_position_type():
    """Update position type (Swing <-> Investment only, not Intraday)"""
    try:
        position_id = int(request.form.get('position_id'))
        position_type = request.form.get('position_type')

        # Validation: Only allow Swing or Investment
        if position_type not in ['Swing', 'Investment']:
            return '<span class="text-loss">Error: Only Swing and Investment types are allowed</span>', 400

        # Additional check: Don't allow changing Intraday positions
        db = get_db()
        position = db.get_position_by_id(position_id)
        if position and position['position_type'] == 'Intraday':
            return '<span class="text-loss">Error: Cannot change Intraday position type</span>', 400

        # Update position type
        with db.get_connection() as conn:
            conn.execute("""
                UPDATE positions SET position_type = ? WHERE position_id = ?
            """, (position_type, position_id))

        return '', 200
    except Exception as e:
        return f'<span class="text-loss">Error: {escape(str(e))}</span>', 500

@app.route('/positions/<int:position_id>/manage-form')
def position_manage_form(position_id):
    """Load manage position form"""
    db = get_db()
    position = db.get_position_by_id(position_id)

    if not position:
        return alert_html('Position not found', 'danger')

    if position['status'] != 'open':
        return alert_html('Can only manage open positions', 'warning')

    today = datetime.now().strftime('%d/%m/%Y')

    return render_template('partials/manage_position_form.html',
                         position=position,
                         today=today)

@app.route('/positions/<int:position_id>/manage', methods=['POST'])
def manage_position(position_id):
    """Handle position management (scale in, scale out, exit all)"""
    try:
        db = get_db()
        position = db.get_position_by_id(position_id)

        if not position:
            return alert_html('Position not found', 'danger')

        if position['status'] != 'open':
            return alert_html('Position is already closed', 'danger')

        # Extract form data
        action = request.form.get('action')
        date_str = request.form.get('date')
        time_str = request.form.get('time', '').strip() or None
        price = float(request.form.get('price'))
        quantity = float(request.form.get('quantity'))
        notes = request.form.get('notes', '').strip() or None

        if quantity <= 0:
            return alert_html('Quantity must be greater than zero.', 'danger')
        if price <= 0:
            return alert_html('Price must be greater than zero.', 'danger')

        # Parse date (DD/MM/YYYY -> YYYY-MM-DD)
        date_obj = datetime.strptime(date_str, '%d/%m/%Y')
        date_formatted = date_obj.strftime('%Y-%m-%d')

        # Validate action
        if action not in ['scale_in', 'scale_out', 'exit_all']:
            return alert_html('Invalid action', 'danger')

        # Handle each action type
        if action == 'scale_in':
            leverage_str = request.form.get('leverage', '').strip()
            leverage = float(leverage_str) if leverage_str else position.get('avg_leverage', 1.0)
            charges = float(request.form.get('charges', 0))

            required_margin = (quantity * price) / leverage
            current_cash = db.get_portfolio_summary().cash_balance
            if current_cash < required_margin:
                return alert_html(
                    f'Insufficient margin: need ₹{required_margin:,.2f}, have ₹{current_cash:,.2f}',
                    'danger')

            entry_exec = Execution(
                position_id=position_id,
                date=date_formatted,
                time=time_str,
                type='entry',
                price=price,
                quantity=quantity,
                charges=charges,
                leverage=leverage,
                notes=notes,
            )
            success, message, exec_id = db.insert_execution(entry_exec)
            if not success:
                return alert_html(escape(message), 'danger')

            db.apply_entry_cash(price, quantity, leverage, charges)
            db.recalculate_position(position_id)

            # Save screenshots
            if exec_id:
                for f in request.files.getlist('screenshots'):
                    if f and f.filename:
                        try:
                            fname, orig = save_screenshot_file(f)
                            db.insert_screenshot(exec_id, fname, orig)
                        except (ValueError, OSError) as e:
                            app.logger.warning(f"Screenshot skipped: {e}")

            return alert_html(f'Scaled in {quantity} units at ₹{price:.2f}', 'success')

        elif action == 'scale_out':
            charges = float(request.form.get('exit_charges', 0))
            current_qty = position.get('current_quantity', position.get('current_qty', 0))

            if quantity > current_qty or quantity <= 0:
                return alert_html(f'Cannot exit {quantity} units — only {current_qty} available.', 'danger')

            exit_exec = Execution(
                position_id=position_id,
                date=date_formatted,
                time=time_str,
                type='exit',
                price=price,
                quantity=quantity,
                charges=charges,
                notes=notes,
            )
            success, message, exec_id = db.insert_execution(exit_exec)
            if not success:
                return alert_html(escape(message), 'danger')

            db.apply_exit_cash(position_id)
            metrics = db.recalculate_position(position_id)

            # Save screenshots
            if exec_id:
                for f in request.files.getlist('screenshots'):
                    if f and f.filename:
                        try:
                            fname, orig = save_screenshot_file(f)
                            db.insert_screenshot(exec_id, fname, orig)
                        except (ValueError, OSError) as e:
                            app.logger.warning(f"Screenshot skipped: {e}")

            remaining = metrics.get('current_qty', 0)
            status_msg = ("Position fully closed" if metrics.get('status') == 'closed'
                          else f"Partial exit - {remaining} units remaining")

            return alert_html(f'Exited {quantity} units at ₹{price:.2f}. {status_msg}.', 'success')

        elif action == 'exit_all':
            charges = float(request.form.get('exit_charges', 0))
            full_quantity = position.get('current_quantity', position.get('current_qty', 0))

            if not full_quantity or full_quantity <= 0:
                return alert_html('Position has no remaining quantity to exit.', 'danger')

            exit_exec = Execution(
                position_id=position_id,
                date=date_formatted,
                time=time_str,
                type='exit',
                price=price,
                quantity=full_quantity,
                charges=charges,
                notes=notes,
            )
            success, message, exec_id = db.insert_execution(exit_exec)
            if not success:
                return alert_html(escape(message), 'danger')

            db.apply_exit_cash(position_id)
            db.recalculate_position(position_id)

            # Save screenshots
            if exec_id:
                for f in request.files.getlist('screenshots'):
                    if f and f.filename:
                        try:
                            fname, orig = save_screenshot_file(f)
                            db.insert_screenshot(exec_id, fname, orig)
                        except (ValueError, OSError) as e:
                            app.logger.warning(f"Screenshot skipped: {e}")

            return alert_html(f'Position fully closed: {full_quantity} units at ₹{price:.2f}', 'success')

    except ValueError as e:
        return alert_html(f'Invalid input: {escape(str(e))}', 'danger')
    except Exception as e:
        return alert_html(f'Error: {escape(str(e))}', 'danger')

@app.route('/portfolio')
def portfolio():
    """Portfolio overview page"""
    db = get_db()
    portfolio = db.get_portfolio_summary()
    cash_transactions = db.get_cash_transactions()

    return render_template('portfolio.html',
                         portfolio=portfolio,
                         cash_transactions=cash_transactions)

@app.route('/portfolio/cash-transaction', methods=['POST'])
def add_cash_transaction():
    """Add deposit/withdrawal/dividend"""
    try:
        txn_type = request.form.get('type')
        amount = float(request.form.get('amount'))
        date = parse_indian_date(request.form.get('date'))
        description = request.form.get('description')

        # Adjust sign based on type
        if txn_type in ['withdrawal', 'fees']:
            amount = -abs(amount)
        else:
            amount = abs(amount)

        # Validate withdrawal won't make capital negative
        if txn_type in ['withdrawal', 'fees']:
            db = get_db()
            current_capital = db.get_total_capital_deployed()
            if current_capital + amount < 0:  # amount is already negative
                return alert_html(
                    'This withdrawal would result in negative total capital. '
                    f'Current capital: ₹{current_capital:.2f}, Withdrawal: ₹{abs(amount):.2f}',
                    'danger', title='Insufficient capital')

        # Create transaction
        txn = CashTransaction(
            transaction_id=None,
            date=date,
            time=None,
            type=txn_type,
            amount=amount,
            description=description,
            created_at=None
        )

        db = get_db()
        success, message, txn_id = db.insert_cash_transaction(txn)

        if success:
            # Update cash balance
            db.update_cash_balance(amount)

            return alert_html(f'Transaction recorded: ₹{amount}', 'success', title='Success')
        else:
            return alert_html(escape(message), 'danger', title='Error')

    except Exception as e:
        return alert_html(escape(str(e)), 'danger', title='Error')

@app.route('/manage-strategies')
def manage_strategies():
    """Manage strategies page"""
    strategies = get_db().get_all_strategies()
    return render_template('manage_strategies.html', strategies=strategies)

@app.route('/all-trades')
def all_trades():
    """All trades page with filtering and management"""
    strategies = get_db().get_active_strategies()
    return render_template('all_trades.html', strategies=strategies)

@app.route('/analytics')
def analytics():
    """Advanced analytics page"""

    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return render_template('analytics.html',
                             has_data=False,
                             time_analysis={},
                             strategy_analysis={},
                             market_analysis={},
                             risk_analysis={},
                             trade_style_analysis={})

    # Perform various analyses
    time_analyzer = TimeBasedAnalyzer(trades_df)
    strategy_analyzer = StrategyAnalyzer(trades_df)
    market_analyzer = MarketAnalyzer(trades_df)

    # Get time-based analysis
    daily_df = time_analyzer.analyze_by_day_of_week()
    hourly_df = time_analyzer.analyze_by_hour()
    monthly_df = time_analyzer.analyze_by_month()

    time_analysis = {}

    # Daily analysis
    if not daily_df.empty:
        time_analysis['daily'] = {row['day_of_week']: {
            'total_trades': row['Total_Trades'],
            'total_pnl': row['Net_PnL'],
            'avg_pnl': row['Avg_PnL'],
            'win_rate': row['Win_Rate'],
            'avg_r_multiple': row['Avg_R_Multiple']
        } for _, row in daily_df.iterrows()}
    else:
        time_analysis['daily'] = {}

    # Hourly analysis
    if not hourly_df.empty:
        time_analysis['hourly'] = {int(row['hour']): {
            'total_trades': row['Total_Trades'],
            'total_pnl': row['Net_PnL'],
            'avg_pnl': row['Avg_PnL'],
            'win_rate': row['Win_Rate']
        } for _, row in hourly_df.iterrows()}
    else:
        time_analysis['hourly'] = {}

    # Monthly analysis (for heatmap)
    if not monthly_df.empty:
        time_analysis['monthly'] = {row['year_month']: {
            'total_trades': row['Total_Trades'],
            'total_pnl': row['Net_PnL'],
            'avg_pnl': row['Avg_PnL_per_Trade'],
            'win_rate': row['Win_Rate']
        } for _, row in monthly_df.iterrows()}
    else:
        time_analysis['monthly'] = {}

    # Current week analysis
    current_week_df = time_analyzer.analyze_current_week()
    if not current_week_df.empty:
        time_analysis['current_week'] = {row['day_of_week']: {
            'total_trades': row['Total_Trades'],
            'total_pnl': row['Net_PnL'],
            'avg_pnl': row['Avg_PnL'],
            'win_rate': row['Win_Rate'],
            'avg_r_multiple': row['Avg_R_Multiple']
        } for _, row in current_week_df.iterrows()}
    else:
        time_analysis['current_week'] = {}

    # Get strategy analysis
    strategy_df = strategy_analyzer.analyze_strategy_performance()
    if not strategy_df.empty:
        strategy_analysis = {row['strategy']: {
            'total_trades': int(row['Total_Trades']),
            'total_pnl': float(row['Net_PnL']),
            'avg_pnl': float(row['Avg_PnL']),
            'win_rate': float(row['Win_Rate']),
            'profit_factor': float(row['Profit_Factor']),
            'profit_factor_display': '∞' if math.isinf(float(row['Profit_Factor'])) else f"{float(row['Profit_Factor']):.2f}",
            'avg_r_multiple': float(row['Avg_R_Multiple']),
            'expectancy': float(row.get('Expectancy', 0))
        } for _, row in strategy_df.iterrows()}
    else:
        strategy_analysis = {}

    # Get market analysis
    market_type_df = market_analyzer.analyze_market_performance()  # Spot, Options, Futures
    market_cond_df = market_analyzer.analyze_by_market_conditions()  # Bullish, Bearish, Neutral, Volatile
    direction_df = market_analyzer.analyze_direction_performance()  # Long, Short

    # Market conditions analysis (Bullish, Bearish, Neutral, Volatile)
    market_by_cond = {}
    if not market_cond_df.empty:
        # DataFrame already has reset_index() called, first column is 'market_conditions'
        market_by_cond = {str(row['market_conditions']): {
            'total_trades': int(row['Total_Trades']),
            'total_pnl': float(row['Net_PnL']),
            'win_rate': float(row['Win_Rate'])
        } for _, row in market_cond_df.iterrows() if pd.notna(row['market_conditions'])}

    # Market type analysis (Spot, Options, Futures)
    market_by_type = {}
    if not market_type_df.empty:
        # DataFrame already has reset_index() called, first column is 'market'
        market_by_type = {str(row['market']): {
            'total_trades': int(row['Total_Trades']),
            'total_pnl': float(row['Net_PnL']),
            'win_rate': float(row['Win_Rate'])
        } for _, row in market_type_df.iterrows() if pd.notna(row['market'])}

    # Direction analysis (Long, Short)
    market_by_dir = {}
    if not direction_df.empty:
        # DataFrame already has reset_index() called, first column is 'direction'
        market_by_dir = {str(row['direction']): {
            'total_trades': int(row['Total_Trades']),
            'total_pnl': float(row['Net_PnL']),
            'win_rate': float(row['Win_Rate'])
        } for _, row in direction_df.iterrows() if pd.notna(row['direction'])}

    market_analysis = {
        'by_condition': market_by_cond,
        'by_market_type': market_by_type,
        'by_direction': market_by_dir
    }

    # Get risk analysis using PerformanceCalculator
    total_capital_deployed = get_db().get_total_capital_deployed()
    calc = PerformanceCalculator(trades_df, starting_balance=total_capital_deployed)
    metrics = calc.calculate_all_metrics()
    # Value at risk (95%): the loss 1 trade in 20 reaches or exceeds; 0 when
    # even the worst 5% of trades are profitable
    var_95 = RiskAnalyzer(trades_df).calculate_var()['var_95']
    risk_analysis = {
        'max_consecutive_losses': metrics.max_consecutive_losses,
        'max_drawdown_percent': metrics.max_drawdown_percent,
        'max_drawdown_amount': metrics.max_drawdown_amount,
        'value_at_risk': max(0.0, -float(var_95)),
        'kelly_percentage': max(0.0, min(metrics.kelly_criterion, 0.25)) * 100,
        'kelly_negative': metrics.kelly_criterion < 0,
    }

    stats = {
        'total_trades': metrics.total_trades,
        'win_rate': metrics.win_rate,
        'profit_factor': metrics.profit_factor,
        'profit_factor_display': '∞' if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.2f}",
        'total_pnl': metrics.total_pnl,
        'gross_profit': metrics.gross_profit,
        'gross_loss': metrics.gross_loss,
        'average_win': metrics.average_win,
        'average_loss': metrics.average_loss,
        'largest_win': metrics.largest_win,
        'largest_loss': metrics.largest_loss,
        'win_loss_ratio': metrics.win_loss_ratio,
        'expectancy': metrics.expectancy,
        'average_trade': metrics.average_trade,
        'average_r_multiple': metrics.average_r_multiple,
        'median_r_multiple': metrics.median_r_multiple,
        'max_drawdown_percent': metrics.max_drawdown_percent,
        'max_drawdown_amount': metrics.max_drawdown_amount,
        'sharpe_ratio': metrics.sharpe_ratio,
        'sortino_ratio': metrics.sortino_ratio,
        'calmar_ratio': metrics.calmar_ratio,
        'recovery_factor': metrics.recovery_factor,
        'kelly_criterion': metrics.kelly_criterion,
        'kelly_percentage': max(0.0, min(metrics.kelly_criterion, 0.25)) * 100,
        'kelly_negative': metrics.kelly_criterion < 0,
        'current_streak': metrics.current_streak,
        'current_streak_type': metrics.current_streak_type,
        'max_consecutive_wins': metrics.max_consecutive_wins,
        'max_consecutive_losses': metrics.max_consecutive_losses,
        'avg_intraday_duration_hours': metrics.avg_intraday_duration_hours,
        'avg_swing_duration_days': metrics.avg_swing_duration_days,
        'avg_trades_per_day': metrics.avg_trades_per_day,
        'avg_trades_per_week': metrics.avg_trades_per_week,
        'avg_trades_per_month': metrics.avg_trades_per_month,
        'std_deviation': metrics.standard_deviation,
        'standard_deviation': metrics.standard_deviation,
        'skewness': metrics.skewness,
        'kurtosis': metrics.kurtosis,
        'median_pnl': trades_df['net_pnl'].median() if not trades_df.empty else 0.0,
        'avg_daily_pnl': metrics.total_pnl / max(pd.to_datetime(trades_df['closed_date']).dt.date.nunique(), 1) if not trades_df.empty else 0.0,  # per day a trade closed
        'avg_roi_per_trade': (metrics.average_trade / total_capital_deployed * 100) if metrics.average_trade != 0 and total_capital_deployed > 0 else 0.0,
        'winning_trades': metrics.winning_trades,
        'losing_trades': metrics.losing_trades,
        'breakeven_trades': metrics.breakeven_trades,
        'total_capital_deployed': total_capital_deployed,
    }

    # Get trade style analysis
    trade_style_analyzer = TradeStyleAnalyzer(trades_df)
    trade_style_df = trade_style_analyzer.analyze_by_trade_style()

    if not trade_style_df.empty:
        trade_style_analysis = {row['Style']: {
            'total_trades': int(row['Total_Trades']),
            'win_rate': float(row['Win_Rate']),
            'total_pnl': float(row['Total_PnL']),
            'profit_factor': float(row['Profit_Factor']),
            'profit_factor_display': '∞' if math.isinf(float(row['Profit_Factor'])) else f"{float(row['Profit_Factor']):.2f}",
            'avg_win': float(row['Avg_Win']),
            'avg_loss': float(row['Avg_Loss']),
            'avg_trade': float(row['Avg_Trade']),
            'avg_duration': float(row['Avg_Duration'])
        } for _, row in trade_style_df.iterrows()}
    else:
        trade_style_analysis = {}

    return render_template('analytics.html',
                         has_data=True,
                         stats=stats,
                         time_analysis=time_analysis,
                         strategy_analysis=strategy_analysis,
                         market_analysis=market_analysis,
                         risk_analysis=risk_analysis,
                         trade_style_analysis=trade_style_analysis)

@app.route('/compare')
def compare():
    """Strategy comparison page"""

    db = get_db()

    # Get all available strategies
    available_strategies = db.get_strategies_list()

    # Get selected strategies from query parameters
    selected_strategies = request.args.getlist('strategy')

    # Get filters from query parameters (Enhanced: All filters)
    filters = {}
    if request.args.get('trade_style'):
        filters['trade_style'] = request.args.get('trade_style')
    if request.args.get('day_of_week'):
        filters['day_of_week'] = int(request.args.get('day_of_week'))
    if request.args.get('hour'):
        filters['hour'] = int(request.args.get('hour'))
    if request.args.get('market_condition'):
        filters['market_condition'] = request.args.get('market_condition')
    if request.args.get('market'):
        filters['market'] = request.args.get('market')
    if request.args.get('setup_type'):
        filters['setup_type'] = request.args.get('setup_type')
    if request.args.get('tags'):
        filters['tags'] = request.args.get('tags')
    if request.args.get('date_from'):
        filters['date_from'] = request.args.get('date_from')
    if request.args.get('date_to'):
        filters['date_to'] = request.args.get('date_to')

    # Validation: Need 2-5 strategies for comparison
    if len(selected_strategies) < 2:
        return render_template('compare.html',
                             available_strategies=available_strategies,
                             selected_strategies=[],
                             comparison_data={},
                             filters=filters,
                             error_message='Please select at least 2 strategies to compare' if selected_strategies else None)

    if len(selected_strategies) > 5:
        return render_template('compare.html',
                             available_strategies=available_strategies,
                             selected_strategies=selected_strategies[:5],
                             comparison_data={},
                             filters=filters,
                             error_message='Maximum 5 strategies can be compared at once')

    # Fetch filtered trades once — then split by strategy in Python (avoids N DB reads)
    all_filtered_df = db.get_filtered_trades(filters=filters)
    starting_balance = db.get_total_capital_deployed()

    comparison_data = {}
    total_trades_count = 0

    for strategy in selected_strategies:
        trades_df = (all_filtered_df[all_filtered_df['strategy'] == strategy]
                     if not all_filtered_df.empty else all_filtered_df)

        if trades_df.empty:
            comparison_data[strategy] = None
        else:
            calculator = PerformanceCalculator(trades_df, starting_balance=starting_balance)
            metrics = calculator.calculate_all_metrics()

            comparison_data[strategy] = {
                'total_trades': metrics.total_trades,
                'win_rate': metrics.win_rate,
                'profit_factor': metrics.profit_factor,
                'total_pnl': metrics.total_pnl,
                'avg_win': metrics.average_win,
                'avg_loss': abs(metrics.average_loss),
                'expectancy': metrics.expectancy,
                'sharpe_ratio': metrics.sharpe_ratio,
                'max_drawdown_percent': metrics.max_drawdown_percent,
                'avg_duration_hours': metrics.avg_intraday_duration_hours if filters.get('trade_style') == 'Intraday' else 0,
                'avg_duration_days': metrics.avg_swing_duration_days if filters.get('trade_style') != 'Intraday' else 0,
                'sortino_ratio': metrics.sortino_ratio,
                'kelly_criterion': metrics.kelly_criterion,
                'avg_r_multiple': metrics.average_r_multiple,
                'recovery_factor': metrics.total_pnl / abs(metrics.max_drawdown_amount) if metrics.max_drawdown_amount != 0 else 0,
                'payoff_ratio': metrics.average_win / abs(metrics.average_loss) if metrics.average_loss != 0 else 0,
                'best_trade': metrics.largest_win,
                'worst_trade': metrics.largest_loss,
                'max_consecutive_wins': metrics.max_consecutive_wins,
                'max_consecutive_losses': metrics.max_consecutive_losses,
                'standard_deviation': metrics.standard_deviation
            }

            total_trades_count += metrics.total_trades

    # Sanitize NaN and infinity values for template display
    for strategy in comparison_data:
        if comparison_data[strategy]:
            for key, value in comparison_data[strategy].items():
                if isinstance(value, float):
                    if math.isnan(value):
                        comparison_data[strategy][key] = 0.0
                    elif math.isinf(value):
                        # Keep infinity as-is for now, template will handle display
                        pass

    # Determine active filter count (Monday is day 0, so count 0 as a filter)
    active_filters_count = len([v for v in filters.values() if v is not None and v != ''])

    return render_template('compare.html',
                         available_strategies=available_strategies,
                         selected_strategies=selected_strategies,
                         comparison_data=comparison_data,
                         filters=filters,
                         total_trades_count=total_trades_count,
                         active_filters_count=active_filters_count,
                         error_message=None)

@app.route('/compare/export')
def compare_export():
    """Export comparison data as CSV"""
    from flask import Response
    import io
    import csv

    db = get_db()

    # Get selected strategies from query parameters
    selected_strategies = request.args.getlist('strategy')

    # Get filters from query parameters
    filters = {}
    if request.args.get('trade_style'):
        filters['trade_style'] = request.args.get('trade_style')
    if request.args.get('day_of_week'):
        filters['day_of_week'] = int(request.args.get('day_of_week'))
    if request.args.get('hour'):
        filters['hour'] = int(request.args.get('hour'))
    if request.args.get('market_condition'):
        filters['market_condition'] = request.args.get('market_condition')
    if request.args.get('market'):
        filters['market'] = request.args.get('market')
    if request.args.get('setup_type'):
        filters['setup_type'] = request.args.get('setup_type')
    if request.args.get('tags'):
        filters['tags'] = request.args.get('tags')
    if request.args.get('date_from'):
        filters['date_from'] = request.args.get('date_from')
    if request.args.get('date_to'):
        filters['date_to'] = request.args.get('date_to')

    # Validation
    if len(selected_strategies) < 2:
        return "Please select at least 2 strategies to export", 400
    if len(selected_strategies) > 5:
        return "Maximum 5 strategies can be exported at once", 400

    # Fetch filtered trades once — split by strategy in Python (avoids N DB reads)
    all_filtered_df = db.get_filtered_trades(filters=filters)
    starting_balance = db.get_total_capital_deployed()
    comparison_data = {}

    for strategy in selected_strategies:
        trades_df = (all_filtered_df[all_filtered_df['strategy'] == strategy]
                     if not all_filtered_df.empty else all_filtered_df)

        if not trades_df.empty:
            calculator = PerformanceCalculator(trades_df, starting_balance=starting_balance)
            metrics = calculator.calculate_all_metrics()

            comparison_data[strategy] = {
                'total_trades': metrics.total_trades,
                'win_rate': metrics.win_rate,
                'profit_factor': metrics.profit_factor,
                'total_pnl': metrics.total_pnl,
                'avg_win': metrics.average_win,
                'avg_loss': abs(metrics.average_loss),
                'expectancy': metrics.expectancy,
                'sharpe_ratio': metrics.sharpe_ratio,
                'max_drawdown_percent': metrics.max_drawdown_percent,
                'avg_duration_hours': metrics.avg_intraday_duration_hours if filters.get('trade_style') == 'Intraday' else 0,
                'avg_duration_days': metrics.avg_swing_duration_days if filters.get('trade_style') != 'Intraday' else 0,
                'sortino_ratio': metrics.sortino_ratio,
                'kelly_criterion': metrics.kelly_criterion,
                'avg_r_multiple': metrics.average_r_multiple,
                'recovery_factor': metrics.total_pnl / abs(metrics.max_drawdown_amount) if metrics.max_drawdown_amount != 0 else 0,
                'payoff_ratio': metrics.average_win / abs(metrics.average_loss) if metrics.average_loss != 0 else 0,
                'best_trade': metrics.largest_win,
                'worst_trade': metrics.largest_loss,
                'max_consecutive_wins': metrics.max_consecutive_wins,
                'max_consecutive_losses': metrics.max_consecutive_losses,
                'standard_deviation': metrics.standard_deviation
            }

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header row
    header = ['Metric'] + selected_strategies
    writer.writerow(header)

    # Define metric labels
    metric_labels = [
        ('total_trades', 'Total Trades'),
        ('win_rate', 'Win Rate (%)'),
        ('profit_factor', 'Profit Factor'),
        ('total_pnl', 'Total P&L (₹)'),
        ('avg_win', 'Avg Win (₹)'),
        ('avg_loss', 'Avg Loss (₹)'),
        ('expectancy', 'Expectancy (₹)'),
        ('sharpe_ratio', 'Sharpe Ratio'),
        ('max_drawdown_percent', 'Max Drawdown (%)'),
        ('avg_duration_hours', 'Avg Duration (Hours)'),
        ('avg_duration_days', 'Avg Duration (Days)'),
        ('sortino_ratio', 'Sortino Ratio'),
        ('kelly_criterion', 'Kelly Criterion (%)'),
        ('avg_r_multiple', 'Avg R-Multiple'),
        ('recovery_factor', 'Recovery Factor'),
        ('payoff_ratio', 'Payoff Ratio'),
        ('best_trade', 'Best Trade (₹)'),
        ('worst_trade', 'Worst Trade (₹)'),
        ('max_consecutive_wins', 'Max Consecutive Wins'),
        ('max_consecutive_losses', 'Max Consecutive Losses'),
        ('standard_deviation', 'Std Deviation')
    ]

    # Write data rows
    for metric_key, metric_label in metric_labels:
        row = [metric_label]
        for strategy in selected_strategies:
            if strategy in comparison_data and comparison_data[strategy]:
                value = comparison_data[strategy].get(metric_key, 'N/A')
                if isinstance(value, (int, float)):
                    row.append(f"{value:.2f}")
                else:
                    row.append(value)
            else:
                row.append('N/A')
        writer.writerow(row)

    # Create response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=strategy_comparison.csv'}
    )

@app.route('/settings')
def settings():
    """Settings page"""
    current_db = get_db()
    settings_data = current_db.get_all_settings()
    # Add database stats
    stats = current_db.get_database_stats()
    settings_data['total_trades'] = stats.get('total_trades', 0)
    settings_data['total_strategies'] = len(current_db.get_all_strategies())

    # Get unique strategies for export filter dropdown
    trades_df = current_db.get_all_trades()
    strategies = []
    if not trades_df.empty and 'strategy' in trades_df.columns:
        strategies = sorted(trades_df['strategy'].dropna().unique().tolist())
    settings_data['strategies'] = strategies

    # Add database info
    settings_data['current_database'] = session.get('current_db', DEFAULT_DB)
    settings_data['available_databases'] = [f.name for f in DATA_DIR.glob('*.db')]

    return render_template('settings.html', settings=settings_data)

@app.route('/api/database/backup', methods=['POST'])
def backup_database():
    """Copy current DB to data/backups/YYYY-MM-DD_HH-MM.db"""
    try:
        db_name = session.get('current_db', DEFAULT_DB)
        src = DATA_DIR / db_name
        if not src.exists():
            return jsonify({'success': False, 'message': 'Database file not found'}), 404

        backup_dir = DATA_DIR / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
        stem = src.stem
        dest = backup_dir / f'{stem}_{timestamp}.db'
        shutil.copy2(str(src), str(dest))

        app.logger.info(f"Database backed up to {dest}")
        return jsonify({'success': True,
                        'message': f'Backup saved to data/backups/{dest.name}'})
    except OSError as e:
        app.logger.error(f"Backup failed: {e}")
        return jsonify({'success': False, 'message': 'Backup failed'}), 500

@app.route('/api/settings/theme', methods=['GET'])
def get_theme():
    """Return persisted theme from settings table."""
    theme = get_db().get_setting('theme', 'dark')
    return jsonify({'theme': theme})

@app.route('/api/settings/theme', methods=['POST'])
def set_theme():
    """Persist theme preference to settings table."""
    theme = request.json.get('theme', 'dark')
    if theme not in ('light', 'dark'):
        return jsonify({'error': 'Invalid theme'}), 400
    get_db().set_setting('theme', theme)
    return jsonify({'theme': theme})

# Database Management Routes
@app.route('/api/database/list')
def list_databases():
    """List all databases in data directory"""
    databases = [f.name for f in DATA_DIR.glob('*.db')]
    current = session.get('current_db', DEFAULT_DB)
    return jsonify({
        'databases': databases,
        'current': current
    })

def _safe_db_filename(raw_name):
    """Sanitize a client-supplied DB filename and confine it to DATA_DIR.

    Runs the name through werkzeug.secure_filename (strips path separators and
    '..'), forces a .db extension, and verifies the resolved path sits directly
    inside DATA_DIR. Returns the safe basename, or None if empty/unsafe.
    """
    raw = (raw_name or '').strip()
    if not raw:
        return None
    # Reject any path-bearing name outright (no separators, no '..').
    if os.path.basename(raw) != raw or '..' in raw:
        return None
    name = secure_filename(raw)
    if not name:
        return None
    if not name.endswith('.db'):
        name += '.db'
    if (DATA_DIR / name).resolve().parent != DATA_DIR.resolve():
        return None
    return name

@app.route('/api/database/create', methods=['POST'])
def create_database():
    """Create a new database"""
    db_name = _safe_db_filename(request.form.get('db_name'))
    if not db_name:
        return jsonify({'success': False, 'message': 'Invalid database name'}), 400

    # Check if already exists
    db_path = DATA_DIR / db_name
    if db_path.exists():
        return jsonify({'success': False, 'message': f'Database {db_name} already exists'}), 400

    try:
        # Create new database
        new_db = DatabaseManager(str(db_path))
        new_db.initialize_database()
        return jsonify({'success': True, 'message': f'Database {db_name} created successfully'})
    except Exception:
        app.logger.exception('create_database failed')
        return jsonify({'success': False, 'message': 'Could not create database'}), 500

@app.route('/api/database/switch', methods=['POST'])
def switch_database():
    """Switch to a different database"""
    db_name = _safe_db_filename(request.form.get('db_name'))
    if not db_name:
        return jsonify({'success': False, 'message': 'Invalid database name'}), 400

    db_path = DATA_DIR / db_name
    if not db_path.exists():
        return jsonify({'success': False, 'message': f'Database {db_name} not found'}), 404

    # Update session
    session['current_db'] = db_name
    return jsonify({'success': True, 'message': f'Switched to database: {db_name}'})

@app.route('/api/database/upload', methods=['POST'])
def upload_database():
    """Upload and load database file from file picker"""
    if 'db_file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'}), 400

    file = request.files['db_file']

    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400

    safe_name = _safe_db_filename(file.filename)
    if not safe_name:
        return jsonify({'success': False, 'message': 'File must be a valid .db file'}), 400

    try:
        # Save to data directory (confined name)
        dest_path = DATA_DIR / safe_name
        file.save(str(dest_path))

        # Switch to the uploaded database
        session['current_db'] = safe_name
        return jsonify({'success': True, 'message': f'Loaded and switched to: {safe_name}'})
    except Exception:
        app.logger.exception('upload_database failed')
        return jsonify({'success': False, 'message': 'Could not upload database'}), 500

# Screenshot routes

@app.route('/screenshots/<path:filename>')
def serve_screenshot(filename):
    """Serve a screenshot image file."""
    return send_from_directory(str(SCREENSHOTS_DIR), filename)


@app.route('/executions/<int:execution_id>/screenshots', methods=['POST'])
def upload_screenshots(execution_id):
    """After-the-fact screenshot upload for an existing execution. Returns refreshed strip."""
    db = get_db()
    files = request.files.getlist('screenshots')
    errors = []
    for f in files:
        if f and f.filename:
            try:
                filename, original_name = save_screenshot_file(f)
                db.insert_screenshot(execution_id, filename, original_name)
            except ValueError as e:
                errors.append(str(e))
            except Exception as e:
                app.logger.error(f"Screenshot upload error: {e}")
                errors.append("Unexpected error saving file.")
    screenshots = db.get_screenshots_by_execution(execution_id)
    return render_template('partials/screenshot_strip.html',
                           execution_id=execution_id,
                           screenshots=screenshots,
                           errors=errors)


@app.route('/screenshots/<int:screenshot_id>', methods=['DELETE'])
def delete_screenshot(screenshot_id):
    """Delete a screenshot record and its physical file."""
    db = get_db()
    success, message, filename = db.delete_screenshot(screenshot_id)
    if success and filename:
        file_path = SCREENSHOTS_DIR / filename
        try:
            if file_path.exists():
                file_path.unlink()
        except OSError as e:
            app.logger.error(f"Could not delete screenshot file {filename}: {e}")
    # Return empty — HTMX swaps outerHTML of the thumbnail wrapper to nothing
    return '', 200


# Trade routes
@app.route('/trades/add', methods=['POST'])
def add_trade():
    """Handle trade form submission with position-based logic"""
    try:
        # Extract entry data
        entry_date = parse_indian_date(request.form.get('date'))
        entry_time = get_str('time')
        symbol = request.form.get('symbol').upper()
        market = get_str('market')
        option_type = get_str('option_type')
        strike_price = get_float('strike_price')
        expiry_date = get_str('expiry_date')
        direction = request.form.get('direction')
        entry_price = get_float('entry_price')
        quantity = get_float('quantity')
        charges = get_float('charges') or 0.0
        leverage = get_float('leverage') or 1.0
        trade_style = get_str('trade_style')

        # Validation
        if not symbol or not direction or not entry_price or not quantity:
            return alert_html('Symbol, Direction, Entry Price, and Quantity are required.',
                              'danger', title='Validation error')
        if quantity <= 0 or entry_price <= 0:
            return alert_html('Entry Price and Quantity must be greater than zero.',
                              'danger', title='Validation error')

        # Capital validation - ensure user has deposited capital before trading
        total_capital_deployed = get_db().get_total_capital_deployed()
        if total_capital_deployed <= 0:
            return alert_html('Please deposit capital via the Portfolio page before creating trades.',
                              'warning', title='No capital deposited')

        # Margin validation - ensure user has sufficient cash for margin requirement
        required_margin = (quantity * entry_price) / leverage
        current_cash = get_db().get_portfolio_summary().cash_balance
        if current_cash < required_margin:
            return alert_html(
                f'This trade requires ₹{required_margin:,.2f} margin, but you only have '
                f'₹{current_cash:,.2f} available cash.',
                'danger', title='Insufficient margin')

        # Extract exit data (optional)
        exit_date_str = get_str('exit_date')
        exit_date = parse_indian_date(exit_date_str) if exit_date_str else None
        exit_time = get_str('exit_time')
        exit_price = get_float('exit_price')
        exit_quantity = get_float('exit_quantity')

        # Intraday validation
        if trade_style == 'Intraday' and (not exit_date or not exit_price):
            return alert_html('Intraday trades must have exit details.', 'danger', title='Validation error')

        # If exit quantity not specified, assume full exit
        if exit_price and not exit_quantity:
            exit_quantity = quantity

        # Validate exit quantity doesn't exceed entry
        if exit_quantity and exit_quantity > quantity:
            return alert_html(
                f'Exit quantity ({exit_quantity}) cannot exceed entry quantity ({quantity}).',
                'danger', title='Validation error')

        db = get_db()

        # Step 1: Find or create position
        position_id = db.find_or_create_position(
            symbol=symbol,
            market=market,
            option_type=option_type,
            strike_price=strike_price,
            expiry_date=expiry_date,
            direction=direction,
            position_type=trade_style,
            opened_date=entry_date,
            strategy=get_str('strategy'),
            notes=get_str('notes'),
            tags=get_str('tags'),
        )

        # Step 2: Insert entry execution
        stop_loss = get_float('stop_loss')
        risk_amount = None
        if stop_loss is not None and entry_price is not None and quantity is not None:
            if direction == 'Long':
                risk_amount = abs(entry_price - stop_loss) * quantity
            elif direction == 'Short':
                risk_amount = abs(stop_loss - entry_price) * quantity

        entry_exec = Execution(
            position_id=position_id,
            date=entry_date,
            time=entry_time,
            type='entry',
            price=entry_price,
            quantity=quantity,
            charges=charges,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=get_float('take_profit'),
            risk_amount=risk_amount,
            market_conditions=get_str('market_conditions'),
            emotions=get_str('emotions'),
            confluence_score=get_int('confluence_score'),
            trade_grade=get_str('trade_grade'),
            setup_type=get_str('setup_type'),
            notes=get_str('notes'),
            mistakes=get_str('mistakes'),
        )

        success, message, entry_exec_id = db.insert_execution(entry_exec)
        if not success:
            return alert_html(escape(message), 'danger', title='Error')

        # Step 3: Set aside the entry's margin and charges
        db.apply_entry_cash(entry_price, quantity, leverage, charges)

        # Save entry screenshots (non-fatal if individual files fail)
        if entry_exec_id:
            for f in request.files.getlist('entry_screenshots'):
                if f and f.filename:
                    try:
                        fname, orig = save_screenshot_file(f)
                        db.insert_screenshot(entry_exec_id, fname, orig)
                    except (ValueError, OSError) as e:
                        app.logger.warning(f"Entry screenshot skipped: {e}")

        # Step 4: If exit provided, insert exit execution
        if exit_date and exit_price and exit_quantity:
            exit_exec = Execution(
                position_id=position_id,
                date=exit_date,
                time=exit_time,
                type='exit',
                price=exit_price,
                quantity=exit_quantity,
                charges=0,  # Charges deducted on entry
                leverage=leverage,
                market_conditions=get_str('market_conditions'),
                emotions=get_str('emotions'),
                notes=get_str('notes'),
            )

            success, message, exit_exec_id = db.insert_execution(exit_exec)
            if not success:
                return alert_html(escape(message), 'danger', title='Error')

            # Margin back, plus the profit or minus the loss
            db.apply_exit_cash(position_id)

            # Save exit screenshots
            if exit_exec_id:
                for f in request.files.getlist('exit_screenshots'):
                    if f and f.filename:
                        try:
                            fname, orig = save_screenshot_file(f)
                            db.insert_screenshot(exit_exec_id, fname, orig)
                        except (ValueError, OSError) as e:
                            app.logger.warning(f"Exit screenshot skipped: {e}")

        # Step 5: Recalculate position status (single call)
        metrics = db.recalculate_position(position_id)

        if exit_date and exit_price and exit_quantity:
            remaining = metrics.get('current_qty', 0)
            status_msg = "Position closed" if metrics.get('status') == 'closed' else f"Partial exit - {remaining} remaining"
            return alert_html(
                f'Trade saved successfully. {status_msg}. '
                '<a href="/" class="link">View Dashboard</a> · '
                '<a href="/positions" class="link">View Positions</a>',
                'success', title='Success')
        else:
            return alert_html(
                f'Position opened - {escape(symbol)} {escape(direction)} {quantity} @ {entry_price}. '
                '<a href="/positions" class="link">View Positions</a>',
                'success', title='Success')

    except Exception as e:
        return alert_html(escape(str(e)), 'danger', title='Error')

# CSV Import routes

# CSV columns the import understands (headers are matched after normalizing:
# 'Entry Price' -> 'entry_price')
IMPORT_COLUMNS = [
    'date', 'time', 'exit_date', 'entry_time', 'exit_time',
    'symbol', 'market', 'direction',
    'entry_price', 'exit_price', 'quantity', 'charges',
    'leverage', 'stop_loss', 'take_profit', 'risk_amount',
    'strategy', 'setup_type', 'market_conditions',
    'emotions', 'confluence_score', 'trade_grade',
    'trade_style', 'position_type',
    'notes', 'tags', 'mistakes',
]


@app.route('/import/csv', methods=['POST'])
def import_csv():
    """Handle CSV file upload and import"""
    import base64

    try:
        # Check if file was uploaded
        if 'csv_file' not in request.files:
            return alert_html('No file uploaded', 'danger')

        file = request.files['csv_file']

        # Check if filename is empty
        if file.filename == '':
            return alert_html('No file selected', 'danger')

        # Check file extension
        if not file.filename.lower().endswith('.csv'):
            return alert_html('Invalid file type. Please upload a CSV file.', 'danger')

        # Get form parameters
        skip_duplicates = request.form.get('skip_duplicates') == 'on'

        # Save file temporarily
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv') as tmp_file:
            file.save(tmp_file.name)
            tmp_path = tmp_file.name

        try:
            # Map each understood field to its column, by normalized header name
            df = pd.read_csv(tmp_path, nrows=1)
            column_mapping = {}
            for col in df.columns:
                name = normalize_column_name(col)
                if name in IMPORT_COLUMNS:
                    column_mapping[name] = name

            # Import trades
            success, message, stats = get_db().import_trades_from_csv(
                tmp_path,
                column_mapping,
                skip_duplicates=skip_duplicates,
                validate_data=True
            )

            # Clean up temp file
            os.unlink(tmp_path)

            errors = stats.get('errors', [])
            failed = stats.get('failed', 0)

            # Build error details section (row messages contain CSV values: escape them)
            error_html = ''
            if errors:
                b64 = base64.b64encode('\n'.join(errors).encode()).decode()
                items = ''.join(f'<li>{escape(e)}</li>' for e in errors[:50])
                more = '<li>…and more (see the downloaded log)</li>' if len(errors) > 50 else ''
                error_html = (
                    '<details class="mt-2">'
                    f'<summary class="cursor-pointer text-warn">{len(errors)} message(s) — click to expand</summary>'
                    f'<a href="data:text/plain;base64,{b64}" download="import_errors.txt" '
                    'class="btn btn-outline btn-sm mt-2">Download log</a>'
                    f'<ul class="list-disc ml-5 mt-2 text-xs">{items}{more}</ul>'
                    '</details>')

            counts = (
                '<ul class="list-disc ml-5 mt-1">'
                f'<li>Processed: {stats.get("processed", 0)} rows</li>'
                f'<li>Imported: {stats.get("imported", 0)} trades</li>'
                f'<li>Failed: {failed} rows</li>'
                f'<li>Skipped: {stats.get("skipped", 0)} duplicates</li>'
                '</ul>')

            if success:
                return alert_html(
                    counts + error_html + '<a href="/" class="link inline-block mt-2">View Dashboard</a>',
                    'success' if not errors else 'warning',
                    title='Import successful' if not errors else 'Import completed with issues')
            else:
                return alert_html(counts + error_html, 'danger', title='Import failed')

        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise e

    except Exception as e:
        return alert_html(escape(str(e)), 'danger', title='Error')

# HTMX partial routes for All Trades
def _render_trades_page(page=1):
    """One page (100 trades, newest close first) of the trades table, with its
    page controls."""
    db = get_db()
    per_page = 100

    total_trades = db.get_trade_count()
    total_pages = max(1, (total_trades + per_page - 1) // per_page)
    page = min(max(page, 1), total_pages)

    trades_df = db.get_all_trades(limit=per_page, offset=(page - 1) * per_page)
    trades = trades_df.to_dict('records') if not trades_df.empty else []

    return render_template('partials/trades_table.html',
                          trades=trades,
                          page=page,
                          total_pages=total_pages,
                          total_trades=total_trades)


@app.route('/api/trades/list')
def trades_list():
    """Return trades table with DB-level pagination"""
    return _render_trades_page(int(request.args.get('page', 1)))

@app.route('/api/trades/search')
def trades_search():
    """Filter and return trades based on search criteria with pagination"""
    trades_df = get_db().get_all_trades()
    page = int(request.args.get('page', 1))
    per_page = 100

    if trades_df.empty:
        return render_template('partials/trades_table.html',
                              trades=[],
                              page=1,
                              total_pages=0,
                              total_trades=0)

    # Apply filters
    symbol = request.args.get('symbol', '').strip().upper()
    strategy = request.args.get('strategy', '').strip()
    tags = request.args.get('tags', '').strip()
    direction = request.args.get('direction', '').strip()
    trade_style = request.args.get('trade_style', '').strip()
    result = request.args.get('result', '').strip()
    sort_by = request.args.get('sort', 'date_desc')
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()

    # Filter by date range — convert once if either filter is active
    if (from_date or to_date) and not trades_df.empty:
        trades_df = trades_df.copy()
        trades_df['date'] = pd.to_datetime(trades_df['date'])
        if from_date:
            try:
                trades_df = trades_df[trades_df['date'] >= pd.to_datetime(from_date)]
            except (ValueError, TypeError):
                pass
        if to_date:
            try:
                to_date_dt = pd.to_datetime(to_date) + pd.Timedelta(days=1)
                trades_df = trades_df[trades_df['date'] < to_date_dt]
            except (ValueError, TypeError):
                pass

    # Filter by symbol
    if symbol:
        trades_df = trades_df[trades_df['symbol'].str.contains(symbol, case=False, na=False)]

    # Filter by strategy
    if strategy:
        trades_df = trades_df[trades_df['strategy'] == strategy]

    # Filter by tags
    if tags:
        trades_df = trades_df[trades_df['tags'].str.contains(tags, case=False, na=False)]

    # Filter by direction
    if direction:
        trades_df = trades_df[trades_df['direction'] == direction]

    # Filter by trade style
    if trade_style:
        trades_df = trades_df[trades_df['position_type'] == trade_style]

    # Filter by result (win/loss)
    if result == 'win':
        trades_df = trades_df[trades_df['net_pnl'] > 0]
    elif result == 'loss':
        trades_df = trades_df[trades_df['net_pnl'] < 0]

    # Sort
    if sort_by == 'date_desc':
        trades_df = trades_df.sort_values('date', ascending=False)
    elif sort_by == 'date_asc':
        trades_df = trades_df.sort_values('date', ascending=True)
    elif sort_by == 'pnl_desc':
        trades_df = trades_df.sort_values('net_pnl', ascending=False)
    elif sort_by == 'pnl_asc':
        trades_df = trades_df.sort_values('net_pnl', ascending=True)

    # Pagination
    total_trades = len(trades_df)
    total_pages = (total_trades + per_page - 1) // per_page
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page

    trades = trades_df.iloc[start_idx:end_idx].to_dict('records') if not trades_df.empty else []

    return render_template('partials/trades_table.html',
                          trades=trades,
                          page=page,
                          total_pages=total_pages,
                          total_trades=total_trades)

@app.route('/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(trade_id):
    """Delete a position and all its trades, including screenshot files."""
    # trade_id from view is actually position_id
    position_id = trade_id
    db = get_db()

    # Collect screenshot filenames BEFORE CASCADE deletes the rows
    screenshots = db.get_screenshots_by_position(position_id)
    all_filenames = [s['filename'] for shots in screenshots.values() for s in shots]

    # Gives back the cash the position's executions moved, then deletes it
    # (ON DELETE CASCADE removes its executions and execution_screenshots rows)
    db.delete_position(position_id)

    # Delete physical files (best-effort — DB rows already gone)
    for filename in all_filenames:
        try:
            fp = SCREENSHOTS_DIR / filename
            if fp.exists():
                fp.unlink()
        except OSError as e:
            app.logger.warning(f"Could not delete screenshot file {filename}: {e}")

    # Return the first page of the updated list
    return _render_trades_page(1)

@app.route('/trades/<int:trade_id>/view')
def view_trade(trade_id):
    """Return view trade modal"""
    db = get_db()
    # trade_id is the position_id. Show the same row the trades list shows (it
    # carries the entry date and time and the analysis fields); empty values
    # become None so the template shows them as blank.
    trades_df = db.get_all_trades()
    trade_row = (trades_df[trades_df['trade_id'] == trade_id]
                 if not trades_df.empty else trades_df)
    if not trade_row.empty:
        position = {k: (None if not isinstance(v, str) and pd.isna(v) else v)
                    for k, v in trade_row.iloc[0].to_dict().items()}
        if position.get('confluence_score') is not None:
            position['confluence_score'] = int(position['confluence_score'])  # 1-5, not 4.0
    else:
        position = db.get_position_by_id(trade_id)
        if not position:
            return alert_html('Trade not found', 'danger')

    executions = db.get_executions_by_position(trade_id)
    screenshots = db.get_screenshots_by_position(trade_id)
    return render_template('partials/view_trade_modal.html',
                           trade=position,
                           executions=executions,
                           screenshots=screenshots)

@app.route('/trades/<int:trade_id>/edit')
def edit_trade(trade_id):
    """Return edit trade modal"""
    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return alert_html('Trade not found', 'danger')

    # Find the trade
    trade_row = trades_df[trades_df['trade_id'] == trade_id]

    if trade_row.empty:
        return alert_html('Trade not found', 'danger')

    trade = trade_row.iloc[0].to_dict()
    strategies = get_db().get_active_strategies()
    today = date.today().strftime('%d/%m/%Y')

    return render_template('partials/edit_trade_modal.html',
                          trade=trade,
                          strategies=strategies,
                          today=today)

@app.route('/trades/<int:trade_id>/update', methods=['POST'])
def update_trade(trade_id):
    """Update trade and return updated table"""
    try:

        # Update position in database (trade_id in view maps to position_id)
        position_id = trade_id
        with get_db().get_connection() as conn:
            # Update position table
            conn.execute("""
                UPDATE positions SET
                    symbol = ?, market = ?, option_type = ?, direction = ?,
                    position_type = ?, strategy = ?, notes = ?, tags = ?
                WHERE position_id = ?
            """, (
                request.form.get('symbol'),
                get_str('market'),
                get_str('option_type'),
                request.form.get('direction'),
                get_str('trade_style'),
                get_str('strategy'),
                get_str('notes'),
                get_str('tags'),
                position_id
            ))
            # Analysis fields live on the position's first entry (the trades
            # table and the analytics read them from there)
            conn.execute("""
                UPDATE executions SET
                    setup_type = ?, market_conditions = ?, emotions = ?,
                    confluence_score = ?, trade_grade = ?, mistakes = ?
                WHERE execution_id = (
                    SELECT MIN(execution_id) FROM executions
                    WHERE position_id = ? AND type = 'entry'
                )
            """, (
                get_str('setup_type'),
                get_str('market_conditions'),
                get_str('emotions'),
                get_int('confluence_score'),
                get_str('trade_grade'),
                get_str('mistakes'),
                position_id
            ))

    except Exception as e:
        app.logger.error(f"[UPDATE TRADE ERROR] {str(e)}")

    # Return the first page of the updated list
    return _render_trades_page(1)

# HTMX partial routes for Manage Strategies
@app.route('/strategies/list')
def strategies_list():
    """Return just the list fragment"""
    strategies = get_db().get_all_strategies()
    return render_template('partials/strategies_list.html',
                          strategies=strategies)

@app.route('/strategies/modal/add')
def add_strategy_modal():
    """Return modal HTML"""
    return render_template('partials/add_strategy_modal.html')

@app.route('/strategies/add', methods=['POST'])
def add_strategy():
    """Handle form submission, return updated list"""
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()

    if name:
        success, message = get_db().add_strategy(name, description)
        app.logger.info(f"[ADD STRATEGY] {message}")

    # Return updated list (always, even on error)
    strategies = get_db().get_all_strategies()
    return render_template('partials/strategies_list.html',
                          strategies=strategies)

@app.route('/strategies/<int:strategy_id>', methods=['DELETE'])
def delete_strategy(strategy_id):
    """Delete strategy, return updated list"""
    success, message = get_db().delete_strategy(strategy_id)
    app.logger.info(f"[DELETE STRATEGY] {message}")

    strategies = get_db().get_all_strategies()
    return render_template('partials/strategies_list.html',
                          strategies=strategies)

@app.route('/strategies/<int:strategy_id>/toggle', methods=['POST'])
def toggle_strategy(strategy_id):
    """Toggle strategy active/inactive status"""
    success, message, new_status = get_db().toggle_strategy_status(strategy_id)
    app.logger.info(f"[TOGGLE STRATEGY] {message} (new status: {new_status})")

    # Return updated list
    strategies = get_db().get_all_strategies()
    return render_template('partials/strategies_list.html',
                          strategies=strategies)

@app.route('/strategies/<int:strategy_id>/edit')
def edit_strategy_modal(strategy_id):
    """Return edit modal HTML"""
    strategies = get_db().get_all_strategies()
    strategy = next((s for s in strategies if s['id'] == strategy_id), None)

    if not strategy:
        return alert_html('Strategy not found', 'danger')

    return render_template('partials/edit_strategy_modal.html',
                          strategy=strategy)

@app.route('/strategies/<int:strategy_id>/update', methods=['POST'])
def update_strategy(strategy_id):
    """Handle edit form submission, return updated list"""
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()

    if name:
        success, message = get_db().update_strategy(strategy_id, name, description)
        app.logger.info(f"[UPDATE STRATEGY] {message}")

    # Return updated list (always, even on error)
    strategies = get_db().get_all_strategies()
    return render_template('partials/strategies_list.html',
                          strategies=strategies)

# ============================================================================
# EXPORT ROUTES
# ============================================================================

def apply_export_filters(trades_df):
    """Apply query parameter filters to trades DataFrame"""
    if trades_df.empty:
        return trades_df

    # Date range filters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    if start_date:
        trades_df['date'] = pd.to_datetime(trades_df['date'])
        trades_df = trades_df[trades_df['date'] >= pd.to_datetime(start_date)]

    if end_date:
        trades_df['date'] = pd.to_datetime(trades_df['date'])
        trades_df = trades_df[trades_df['date'] <= pd.to_datetime(end_date)]

    # Strategy filter
    strategy = request.args.get('strategy')
    if strategy and strategy != '':
        trades_df = trades_df[trades_df['strategy'] == strategy]

    # Direction filter
    direction = request.args.get('direction')
    if direction and direction != '':
        trades_df = trades_df[trades_df['direction'] == direction]

    # Result filter (win/loss)
    result = request.args.get('result')
    if result == 'win':
        trades_df = trades_df[trades_df['net_pnl'] > 0]
    elif result == 'loss':
        trades_df = trades_df[trades_df['net_pnl'] < 0]
    elif result == 'breakeven':
        trades_df = trades_df[trades_df['net_pnl'] == 0]

    # Symbol filter
    symbol = request.args.get('symbol')
    if symbol and symbol != '':
        trades_df = trades_df[trades_df['symbol'].str.contains(symbol, case=False, na=False)]

    return trades_df

@app.route('/export/trades/csv')
def export_trades_csv():
    """Export trades to CSV with optional filters

    Query Parameters:
    - start_date: Filter trades from this date (YYYY-MM-DD)
    - end_date: Filter trades up to this date (YYYY-MM-DD)
    - strategy: Filter by strategy name
    - direction: Filter by direction (Long/Short)
    - result: Filter by result (win/loss/breakeven)
    - symbol: Filter by symbol (partial match)
    """
    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'error': 'No trades to export'}), 404

    # Apply filters
    trades_df = apply_export_filters(trades_df)

    if trades_df.empty:
        return jsonify({'error': 'No trades match the selected filters'}), 404

    # Create CSV in memory
    output = io.StringIO()
    trades_df.to_csv(output, index=False)
    output.seek(0)

    # Create response
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=trading_journal_trades_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    response.headers['Content-Type'] = 'text/csv'

    return response

@app.route('/export/trades/json')
def export_trades_json():
    """Export trades to JSON with optional filters

    Query Parameters:
    - start_date: Filter trades from this date (YYYY-MM-DD)
    - end_date: Filter trades up to this date (YYYY-MM-DD)
    - strategy: Filter by strategy name
    - direction: Filter by direction (Long/Short)
    - result: Filter by result (win/loss/breakeven)
    - symbol: Filter by symbol (partial match)
    """
    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'error': 'No trades to export'}), 404

    # Apply filters
    trades_df = apply_export_filters(trades_df)

    if trades_df.empty:
        return jsonify({'error': 'No trades match the selected filters'}), 404

    # Convert DataFrame to dict, handle NaN values
    trades_dict = trades_df.where(pd.notna(trades_df), None).to_dict('records')

    # Create response
    response = make_response(json.dumps(trades_dict, indent=2, default=str))
    response.headers['Content-Disposition'] = f'attachment; filename=trading_journal_trades_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    response.headers['Content-Type'] = 'application/json'

    return response

@app.route('/export/performance/json')
def export_performance_json():
    """Export comprehensive performance metrics to JSON"""

    trades_df = get_db().get_all_trades()

    if trades_df.empty:
        return jsonify({'error': 'No trades to export'}), 404

    # Calculate all metrics
    total_capital_deployed = get_db().get_total_capital_deployed()
    calc = PerformanceCalculator(trades_df, starting_balance=total_capital_deployed)
    metrics = calc.calculate_all_metrics()

    # Get time-based analysis
    time_analyzer = TimeBasedAnalyzer(trades_df)
    daily_df = time_analyzer.analyze_by_day_of_week()
    monthly_df = time_analyzer.analyze_by_month()

    # Get strategy analysis
    strategy_analyzer = StrategyAnalyzer(trades_df)
    strategy_df = strategy_analyzer.analyze_strategy_performance()

    # Build comprehensive report
    report = {
        'generated_at': datetime.now().isoformat(),
        'total_trades': len(trades_df),
        'total_capital_deployed': total_capital_deployed,
        'date_range': {
            'start': trades_df['date'].min().isoformat() if not trades_df.empty else None,
            'end': trades_df['date'].max().isoformat() if not trades_df.empty else None
        },
        'performance_metrics': {
            'total_trades': metrics.total_trades,
            'winning_trades': metrics.winning_trades,
            'losing_trades': metrics.losing_trades,
            'breakeven_trades': metrics.breakeven_trades,
            'win_rate': metrics.win_rate,
            'total_pnl': metrics.total_pnl,
            'gross_profit': metrics.gross_profit,
            'gross_loss': metrics.gross_loss,
            'profit_factor': metrics.profit_factor,
            'average_win': metrics.average_win,
            'average_loss': metrics.average_loss,
            'largest_win': metrics.largest_win,
            'largest_loss': metrics.largest_loss,
            'win_loss_ratio': metrics.win_loss_ratio,
            'expectancy': metrics.expectancy,
            'average_r_multiple': metrics.average_r_multiple,
            'max_drawdown_percent': metrics.max_drawdown_percent,
            'max_drawdown_amount': metrics.max_drawdown_amount,
            'sharpe_ratio': metrics.sharpe_ratio,
            'kelly_criterion': metrics.kelly_criterion,
            'max_consecutive_wins': metrics.max_consecutive_wins,
            'max_consecutive_losses': metrics.max_consecutive_losses,
            'current_streak': metrics.current_streak,
            'current_streak_type': metrics.current_streak_type
        },
        'time_analysis': {
            'by_day_of_week': daily_df.to_dict('records') if not daily_df.empty else [],
            'by_month': monthly_df.to_dict('records') if not monthly_df.empty else []
        },
        'strategy_analysis': strategy_df.to_dict('records') if not strategy_df.empty else []
    }

    # Create response
    response = make_response(json.dumps(report, indent=2, default=str))
    response.headers['Content-Disposition'] = f'attachment; filename=trading_journal_performance_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    response.headers['Content-Type'] = 'application/json'

    return response

if __name__ == '__main__':
    # Suppress the Werkzeug/Python version banner in the Server header
    # (the dev server emits it at the HTTP-handler layer, not via app headers).
    from werkzeug.serving import WSGIRequestHandler

    class _NoBannerRequestHandler(WSGIRequestHandler):
        def version_string(self):
            return 'OneJournal'

    # This computer only, unless ONEJOURNAL_HOST says otherwise. Inside Docker,
    # docker-compose.yml sets 0.0.0.0 (needed to reach the container); the
    # host side of its port is still bound to 127.0.0.1.
    app.run(debug=False, host=os.environ.get('ONEJOURNAL_HOST', '127.0.0.1'), port=8090,
            request_handler=_NoBannerRequestHandler)

================================================================================
                                  OneJournal
                     A local trading journal in your browser
================================================================================

WHAT THIS IS
------------
A trading journal that runs on your own computer, in Docker, and opens in your
browser at http://localhost:8090. It is for one person and sits behind a login.

- Log trades: entry and exit, charges, leverage, stop loss and target, the
  setup, market conditions, your emotions, a grade, mistakes, notes, tags and
  chart screenshots.
- Manage open positions: add to one (scale in), sell part of it (scale out)
  or close it, each step recorded separately.
- Track cash: deposits, withdrawals, dividends or interest, and fees.
- Analyse: equity curve, drawdown, win rate, profit factor, R-multiples,
  Sharpe and Sortino ratios, value at risk, and breakdowns by day, hour,
  month, strategy, trade style and market.
- Compare two to five strategies side by side.
- Import trades from a CSV file; export trades as CSV or JSON.
- Keep several journals (one per account, say) and switch between them.
- Light and dark themes.

The app is reachable from this computer only. Nothing is fetched from the
internet while it runs: the chart, page and date-picker scripts and the fonts
are bundled in static/.


WHAT YOU NEED
-------------
- Docker Desktop: https://www.docker.com/products/docker-desktop
- Windows 10/11, macOS or Linux, and a current web browser.
- An internet connection for the first start only. It downloads the Debian and
  Python base images, the Tailwind CSS compiler (which builds the stylesheet)
  and the Python packages.


SETTING UP THE LOGIN
--------------------
OneJournal will not start without a login name and password. Set them once:

1. In this folder, copy .env.example to a new file named .env
2. Open .env in a text editor and fill in both lines, for example
       AUTH_USERNAME=me
       AUTH_PASSWORD=a long password of your own
3. Save it.

Your browser asks for this name and password when you open OneJournal. The
.env file stays on your computer: it is listed in .gitignore and is not copied
into the Docker image. To change the login, edit .env and start OneJournal
again.


STARTING AND STOPPING
---------------------
1. Start Docker Desktop and wait until it is running.
2. Start OneJournal:
   - Mac: double-click start.command (if it will not open: right-click > Open)
   - Windows: double-click start.bat
   - Linux: run ./start.command in a terminal
   The first start builds OneJournal, which takes a few minutes; later starts
   take seconds. The page then opens in your browser.
3. To stop: stop.command (Mac/Linux) or stop.bat (Windows). Your journals and
   screenshots are kept.

The start script first checks that Docker is installed and running and that
the login is set up, and says what is missing if not. If OneJournal cannot
start, it says FAILED and does not open the browser.

From a terminal, in this folder:
     docker-compose up -d --build       start
     docker-compose down                stop
     docker logs onejournal             messages from the app


THE PAGES
---------
Dashboard    Headline figures, this week / month / year, the latest closed
             trades, warnings (drawdown, a losing streak, a low win rate,
             negative expectancy), a note at a new equity high, and six
             charts.
Analytics    Breakdowns by day of week, hour, month, strategy, trade style,
             market and market conditions; risk figures; every metric in one
             table.
Compare      Two to five strategies side by side, with optional filters (trade
             style, weekday, hour, market, conditions, setup, tags, dates).
             Filter sets can be saved as templates; they are kept in this
             browser only.
Trades       Every closed trade, with search and filters. View, edit or delete
             a trade; add screenshots from its view window.
Positions    Open and closed positions. Enter a current price for an open
             position to see its unrealized profit or loss. The pencil button
             adds to, partly sells or closes a position.
Portfolio    Cash balance, total capital, equity, and the cash transactions.
Strategies   The strategy names you tag trades with.
Settings     Switch, create, upload or back up a journal; import a CSV; export.

"New trade" (top right) records a trade. Fill in the exit as well to record a
finished trade, or leave it empty to open a position.


HOW CASH AND PROFIT ARE COUNTED
-------------------------------
Total capital: deposits and dividends, minus withdrawals and fees.

Cash moves by margin, long or short:
- Opening or adding to a position sets aside its value divided by its
  leverage, plus its charges. At leverage 1 that is the full value.
- A sale is measured against the average cost of what is held at that
  moment: it returns that share of the margin set aside, plus the profit or
  minus the loss, less its charges.
- Deleting a trade gives back every rupee its steps moved.

Example: buy 10 at ₹1,000 with leverage 5 and ₹20 charges: ₹2,020 leaves
cash. Sell all 10 at ₹1,100: ₹2,000 + ₹1,000 profit comes back. Net +₹980.

Equity is cash plus each open position at its margin plus its unrealized
profit or loss. A position with no current price entered counts at its margin.

A closed trade's profit or loss is (exit - entry) x quantity for a long,
(entry - exit) x quantity for a short, minus all its charges, entry and exit.
Week, month and year figures, the monthly chart and table, and the analytics
page's current week count a trade in the period it closed; the day-of-week
and hour-of-day breakdowns use when it was entered.

Holding time runs from the first entry to the last exit: from the two times
when both are recorded, otherwise from the dates.

R-multiple: a trade's profit or loss divided by the amount it risked, which is
the distance to its stop loss times the quantity. Trades without a stop loss
have no R-multiple and are left out of the averages.

Value at risk (95%): the loss that one trade in twenty reaches or exceeds,
from your closed trades. It shows ₹0 when even the worst trades are profits.


IMPORTING TRADES FROM A CSV FILE
--------------------------------
Settings > Import. Each row is one finished trade: OneJournal records an entry
and an exit for it.

Required columns:
    date, symbol, direction, entry_price, exit_price, quantity

Optional columns:
    exit_date, time (or entry_time), exit_time, trade_style (or
    position_type), market, charges, leverage, stop_loss, take_profit,
    risk_amount, strategy, setup_type, market_conditions, emotions,
    confluence_score, trade_grade, notes, tags, mistakes

Headers are matched without regard to case or spaces: "Entry Price" reads as
entry_price.

Values:
- Dates: YYYY-MM-DD or DD/MM/YYYY. A row whose date cannot be read is skipped
  and listed. exit_date defaults to the entry date.
- Times: HH:MM.
- direction: Long or Short. trade_style: Intraday, Swing or Investment
  (default Swing).
- market: Index Futures, Index Options, Equity, Equity Futures, Equity
  Options, Commodity Futures or Commodity Options.
- market_conditions: Bullish Range, Bearish Range, Bullish Trending, Bearish
  Trending, Undecided or Volatile.
- emotions: Confident, Calm, Anxious, Frustrated, Excited, Patient or Focused.
- confluence_score: 1 to 5. trade_grade: A, B, C, D or F.
- charges: the trade's charges, counted at entry.

With "Skip duplicate trades" ticked, a row with the same symbol, entry date, entry
price and quantity as a trade already in the journal is skipped. After the
import a summary lists every skipped or failed row and why; "Download log"
saves that list.


YOUR DATA
---------
- Journals:     data/trading_journal.db, plus any other data/<name>.db
- Screenshots:  data/screenshots/
- Backups:      data/backups/   (Settings > Backup)
- Exports and the error log of an import download through your browser.

data/ is kept when OneJournal stops or is rebuilt; only deleting the folder
deletes your journals. Back up by copying data/, or with Settings > Backup.

Each browser session starts on trading_journal.db. Switching journals in
Settings lasts until the browser is closed.


UPDATING
--------
1. Stop OneJournal.
2. Replace the program files with the new version. Keep data/ and .env.
3. Start OneJournal; the start script rebuilds it with the new files.


UNINSTALLING
------------
1. Stop OneJournal.
2. Delete the OneJournal image in Docker Desktop (Images).
3. Delete this folder. Your journals go with it unless you copied data/.


RUNNING WITHOUT DOCKER (FOR DEVELOPERS)
---------------------------------------
Needs Python 3.11 or newer (the packages in requirements.txt install on 3.11
to 3.14; tested on 3.13) and the standalone Tailwind CSS compiler v3.4.17 for
your system, from https://github.com/tailwindlabs/tailwindcss/releases saved as
.bin/tailwindcss and made executable.

     ./build-css.sh                     build static/css/app.css
     python3 -m venv venv
     source venv/bin/activate           (Windows: venv\Scripts\activate)
     pip install -r requirements.txt
     python3 app.py                     http://localhost:8090

app.py reads the login from .env, as above. Run this way, it accepts
connections from this computer only.

requirements.lock lists every Python package and version of a tested install.

How it is built: Flask and SQLite on the server; pages updated in place with
HTMX; charts by Plotly.js; dates picked with Flatpickr; the stylesheet compiled
by Tailwind CSS from static/css/input.css; Geist fonts; Lucide icons. The
Dockerfile compiles the stylesheet in a first stage and runs the app on
Python 3.11 in a second.


TROUBLESHOOTING
---------------
The start script says the login is not set up
    See "Setting up the login" above.

The page will not load
    Check OneJournal is running: docker ps should list "onejournal". Its
    messages: docker logs onejournal --tail 200

Port 8090 is already in use
    Another program uses it. Stop that program, or change the first 8090 in
    docker-compose.yml (for example "127.0.0.1:8091:8090") and open that
    address instead.

The page looks unstyled
    Reload it with Cmd/Ctrl + Shift + R.

An import fails
    The summary under the import box names each failed row and the reason.


LICENCE
-------
MIT - see LICENSE. The bundled Plotly.js and Flatpickr are MIT licensed, HTMX
is BSD 2-clause, the Lucide icon shapes are ISC (those derived from Feather,
MIT), and the Geist fonts are under the SIL Open Font License - see
THIRD_PARTY_LICENSES. The Python packages are installed by pip from
requirements.txt and keep their own licences.


================================================================================
                    From ChorBazaar - https://chorbazaar.co.in
================================================================================

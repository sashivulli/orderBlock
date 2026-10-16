#!/usr/bin/env python3
"""
Order Block Finder -> Telegram alerts
Python port of the TradingView indicator "Order Block Finder" (© wugamlo).

Logic (same as the Pine script):
  * Bullish OB = last red candle followed by `PERIODS` consecutive green candles
  * Bearish OB = last green candle followed by `PERIODS` consecutive red candles
  * Optional minimum % move filter (close of OB candle -> close of last candle in the run)
  * Alert fires on the bar AFTER the run completes (same delay as the Pine alert)
  * Only fully closed candles are used (the in-progress candle is dropped)

Setup:
  pip install yfinance pandas requests
  export TELEGRAM_BOT_TOKEN="123456:ABC..."   # from @BotFather
  export TELEGRAM_CHAT_ID="123456789"         # from @userinfobot, or your group/channel id

Run:
  python order_block_alert.py                 # loop during NSE market hours
  python order_block_alert.py --once          # single check, then exit (good for cron)
  python order_block_alert.py --test          # send a test Telegram message
  python order_block_alert.py --history 10    # print the last 10 OBs found per symbol

If the Telegram variables are not set, alerts are printed to the console only.
NOT a buy/sell signal - same disclaimer as the original indicator.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ----------------------------- CONFIG ---------------------------------------
SYMBOLS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"]  # NSE = .NS, BSE = .BO
INTERVAL = "15m"        # 5m, 15m, 30m, 60m, 1d
PERIODS = 5             # "Relevant Periods to identify OB"
THRESHOLD = 0.0         # "Min. Percent move to identify OB"
USE_WICKS = False       # True = full High/Low range, False = Open/Low (bull) or Open/High (bear)
MARKET_HOURS_ONLY = True
STATE_FILE = Path("ob_alert_state.json")
BUFFER_SECONDS = 20     # wait after candle close so the data provider has the final bar
# -----------------------------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata")
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60, "1d": 1440}
HISTORY_PERIOD = {"1m": "5d", "5m": "30d", "15m": "30d", "30m": "30d", "60m": "90d", "1h": "90d", "1d": "1y"}
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)


# ------------------------------ detection ------------------------------------
def detect_order_blocks(df: pd.DataFrame, periods=PERIODS, threshold=THRESHOLD, use_wicks=USE_WICKS) -> pd.DataFrame:
    """Vectorised port of the Pine logic. Row t = the bar on which the Pine alert would fire.
    The OB candle itself sits at t - (periods + 1)."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    k = periods + 1

    absmove = (c.shift(k) - c.shift(1)).abs() / c.shift(k) * 100
    relmove = absmove >= threshold

    up_run = (c > o).astype(float).shift(1).rolling(periods).sum() == periods      # close[1..periods] > open[1..periods]
    down_run = (c < o).astype(float).shift(1).rolling(periods).sum() == periods

    bull = (c.shift(k) < o.shift(k)) & up_run & relmove
    bear = (c.shift(k) > o.shift(k)) & down_run & relmove

    out = pd.DataFrame(index=df.index)
    out["bull"] = bull
    out["bear"] = bear
    out["ob_time"] = pd.Series(df.index, index=df.index).shift(k)

    out["bull_high"] = (h if use_wicks else o).shift(k).where(bull)
    out["bull_low"] = l.shift(k).where(bull)
    out["bull_avg"] = (out["bull_high"] + out["bull_low"]) / 2

    out["bear_high"] = h.shift(k).where(bear)
    out["bear_low"] = (l if use_wicks else o).shift(k).where(bear)
    out["bear_avg"] = (out["bear_high"] + out["bear_low"]) / 2
    return out


# ------------------------------ data -----------------------------------------
def fetch_closed_bars(symbol: str, interval: str) -> pd.DataFrame:
    df = yf.Ticker(symbol).history(period=HISTORY_PERIOD[interval], interval=interval, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data returned for {symbol}")
    df = df[["Open", "High", "Low", "Close"]].dropna()

    # Drop the still-forming candle so signals are only on closed bars
    now = pd.Timestamp.now(tz=df.index.tz)
    last = df.index[-1]
    if interval == "1d":
        bar_end = last + timedelta(hours=15, minutes=45)   # treat daily bar as final after market close
    else:
        bar_end = last + timedelta(minutes=INTERVAL_MINUTES[interval])
    if bar_end > now:
        df = df.iloc[:-1]
    return df


# ------------------------------ alerts ---------------------------------------
def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[console alert]\n" + text + "\n")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    if not r.ok:
        print(f"Telegram error {r.status_code}: {r.text}", file=sys.stderr)


def format_alert(symbol: str, side: str, row: pd.Series) -> str:
    p = "bull" if side == "Bullish" else "bear"
    ob_time = row["ob_time"]
    ob_time = ob_time.strftime("%d %b %H:%M") if pd.notna(ob_time) else "?"
    return (
        f"{'🟢' if side == 'Bullish' else '🔴'} {side} Order Block - {symbol} ({INTERVAL})\n"
        f"OB candle: {ob_time}\n"
        f"High: {row[f'{p}_high']:.2f}\n"
        f"Avg : {row[f'{p}_avg']:.2f}\n"
        f"Low : {row[f'{p}_low']:.2f}\n"
        f"Not a {'BUY' if side == 'Bullish' else 'SELL'} signal - a zone to watch for limit orders."
    )


# ------------------------------ state ----------------------------------------
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ------------------------------ main logic -----------------------------------
def check_symbol(symbol: str, state: dict) -> int:
    df = fetch_closed_bars(symbol, INTERVAL)
    det = detect_order_blocks(df)

    last_seen = state.get(symbol)
    if last_seen:
        new_bars = det[det.index > pd.Timestamp(last_seen)]
    else:
        new_bars = det.tail(1)          # first run: only the latest bar, avoids flooding old signals

    sent = 0
    for ts, row in new_bars.iterrows():
        if row["bull"]:
            send_telegram(format_alert(symbol, "Bullish", row)); sent += 1
        if row["bear"]:
            send_telegram(format_alert(symbol, "Bearish", row)); sent += 1

    state[symbol] = det.index[-1].isoformat()
    return sent


def run_cycle() -> None:
    state = load_state()
    total = 0
    for sym in SYMBOLS:
        try:
            total += check_symbol(sym, state)
        except Exception as e:
            print(f"[{datetime.now(IST):%H:%M:%S}] {sym}: {e}", file=sys.stderr)
    save_state(state)
    print(f"[{datetime.now(IST):%a %H:%M:%S}] checked {len(SYMBOLS)} symbols, {total} alert(s)")


def market_is_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    # small grace after close so the final candle gets checked
    return MARKET_OPEN <= t <= (15, 40)


def seconds_to_next_bar(now: datetime) -> float:
    minutes = INTERVAL_MINUTES[INTERVAL]
    if minutes >= 1440:
        return 1800
    since_open = now.hour * 60 + now.minute - (MARKET_OPEN[0] * 60 + MARKET_OPEN[1])
    elapsed = (since_open % minutes) * 60 + now.second
    return minutes * 60 - elapsed + BUFFER_SECONDS


def show_history(n: int) -> None:
    for sym in SYMBOLS:
        det = detect_order_blocks(fetch_closed_bars(sym, INTERVAL))
        hits = det[det.bull | det.bear].tail(n)
        print(f"\n== {sym} ==")
        for ts, row in hits.iterrows():
            side = "Bullish" if row["bull"] else "Bearish"
            p = "bull" if row["bull"] else "bear"
            print(f"{row['ob_time']}  {side:7}  high {row[p + '_high']:.2f}  avg {row[p + '_avg']:.2f}  low {row[p + '_low']:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one check and exit")
    ap.add_argument("--test", action="store_true", help="send a test Telegram message")
    ap.add_argument("--history", type=int, metavar="N", help="print last N order blocks per symbol")
    args = ap.parse_args()

    if args.test:
        send_telegram("✅ Order Block alert script: Telegram connection works.")
        return
    if args.history:
        show_history(args.history)
        return
    if args.once:
        run_cycle()
        return

    print(f"Watching {SYMBOLS} on {INTERVAL} (periods={PERIODS}, threshold={THRESHOLD}%). Ctrl+C to stop.")
    while True:
        now = datetime.now(IST)
        if MARKET_HOURS_ONLY and not market_is_open(now):
            time.sleep(300)
            continue
        run_cycle()
        time.sleep(seconds_to_next_bar(datetime.now(IST)))


if __name__ == "__main__":
    main()

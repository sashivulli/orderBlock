To get it running:

Install the libraries: pip install yfinance pandas requests
Create a bot with @BotFather on Telegram and copy its token.
Get your chat ID from @userinfobot.
Set the two environment variables TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
Run python order_block_alert.py --test to confirm the message arrives.
Run python order_block_alert.py to start watching.

What to know:

Same logic as Pine: a bullish block is the last red candle followed by 5 green ones, and a bearish block is the reverse. The alert fires one bar after the run completes, like your TradingView alert. The settings at the top (SYMBOLS, INTERVAL, PERIODS, THRESHOLD, USE_WICKS) match your indicator inputs.
Defaults: four NSE stocks on 15-minute candles, checked only during market hours (9:15–15:40 IST, Monday to Friday). Edit SYMBOLS to use your own list.
Closed candles only: the half-formed current candle is dropped, so you won’t get alerts that disappear later.
No repeats: it saves the last bar it checked in ob_alert_state.json, so restarting won’t resend old alerts.
Handy flags: --history 10 prints the last 10 order blocks per stock so you can compare them with your TradingView chart. --once does a single check, useful with cron.
Data limits: yfinance intraday data for NSE is free but sometimes delayed by a minute or two, so very short intervals can be unreliable.
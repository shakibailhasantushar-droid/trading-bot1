import requests
import time
import threading
import certifi
import traceback
from concurrent.futures import ThreadPoolExecutor

# =========================
# TELEGRAM
# =========================
TELEGRAM_BOT_TOKEN = "8626:AAGv8017474Ww5PoksQUoFYs1nVEBHPROr30SgCTI"
TELEGRAM_CHAT_ID = "6516267389"

# =========================
# SETTINGS
# =========================
SCAN_INTERVAL = 40
COOLDOWN_SECONDS = 120
MAX_THREADS = 10
MAX_PAIRS = 200

session = requests.Session()
lock = threading.Lock()
last_sent = {}
memory = {}

# =========================
# LOG SAFE PRINT
# =========================
def log(msg):
    print(f"[BOT] {msg}", flush=True)

# =========================
# PAIRS
# =========================
def get_all_pairs():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"

    try:
        r = session.get(url, timeout=10, verify=certifi.where())
        data = r.json()

        # ✅ SAFE CHECK (IMPORTANT FIX)
        if "symbols" not in data:
            print("BINANCE RESPONSE ERROR:", data)
            return []

        pairs = [
            s["symbol"]
            for s in data["symbols"]
            if s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ]

        priority = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
            "XRPUSDT", "ADAUSDT", "DOGEUSDT", "TONUSDT",
            "AVAXUSDT", "LINKUSDT"
        ]

        ordered = priority + [p for p in pairs if p not in priority]

        return ordered[:MAX_PAIRS]

    except Exception as e:
        print("PAIR FETCH ERROR:", e)
        return []
# =========================
# KLINES
# =========================
def get_klines(symbol, interval, limit=100):
    url = "https://fapi.binance.com/fapi/v1/klines"

    try:
        r = session.get(
            url,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
            verify=certifi.where()
        )

        data = r.json()

        if not isinstance(data, list):
            return None

        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        closes = [float(x[4]) for x in data]

        return highs, lows, closes

    except Exception as e:
        log(f"KLINES ERROR {symbol}: {e}")
        return None

# =========================
# ATR
# =========================
def atr(highs, lows, closes, period=14):
    trs = []

    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / max(len(trs), 1)

    return sum(trs[-period:]) / period

# =========================
# EMA
# =========================
def ema(data, period):
    k = 2 / (period + 1)
    ema_val = data[0]

    for price in data[1:]:
        ema_val = price * k + ema_val * (1 - k)

    return ema_val

# =========================
# RSI
# =========================
def rsi(closes):
    if len(closes) < 15:
        return 50

    gains = losses = 0

    for i in range(-14, -1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)

    if losses == 0:
        return 100

    rs = gains / losses
    return 100 - (100 / (1 + rs))

# =========================
# ORDERBOOK
# =========================
def orderbook_pressure(symbol):
    url = "https://fapi.binance.com/fapi/v1/depth"

    try:
        r = session.get(url, params={"symbol": symbol, "limit": 20}, timeout=5, verify=certifi.where())
        data = r.json()

        bids = sum(float(x[1]) for x in data.get("bids", []))
        asks = sum(float(x[1]) for x in data.get("asks", []))

        return bids / asks if asks != 0 else 1

    except:
        return 1

# =========================
# MULTI TF
# =========================
def get_multi(symbol):
    return (
        get_klines(symbol, "1m"),
        get_klines(symbol, "5m"),
        get_klines(symbol, "15m")
    )

# =========================
# TP/SL SAFE FIX
# =========================
def smart_tp_sl(price, vol, score, direction):

    # safe risk cap (VERY IMPORTANT)
    risk = min(price * 0.004, vol * 1.5)

    if direction == "BUY":
        sl = price - risk

        if score >= 6:
            tp = price + risk * 3
        elif score >= 3:
            tp = price + risk * 2
        else:
            tp = price + risk * 1.5

    else:
        sl = price + risk

        if score <= -6:
            tp = price - risk * 3
        elif score <= -3:
            tp = price - risk * 2
        else:
            tp = price - risk * 1.5

    return tp, sl

# =========================
# ANALYZE
# =========================
def analyze(symbol):

    data = get_multi(symbol)
    if not data:
        return None

    (h1, l1, c1), (h5, l5, c5), (h15, l15, c15) = data

    if len(c1) < 50:
        return None

    price = c1[-1]

    ema20 = ema(c1, 20)
    ema50 = ema(c1, 50)
    rsi_val = rsi(c1)

    trend5 = 1 if ema(c5, 20) > ema(c5, 50) else -1
    trend15 = 1 if ema(c15, 20) > ema(c15, 50) else -1

    pressure = orderbook_pressure(symbol)

    score = 0
    score += 1 if ema20 > ema50 else -1

    if rsi_val > 55:
        score += 1
    elif rsi_val < 45:
        score -= 1

    score += trend5 * 2
    score += trend15 * 2

    if pressure > 1.1:
        score += 0.5
    elif pressure < 0.9:
        score -= 0.5

    vol = atr(h1, l1, c1)

    if score >= 2:
        tp, sl = smart_tp_sl(price, vol, score, "BUY")
        return "BUY 🟢", price, sl, tp, score

    elif score <= -2:
        tp, sl = smart_tp_sl(price, vol, score, "SELL")
        return "SELL 🔴", price, sl, tp, score

    return None

# =========================
# SEND TELEGRAM
# =========================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        session.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"TELEGRAM ERROR: {e}")

# =========================
# PROCESS
# =========================
def process(symbol):

    try:
        res = analyze(symbol)
        if not res:
            return

        signal, price, sl, tp, score = res

        key = f"{symbol}_{signal}"
        now = time.time()

        with lock:
            if key in last_sent and now - last_sent[key] < COOLDOWN_SECONDS:
                return
            last_sent[key] = now

        msg = f"""
🚀 SNIPER BOT FIXED

💱 Pair: {symbol}
📊 Signal: {signal}
📈 Score: {score}

📥 Entry: {price}
🎯 TP: {tp}
🛑 SL: {sl}
"""

        log(msg)
        send_telegram(msg)

    except Exception as e:
        log(f"PROCESS ERROR {symbol}: {e}")
        traceback.print_exc()

# =========================
# MAIN LOOP (RAILWAY SAFE)
# =========================
log("🔥 BOT STARTED (RAILWAY STABLE VERSION)")

pairs = get_all_pairs()

while True:
    try:
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
            list(ex.map(process, pairs))

    except Exception as e:
        log(f"MAIN LOOP ERROR: {e}")
        traceback.print_exc()

    log("Scanning...")
    time.sleep(SCAN_INTERVAL)

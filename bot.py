import requests
import time
import threading
import certifi
from concurrent.futures import ThreadPoolExecutor

# =========================
# TELEGRAM
# =========================
TELEGRAM_BOT_TOKEN = "8626:AAGv8017474Ww5PoksQUoFYs1nVEBHPROr30SgCTI"
TELEGRAM_CHAT_ID = "6516267389"

# =========================
# SETTINGS (TUNED)
# =========================
SCAN_INTERVAL = 20
COOLDOWN_SECONDS = 120
MAX_THREADS = 8
MAX_PAIRS = 250

# =========================
# MODE SYSTEM
# =========================
MODE = "HIGH"   # SNIPER / BALANCED / HIGH

# =========================
# ADAPTIVE SYSTEM
# =========================
ADAPTIVE = True

session = requests.Session()
lock = threading.Lock()
last_sent = {}

# =========================
# AI MEMORY SYSTEM
# =========================
memory = {}

def update_memory(symbol, result):
    if symbol not in memory:
        memory[symbol] = {"wins": 0, "loss": 0}

    if result == "WIN":
        memory[symbol]["wins"] += 1
    else:
        memory[symbol]["loss"] += 1


def ai_bias(symbol):
    if symbol not in memory:
        return 0

    d = memory[symbol]
    total = d["wins"] + d["loss"]

    if total == 0:
        return 0

    return (d["wins"] / total) * 2 - 1


# =========================
# ORDER BOOK
# =========================
def orderbook_pressure(symbol):
    url = "https://fapi.binance.com/fapi/v1/depth"

    try:
        r = session.get(
            url,
            params={"symbol": symbol, "limit": 20},
            timeout=5,
            verify=certifi.where()
        )
        data = r.json()

        bids = sum(float(x[1]) for x in data["bids"])
        asks = sum(float(x[1]) for x in data["asks"])

        return bids / asks if asks != 0 else 1

    except:
        return 1


# =========================
# TELEGRAM
# =========================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        session.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=(10, 30),
            verify=certifi.where()
        )
    except Exception as e:
        print("Telegram Error:", e)


# =========================
# PAIRS
# =========================
def get_all_pairs():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"

    try:
        r = session.get(url, timeout=10, verify=certifi.where())
        data = r.json()

        pairs = [
            s["symbol"]
            for s in data["symbols"]
            if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
        ]

        top_pairs = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
            "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
            "LINKUSDT", "MATICUSDT"
        ]

        final = [p for p in top_pairs if p in pairs] + [p for p in pairs if p not in top_pairs]
        return final[:MAX_PAIRS]

    except:
        return []


# =========================
# KLINES
# =========================
def get_klines(symbol, interval="5m", limit=100):
    url = "https://fapi.binance.com/fapi/v1/klines"

    try:
        r = session.get(
            url,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
            verify=certifi.where()
        )

        data = r.json()

        closes = [float(x[4]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        volumes = [float(x[5]) for x in data]

        return highs, lows, closes, volumes

    except:
        return None


# =========================
# INDICATORS
# =========================
def ema(data, period):
    if len(data) < period:
        return data[-1]

    k = 2 / (period + 1)
    ema_val = sum(data[:period]) / period

    for p in data[period:]:
        ema_val = p * k + ema_val * (1 - k)

    return ema_val


def rsi(closes, period=14):
    gains, losses = [], []

    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]

        if diff > 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(highs, lows, closes):
    trs = []

    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)

    return sum(trs[-14:]) / 14


# =========================
# ANALYZE
# =========================
def analyze(symbol):
    data = get_klines(symbol)

    if not data:
        return None

    highs, lows, closes, volumes = data

    price = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi_val = rsi(closes)
    vol = atr(highs, lows, closes)

    support = min(lows[-20:])
    resistance = max(highs[-20:])

    regime = "TREND" if abs(ema20 - ema50) / price > 0.003 else "SIDEWAYS"
    vol_ok = volumes[-1] > sum(volumes[-20:]) / 20

    pressure = orderbook_pressure(symbol)
    bias = ai_bias(symbol)

    score = 0

    if ema20 > ema50:
        score += 2
    else:
        score -= 2

    # RSI widened
    if 45 < rsi_val < 75:
        score += 1
    elif rsi_val < 35:
        score -= 1

    if vol_ok:
        score += 2

    if regime == "TREND":
        score += 2
    else:
        score -= 1

    if pressure > 1.2:
        score += 1
    elif pressure < 0.8:
        score -= 1

    score += bias

    # MODE
    if MODE == "SNIPER":
        buy_th = 6
        sell_th = -6
    elif MODE == "BALANCED":
        buy_th = 5
        sell_th = -5
    else:
        buy_th = 3
        sell_th = -3

    # ADAPTIVE
    if ADAPTIVE:
        avg_vol = sum(volumes[-20:]) / 20

        if vol > avg_vol * 1.5:
            buy_th += 1
            sell_th -= 1
        elif vol < avg_vol * 0.8:
            buy_th -= 1
            sell_th += 1

    if score >= buy_th:
        signal = "BUY 🟢"
    elif score <= sell_th:
        signal = "SELL 🔴"
    else:
        return None

    probability = min(97, max(70, int(80 + abs(score) * 5)))

    return signal, price, support, resistance, vol, probability


# =========================
# PROCESS
# =========================
def process_symbol(symbol):
    global last_sent

    try:
        result = analyze(symbol)

        if not result:
            return

        signal, price, support, resistance, vol, probability = result

        key = f"{symbol}_{signal}"
        now = time.time()

        with lock:
            if key in last_sent and now - last_sent[key] < COOLDOWN_SECONDS:
                return
            last_sent[key] = now

        risk = vol * 2
        reward = vol * 3

        if "BUY" in signal:
            tp = price + reward
            sl = price - risk
        else:
            tp = price - reward
            sl = price + risk

        msg = f"""
🚀 INSTITUTIONAL SNIPER BOT

💱 Pair: {symbol}
⚙ Mode: {MODE}
🧠 Adaptive: {ADAPTIVE}

{signal}
🔥 Probability: {probability}%

📥 Entry: {round(price, 6)}
🎯 TP: {round(tp, 6)}
🛑 SL: {round(sl, 6)}

📊 Support: {round(support, 6)}
📊 Resistance: {round(resistance, 6)}
"""

        print(msg)
        send_telegram(msg)

    except Exception as e:
        print(symbol, "Error:", e)


# =========================
# MAIN
# =========================
print("🔥 INSTITUTIONAL SNIPER BOT RUNNING")

pairs = get_all_pairs()
print("Loaded Pairs:", len(pairs))

while True:
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as ex:
        ex.map(process_symbol, pairs)

    print("⏳ scanning...")
    time.sleep(SCAN_INTERVAL)

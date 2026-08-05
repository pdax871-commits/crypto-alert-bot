import asyncio
import sqlite3
import requests
import pandas as pd
from ta.trend import EMAIndicator
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from typing import List, Dict

app = FastAPI()
templates = Jinja2Templates(directory="templates")

http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TradingAlertBot/2.0"
})

TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
DB_FILE = "alerts.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS horizontal_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            message TEXT NOT NULL,
            triggered INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def load_alerts_from_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, symbol, price, message, triggered FROM horizontal_alerts")
    rows = cursor.fetchall()
    conn.close()
    
    alerts = []
    for row in rows:
        alerts.append({
            "id": row[0],
            "symbol": row[1],
            "price": row[2],
            "message": row[3],
            "triggered": bool(row[4])
        })
    return alerts

def db_add_alert(symbol: str, price: float, message: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO horizontal_alerts (symbol, price, message, triggered) VALUES (?, ?, ?, 0)",
        (symbol, price, message)
    )
    alert_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return alert_id

def db_delete_alert(alert_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM horizontal_alerts WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()

def db_mark_triggered(alert_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE horizontal_alerts SET triggered = 1 WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()

# BY DEFAULT OFF (False)
SYSTEM_STATE = {
    "ema_9_20_active": False,
    "ema_200_active": False,
    "instruments": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
    "timeframes": ["1m", "5m", "15m", "1h", "4h"],
    "horizontal_alerts": []
}

class CustomAlertRequest(BaseModel):
    model_config = ConfigDict(strict=False)
    symbol: str
    price: float
    message: str

class ToggleRequest(BaseModel):
    model_config = ConfigDict(strict=False)
    alert_type: str
    status: bool

async def async_send_telegram_msg(msg: str):
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print(f"\n[LOCAL ALERT - TELEGRAM NOT CONFIGURED]:\n{msg}\n")
        return
    
    def _send():
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        try:
            http_session.post(url, json=payload, timeout=4)
        except Exception as e:
            print(f"Telegram Delivery Error: {e}")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send)

def fetch_klines(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    endpoints = [
        f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
        f"https://api1.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
        f"https://api2.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
        f"https://api3.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    ]
    
    for url in endpoints:
        try:
            res = http_session.get(url, timeout=5).json()
            if isinstance(res, list) and len(res) > 0:
                df = pd.DataFrame(res, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'qav', 'num_trades', 'tb_base', 'tb_quote', 'ignore'
                ])
                df['open'] = df['open'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['close'] = df['close'].astype(float)
                return df
        except Exception as e:
            continue
            
    return pd.DataFrame()

async def background_alert_scanner():
    while True:
        try:
            for symbol in SYSTEM_STATE["instruments"]:
                for tf in SYSTEM_STATE["timeframes"]:
                    df = fetch_klines(symbol, tf, limit=250)
                    if df.empty or len(df) < 200:
                        continue
                    
                    df['EMA_9'] = EMAIndicator(close=df['close'], window=9).ema_indicator()
                    df['EMA_20'] = EMAIndicator(close=df['close'], window=20).ema_indicator()
                    df['EMA_200'] = EMAIndicator(close=df['close'], window=200).ema_indicator()

                    curr_close = df['close'].iloc[-1]
                    prev_close = df['close'].iloc[-2]
                    curr_high = df['high'].iloc[-1]
                    curr_low = df['low'].iloc[-1]

                    curr_ema9 = df['EMA_9'].iloc[-1]
                    curr_ema20 = df['EMA_20'].iloc[-1]
                    prev_ema9 = df['EMA_9'].iloc[-2]
                    prev_ema20 = df['EMA_20'].iloc[-2]
                    curr_ema200 = df['EMA_200'].iloc[-1]

                    if SYSTEM_STATE["ema_9_20_active"]:
                        if prev_ema9 <= prev_ema20 and curr_ema9 > curr_ema20:
                            msg = f"🚨 *9-20 EMA BULLISH CROSSOVER* 🚨\n\n*Symbol:* {symbol}\n*Timeframe:* {tf}\n*Price:* ${curr_close}\n*EMA 9:* {curr_ema9:.2f} | *EMA 20:* {curr_ema20:.2f}"
                            await async_send_telegram_msg(msg)
                        elif prev_ema9 >= prev_ema20 and curr_ema9 < curr_ema20:
                            msg = f"⚠️ *9-20 EMA BEARISH CROSSOVER* ⚠️\n\n*Symbol:* {symbol}\n*Timeframe:* {tf}\n*Price:* ${curr_close}\n*EMA 9:* {curr_ema9:.2f} | *EMA 20:* {curr_ema20:.2f}"
                            await async_send_telegram_msg(msg)

                    if SYSTEM_STATE["ema_200_active"]:
                        if curr_low <= curr_ema200 <= curr_high:
                            msg = f"🎯 *PRICE TOUCHED 200 EMA* 🎯\n\n*Symbol:* {symbol}\n*Timeframe:* {tf}\n*Current Price:* ${curr_close}\n*EMA 200:* {curr_ema200:.2f}"
                            await async_send_telegram_msg(msg)

                    for h_alert in SYSTEM_STATE["horizontal_alerts"]:
                        if h_alert["symbol"] == symbol and not h_alert["triggered"]:
                            target_p = h_alert["price"]
                            if (prev_close < target_p <= curr_close) or (prev_close > target_p >= curr_close):
                                msg = f"🔔 *HORIZONTAL ALERT TRIGGERED* 🔔\n\n*Symbol:* {symbol}\n*Target Price:* ${target_p}\n*Current Price:* ${curr_close}\n*Note:* {h_alert['message']}"
                                await async_send_telegram_msg(msg)
                                h_alert["triggered"] = True
                                db_mark_triggered(h_alert["id"])

        except Exception as e:
            print(f"Error in background scanner loop: {e}")

        await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    init_db()
    SYSTEM_STATE["horizontal_alerts"] = load_alerts_from_db()
    asyncio.create_task(background_alert_scanner())

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/klines")
def get_klines(symbol: str = "BTCUSDT", interval: str = "15m"):
    df = fetch_klines(symbol, interval, limit=300)
    if df.empty:
        return {"candles": [], "ema9": [], "ema20": [], "ema200": []}
    
    df['EMA_9'] = EMAIndicator(close=df['close'], window=9).ema_indicator()
    df['EMA_20'] = EMAIndicator(close=df['close'], window=20).ema_indicator()
    df['EMA_200'] = EMAIndicator(close=df['close'], window=200).ema_indicator()

    candles = []
    ema9, ema20, ema200 = [], [], []

    for _, row in df.iterrows():
        t = int(row['timestamp'] // 1000)
        candles.append({"time": t, "open": row['open'], "high": row['high'], "low": row['low'], "close": row['close']})
        if pd.notna(row['EMA_9']): ema9.append({"time": t, "value": float(row['EMA_9'])})
        if pd.notna(row['EMA_20']): ema20.append({"time": t, "value": float(row['EMA_20'])})
        if pd.notna(row['EMA_200']): ema200.append({"time": t, "value": float(row['EMA_200'])})

    return {"candles": candles, "ema9": ema9, "ema20": ema20, "ema200": ema200}

@app.get("/api/system-state")
def get_system_state():
    return SYSTEM_STATE

@app.post("/api/toggle-alert")
def toggle_alert(data: ToggleRequest):
    if data.alert_type == "ema_9_20":
        SYSTEM_STATE["ema_9_20_active"] = data.status
    elif data.alert_type == "ema_200":
        SYSTEM_STATE["ema_200_active"] = data.status
    return {"status": "success", "state": SYSTEM_STATE}

@app.post("/api/add-horizontal-alert")
def add_horizontal_alert(data: CustomAlertRequest):
    alert_id = db_add_alert(data.symbol, data.price, data.message)
    SYSTEM_STATE["horizontal_alerts"] = load_alerts_from_db()
    return {"status": "success", "alerts": SYSTEM_STATE["horizontal_alerts"]}

@app.delete("/api/delete-horizontal-alert/{alert_id}")
def delete_horizontal_alert(alert_id: int):
    db_delete_alert(alert_id)
    SYSTEM_STATE["horizontal_alerts"] = load_alerts_from_db()
    return {"status": "success", "alerts": SYSTEM_STATE["horizontal_alerts"]}

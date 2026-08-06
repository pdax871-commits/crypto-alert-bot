import os
import json
import time
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder='templates')

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ALERTS_FILE = "alerts.json"

def load_alerts():
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, "r") as f: 
                return json.load(f)
        except: pass
    return []

def save_alerts(alerts):
    try:
        with open(ALERTS_FILE, "w") as f: 
            json.dump(alerts, f)
    except: pass

def send_telegram_alert(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: 
        print(f"[TELEGRAM NOT CONFIGURED]: {msg}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema_list = []
    ema = prices[0]
    for i, p in enumerate(prices):
        if i == 0:
            ema = p
        else:
            ema = (p * k) + (ema * (1 - k))
        if i >= period - 1:
            ema_list.append(ema)
    return ema_list

# 24x7 BACKGROUND SCAN ENGINE (UptimeRobot / Ping Triggered)
def process_active_alerts():
    alerts = load_alerts()
    if not alerts: return []
    
    remaining = []
    triggered_any = False

    mexc_tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "4h"}

    for a in alerts:
        sym = a.get('symbol', '').upper()
        
        # 1. HANDLE 9-20 EMA CROSSOVER ALERTS IN BACKGROUND
        if a.get('type') == 'ema_9_20':
            tf_str = a.get('tf', '15m')
            tf_mexc = mexc_tf_map.get(tf_str, '15m')
            try:
                url = f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval={tf_mexc}&limit=100"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    candles = res.json()
                    if len(candles) >= 25:
                        closes = [float(c[4]) for c in candles]
                        ema9 = calculate_ema(closes, 9)
                        ema20 = calculate_ema(closes, 20)

                        # Closed Candle Index -2 and Previous Index -3
                        closed_ema9 = ema9[-2]
                        closed_ema20 = ema20[-2]
                        prev_ema9 = ema9[-3]
                        prev_ema20 = ema20[-3]
                        
                        last_notified = a.get('last_notified_time', 0)
                        candle_close_time = candles[-2][6]

                        if candle_close_time != last_notified:
                            if prev_ema9 <= prev_ema20 and closed_ema9 > closed_ema20:
                                send_telegram_alert(f"🚨 *9-20 EMA BULLISH CROSSOVER!*\n\nCoin: *{sym}*\nTimeframe: *{tf_str}*\nPrice: *${closes[-2]:.4f}*")
                                a['last_notified_time'] = candle_close_time
                                triggered_any = True
                            elif prev_ema9 >= prev_ema20 and closed_ema9 < closed_ema20:
                                send_telegram_alert(f"⚠️ *9-20 EMA BEARISH CROSSOVER!*\n\nCoin: *{sym}*\nTimeframe: *{tf_str}*\nPrice: *${closes[-2]:.4f}*")
                                a['last_notified_time'] = candle_close_time
                                triggered_any = True

            except Exception as e:
                print(f"EMA Scan Error for {sym}: {e}")
            remaining.append(a)

        # 2. HANDLE 200 EMA TOUCH ALERTS IN BACKGROUND
        elif a.get('type') == 'ema_200':
            tf_str = a.get('tf', '15m')
            tf_mexc = mexc_tf_map.get(tf_str, '15m')
            try:
                url = f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval={tf_mexc}&limit=250"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    candles = res.json()
                    if len(candles) >= 205:
                        closes = [float(c[4]) for c in candles]
                        ema200 = calculate_ema(closes, 200)

                        closed_candle = candles[-2]
                        low = float(closed_candle[3])
                        high = float(closed_candle[2])
                        closed_ema200 = ema200[-2]

                        last_notified = a.get('last_notified_time', 0)
                        candle_close_time = closed_candle[6]

                        if low <= closed_ema200 <= high and candle_close_time != last_notified:
                            send_telegram_alert(f"🎯 *200 EMA TOUCHED!*\n\nCoin: *{sym}*\nTimeframe: *{tf_str}*\nPrice: *${closes[-2]:.4f}*")
                            a['last_notified_time'] = candle_close_time
                            triggered_any = True

            except Exception as e:
                print(f"200 EMA Scan Error for {sym}: {e}")
            remaining.append(a)

        # 3. HANDLE HORIZONTAL PRICE ALERTS
        else:
            target = float(a.get('price', 0))
            created_at_ms = a.get('created_at', 0)
            hit = False

            try:
                url = f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=1m&limit=15"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    candles = res.json()
                    for c in candles:
                        if c[6] >= created_at_ms and float(c[3]) <= target <= float(c[2]):
                            hit = True
                            break
                    if hit:
                        send_telegram_alert(f"🔔 *HORIZONTAL ALERT TRIGGERED!*\n\nCoin: *{sym}*\nTarget Hit: *${target:.4f}*\n📝 *{a.get('message', 'Level Reached')}*")
                        triggered_any = True
                    else:
                        remaining.append(a)
                else:
                    remaining.append(a)
            except Exception:
                remaining.append(a)

    if triggered_any:
        save_alerts(remaining)
        
    return remaining

@app.route('/ping')
def ping():
    remaining = process_active_alerts()
    return jsonify({"status": "awake", "active_alerts": len(remaining)})

@app.route('/api/klines')
def get_klines():
    symbol = request.args.get('symbol', 'BTCUSDT').upper()
    interval = request.args.get('interval', '15m')
    
    mexc_tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "4h"}
    tf = mexc_tf_map.get(interval, "15m")
    
    url = f"https://api.mexc.com/api/v3/klines?symbol={symbol}&interval={tf}&limit=250"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return jsonify(res.json())
    except Exception as e:
        print(f"Klines Error: {e}")
        
    return jsonify([])

@app.route('/api/get_alerts', methods=['GET'])
def get_alerts():
    process_active_alerts()
    return jsonify(load_alerts())

@app.route('/')
@app.route('/index')
def index(): 
    return render_template('index.html')

@app.route('/api/add_alert', methods=['POST'])
def add_alert():
    data = request.json
    try:
        server_time_ms = requests.get("https://api.mexc.com/api/v3/time", timeout=3).json()['serverTime']
    except:
        server_time_ms = int(time.time() * 1000)
        
    data['created_at'] = server_time_ms
    
    alerts = load_alerts()
    if data.get('type') in ['ema_9_20', 'ema_200']:
        alerts = [a for a in alerts if not (a.get('type') == data.get('type') and a.get('symbol') == data.get('symbol') and a.get('tf') == data.get('tf'))]
    
    alerts.append(data)
    save_alerts(alerts)
    return jsonify({"status": "success", "alerts": alerts})

@app.route('/api/trigger_alert', methods=['POST'])
def trigger_alert():
    data = request.json
    sym = data.get('symbol', '').upper()
    price = float(data.get('price', 0))
    msg = data.get('message', '')

    telegram_msg = f"🚨 *INDICATOR ALERT TRIGGERED!*\n\nCoin: *{sym}*\nPrice: *${price:.4f}*\n📝 *{msg}*"
    send_telegram_alert(telegram_msg)
    return jsonify({"status": "triggered"})

@app.route('/api/delete_alert', methods=['POST'])
def delete_alert():
    data = request.json
    alerts = load_alerts()
    
    if data.get('type') in ['ema_9_20', 'ema_200']:
        alerts = [a for a in alerts if not (a.get('type') == data.get('type') and a.get('symbol') == data.get('symbol') and a.get('tf') == data.get('tf'))]
    else:
        sym = data.get('symbol', '').upper()
        price = float(data.get('price', 0))
        alerts = [a for a in alerts if not (a.get('symbol', '').lower() == sym.lower() and abs(float(a.get('price', 0)) - price) < 0.0001)]
        
    save_alerts(alerts)
    return jsonify({"status": "deleted", "alerts": alerts})

@app.route('/api/clear_alerts', methods=['POST'])
def clear_alerts():
    save_alerts([])
    return jsonify({"status": "cleared"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

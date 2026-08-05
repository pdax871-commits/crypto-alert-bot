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

def process_active_alerts():
    alerts = load_alerts()
    if not alerts: return []
    
    remaining = []
    triggered_any = False

    for a in alerts:
        sym = a['symbol'].upper()
        target = float(a['price'])
        created_at_ms = a.get('created_at', 0)

        try:
            url = f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=1m&limit=15"
            res = requests.get(url, timeout=5)
            
            if res.status_code == 200:
                candles = res.json()
                hit = False
                
                for c in candles:
                    candle_close_time_ms = c[6]
                    if candle_close_time_ms < created_at_ms:
                        continue
                    
                    high = float(c[2])
                    low = float(c[3])
                    
                    if low <= target <= high:
                        hit = True
                        break
                
                if hit:
                    msg = f"🔔 *HORIZONTAL ALERT TRIGGERED!*\n\nCoin: *{sym}*\nTarget Hit: *${target:.4f}*\n📝 *{a.get('message', 'Level Reached')}*"
                    send_telegram_alert(msg)
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
    alerts.append(data)
    save_alerts(alerts)
    return jsonify({"status": "success", "total": len(alerts)})

@app.route('/api/trigger_alert', methods=['POST'])
def trigger_alert():
    data = request.json
    sym = data.get('symbol', '').upper()
    price = float(data.get('price', 0))
    msg = data.get('message', '')

    telegram_msg = f"🚨 *ALERT TRIGGERED!*\n\nCoin: *{sym}*\nPrice: *${price:.4f}*\n📝 *{msg}*"
    send_telegram_alert(telegram_msg)
    
    alerts = load_alerts()
    alerts = [a for a in alerts if not (a['symbol'].lower() == sym.lower() and abs(float(a['price']) - price) < 0.0001)]
    save_alerts(alerts)
    return jsonify({"status": "triggered"})

@app.route('/api/clear_alerts', methods=['POST'])
def clear_alerts():
    save_alerts([])
    return jsonify({"status": "cleared"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

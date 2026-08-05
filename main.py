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
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except: pass

# ========================================================
# ENGINE: BACKGROUND SCANNER (Geo-Block Bypassed via MEXC)
# ========================================================
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
            # 🛡️ BYPASS: Fetching from MEXC API (Allows Render's US IP)
            url = f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=1m&limit=15"
            res = requests.get(url, timeout=5)
            
            if res.status_code == 200:
                candles = res.json()
                hit = False
                
                for c in candles:
                    candle_close_time_ms = c[6] # Candle closing timestamp
                    
                    # Agar ye candle alert lagane se pehle ban chuki thi, toh ignore karo
                    if candle_close_time_ms < created_at_ms:
                        continue
                    
                    high = float(c[2])
                    low = float(c[3])
                    
                    if low <= target <= high:
                        hit = True
                        break
                
                if hit:
                    msg = f"🚨 *BACKGROUND ALERT TRIGGERED!*\n\nCoin: *{sym}*\nTarget Hit: *${target:.4f}*\n📝 *{a.get('message', 'Level Reached')}*"
                    send_telegram_alert(msg)
                    triggered_any = True
                else:
                    remaining.append(a)
            else:
                remaining.append(a)
        except Exception as e:
            remaining.append(a)

    if triggered_any:
        save_alerts(remaining)
        
    return remaining

# 🌐 API ENDPOINTS

@app.route('/ping')
def ping():
    # Jab UptimeRobot hit karega, background check trigger hoga
    remaining = process_active_alerts()
    return jsonify({"status": "awake", "active_alerts": len(remaining)})

@app.route('/api/get_alerts', methods=['GET'])
def get_alerts():
    return jsonify(load_alerts())

@app.route('/')
@app.route('/index')
def index(): 
    return render_template('index.html')

@app.route('/api/add_alert', methods=['POST'])
def add_alert():
    data = request.json
    
    # ⏱️ DOUBLE CHECK FIX: Accurate Cloud Server Time
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

    telegram_msg = f"🚨 *INSTANT ALERT!*\n\nCoin: *{sym}*\nPrice: *${price:.4f}*\n📝 *{msg}*"
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

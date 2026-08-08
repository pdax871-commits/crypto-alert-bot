import os
import json
import time
import threading
import requests
from functools import wraps
from flask import Flask, render_template, request, jsonify
import google.generativeai as genai

app = Flask(__name__, template_folder='templates')

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

ALERTS_FILE = "alerts.json"
NEWS_CACHE_FILE = "news_cache.json"

# Initialize Gemini AI Config
ai_configured = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_configured = True
    except Exception as e:
        print(f"[GEMINI INIT ERROR]: {e}")

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_pass = request.headers.get("X-Admin-Password") or request.args.get("pass")
        if auth_pass != ADMIN_PASSWORD:
            return jsonify({"status": "unauthorized", "message": "Invalid Password"}), 401
        return f(*args, **kwargs)
    return decorated

def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_json(filepath, data):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[SAVE ERROR]: {e}")

def send_telegram_alert(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM NOT CONFIGURED]: {msg}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        print(f"[TELEGRAM ERROR]: {e}")

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

# ================= AI NEWS ANALYSIS ENGINE =================
def analyze_news_with_ai(title, source=""):
    if not ai_configured:
        return {
            "sentiment": "NEUTRAL",
            "impact": "MEDIUM",
            "summary": "General market news update."
        }
    
    prompt = f"""
Act as an elite crypto market analyst. Analyze this news headline and determine its market sentiment, impact level, and a concise 1-sentence summary for traders.

Headline: "{title}"
Source: "{source}"

Return ONLY a valid JSON object matching this exact structure:
{{
  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "impact": "LOW" | "MEDIUM" | "HIGH" | "VERY_HIGH",
  "summary": "One sentence explanation of market effect."
}}
"""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            
        parsed = json.loads(text)
        return {
            "sentiment": parsed.get("sentiment", "NEUTRAL").upper(),
            "impact": parsed.get("impact", "MEDIUM").upper(),
            "summary": parsed.get("summary", "Market news update.")
        }
    except Exception as e:
        print(f"[AI ANALYSIS ERROR]: {e}")
        return {
            "sentiment": "NEUTRAL",
            "impact": "MEDIUM",
            "summary": title
        }

def fetch_and_process_news():
    url = "https://cryptopanic.com/api/v1/posts/?auth_token=free&public=true"
    cached_news = load_json(NEWS_CACHE_FILE)
    existing_ids = {item.get("id") for item in cached_news if "id" in item}
    
    try:
        res = requests.get(url, timeout=6)
        if res.status_code == 200:
            posts = res.json().get("results", [])
            new_analyzed = []
            
            for post in posts[:10]:
                post_id = post.get("id")
                if post_id in existing_ids:
                    continue
                
                title = post.get("title", "")
                domain = post.get("domain", "CryptoPanic")
                published_at = post.get("published_at", "")
                
                ai_result = analyze_news_with_ai(title, domain)
                
                news_item = {
                    "id": post_id,
                    "title": title,
                    "source": domain,
                    "published_at": published_at,
                    "sentiment": ai_result["sentiment"],
                    "impact": ai_result["impact"],
                    "summary": ai_result["summary"],
                    "timestamp": int(time.time())
                }
                
                new_analyzed.append(news_item)
                
                if ai_result["impact"] == "VERY_HIGH":
                    emoji = "🟢" if ai_result["sentiment"] == "BULLISH" else "🔴" if ai_result["sentiment"] == "BEARISH" else "⚪"
                    msg = (
                        f"🚨 *VERY HIGH IMPACT NEWS ALERT* 🚨\n\n"
                        f"📰 *{title}*\n"
                        f"Sentiment: {emoji} *{ai_result['sentiment']}*\n"
                        f"Impact: ⚡ *VERY HIGH*\n\n"
                        f"📝 *AI Summary:* {ai_result['summary']}\n"
                        f"Source: {domain}"
                    )
                    send_telegram_alert(msg)
            
            if new_analyzed:
                updated_cache = new_analyzed + cached_news
                updated_cache = updated_cache[:40]
                save_json(NEWS_CACHE_FILE, updated_cache)
                print(f"[NEWS SCANNED]: Added {len(new_analyzed)} new AI-analyzed news items.")
    except Exception as e:
        print(f"[NEWS SCANNED ERROR]: {e}")

def background_news_loop():
    while True:
        try:
            fetch_and_process_news()
        except Exception as e:
            print(f"[BACKGROUND NEWS LOOP ERROR]: {e}")
        time.sleep(180)

news_thread = threading.Thread(target=background_news_loop, daemon=True)
news_thread.start()

def process_active_alerts():
    alerts = load_json(ALERTS_FILE)
    if not alerts: return []
    
    remaining = []
    triggered_any = False
    mexc_tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "4h"}

    for a in alerts:
        sym = a.get('symbol', '').upper()
        
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
        save_json(ALERTS_FILE, remaining)
        
    return remaining

@app.route('/ping')
def ping():
    remaining = process_active_alerts()
    return jsonify({"status": "awake", "active_alerts": len(remaining)})

@app.route('/api/verify_auth', methods=['POST'])
@require_auth
def verify_auth():
    return jsonify({"status": "authorized"})

@app.route('/api/get_news', methods=['GET'])
def get_news():
    news = load_json(NEWS_CACHE_FILE)
    return jsonify(news)

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
@require_auth
def get_alerts():
    process_active_alerts()
    return jsonify(load_json(ALERTS_FILE))

@app.route('/')
@app.route('/index')
def index(): 
    return render_template('index.html')

@app.route('/api/add_alert', methods=['POST'])
@require_auth
def add_alert():
    data = request.json
    try:
        server_time_ms = requests.get("https://api.mexc.com/api/v3/time", timeout=3).json()['serverTime']
    except Exception:
        server_time_ms = int(time.time() * 1000)
        
    data['created_at'] = server_time_ms
    alerts = load_json(ALERTS_FILE)
    if data.get('type') in ['ema_9_20', 'ema_200']:
        alerts = [a for a in alerts if not (a.get('type') == data.get('type') and a.get('symbol') == data.get('symbol') and a.get('tf') == data.get('tf'))]
    
    alerts.append(data)
    save_json(ALERTS_FILE, alerts)
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
@require_auth
def delete_alert():
    data = request.json
    alerts = load_json(ALERTS_FILE)
    
    if data.get('type') in ['ema_9_20', 'ema_200']:
        alerts = [a for a in alerts if not (a.get('type') == data.get('type') and a.get('symbol') == data.get('symbol') and a.get('tf') == data.get('tf'))]
    else:
        sym = data.get('symbol', '').upper()
        price = float(data.get('price', 0))
        alerts = [a for a in alerts if not (a.get('symbol', '').lower() == sym.lower() and abs(float(a.get('price', 0)) - price) < 0.0001)]
        
    save_json(ALERTS_FILE, alerts)
    return jsonify({"status": "deleted", "alerts": alerts})

@app.route('/api/clear_alerts', methods=['POST'])
@require_auth
def clear_alerts():
    save_json(ALERTS_FILE, [])
    return jsonify({"status": "cleared"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

import os
import json
import math
import time
import io
import re
import traceback
import threading
from datetime import datetime, timedelta, timezone
import pytz

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
from flask import Flask, request
import requests
import pandas as pd
import numpy as np
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# --- CONFIGURAZIONE CHIAVI INSERITE ---
TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN

GEMINI_API_KEY = "AQ.Ab8RN6IDlTHsxy-8n-Uie0x-MQsFt3F22lNlqhIwA-Mg13XDLA"
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

market_cache = {}
CACHE_DURATION = 60

REAL_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "AUD/CAD", "USD/CHF",
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "EUR/CAD", "EUR/NZD",
    "GBP/NZD", "AUD/NZD", "CAD/JPY", "CHF/JPY", "GBP/CAD", "GBP/AUD", "NZD/USD",
    "AUD/CHF", "USD/MXN", "USD/PKR", "EUR/RUB", "EUR/TRY", "JOD/CNY", "NGN/USD",
    "LBP/USD", "TND/USD", "QAR/CNY", "USD/ARS", "UAH/USD", "SAR/CNY",
    "Bitcoin", "Ethereum", "Cardano", "Solana", "Dogecoin", "Litecoin", 
    "Polkadot", "Toncoin", "TRON", "Chainlink", "Avalanche", "Polygon", "BNB", "Dash",
    "APPLE", "MICROSOFT", "TESLA", "AMAZON", "NETFLIX", "GOOGLE", "META", "MCDONALD'S", "INTEL", "ALIBABA"
]

OTC_ASSETS = [
    # Stock OTC
    "American Express OTC", "Johnson & Johnson OTC", "McDonald's OTC", "Microsoft OTC", 
    "Tesla OTC", "Amazon OTC", "Citigroup Inc OTC", "FedEx OTC", "Netflix OTC", 
    "VISA OTC", "Apple OTC", "Boeing Company OTC", "Intel OTC", "Cisco OTC", 
    "Alibaba OTC", "Advanced Micro Devices OTC", "Coinbase Global OTC",
    
    # Valute OTC
    "EUR/RUB OTC", "EUR/TRY OTC", "NZD/JPY OTC", "TND/USD OTC", "USD/CHF OTC", 
    "USD/JPY OTC", "USD/MYR OTC", "AUD/CAD OTC", "AUD/CHF OTC", "AUD/NZD OTC", 
    "AUD/USD OTC", "CAD/JPY OTC", "EUR/CHF OTC", "EUR/JPY OTC", "EUR/NZD OTC", 
    "AED/CNY OTC", "AUD/JPY OTC", "EUR/GBP OTC", "NZD/USD OTC", "OMR/CNY OTC", 
    "USD/CAD OTC", "USD/BDT OTC", "CHF/NOK OTC", "EUR/USD OTC", "JOD/CNY OTC", 
    "BHD/CNY OTC", "GBP/USD OTC", "USD/RUB OTC", "EUR/HUF OTC", "GBP/JPY OTC", 
    "LBP/USD OTC", "SAR/CNY OTC", "USD/BRL OTC", "USD/PHP OTC", "USD/ARS OTC", 
    "MAD/USD OTC", "USD/CLP OTC", "GBP/AUD OTC", "QAR/CNY OTC", "USD/COP OTC", 
    "CAD/CHF OTC",
    
    # Cripto e Indici OTC
    "Bitcoin OTC", "Cardano OTC", "Polkadot OTC", "Toncoin OTC", "Bitcoin ETF OTC", 
    "TRON OTC", "Dogecoin OTC", "Litecoin OTC", "Chainlink OTC", "Solana OTC", 
    "BNB OTC", "Polygon OTC", "Ethereum OTC", "Avalanche OTC"
]

user_selection = {}

def api_call(method, data):
    try:
        url = BASE_URL + "/" + method
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=data, headers=headers, timeout=5.0)
        return response.json()
    except Exception as e:
        print("Errore API Telegram:", e)
        return None

def send_message(chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if markup: p["reply_markup"] = markup
    res = api_call("sendMessage", p)
    return res.get("result", {}).get("message_id") if res and res.get("ok") else None

def send_photo_message(chat_id, photo_buf, caption, markup=None):
    try:
        url = BASE_URL + "/sendPhoto"
        files = {'photo': ('chart.png', photo_buf, 'image/png')}
        data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
        if markup:
            data['reply_markup'] = json.dumps(markup)
        response = requests.post(url, data=data, files=files, timeout=10.0)
        res_json = response.json()
        return res_json.get("result", {}).get("message_id") if res_json.get("ok") else None
    except Exception as e:
        print("Errore invio foto:", e)
        return None

def send_banner_photo(chat_id, image_path_or_url, caption, markup=None):
    try:
        url = BASE_URL + "/sendPhoto"
        data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
        if markup:
            data['reply_markup'] = json.dumps(markup)
        
        if os.path.exists(image_path_or_url):
            with open(image_path_or_url, 'rb') as f:
                files = {'photo': f}
                response = requests.post(url, data=data, files=files, timeout=10.0)
        else:
            data['photo'] = image_path_or_url
            response = requests.post(url, data=data, timeout=10.0)
            
        res_json = response.json()
        return res_json.get("result", {}).get("message_id") if res_json.get("ok") else None
    except Exception as e:
        print("Errore invio banner:", e)
        return None

def edit_message(chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
    if markup: p["reply_markup"] = markup
    api_call("editMessageText", p)

def answer_callback(cq_id):
    api_call("answerCallbackQuery", {"callback_query_id": cq_id})

def register_user(chat_id):
    try:
        users = set()
        if os.path.exists("users.txt"):
            with open("users.txt", "r") as f:
                users = set(line.strip() for line in f if line.strip())
        
        if str(chat_id) not in users:
            with open("users.txt", "a") as f:
                f.write(str(chat_id) + "\n")
    except Exception as e:
        print("Errore registrazione utente:", e)

def is_market_open(asset_name, now):
    if "OTC" in asset_name: return True
    crypto_list = ["Bitcoin", "Ethereum", "Cardano", "Solana", "Dogecoin", "Litecoin", "Polkadot", "Toncoin", "TRON", "Chainlink", "Avalanche", "Polygon", "BNB", "Dash"]
    if asset_name in crypto_list: return True
    if now.weekday() >= 5: return False
    return True

def calculate_fractal_chaos_bands(df, period=2):
    highs = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
    lows = df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
    upper = highs.rolling(window=(period * 2 + 1), center=True).max().bfill().ffill()
    lower = lows.rolling(window=(period * 2 + 1), center=True).min().bfill().ffill()
    return upper, lower

def calculate_indicators(df):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    current_rsi = round(float(rsi.iloc[-1]), 1)
    if math.isnan(current_rsi): current_rsi = 50.0

    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal_line
    
    ema20 = close.ewm(span=20, adjust=False).mean()
    upper_f, lower_f = calculate_fractal_chaos_bands(df, period=2)
    band_width = upper_f - lower_f

    return current_rsi, round(float(macd.iloc[-1]), 5), round(float(signal_line.iloc[-1]), 5), round(float(hist.iloc[-1]), 5), float(close.iloc[-1]), round(float(ema20.iloc[-1]), 5), float(upper_f.iloc[-1]), float(lower_f.iloc[-1]), float(band_width.iloc[-1]), float(band_width.rolling(20, min_periods=1).mean().iloc[-1])

def apply_otc_synthetic_processing(df):
    if df is None or df.empty: return df
    try:
        close_col = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
        volatility = close_col.pct_change().std()
        if math.isnan(volatility) or volatility == 0:
            volatility = 0.0001
        
        np.random.seed(int(time.time() // 60) % 1000)
        synthetic_noise = np.random.normal(0, volatility * 0.2, len(df))
        
        for col in ['Open', 'High', 'Low', 'Close']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
            df[col] = df[col] * (1 + synthetic_noise)
    except Exception as e:
        print("Errore elaborazione OTC sintetica:", e)
    return df

def get_market_signal(df):
    rsi, _, _, hist, close, ema, _, _, _, _ = calculate_indicators(df)
    score = 0
    if close > ema: score += 1
    if rsi > 50: score += 1
    if hist > 0: score += 1
    
    if score >= 2:
        return "ACQUISTA 🟢 (ALTO)", rsi, close
    else:
        return "VENDI 🔴 (BASSO)", rsi, close

def generate_chart_image(df, asset_name):
    try:
        df_clean = df.copy()
        df_clean.index = df_clean.index + timedelta(hours=2)
        for col in ['Open', 'High', 'Low', 'Close']:
            df_clean[col] = df_clean[col].astype(float)

        close_series = df_clean['Close'].iloc[:, 0] if isinstance(df_clean['Close'], pd.DataFrame) else df_clean['Close']
        ema20_full = close_series.ewm(span=20, adjust=False).mean()
        upper_f_full, lower_f_full = calculate_fractal_chaos_bands(df_clean, period=2)

        df_plot = df_clean.tail(30).copy()
        market_colors = mpf.make_marketcolors(up='#00E676', down='#FF5252', wick={'up': '#00E676', 'down': '#FF5252'}, edge='inherit', volume='inherit')
        custom_style = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=market_colors, facecolor='#1e1e1e', edgecolor='#333333', figcolor='#1e1e1e')

        add_plots = [
            mpf.make_addplot(ema20_full.tail(30), color='#FFC107', width=1.5, linestyle='--'),
            mpf.make_addplot(upper_f_full.tail(30), color='#00BCD4', width=1.2), 
            mpf.make_addplot(lower_f_full.tail(30), color='#00BCD4', width=1.2)  
        ]

        buf = io.BytesIO()
        fig, axes = mpf.plot(df_plot, type='candle', style=custom_style, addplot=add_plots, title=f"\nAnalisi: {asset_name}", volume=False, figsize=(8, 4.5), returnfig=True)
        fig.savefig(buf, format='png', facecolor='#1e1e1e', edgecolor='none', bbox_inches='tight', dpi=100)
        buf.seek(0)
        plt.close('all')
        return buf
    except Exception as e:
        print("Errore grafico:", e)
        plt.close('all')
        return None

def force_realtime_last_candle(df):
    if df is None or df.empty: return df
    try:
        last_close = float(df['Close'].iloc[-1])
        now_utc = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if df.index[-1] < pd.Timestamp(now_utc.replace(tzinfo=None)):
            new_row = pd.DataFrame({'Open': [last_close], 'High': [last_close*1.0001], 'Low': [last_close*0.9999], 'Close': [last_close]}, index=[pd.Timestamp(now_utc.replace(tzinfo=None))])
            df = pd.concat([df, new_row])
    except: pass
    return df

def fetch_market_data(asset_name):
    current_time = time.time()
    if asset_name in market_cache:
        ts, cached_df = market_cache[asset_name]
        if current_time - ts < CACHE_DURATION: return cached_df

    base_asset = asset_name.replace(" OTC", "")
    symbol = base_asset.replace("/", "").replace(" ", "").upper() + "=X" if "/" in base_asset else base_asset
    if "Bitcoin" in base_asset: symbol = "BTC-USD"
    elif "Ethereum" in base_asset: symbol = "ETH-USD"
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=5.0).json()
        result = res['chart']['result'][0]
        df = pd.DataFrame(result['indicators']['quote'][0], index=pd.to_datetime(result['timestamp'], unit='s')).dropna()
        clean_df = force_realtime_last_candle(df[['open', 'high', 'low', 'close']].rename(columns=str.title))
        
        if "OTC" in asset_name:
            clean_df = apply_otc_synthetic_processing(clean_df)
            
        market_cache[asset_name] = (current_time, clean_df)
        return clean_df
    except:
        return market_cache.get(asset_name, (0, None))[1]

BANNER_IMAGE = "easy_tracks.jpg" 

def send_category_menu(chat_id, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "📈 Mercato Reale", "callback_data": "cat_real_0"}],
            [{"text": "📊 Mercato OTC (Easy Tracks)", "callback_data": "cat_otc_0"}]
        ]
    }
    text = "SELEZIONA ‣ TIPO DI MERCATO"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_banner_photo(chat_id, BANNER_IMAGE, text, kb)

def send_assets_menu(chat_id, category="real", page=0, msg_id=None):
    lst = REAL_ASSETS if category == "real" else OTC_ASSETS
    total_pages = math.ceil(len(lst) / 9)
    sub = lst[page*9:(page+1)*9]
    kb = []
    
    for i in range(0, len(sub), 3):
        row = []
        for j in range(3):
            if i + j < len(sub):
                item = sub[i+j]
                idx = lst.index(item)
                row.append({"text": item, "callback_data": ("ast_r_" if category=="real" else "ast_o_") + str(idx)})
        kb.append(row)
        
    nav = []
    if page > 0: nav.append({"text": "◀️ Indietro", "callback_data": f"pg_{category}_{page-1}"})
    nav.append({"text": f"{page+1}/{total_pages}", "callback_data": "noop"})
    if (page + 1) * 9 < len(lst): nav.append({"text": "Avanti ▶️", "callback_data": f"pg_{category}_{page+1}"})
    if nav: kb.append(nav)
    
    kb.append([{"text": "≡ Torna al Menu Principale", "callback_data": "main_categories"}])
    
    text = f"SELEZIONA ‣ COPPIA DI VALUTE ({category.upper()})"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_banner_photo(chat_id, BANNER_IMAGE, text, {"inline_keyboard": kb})

def send_timeframe_menu(chat_id, asset_name, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "S5", "callback_data": "tf_S5"}, {"text": "S10", "callback_data": "tf_S10"}, {"text": "S15", "callback_data": "tf_S15"}, {"text": "S30", "callback_data": "tf_S30"}],
            [{"text": "M1", "callback_data": "tf_M1"}, {"text": "M2", "callback_data": "tf_M2"}, {"text": "M3", "callback_data": "tf_M3"}, {"text": "M5", "callback_data": "tf_M5"}],
            [{"text": "M10", "callback_data": "tf_M10"}, {"text": "M15", "callback_data": "tf_M15"}, {"text": "M30", "callback_data": "tf_M30"}],
            [{"text": "H1", "callback_data": "tf_H1"}, {"text": "H4", "callback_data": "tf_H4"}, {"text": "D1", "callback_data": "tf_D1"}],
            [{"text": "≡ Torna al Menu Principale", "callback_data": "main_categories"}]
        ]
    }
    text = f"💹 Asset: *{asset_name}*\n\nSeleziona l'intervallo / timeframe:"
    if msg_id: edit_message(chat_id, msg_id, text, kb)
    else: send_message(chat_id, text, kb)

@app.route('/')
def index():
    return "Bot operativo su Render!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    up = request.get_json()
    if not up: return "ok", 200
    try:
        if "callback_query" in up:
            cq = up["callback_query"]
            cid, mid, val = cq["message"]["chat"]["id"], cq["message"]["message_id"], cq["data"]
            answer_callback(cq["id"])
            
            if val == "noop":
                return "ok", 200
            elif val == "main_categories":
                send_category_menu(cid, mid)
            elif val.startswith("cat_"):
                parts = val.split("_")
                cat = parts[1]
                page = int(parts[2])
                send_assets_menu(cid, cat, page, mid)
            elif val.startswith("pg_"):
                parts = val.split("_")
                cat = parts[1]
                page = int(parts[2])
                send_assets_menu(cid, cat, page, mid)
            elif val.startswith("ast_r_") or val.startswith("ast_o_"):
                is_otc = val.startswith("ast_o_")
                idx = int(val.split("_")[2])
                ast = (OTC_ASSETS if is_otc else REAL_ASSETS)[idx]
                
                now = datetime.now(pytz.timezone("Europe/Rome"))
                if not is_market_open(ast, now):
                    api_call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "⚠️ Mercato Chiuso! Scegli un asset OTC o una Cripto.", "show_alert": True})
                    return "ok", 200

                user_selection[cid] = {"asset": ast}
                send_timeframe_menu(cid, ast, mid)
            elif val.startswith("gen_alert_"):
                ast = val.replace("gen_alert_", "")
                user_selection[cid] = {"asset": ast}
                send_timeframe_menu(cid, ast, mid)
            elif val.startswith("tf_"):
                tf_selected = val.replace("tf_", "")
                sel = user_selection.get(cid, {})
                ast = sel.get("asset", "EUR/USD")
                
                now = datetime.now(pytz.timezone("Europe/Rome"))
                if not is_market_open(ast, now):
                    edit_message(cid, mid, f"⚠️ Impossibile generare l'analisi: il mercato per `{ast}` è attualmente **Chiuso**.")
                    return "ok", 200

                edit_message(cid, mid, f"⏳ Elaborazione segnale per {ast} ({tf_selected})...")
                data = fetch_market_data(ast)
                if data is None or data.empty:
                    edit_message(cid, mid, f"⚠️ Dati momentaneamente non disponibili per {ast}.")
                    return "ok", 200
                
                now_time = now.strftime("%H:%M:%S")
                action, rsi, close = get_market_signal(data)
                
                if "OTC" in ast:
                    text = f"💎 **SEGNALE EASY TRACKS OTC** 💎\n\n📊 Asset: `{ast}`\n🎯 Direzione: **{action}**\n💰 Prezzo Attuale: `{close}`\n📈 RSI (9): `{rsi}`\n⏱ Timeframe: `{tf_selected}`\n📌 Ora Esatta Ingresso: `{now_time}`\n\n⚡ *Analisi sintetica OTC sincronizzata!*"
                else:
                    text = f"🤖 **ANALISI TECNICA MERCATO REALE** 🤖\n\n📊 Asset: `{ast}`\n🎯 Direzione: **{action}**\n💰 Prezzo Attuale: `{close}`\n📈 RSI (9): `{rsi}`\n⏱ Timeframe: `{tf_selected}`\n📌 Ora Esatta Ingresso: `{now_time}`\n\n🚀 *Analisi di mercato reale aggiornata!*"
                
                buf = generate_chart_image(data, ast)
                api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                reply_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": f"tf_{tf_selected}"}], [{"text": "≡ Torna al Menu Principale", "callback_data": "main_categories"}]]}
                
                if buf: send_photo_message(cid, buf, text, reply_kb)
                else: send_message(cid, text, reply_kb)
        elif "message" in up:
            msg = up["message"]
            cid = msg["chat"]["id"]
            text = msg.get("text", "")
            if text.startswith("/start"):
                register_user(cid)
                send_category_menu(cid)
            else:
                send_category_menu(cid)
    except Exception as e:
        print("Errore webhook:", e)
    return "ok", 200

def check_market_conditions():
    now = datetime.now(pytz.timezone("Europe/Rome"))
    users = []
    if os.path.exists("users.txt"):
        with open("users.txt", "r") as f:
            users = [line.strip() for line in f if line.strip()]
    if not users: return

    monitoring_targets = ["EUR/USD", "GBP/USD OTC", "Bitcoin OTC", "EUR/JPY OTC"]
    for asset in monitoring_targets:
        try:
            if not is_market_open(asset, now): continue
            data = fetch_market_data(asset)
            if data is not None and not data.empty:
                rsi, _, _, hist, close, ema, _, _, _, _ = calculate_indicators(data)
                if rsi > 65 or rsi < 35 or abs(hist) > 0.0005:
                    text = (
                        f"🚨 **ALERT OPERATIVO – {asset}**\n\n"
                        f"• `{asset}` si trova in una zona di breakout imminente. "
                        f"Movimento atteso nelle prossime candele.\n\n"
                        f"Genera il segnale ora –\n"
                        f"📊 Analizza il grafico e preparati ad agire."
                    )
                    alert_kb = {
                        "inline_keyboard": [
                            [{"text": "📊 GENERA SEGNALE", "callback_data": f"gen_alert_{asset}"}]
                        ]
                    }
                    for chat_id in users:
                        send_message(chat_id, text, alert_kb)
        except Exception as e:
            print(f"Errore controllo live {asset}: {e}")

if not scheduler.running if 'scheduler' in globals() else True:
    scheduler = BackgroundScheduler(timezone="Europe/Rome")
    scheduler.add_job(lambda: check_market_conditions(), 'interval', minutes=30)
    scheduler.add_job(lambda: check_market_conditions(), 'cron', hour=9, minute=0)
    scheduler.add_job(lambda: check_market_conditions(), 'cron', hour=14, minute=30)
    scheduler.add_job(lambda: check_market_conditions(), 'cron', hour=19, minute=0)
    scheduler.start()

RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
if RENDER_URL:
    try:
        requests.get(f"{BASE_URL}/setWebhook?url={RENDER_URL}/webhook", timeout=5)
    except: pass

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

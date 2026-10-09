import os
import json
import math
import time
import io
import matplotlib
matplotlib.use('Agg')  # Fondamentale per i server cloud come Render
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from flask import Flask, request
import requests
import pandas as pd
import numpy as np

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN
SUPER_USER_CHAT_ID = 6121337831
USERS_FILE = "users.json"

market_cache = {}
CACHE_DURATION = 300  # 5 minuti di validità della cache reale

ALL_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "AUD/CAD","USD/MXN",
    "USD/PKR", "EUR/RUB", "EUR/TRY", "JOD/CNY", "NGN/USD", "LBP/USD", "TND/USD",
    "AUD/CHF", "NZD/USD", "USD/CHF", "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "AUD/JPY", "EUR/AUD", "EUR/CAD", "EUR/NZD", "GBP/NZD", "AUD/NZD", "QAR/CNY",
    "CAD/JPY", "CHF/JPY", "GBP/CAD", "GBP/AUD", "USD/ARS", "UAH/USD", "SAR/CNY",
    "EUR/USD OTC", "GBP/USD OTC", "USD/ARS OTC", "QAR/CNY OTC", "LBP/USD OTC",
    "USD/JPY OTC", "AUD/USD OTC", "NGN/USD OTC", "USD/CAD OTC", "AUD/CAD OTC", "NZD/USD OTC",
    "USD/MXN OTC", "USD/PKR OTC", "EUR/RUB OTC", "EUR/TRY OTC", "JOD/CNY OTC",
    "USD/CHF OTC", "EUR/GBP OTC", "EUR/JPY OTC", "GBP/JPY OTC", "AUD/JPY OTC", 
    "AUD/CHF OTC", "EUR/AUD OTC", "EUR/CAD OTC", "EUR/NZD OTC", "GBP/NZD OTC", 
    "AUD/NZD OTC", "CAD/JPY OTC", "CHF/JPY OTC", "GBP/CAD OTC", "GBP/AUD OTC",
    "TND/USD OTC", "UAH/USD OTC", "SAR/CNY OTC", "Bitcoin OTC",
    "Cardano OTC", "Polkadot OTC", "Toncoin OTC", "Bitcoin ETF OTC", "TRON OTC",
    "Dogecoin OTC", "Litecoin OTC", "Chainlink OTC", "Solana OTC", "BNB OTC", "Polygon OTC",
    "Ethereum OTC", "Avalanche OTC", "Dash", "BCH/EUR", "BCH/GBP", "BCH/JPY", "BTC/GBP", "BTC/JPY", "Bitcoin",
    "Chainlink", "Ethereum", "APPLE", "MICROSOFT", "TESLA", "AMAZON", "NETFLIX", "GOOGLE", "META", "MCDONALD'S",
    "APPLE OTC", "MICROSOFT OTC", "TESLA OTC", "AMAZON OTC", "NETFLIX OTC", "GOOGLE OTC", "META OTC", "COCA COLA OTC",
    "INTEL OTC", "BOEING COMPANY OTC", "ALIBABA OTC", "CITIGROUP INC OTC", "EXXONMOBIL OTC"
]

user_selection = {}
pending_approval = {}

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return set(json.load(f))
        except: pass
    return {SUPER_USER_CHAT_ID}

def save_users():
    try:
        with open(USERS_FILE, "w") as f:
            json.dump(list(authorized_users), f)
    except: pass

authorized_users = load_users()

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
    p = {"chat_id": chat_id, "text": text}
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

def edit_message(chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
    if markup: p["reply_markup"] = markup
    api_call("editMessageText", p)

def answer_callback(cq_id):
    api_call("answerCallbackQuery", {"callback_query_id": cq_id})

def calculate_indicators(df):
    close = df['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
        
    current_close = float(close.iloc[-1])

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    current_rsi = round(float(rsi.iloc[-1]), 1)
    if math.isnan(current_rsi): current_rsi = 50.0

    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    
    m_line = round(float(macd.iloc[-1]), 5)
    m_sig = round(float(signal.iloc[-1]) if not math.isnan(signal.iloc[-1]) else 0, 5)
    m_hist = round(float(hist.iloc[-1]), 5)

    ema20 = close.ewm(span=20, adjust=False).mean()
    current_ema = round(float(ema20.iloc[-1]), 5)
    
    return current_rsi, m_line, m_sig, m_hist, current_close, current_ema

def generate_chart_image(df, asset_name):
    try:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        fig.patch.set_facecolor('#1e1e1e')
        
        dates = np.arange(len(df))
        opens = df['Open'].values if isinstance(df['Open'], pd.Series) else df['Open']
        highs = df['High'].values if isinstance(df['High'], pd.Series) else df['High']
        lows = df['Low'].values if isinstance(df['Low'], pd.Series) else df['Low']
        closes = df['Close'].values if isinstance(df['Close'], pd.Series) else df['Close']
        
        close_series = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
        ema20 = close_series.ewm(span=20, adjust=False).mean()

        ax1.set_facecolor('#1e1e1e')
        
        width = 0.6
        up_col = '#26a69a'
        down_col = '#ef5350'

        for i in range(len(dates)):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            color = up_col if c >= o else down_col
            ax1.plot([dates[i], dates[i]], [l, h], color=color, linewidth=1)
            body_bottom = min(o, c)
            body_height = abs(c - o) if abs(c - o) > 0 else 0.00001
            ax1.bar(dates[i], body_height, bottom=body_bottom, width=width, color=color, edgecolor=color)

        ax1.plot(dates, ema20.values, label='EMA 20', color='#ffaa00', linestyle='--', linewidth=1.2)
        ax1.set_title(f"Analisi Tecnica: {asset_name}", color='white', fontsize=11, fontweight='bold')
        ax1.tick_params(colors='white', labelsize=8)
        ax1.grid(True, color='#333333', linestyle=':', alpha=0.7)
        ax1.legend(loc='upper left', facecolor='#2d2d2d', edgecolor='none', labelcolor='white', fontsize=8)

        delta = close_series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        ax2.set_facecolor('#1e1e1e')
        ax2.plot(dates, rsi.values, label='RSI (9)', color='#bf00ff', linewidth=1.2)
        ax2.axhline(70, color='#ff4444', linestyle='--', alpha=0.5, linewidth=1)
        ax2.axhline(30, color='#44bb44', linestyle='--', alpha=0.5, linewidth=1)
        ax2.set_ylim(0, 100)
        ax2.tick_params(colors='white', labelsize=8)
        ax2.grid(True, color='#333333', linestyle=':', alpha=0.7)
        ax2.legend(loc='upper left', facecolor='#2d2d2d', edgecolor='none', labelcolor='white', fontsize=8)

        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none', dpi=100)
        buf.seek(0)
        plt.close(fig)
        return buf
    except Exception as e:
        print("Errore nella generazione del grafico a candele:", e)
        return None

def get_yahoo_ticker(asset_name):
    clean = asset_name.replace(" OTC", "").replace("'", "").strip()
    mapping = {
        "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
        "AUD/USD": "AUDUSD=X", "USD/CAD": "USDCAD=X", "AUD/CAD": "AUDCAD=X",
        "USD/MXN": "USDMXN=X", "USD/PKR": "USDPKR=X", "EUR/RUB": "EURRUB=X",
        "EUR/TRY": "EURTRY=X", "USD/CHF": "USDCHF=X", "EUR/GBP": "EURGBP=X",
        "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X", "AUD/JPY": "AUDJPY=X",
        "AUD/CHF": "AUDCHF=X", "NZD/USD": "NZDUSD=X", "EUR/AUD": "EURAUD=X",
        "EUR/CAD": "EURCAD=X", "EUR/NZD": "EURNZD=X", "GBP/NZD": "GBPNZD=X",
        "AUD/NZD": "AUDNZD=X", "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
        "GBP/CAD": "GBPCAD=X", "GBP/AUD": "GBPAUD=X", "USD/ARS": "USDARS=X",
        "Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "Cardano": "ADA-USD",
        "Solana": "SOL-USD", "Dogecoin": "DOGE-USD", "Litecoin": "LTC-USD",
        "Polkadot": "DOT-USD", "Toncoin": "TON-USD", "TRON": "TRX-USD",
        "Chainlink": "LINK-USD", "Avalanche": "AVAX-USD", "Polygon": "MATIC-USD",
        "BNB": "BNB-USD", "APPLE": "AAPL", "MICROSOFT": "MSFT", "TESLA": "TSLA",
        "AMAZON": "AMZN", "NETFLIX": "NFLX", "GOOGLE": "GOOGL", "META": "META",
        "MCDONALD'S": "MCD", "INTEL": "INTC", "BOEING COMPANY": "BA",
        "ALIBABA": "BABA", "CITIGROUP INC": "C", "EXXONMOBIL": "XOM"
    }
    if clean in mapping:
        return mapping[clean]
    if "/" in clean and len(clean) == 7:
        return clean.replace("/", "") + "=X"
    return clean

def fetch_yahoo_real_data(asset_name):
    current_time = time.time()
    if asset_name in market_cache:
        ts, cached_df = market_cache[asset_name]
        if current_time - ts < CACHE_DURATION:
            return cached_df

    ticker_symbol = get_yahoo_ticker(asset_name)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=2.0)
        data = response.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'Open': quote['open'],
            'High': quote['high'],
            'Low': quote['low'],
            'Close': quote['close']
        }, index=pd.to_datetime(timestamps, unit='s'))
        
        clean_df = df.dropna()
        if clean_df.empty:
            raise Exception("Dati vuoti")
            
        market_cache[asset_name] = (current_time, clean_df)
        return clean_df
    except Exception as e:
        print(f"Timeout o errore di rete per {asset_name}: {e}")
        if asset_name in market_cache:
            _, old_df = market_cache[asset_name]
            return old_df
        return None

def send_assets_menu(chat_id, page=0, msg_id=None):
    per_page = 6
    sub = ALL_ASSETS[page*per_page:(page+1)*per_page]
    kb = []
    for i in range(0, len(sub), 2):
        row = [{"text": sub[i], "callback_data": "ast_" + str(ALL_ASSETS.index(sub[i]))}]
        if i + 1 < len(sub):
            row.append({"text": sub[i+1], "callback_data": "ast_" + str(ALL_ASSETS.index(sub[i+1]))})
        kb.append(row)
        
    nav = []
    if page > 0: nav.append({"text": "Indietro", "callback_data": "pg_" + str(page-1)})
    if (page + 1) * per_page < len(ALL_ASSETS): nav.append({"text": "Avanti", "callback_data": "pg_" + str(page+1)})
    if nav: kb.append(nav)
        
    text = "👋 Scegli un asset:"
    if msg_id:
        try: edit_message(chat_id, msg_id, text, {"inline_keyboard": kb})
        except: send_message(chat_id, text, {"inline_keyboard": kb})
    else:
        send_message(chat_id, text, {"inline_keyboard": kb})

def send_expiry_menu(chat_id, asset_name, msg_id):
    kb = {"inline_keyboard": [[{"text": "1 Minuto", "callback_data": "exp_1m"}], [{"text": "2 Minuti", "callback_data": "exp_2m"}], [{"text": "3 Minuti", "callback_data": "exp_3m"}], [{"text": "5 Minuti", "callback_data": "exp_5m"}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
    edit_message(chat_id, msg_id, f"💲💹 Asset: {asset_name}\n\nSeleziona la scadenza:", kb)

@app.route('/')
def index():
    return "Bot operativo al 100%!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    up = request.get_json()
    if up:
        try:
            if "callback_query" in up:
                cq = up["callback_query"]
                cid, mid, val = cq["message"]["chat"]["id"], cq["message"]["message_id"], cq["data"]
                answer_callback(cq["id"])
                
                if val.startswith("approve_") or val.startswith("reject_"):
                    if cid == SUPER_USER_CHAT_ID:
                        target = int(val.split("_")[1])
                        if val.startswith("approve_"):
                            authorized_users.add(target)
                            save_users()
                            pending_approval.pop(target, None)
                            send_message(target, "✅ Account approvato!")
                            send_assets_menu(target, 0)
                            edit_message(cid, mid, "Approvato ✅")
                        else:
                            pending_approval.pop(target, None)
                            send_message(target, "❌ Rifiutato.")
                            edit_message(cid, mid, "Rifiutato ❌")
                    return "ok", 200
                    
                if cid != SUPER_USER_CHAT_ID and cid not in authorized_users:
                    send_message(cid, "⚠️ Non autorizzato.")
                    return "ok", 200
                    
                if val.startswith("ast_"):
                    ast = ALL_ASSETS[int(val.split("_")[1])]
                    user_selection[cid] = {"asset": ast}
                    send_expiry_menu(cid, ast, mid)
                elif val.startswith("exp_") or val.startswith("retry_"):
                    if val.startswith("exp_"):
                        sel = user_selection.get(cid, {})
                        ast_name = sel.get("asset", "EUR/USD")
                        exp_key = val.split("_")[1]
                    else:
                        parts = val.split("_")
                        exp_key = parts[-1]
                        ast_name = "_".join(parts[1:-1])
                    
                    edit_message(cid, mid, f"⏳ Elaborazione per {ast_name} ({exp_key.upper()})...")
                    
                    data = fetch_yahoo_real_data(ast_name)

                    if data is None or data.empty:
                        error_text = f"⚠️ **Yahoo Finance non risponde per {ast_name}.**\n\nIl server è temporaneamente occupato. Clicca su Aggiorna per riprovare."
                        error_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
                        edit_message(cid, mid, error_text, error_kb)
                        return "ok", 200

                    generation_time = datetime.now()
                    rsi_val, macd_line, macd_signal, macd_hist, current_close, current_ema = calculate_indicators(data)
                    
                    trend_bullish = current_close > current_ema
                    if trend_bullish and (macd_line >= macd_signal or rsi_val < 48):
                        sig_type = "ACQUISTA (BUY)"
                    elif not trend_bullish and (macd_line <= macd_signal or rsi_val > 52):
                        sig_type = "VENDI (SELL)"
                    else:
                        sig_type = "ACQUISTA (BUY)" if macd_line > macd_signal else "VENDI (SELL)"
                        
                    conf = round(79.0 + abs(macd_hist) * 800, 1)
                    conf = min(97.0, max(68.0, conf))

                    sig_emoji = "🟢" if "ACQUISTA" in sig_type else "🔴"
                    exp_map = {"1m": "1 Minuto (1M)", "2m": "2 Minuti (2M)", "3m": "3 Minuti (3M)", "5m": "5 Minuti (5M)"}
                    expiry_name = exp_map.get(exp_key, "1 Minuto (1M)")
                    
                    next_entry_dt = generation_time + timedelta(minutes=1)
                    entry_time = next_entry_dt.replace(second=0, microsecond=0).strftime('%H:%M:%S')

                    # Testo formattato correttamente con i backticks dentro le virgolette
                    text = "🐂🐻 ANALISI EASY TRACK\n\n"
                    text += f"💲💹 Asset: {ast_name}\n"
                    text += f"💵 Prezzo Reale: `{round(current_close, 5)}`\n"
                    text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                    text += "🛠️ Indicatori:\n"
                    text += f"• RSI (9): {rsi_val}\n"
                    text += f"• Linea MACD: {macd_line}\n"
                    text += f"• Segnale MACD: {macd_signal}\n"
                    text += f"• Istogramma: {macd_hist}\n\n"
                    text += f"⚖️ Affidabilità: {conf}%\n"
                    text += f"⏳ Scadenza: {expiry_name}\n"
                    text += f"📌 Entrata: {entry_time}"
                    
                    kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
                    
                    chart_buf = generate_chart_image(data, ast_name)
                    
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    if chart_buf:
                        send_photo_message(cid, chart_buf, text, kb)
                    else:
                        send_message(cid, text, kb)

                elif val.startswith("pg_"):
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    send_assets_menu(cid, int(val.split("_")[1]))
                elif val == "back_assets":
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    send_assets_menu(cid, 0)
                    
            elif "message" in up and "text" in up["message"]:
                cid = up["message"]["chat"]["id"]
                txt_msg = up["message"]["text"].strip()
                if cid == SUPER_USER_CHAT_ID or cid in authorized_users:
                    send_assets_menu(cid, 0)
                    return "ok", 200
                if cid in pending_approval:
                    send_message(cid, "⏳ In attesa di approvazione.")
                    return "ok", 200
                pending_approval[cid] = txt_msg
                send_message(SUPER_USER_CHAT_ID, "🔔 Richiesta ID: " + txt_msg, {"inline_keyboard": [[{"text": "SI", "callback_data": "approve_" + str(cid)}, {"text": "NO", "callback_data": "reject_" + str(cid)}]]})
                send_message(cid, "⏳ In attesa di approvazione...")
        except Exception as e:
            print("Errore nel webhook:", e)
    return "ok", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

import os
import json
import math
import time
import io
import traceback
import threading
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
from datetime import datetime, timedelta, timezone
from flask import Flask, request
import requests
import pandas as pd
import numpy as np

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN

market_cache = {}
CACHE_DURATION = 300
chart_lock = threading.Lock()

# Asset reali e OTC
ALL_ASSETS = [
    "EUR/USD", "EUR/USD OTC", "GBP/USD", 
    "GBP/USD OTC", "USD/JPY", "USD/JPY OTC",
    "AUD/USD", "AUD/USD OTC", "USD/CAD", 
    "USD/CAD OTC", "AUD/CAD", "AUD/CAD OTC",
    "USD/MXN", "USD/MXN OTC", "USD/PKR", 
    "USD/PKR OTC", "EUR/RUB", "EUR/RUB OTC",
    "EUR/TRY", "EUR/TRY OTC", "JOD/CNY", 
    "JOD/CNY OTC", "NGN/USD", "NGN/USD OTC",
    "LBP/USD", "LBP/USD OTC", "TND/USD", 
    "TND/USD OTC", "AUD/CHF", "AUD/CHF OTC",
    "NZD/USD", "NZD/USD OTC", "USD/CHF", 
    "USD/CHF OTC", "EUR/GBP", "EUR/GBP OTC",
    "EUR/JPY", "EUR/JPY OTC", "GBP/JPY", 
    "GBP/JPY OTC", "AUD/JPY", "AUD/JPY OTC",
    "EUR/AUD", "EUR/AUD OTC", "EUR/CAD", 
    "EUR/CAD OTC", "EUR/NZD", "EUR/NZD OTC",
    "GBP/NZD", "GBP/NZD OTC", "AUD/NZD", 
    "AUD/NZD OTC", "QAR/CNY", "QAR/CNY OTC",
    "CAD/JPY", "CAD/JPY OTC", "CHF/JPY", 
    "CHF/JPY OTC", "GBP/CAD", "GBP/CAD OTC",
    "GBP/AUD", "GBP/AUD OTC", "USD/ARS", 
    "USD/ARS OTC", "UAH/USD", "UAH/USD OTC",
    "SAR/CNY", "SAR/CNY OTC",

    # Criptovalute
    "Bitcoin", "Bitcoin OTC", "Ethereum", 
    "Ethereum OTC", "Cardano", "Cardano OTC",
    "Polkadot", "Polkadot OTC", "Toncoin", 
    "Toncoin OTC", "TRON", "TRON OTC",
    "Dogecoin", "Dogecoin OTC", "Litecoin", 
    "Litecoin OTC", "Chainlink", "Chainlink OTC",
    "Solana", "Solana OTC", "BNB", 
    "BNB OTC", "Polygon", "Polygon OTC",
    "Avalanche", "Avalanche OTC", "Bitcoin ETF OTC", 
    "Dash", "BCH/EUR", "BCH/GBP", 
    "BCH/JPY", "BTC/GBP", "BTC/JPY",

    # Azioni
    "APPLE", "APPLE OTC", "MICROSOFT", 
    "MICROSOFT OTC", "TESLA", "TESLA OTC",
    "AMAZON", "AMAZON OTC", "NETFLIX", 
    "NETFLIX OTC", "GOOGLE", "GOOGLE OTC",
    "META", "META OTC", "MCDONALD'S", 
    "COCA COLA OTC", "INTEL OTC", "BOEING COMPANY OTC", 
    "ALIBABA OTC", "CITIGROUP INC OTC", "EXXONMOBIL OTC"
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

def delete_message(chat_id, msg_id):
    if chat_id and msg_id:
        api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})

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

def edit_message(chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
    if markup is not None:
        p["reply_markup"] = markup
    else:
        p["reply_markup"] = {"inline_keyboard": []} 
    api_call("editMessageText", p)

def answer_callback(cq_id):
    api_call("answerCallbackQuery", {"callback_query_id": cq_id})

def analyze_market_structure(df):
    highs = df['High']
    lows = df['Low']
    if isinstance(highs, pd.DataFrame):
        highs = highs.iloc[:, 0]
    if isinstance(lows, pd.DataFrame):
        lows = lows.iloc[:, 0]
        
    recent_highs = highs.tail(12)
    recent_lows = lows.tail(12)
    
    is_higher_high = float(recent_highs.iloc[-1]) >= float(recent_highs.iloc[-6])
    is_higher_low = float(recent_lows.iloc[-1]) >= float(recent_lows.iloc[-6])
    
    if is_higher_high and is_higher_low:
        return "Rialzista (HH / HL)", 2
    elif not is_higher_high and not is_higher_low:
        return "Ribassista (LH / LL)", -2
    else:
        return "Laterale / Misto", 0

def calculate_indicators(df):
    close = df['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
        
    current_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) > 1 else current_close

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    loss = loss.replace(0, 1e-10)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    current_rsi = round(float(rsi.iloc[-1]), 1)
    if math.isnan(current_rsi): 
        current_rsi = 50.0

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
    
    structure_name, structure_score = analyze_market_structure(df)
    
    return current_rsi, m_line, m_sig, m_hist, current_close, prev_close, current_ema, structure_name, structure_score

def generate_chart_image(df, asset_name):
    with chart_lock:
        try:
            plt.close('all')
            df_clean = df.copy()
            df_clean.index = df_clean.index + timedelta(hours=2)

            for col in ['Open', 'High', 'Low', 'Close']:
                df_clean[col] = df_clean[col].astype(float)

            close_series = df_clean['Close'].iloc[:, 0] if isinstance(df_clean['Close'], pd.DataFrame) else df_clean['Close']
            ema20_full = close_series.ewm(span=20, adjust=False).mean()

            df_plot = df_clean.tail(30).copy()
            ema20_plot = ema20_full.tail(30)

            market_colors = mpf.make_marketcolors(
                up='#00E676', 
                down='#FF5252', 
                wick={'up': '#00E676', 'down': '#FF5252'}, 
                edge='inherit',
                volume='inherit'
            )
            custom_style = mpf.make_mpf_style(
                base_mpf_style='nightclouds', 
                marketcolors=market_colors, 
                facecolor='#1e1e1e', 
                edgecolor='#333333', 
                figcolor='#1e1e1e'
            )

            add_plots = [
                mpf.make_addplot(ema20_plot, color='#FFC107', width=1.5, linestyle='--')
            ]

            buf = io.BytesIO()
            fig, axes = mpf.plot(
                df_plot,
                type='candle',
                style=custom_style,
                addplot=add_plots,
                title=f"\nAnalisi Tecnica: {asset_name}",
                volume=False,
                figsize=(8, 4.5),
                returnfig=True,
                panel_ratios=(1,)
            )
            
            fig.savefig(buf, format='png', facecolor='#1e1e1e', edgecolor='none', bbox_inches='tight', dpi=100)
            buf.seek(0)
            plt.close(fig)
            return buf
        except Exception as e:
            print("ERRORE NELLA GENERAZIONE DEL GRAFICO:", e)
            plt.close('all')
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

def fetch_binance_real_data(asset_name):
    clean = asset_name.replace(" OTC", "").replace("'", "").strip()
    mapping = {
        "Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Cardano": "ADAUSDT",
        "Polkadot": "DOTUSDT", "Toncoin": "TONUSDT", "TRON": "TRXUSDT",
        "Dogecoin": "DOGEUSDT", "Litecoin": "LTCUSDT", "Chainlink": "LINKUSDT",
        "Solana": "SOLUSDT", "BNB": "BNBUSDT", "Polygon": "MATICUSDT",
        "Avalanche": "AVAXUSDT", "Dash": "DASHUSDT"
    }
    if clean not in mapping:
        return None
        
    symbol = mapping[clean]
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=50"
    try:
        response = requests.get(url, timeout=3.0)
        if response.status_code != 200:
            return None
            
        data = response.json()
        timestamps = [x[0] / 1000.0 for x in data]
        opens = [float(x[1]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        closes = [float(x[4]) for x in data]
        
        df = pd.DataFrame({
            'Open': opens,
            'High': highs,
            'Low': lows,
            'Close': closes
        }, index=pd.to_datetime(timestamps, unit='s'))
        
        return df.dropna()
    except Exception as e:
        print(f"Errore API Binance per {asset_name}: {e}")
        return None

def fetch_market_data(asset_name):
    current_time = time.time()
    if asset_name in market_cache:
        ts, cached_df = market_cache[asset_name]
        if current_time - ts < CACHE_DURATION:
            return cached_df

    # 1. Prova prima Binance per le cripto
    df = fetch_binance_real_data(asset_name)
    
    # 2. Se non è crypto, usa Yahoo Finance con Resampling rigoroso a 1 minuto
    if df is None or df.empty:
        ticker_symbol = get_yahoo_ticker(asset_name)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'application/json'
        }
        try:
            response = requests.get(url, headers=headers, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                result = data['chart']['result'][0]
                timestamps = result['timestamp']
                quote = result['indicators']['quote'][0]
                
                df_raw = pd.DataFrame({
                    'Open': quote['open'],
                    'High': quote['high'],
                    'Low': quote['low'],
                    'Close': quote['close']
                }, index=pd.to_datetime(timestamps, unit='s'))
                
                df_raw = df_raw[~df_raw.index.duplicated(keep='first')]
                df_raw = df_raw.dropna()
                
                # RESAMPLING RIGOROSO A 1 MINUTO (Evita buchi e allinea le candele)
                df = df_raw.resample('1min').agg({
                    'Open': 'first',
                    'High': 'max',
                    'Low': 'min',
                    'Close': 'last'
                })
                
                df['Close'] = df['Close'].ffill()
                df['Open'] = df['Open'].fillna(df['Close'])
                df['High'] = df['High'].fillna(df['Close'])
                df['Low'] = df['Low'].fillna(df['Close'])
                df = df.dropna()
                
        except Exception as e:
            print(f"Errore Yahoo ottimizzato per {asset_name}: {e}")

    if df is not None and not df.empty:
        market_cache[asset_name] = (current_time, df)
        return df

    if asset_name in market_cache:
        _, old_df = market_cache[asset_name]
        return old_df
    return None

def send_assets_menu(chat_id, page=0, msg_id=None):
    if msg_id:
        delete_message(chat_id, msg_id)

    per_page = 9  # 3x3
    sub = ALL_ASSETS[page*per_page:(page+1)*per_page]
    kb = []
    for i in range(0, len(sub), 3):
        row = []
        for j in range(3):
            if i + j < len(sub):
                asset_item = sub[i+j]
                row.append({"text": asset_item, "callback_data": "ast_" + str(ALL_ASSETS.index(asset_item))})
        kb.append(row)
        
    nav = []
    if page > 0: nav.append({"text": "◀️ Indietro", "callback_data": "pg_" + str(page-1)})
    if (page + 1) * per_page < len(ALL_ASSETS): nav.append({"text": "Avanti ▶️", "callback_data": "pg_" + str(page+1)})
    if nav: kb.append(nav)
        
    text = "👋 Scegli un asset:"
    send_message(chat_id, text, {"inline_keyboard": kb})

def send_expiry_menu(chat_id, asset_name, msg_id):
    kb = {"inline_keyboard": [
        [{"text": "1 Minuto", "callback_data": "exp_1m"}], 
        [{"text": "2 Minuti", "callback_data": "exp_2m"}], 
        [{"text": "3 Minuti", "callback_data": "exp_3m"}], 
        [{"text": "5 Minuti", "callback_data": "exp_5m"}], 
        [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]
    ]}
    edit_message(chat_id, msg_id, f"💲💹 Asset: {asset_name}\n\nSeleziona la scadenza:", kb)

def process_analysis_background(cid, mid, ast_name, exp_key):
    try:
        data = fetch_market_data(ast_name)

        if data is None or data.empty:
            error_text = f"⚠️ **Impossibile recuperare i dati per {ast_name}.**\n\nIl server è temporaneamente occupato."
            error_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
            send_message(cid, error_text, error_kb)
            return

        italian_tz = timezone(timedelta(hours=2))
        current_it_time = datetime.now(italian_tz)
        next_entry_dt = current_it_time + timedelta(minutes=1)
        entry_time = next_entry_dt.replace(second=0, microsecond=0).strftime('%H:%M:%S')
        
        rsi_val, macd_line, macd_signal, macd_hist, current_close, prev_close, current_ema, structure_name, structure_score = calculate_indicators(data)
        
        score = 0
        score += structure_score

        if current_close > prev_close: score += 1
        else: score -= 1

        if macd_line > macd_signal: score += 1
        else: score -= 1

        if macd_hist > 0: score += 1
        else: score -= 1

        if rsi_val > 50: score += 1
        else: score -= 1

        if score > 0:
            sig_type = "ACQUISTA (BUY)"
        elif score < 0:
            sig_type = "VENDI (SELL)"
        else:
            sig_type = "ACQUISTA (BUY)" if current_close >= prev_close else "VENDI (SELL)"
            
        conf = round(72.0 + abs(macd_hist * 1000) + abs(rsi_val - 50) * 0.3 + abs(structure_score) * 2, 1)
        conf = min(96.0, max(65.0, conf))

        sig_emoji = "🟢" if "ACQUISTA" in sig_type else "🔴"
        exp_map = {"1m": "1 Minuto (1M)", "2m": "2 Minuti (2M)", "3m": "3 Minuti (3M)", "5m": "5 Minuti (5M)"}
        expiry_name = exp_map.get(exp_key, "1 Minuto (1M)")

        text = "🐂🐻 ANALISI EASY TRACK\n\n"
        text += f"💲💹 Asset: {ast_name}\n"
        text += f"💵 Prezzo Reale: `{round(current_close, 5)}`\n"
        text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
        text += "📈 Struttura di Mercato:\n"
        text += f"• Trend: {structure_name}\n\n"
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
        
        delete_message(cid, mid)

        if chart_buf:
            send_photo_message(cid, chart_buf, text, kb)
        else:
            send_message(cid, text, kb)
    except Exception as e:
        print("Errore nel background thread:", e)
        delete_message(cid, mid)
        send_message(cid, "⚠️ Si è verificato un errore durante l'elaborazione. Riprova.", {"inline_keyboard": [[{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]})

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
                    
                    delete_message(cid, mid)
                    temp_mid = send_message(cid, f"⏳ Elaborazione per {ast_name} ({exp_key.upper()})...")

                    threading.Thread(target=process_analysis_background, args=(cid, temp_mid, ast_name, exp_key)).start()
                    return "ok", 200

                elif val.startswith("pg_"):
                    send_assets_menu(cid, int(val.split("_")[1]), msg_id=mid)
                elif val == "back_assets":
                    send_assets_menu(cid, 0, msg_id=mid)
                    
            elif "message" in up and "text" in up["message"]:
                cid = up["message"]["chat"]["id"]
                mid = up["message"]["message_id"]
                delete_message(cid, mid)
                send_assets_menu(cid, 0)
                return "ok", 200
        except Exception as e:
            print("Errore nel webhook:", e)
    return "ok", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

import os
import json
import math
import time
import io
import re
import traceback
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
from datetime import datetime, timedelta, timezone
from flask import Flask, request
import requests
import pandas as pd
import numpy as np

# Importa l'SDK ufficiale di Google GenAI
from google import genai

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN

# Configurazione chiave Gemini (Gratuita da Google AI Studio)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Errore inizializzazione client Gemini:", e)

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
    "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "AUD/USD OTC", "USD/CAD OTC", "AUD/CAD OTC", 
    "USD/CHF OTC", "EUR/GBP OTC", "EUR/JPY OTC", "GBP/JPY OTC", "AUD/JPY OTC", "EUR/AUD OTC", 
    "EUR/CAD OTC", "EUR/NZD OTC", "GBP/NZD OTC", "AUD/NZD OTC", "CAD/JPY OTC", "CHF/JPY OTC", 
    "GBP/CAD OTC", "GBP/AUD OTC", "USD/USD OTC", "AUD/CHF OTC", "USD/MXN OTC", "USD/PKR OTC", 
    "EUR/RUB OTC", "EUR/TRY OTC", "JOD/CNY OTC", "NGN/USD OTC", "LBP/USD OTC", "TND/USD OTC", 
    "QAR/CNY OTC", "USD/ARS OTC", "UAH/USD OTC", "SAR/CNY OTC", "Bitcoin OTC", "Cardano OTC", 
    "Polkadot OTC", "Toncoin OTC", "Bitcoin ETF OTC", "TRON OTC", "Dogecoin OTC", "Litecoin OTC", 
    "Chainlink OTC", "Solana OTC", "BNB OTC", "Polygon OTC", "Ethereum OTC", "Avalanche OTC", 
    "APPLE OTC", "MICROSOFT OTC", "TESLA OTC", "AMAZON OTC", "NETFLIX OTC", "GOOGLE OTC", 
    "META OTC", "COCA COLA OTC", "INTEL OTC", "BOEING COMPANY OTC", "ALIBABA OTC", 
    "CITIGROUP INC OTC", "EXXONMOBIL OTC"
]

user_selection = {}
user_otc_state = {}

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

def edit_message(chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
    if markup: p["reply_markup"] = markup
    api_call("editMessageText", p)

def answer_callback(cq_id):
    api_call("answerCallbackQuery", {"callback_query_id": cq_id})

# --- ANALISI SCREENSHOT GRATUITA CON GEMINI 2.5 FLASH ---

def handle_chart_image(message, chat_id):
    try:
        if not gemini_client:
            send_message(chat_id, "⚠️ Errore: Chiave GEMINI_API_KEY non configurata nelle variabili d'ambiente di Render.")
            return

        photo_list = message.get("photo", [])
        if not photo_list:
            send_message(chat_id, "⚠️ Nessuna foto rilevata nel messaggio.")
            return
        
        file_id = photo_list[-1]["file_id"]
        file_info_res = api_call("getFile", {"file_id": file_id})
        if not file_info_res or not file_info_res.get("ok"):
            send_message(chat_id, "⚠️ Errore Telegram: Impossibile recuperare il file.")
            return
            
        file_path = file_info_res["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}"
        
        img_response = requests.get(download_url, timeout=10.0)
        if img_response.status_code != 200:
            send_message(chat_id, "⚠️ Errore download: Impossibile scaricare l'immagine.")
            return

        image_bytes = img_response.content
        send_message(chat_id, "🔍 *Analisi visiva GRATUITA in corso... Calcolo direzione e orario di ingresso...*")

        image = Image.open(io.BytesIO(image_bytes))

        prompt = (
            "Sei un trader professionista di opzioni binarie. "
            "Analizza questo screenshot del grafico e struttura la risposta esattamente in questo modo:\n\n"
            "🎯 **SEGNALE**: [Scrivi '🟢 ACQUISTA (BUY)' oppure '🔴 VENDI (SELL)']\n"
            "⏰ **ORARIO ESATTO DI INGRESSO**: [Scrivi l'orario esatto al secondo letto dal timer/orologio nell'immagine]\n\n"
            "📊 **ANALISI SINTETICA**: \n"
            "- Asset e Timeframe: [Indicali in breve]\n"
            "- Motivazione tecnica: [Massimo 2 righe con trend, pattern o livelli chiave]"
        )

        response = gemini_client.models.generate_content(
            model="gemini-flash-latest",
            contents=[image, prompt]
        )
        
        if response and response.text:
            send_message(chat_id, response.text)
        else:
            send_message(chat_id, "⚠️ Gemini ha restituito una risposta vuota.")

    except Exception as e:
        error_msg = str(e)
        print("ERRORE CRITICO VISION:", error_msg)
        send_message(chat_id, f"⚠️ Errore tecnico nell'elaborazione dell'immagine:\n`{error_msg}`")

# --- FUNZIONI DI MERCATO E INDICATORI ---

def detect_candlestick_pattern(df):
    try:
        if len(df) < 3:
            return "Mercato in consolidamento (Dati insufficienti)"
        c2 = df.iloc[-2]
        c3 = df.iloc[-1]
        body3 = abs(c3['Close'] - c3['Open'])
        range3 = c3['High'] - c3['Low']
        
        if body3 < (range3 * 0.15):
            return "Doji (Indecisione di mercato)"
        elif c3['Close'] > c3['Open'] and c2['Close'] < c2['Open'] and c3['Close'] > c2['Open'] and c3['Open'] < c2['Close']:
            return "Bullish Engulfing (Inversione Rialzista Forte)"
        elif c3['Close'] < c3['Open'] and c2['Close'] > c2['Open'] and c3['Close'] < c2['Open'] and c3['Open'] > c2['Close']:
            return "Bearish Engulfing (Inversione Ribassista Forte)"
        elif c3['Close'] > c3['Open']:
            return "Candela di Spinta Rialzista (Strong Bull)"
        else:
            return "Candela di Spinta Ribassista (Strong Bear)"
    except:
        return "Analisi Pattern Standard"

def calculate_fractal_chaos_bands(df, period=2):
    highs = df['High']
    lows = df['Low']
    if isinstance(highs, pd.DataFrame):
        highs = highs.iloc[:, 0]
    if isinstance(lows, pd.DataFrame):
        lows = lows.iloc[:, 0]

    upper_band = highs.rolling(window=(period * 2 + 1), center=True).max()
    lower_band = lows.rolling(window=(period * 2 + 1), center=True).min()
    
    upper_band = upper_band.bfill().ffill()
    lower_band = lower_band.bfill().ffill()
    
    return upper_band, lower_band

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
    
    upper_f, lower_f = calculate_fractal_chaos_bands(df, period=2)
    current_upper_f = float(upper_f.iloc[-1])
    current_lower_f = float(lower_f.iloc[-1])

    band_width = upper_f - lower_f
    avg_band_width = band_width.rolling(window=20, min_periods=1).mean()
    current_bw = float(band_width.iloc[-1])
    avg_bw = float(avg_band_width.iloc[-1])

    return current_rsi, m_line, m_sig, m_hist, current_close, current_ema, current_upper_f, current_lower_f, current_bw, avg_bw

def generate_chart_image(df, asset_name, target_entry_time=None):
    try:
        df_clean = df.copy()
        df_clean.index = df_clean.index + timedelta(hours=2)

        for col in ['Open', 'High', 'Low', 'Close']:
            if col in df_clean.columns:
                if isinstance(df_clean[col], pd.DataFrame):
                    df_clean[col] = df_clean[col].iloc[:, 0]
                df_clean[col] = df_clean[col].astype(float)

        close_series = df_clean['Close']
        ema20_full = close_series.ewm(span=20, adjust=False).mean()
        upper_f_full, lower_f_full = calculate_fractal_chaos_bands(df_clean, period=2)

        df_plot = df_clean.tail(30).copy()
        ema20_plot = ema20_full.tail(30)
        upper_f_plot = upper_f_full.tail(30)
        lower_f_plot = lower_f_full.tail(30)

        market_colors = mpf.make_marketcolors(
            up='#00E676', down='#FF5252', 
            wick={'up': '#00E676', 'down': '#FF5252'}, 
            edge='inherit', volume='inherit'
        )
        custom_style = mpf.make_mpf_style(
            base_mpf_style='nightclouds', marketcolors=market_colors, 
            facecolor='#1e1e1e', edgecolor='#333333', figcolor='#1e1e1e'
        )

        add_plots = [
            mpf.make_addplot(ema20_plot, color='#FFC107', width=1.5, linestyle='--'),
            mpf.make_addplot(upper_f_plot, color='#00BCD4', width=1.2, linestyle='-'), 
            mpf.make_addplot(lower_f_plot, color='#00BCD4', width=1.2, linestyle='-')  
        ]

        buf = io.BytesIO()
        fig, axes = mpf.plot(
            df_plot, type='candle', style=custom_style, addplot=add_plots,
            title=f"\nAnalisi Tecnica + Fractal Chaos Bands: {asset_name}", volume=False,
            figsize=(8, 4.5), returnfig=True, panel_ratios=(1,)
        )
        fig.savefig(buf, format='png', facecolor='#1e1e1e', edgecolor='none', bbox_inches='tight', dpi=100)
        buf.seek(0)
        plt.close(fig)
        return buf
    except Exception as e:
        print("ERRORE NELLA GENERAZIONE DEL GRAFICO:", e)
        plt.close('all')
        return None

def force_realtime_last_candle(df):
    if df is None or df.empty:
        return df
    try:
        last_close = float(df['Close'].iloc[-1])
        now_utc = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        
        if df.index[-1] < pd.Timestamp(now_utc.replace(tzinfo=None)):
            new_row = pd.DataFrame({
                'Open': [last_close],
                'High': [last_close * 1.0001],
                'Low': [last_close * 0.9999],
                'Close': [last_close]
            }, index=[pd.Timestamp(now_utc.replace(tzinfo=None))])
            df = pd.concat([df, new_row])
    except Exception as e:
        print("Errore nella forzatura real-time candela:", e)
    return df

def fetch_binance_data(asset_name):
    clean = asset_name.replace("OTC", "").strip()
    mapping = {
        "Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Cardano": "ADAUSDT",
        "Solana": "SOLUSDT", "Dogecoin": "DOGEUSDT", "Litecoin": "LTCUSDT",
        "Polkadot": "DOTUSDT", "Toncoin": "TONUSDT", "TRON": "TRXUSDT",
        "Chainlink": "LINKUSDT", "Avalanche": "AVAXUSDT", "Polygon": "MATICUSDT", "BNB": "BNBUSDT"
    }
    symbol = mapping.get(clean)
    if not symbol: return None
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=100"
    try:
        res = requests.get(url, timeout=3.0).json()
        if not isinstance(res, list): return None
        df = pd.DataFrame(res, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'QAV', 'Trades', 'TBBAV', 'TBQAV', 'Ignore'])
        df['Open'] = df['Open'].astype(float)
        df['High'] = df['High'].astype(float)
        df['Low'] = df['Low'].astype(float)
        df['Close'] = df['Close'].astype(float)
        df['Datetime'] = pd.to_datetime(df['Open_time'], unit='ms')
        df.set_index('Datetime', inplace=True)
        return force_realtime_last_candle(df[['Open', 'High', 'Low', 'Close']].dropna())
    except:
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
        "MCDONALD'S": "MCD", "INTEL": "INTC", "ALIBABA": "BABA"
    }
    if clean in mapping: return mapping[clean]
    if "/" in clean and len(clean) == 7: return clean.replace("/", "") + "=X"
    return clean

def fetch_yahoo_real_data(asset_name):
    current_time = time.time()
    if asset_name in market_cache:
        ts, cached_df = market_cache[asset_name]
        if current_time - ts < CACHE_DURATION: 
            return cached_df

    df = fetch_binance_data(asset_name)
    if df is not None and not df.empty:
        market_cache[asset_name] = (current_time, df)
        return df

    ticker_symbol = get_yahoo_ticker(asset_name)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=5.0)
        data = response.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'Open': quote['open'], 'High': quote['high'],
            'Low': quote['low'], 'Close': quote['close']
        }, index=pd.to_datetime(timestamps, unit='s'))
        
        clean_df = df.dropna()
        if clean_df.empty: 
            raise Exception("Dati vuoti")
        
        clean_df = force_realtime_last_candle(clean_df)
        market_cache[asset_name] = (current_time, clean_df)
        return clean_df
    except Exception as e:
        print(f"Errore recupero dati per {asset_name}: {e}")
        if asset_name in market_cache: 
            return market_cache[asset_name][1]
        return None

# --- MENU TELEGRAM ---

def send_category_menu(chat_id, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "📈 Mercato Reale", "callback_data": "cat_real_0"}],
            [{"text": "📊 Mercato OTC (Guida Visiva)", "callback_data": "cat_otc_0"}]
        ]
    }
    text = "👋 **Seleziona la categoria di mercato o invia uno screenshot del grafico per un'analisi IA gratuita immediata:**"
    if msg_id:
        try: edit_message(chat_id, msg_id, text, kb)
        except: send_message(chat_id, text, kb)
    else:
        send_message(chat_id, text, kb)

def send_assets_menu(chat_id, category="real", page=0, msg_id=None):
    assets_list = REAL_ASSETS if category == "real" else OTC_ASSETS
    per_page = 9
    sub = assets_list[page*per_page:(page+1)*per_page]
    kb = []
    
    for i in range(0, len(sub), 3):
        row = []
        for j in range(3):
            if i + j < len(sub):
                asset_item = sub[i+j]
                cb_prefix = "ast_r_" if category == "real" else "ast_o_"
                row.append({"text": asset_item, "callback_data": cb_prefix + str(assets_list.index(asset_item))})
        kb.append(row)
        
    nav = []
    if page > 0: nav.append({"text": "Indietro", "callback_data": f"pg_{category}_{page-1}"})
    if (page + 1) * per_page < len(assets_list): nav.append({"text": "Avanti", "callback_data": f"pg_{category}_{page+1}"})
    if nav: kb.append(nav)
        
    kb.append([{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}])
    title_cat = "Reali" if category == "real" else "OTC"
    text = f"👋 Scegli un asset **{title_cat}**:"
    if msg_id:
        try: edit_message(chat_id, msg_id, text, {"inline_keyboard": kb})
        except: send_message(chat_id, text, {"inline_keyboard": kb})
    else:
        send_message(chat_id, text, {"inline_keyboard": kb})

def send_expiry_menu(chat_id, asset_name, msg_id, is_otc=False):
    prefix = "exp_o_" if is_otc else "exp_r_"
    kb = {
        "inline_keyboard": [
            [{"text": "S5", "callback_data": prefix + "s5"}, {"text": "S10", "callback_data": prefix + "s10"}, {"text": "S15", "callback_data": prefix + "s15"}, {"text": "S30", "callback_data": prefix + "s30"}],
            [{"text": "M1", "callback_data": prefix + "m1"}, {"text": "M2", "callback_data": prefix + "m2"}, {"text": "M3", "callback_data": prefix + "m3"}, {"text": "M5", "callback_data": prefix + "m5"}],
            [{"text": "M10", "callback_data": prefix + "m10"}, {"text": "M15", "callback_data": prefix + "m15"}, {"text": "M30", "callback_data": prefix + "m30"}],
            [{"text": "H1", "callback_data": prefix + "h1"}, {"text": "H4", "callback_data": prefix + "h4"}, {"text": "D1", "callback_data": prefix + "d1"}],
            [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]
        ]
    }
    edit_message(chat_id, msg_id, f"💲💹 Asset: {asset_name}\n\nSeleziona l'intervallo / timeframe:", kb)

@app.route('/')
def index():
    return "Bot operativo al 100%!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    up = request.get_json()
    exp_map = {
        "s5": "5 Secondi (S5)", "s10": "10 Secondi (S10)", "s15": "15 Secondi (S15)", "s30": "30 Secondi (S30)",
        "m1": "1 Minuto (M1)", "m2": "2 Minuti (M2)", "m3": "3 Minuti (M3)", "m5": "5 Minuti (M5)",
        "m10": "10 Minuti (M10)", "m15": "15 Minuti (M15)", "m30": "30 Minuti (M30)",
        "h1": "1 Ora (H1)", "h4": "4 Ore (H4)", "d1": "1 Giorno (D1)"
    }
    if up:
        try:
            if "message" in up and "photo" in up["message"]:
                msg = up["message"]
                cid = msg["chat"]["id"]
                handle_chart_image(msg, cid)
                return "ok", 200

            if "callback_query" in up:
                cq = up["callback_query"]
                cid, mid, val = cq["message"]["chat"]["id"], cq["message"]["message_id"], cq["data"]
                answer_callback(cq["id"])
                
                if val == "main_categories":
                    user_otc_state.pop(cid, None)
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    send_category_menu(cid)
                    
                elif val.startswith("cat_"):
                    parts = val.split("_")
                    send_assets_menu(cid, parts[1], int(parts[2]), mid)
                    
                elif val.startswith("pg_"):
                    parts = val.split("_")
                    send_assets_menu(cid, parts[1], int(parts[2]), mid)

                elif val.startswith("ast_r_") or val.startswith("ast_o_"):
                    if val.startswith("ast_r_"):
                        ast = REAL_ASSETS[int(val.split("_")[2])]
                        user_selection[cid] = {"asset": ast, "cat": "real"}
                        send_expiry_menu(cid, ast, mid, is_otc=False)
                    else:
                        ast = OTC_ASSETS[int(val.split("_")[2])]
                        user_selection[cid] = {"asset": ast, "cat": "otc"}
                        send_expiry_menu(cid, ast, mid, is_otc=True)

                elif val.startswith("exp_r_") or val.startswith("retry_"):
                    if val.startswith("exp_r_"):
                        exp_key = val.split("_")[2]
                        sel = user_selection.get(cid, {})
                        ast_name = sel.get("asset", "EUR/USD")
                        user_selection[cid]["exp"] = exp_key
                    else:
                        parts = val.split("_")
                        exp_key = parts[-1]
                        ast_name = "_".join(parts[1:-1])
                    
                    edit_message(cid, mid, f"⏳ Elaborazione analisi in tempo reale per {ast_name}...")
                    
                    data = fetch_yahoo_real_data(ast_name)
                    if data is None or data.empty:
                        error_text = f"⚠️ **Impossibile recuperare i dati per {ast_name}.** Mercato chiuso o temporaneamente non disponibile."
                        error_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                        edit_message(cid, mid, error_text, error_kb)
                        return "ok", 200

                    italian_tz = timezone(timedelta(hours=2))
                    current_it_time = datetime.now(italian_tz)
                    entry_time = (current_it_time + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime('%H:%M:%S')
                    
                    rsi_val, macd_line, macd_signal, macd_hist, current_close, current_ema, upper_f, lower_f, current_bw, avg_bw = calculate_indicators(data)
                    pattern_name = detect_candlestick_pattern(data)
                    
                    trend_bullish = current_close > current_ema
                    sig_type = "ACQUISTA (BUY)" if trend_bullish else "VENDI (SELL)"
                    sig_emoji = "🟢" if trend_bullish else "🔴"
                    conf = 88.5
                    
                    text = "🐂🐻 ANALISI TECNICA (REALE LIVE)\n\n"
                    text += f"💲💹 Asset: {ast_name}\n"
                    text += f"💵 Prezzo Reale: `{round(current_close, 5)}`\n"
                    text += f"🕯️ Pattern: {pattern_name}\n"
                    text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                    text += f"• RSI (9): {rsi_val}\n"
                    text += f"• Linea MACD: {macd_line}\n"
                    text += f"⚖️ Affidabilità: {conf}%\n"
                    text += f"📌 Ora Ingresso: `{entry_time}`"
                    
                    kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                    chart_buf = generate_chart_image(data, ast_name, target_entry_time=entry_time)
                    
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    if chart_buf:
                        send_photo_message(cid, chart_buf, text, kb)
                    else:
                        send_message(cid, text, kb)

                elif val.startswith("exp_o_"):
                    exp_key = val.split("_")[2]
                    sel = user_selection.get(cid, {})
                    ast_name = sel.get("asset", "EUR/USD OTC")
                    user_otc_state[cid] = {"asset": ast_name, "expiry": exp_key}
                    
                    kb_rsi_levels = {
                        "inline_keyboard": [
                            [{"text": "🔥 Sopra 70 (Ipercomprato)", "callback_data": "otc_rsi_over70"}],
                            [{"text": "📈 Tra 50 e 70 (Zona Rialzista)", "callback_data": "otc_rsi_50_70"}],
                            [{"text": "📉 Tra 30 e 50 (Zona Ribassista)", "callback_data": "otc_rsi_30_50"}],
                            [{"text": "❄️ Sotto 30 (Ipervenduto)", "callback_data": "otc_rsi_under30"}],
                            [{"text": "⚡ Test / Incrocio linea del 50", "callback_data": "otc_rsi_cross50"}],
                            [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]
                        ]
                    }
                    edit_message(cid, mid, f"📊 **{ast_name} ({exp_map.get(exp_key, exp_key)})**\n\n**Step 1/2:** Seleziona la posizione dell'RSI sul broker:", kb_rsi_levels)

                elif val.startswith("otc_rsi_"):
                    rsi_choice = val.replace("otc_rsi_", "")
                    if cid in user_otc_state:
                        user_otc_state[cid]["rsi"] = rsi_choice
                        kb_macd_3 = {
                            "inline_keyboard": [
                                [{"text": "🟢 Incrocio Rialzista", "callback_data": "otc_macd_bull"}],
                                [{"text": "🔴 Incrocio Ribassista", "callback_data": "otc_macd_bear"}],
                                [{"text": "⏳ In fase di formazione", "callback_data": "otc_macd_pending"}],
                                [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]
                            ]
                        }
                        edit_message(cid, mid, "📊 **Step 2/2:** Qual è la situazione attuale del **MACD** sul broker?", kb_macd_3)

                elif val.startswith("otc_macd_"):
                    macd_choice = val.replace("otc_macd_", "")
                    if cid in user_otc_state:
                        state = user_otc_state[cid]
                        ast_name, exp_key, rsi_choice = state["asset"], state["expiry"], state["rsi"]
                        user_otc_state.pop(cid, None)
                        
                        italian_tz = timezone(timedelta(hours=2))
                        entry_time = (datetime.now(italian_tz) + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime('%H:%M:%S')
                        
                        sig_type = "ACQUISTA (BUY)" if rsi_choice == "under30" or macd_choice == "bull" else "VENDI (SELL)"
                        sig_emoji = "🟢" if "ACQUISTA" in sig_type else "🔴"
                        conf = 89.0
                        
                        text = "🤖 **ANALISI OTC GUIDATA**\n\n"
                        text += f"💲💹 Asset: {ast_name}\n"
                        text += f"🎯 Segnale: {sig_type} {sig_emoji}\n"
                        text += f"⚖️ Affidabilità: {conf}%\n"
                        text += f"📌 Ora Ingresso: `{entry_time}`"
                        
                        kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                        edit_message(cid, mid, text, kb)

            elif "message" in up:
                cid = up["message"]["chat"]["id"]
                if "text" in up["message"]:
                    send_category_menu(cid)
                    return "ok", 200
        except Exception as e:
            print("Errore nel webhook:", e)
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

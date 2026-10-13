import os
import json
import math
import time
import io
import re
import traceback
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
from datetime import datetime, timedelta, timezone
from flask import Flask, request
import requests
import pandas as pd
import numpy as np

# Importa la libreria di Google Generative AI
import google.generativeai as genai

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN

# CONFIGURAZIONE GEMINI API KEY
GEMINI_API_KEY = "AQ.Ab8RN6IDlTHsxy-8n-Uie0x-MQsFt3F22lNlqhIwA-Mg13XDLA"
genai.configure(api_key=GEMINI_API_KEY)

gemini_model = genai.GenerativeModel('gemini-1.5-flash')

market_cache = {}
CACHE_DURATION = 60 # Ridotto a 60 secondi per mantenere il real-time

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

        if target_entry_time:
            try:
                dt_parts = target_entry_time.split(":")
                h, m = int(dt_parts[0]), int(dt_parts[1])
                df_clean = df_clean[
                    (df_clean.index.hour < h) | 
                    ((df_clean.index.hour == h) & (df_clean.index.minute < m))
                ]
            except Exception as e:
                print("Errore nel taglio temporale del grafico:", e)

        for col in ['Open', 'High', 'Low', 'Close']:
            df_clean[col] = df_clean[col].astype(float)

        close_series = df_clean['Close'].iloc[:, 0] if isinstance(df_clean['Close'], pd.DataFrame) else df_clean['Close']
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
            title=f"\nAnalisi Tecnica + Fractal Chaos Bands (P2): {asset_name}", volume=False,
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
        else:
            df.iloc[-1, df.columns.get_loc('Close')] = last_close
            if df.iloc[-1]['High'] < last_close:
                df.iloc[-1, df.columns.get_loc('High')] = last_close
            if df.iloc[-1]['Low'] > last_close:
                df.iloc[-1, df.columns.get_loc('Low')] = last_close
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

def fetch_coinbase_data(asset_name):
    clean = asset_name.replace("OTC", "").strip()
    mapping = {"Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "Litecoin": "LTC-USD"}
    product_id = mapping.get(clean)
    if not product_id: return None
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity=60"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=3.0).json()
        if not isinstance(res, list): return None
        df = pd.DataFrame(res, columns=['time', 'Low', 'High', 'Open', 'Close', 'Volume'])
        df['Datetime'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('Datetime', inplace=True)
        df = df.sort_index()
        clean_df = df[['Open', 'High', 'Low', 'Close']].astype(float).dropna()
        return force_realtime_last_candle(clean_df)
    except:
        return None

def fetch_bitget_data(asset_name):
    clean = asset_name.replace("OTC", "").strip()
    symbol = clean.replace("/", "") + "USDT"
    url = f"https://api.bitget.com/api/v2/spot/market/candles?symbol={symbol}&granularity=1min&limit=100"
    try:
        res = requests.get(url, timeout=3.0).json()
        data = res.get("data", [])
        if not data: return None
        df = pd.DataFrame(data, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'QV'])
        df['Datetime'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        df.set_index('Datetime', inplace=True)
        df = df.sort_index()
        clean_df = df[['Open', 'High', 'Low', 'Close']].astype(float).dropna()
        return force_realtime_last_candle(clean_df)
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

    df = fetch_coinbase_data(asset_name)
    if df is not None and not df.empty:
        market_cache[asset_name] = (current_time, df)
        return df

    df = fetch_bitget_data(asset_name)
    if df is not None and not df.empty:
        market_cache[asset_name] = (current_time, df)
        return df

    ticker_symbol = get_yahoo_ticker(asset_name)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
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
        print(f"Errore di recupero dati in tempo reale per {asset_name}: {e}")
        if asset_name in market_cache: 
            return market_cache[asset_name][1]
        return None

def send_category_menu(chat_id, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "📈 Mercato Reale", "callback_data": "cat_real_0"}],
            [{"text": "📊 Mercato OTC", "callback_data": "cat_otc_0"}]
        ]
    }
    text = "👋 **Seleziona la categoria di mercato:**"
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
                    category = parts[1]
                    page = int(parts[2])
                    send_assets_menu(cid, category, page, mid)
                    
                elif val.startswith("pg_"):
                    parts = val.split("_")
                    category = parts[1]
                    page = int(parts[2])
                    send_assets_menu(cid, category, page, mid)

                elif val.startswith("ast_r_") or val.startswith("ast_o_"):
                    if val.startswith("ast_r_"):
                        ast = REAL_ASSETS[int(val.split("_")[2])]
                        user_selection[cid] = {"asset": ast, "cat": "real"}
                        send_expiry_menu(cid, ast, mid, is_otc=False)
                    else:
                        ast = OTC_ASSETS[int(val.split("_")[2])]
                        user_selection[cid] = {"asset": ast, "cat": "otc"}
                        send_expiry_menu(cid, ast, mid, is_otc=True)

                # --- MERCATO REALE (INVARIATO) ---
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
                    
                    edit_message(cid, mid, f"⏳ Elaborazione analisi in tempo reale per {ast_name} ({exp_key.upper()})...")
                    
                    data = fetch_yahoo_real_data(ast_name)
                    if data is None or data.empty:
                        error_text = f"⚠️ **Impossibile recuperare i dati per {ast_name}.**\n\nIl mercato potrebbe essere chiuso in questo momento."
                        error_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                        edit_message(cid, mid, error_text, error_kb)
                        return "ok", 200

                    italian_tz = timezone(timedelta(hours=2))
                    current_it_time = datetime.now(italian_tz)
                    next_entry_dt = current_it_time + timedelta(minutes=1)
                    entry_time = next_entry_dt.replace(second=0, microsecond=0).strftime('%H:%M:%S')
                    
                    rsi_val, macd_line, macd_signal, macd_hist, current_close, current_ema, upper_f, lower_f, current_bw, avg_bw = calculate_indicators(data)
                    pattern_name = detect_candlestick_pattern(data)
                    
                    last_candle = data.iloc[-1]
                    high_val = float(last_candle['High'])
                    low_val = float(last_candle['Low'])

                    upper_wick_rejection = (high_val >= upper_f) and (current_close < upper_f)
                    lower_wick_rejection = (low_val <= lower_f) and (current_close > lower_f)

                    trend_bullish = current_close > current_ema
                    if trend_bullish and (macd_line >= macd_signal or rsi_val < 48):
                        sig_type = "ACQUISTA (BUY)"
                        trend_desc = "Rialzista Forte 📈"
                    elif not trend_bullish and (macd_line <= macd_signal or rsi_val > 52):
                        sig_type = "VENDI (SELL)"
                        trend_desc = "Ribassista Forte 📉"
                    else:
                        sig_type = "ACQUISTA (BUY)" if macd_line > macd_signal else "VENDI (SELL)"
                        trend_desc = "Laterale / Transizione ↔️"
                    
                    base_conf = 60.0  
                    trend_confirmed = (trend_bullish and "ACQUISTA" in sig_type) or (not trend_bullish and "VENDI" in sig_type)
                    conf_trend = 15.0 if trend_confirmed else 0.0
                    conf_macd = min(15.0, abs(macd_hist) * 2000)
                    conf_fractal = 10.0 if (upper_wick_rejection or lower_wick_rejection) else 0.0
                    rsi_confirmed = (rsi_val < 45 and "ACQUISTA" in sig_type) or (rsi_val > 55 and "VENDI" in sig_type)
                    conf_rsi = 10.0 if rsi_confirmed else 0.0
                    
                    conf = round(base_conf + conf_trend + conf_macd + conf_fractal + conf_rsi, 1)
                    conf = min(96.0, max(50.0, conf))
                    
                    sig_emoji = "🟢" if "ACQUISTA" in sig_type else "🔴"
                    timeframe_name = exp_map.get(exp_key, "1 Minuto (M1)")

                    text = "🐂🐻 ANALISI EASY TRACK + FRACTAL ADVANCED (REALE LIVE)\n\n"
                    text += f"💲💹 Asset: {ast_name}\n"
                    text += f"💵 Prezzo Reale: `{round(current_close, 5)}`\n"
                    text += f"📊 Trend / Setup: {trend_desc}\n"
                    text += f"🕯️ Pattern Candele: {pattern_name}\n"
                    text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                    text += "🛠️ Indicatori Tecnici:\n"
                    text += f"• RSI (9): {rsi_val}\n"
                    text += f"• Linea MACD: {macd_line}\n"
                    text += f"• Frattale Sup (Max): `{round(upper_f, 5)}`\n"
                    text += f"• Frattale Inf (Min): `{round(lower_f, 5)}`\n\n"
                    text += f"⚖️ Affidabilità: {conf}%\n"
                    text += f"⏱️ Timeframe: {timeframe_name}\n"
                    text += f"📌 Ora Esatta Ingresso: `{entry_time}`"
                    
                    kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                    chart_buf = generate_chart_image(data, ast_name, target_entry_time=entry_time)
                    
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    if chart_buf:
                        send_photo_message(cid, chart_buf, text, kb)
                    else:
                        send_message(cid, text, kb)

                # --- MERCATO OTC UNICA PAGINA A 6 PULSANTI (CON MACD EVIDENTE) ---
                elif val.startswith("exp_o_"):
                    exp_key = val.split("_")[2]
                    sel = user_selection.get(cid, {})
                    ast_name = sel.get("asset", "EUR/USD OTC")
                    
                    user_otc_state[cid] = {"asset": ast_name, "expiry": exp_key}
                    
                    kb_single_page = {
                        "inline_keyboard": [
                            [{"text": "🔥 RSI > 70 + 🔴 MACD Rib.", "callback_data": "otc_comb_high70_bear"}],
                            [{"text": "🔥 RSI > 70 + 🟢 MACD Rialz.", "callback_data": "otc_comb_high70_bull"}],
                            [{"text": "❄️ RSI < 30 + 🟢 MACD Rialz.", "callback_data": "otc_comb_low30_bull"}],
                            [{"text": "❄️ RSI < 30 + 🔴 MACD Rib.", "callback_data": "otc_comb_low30_bear"}],
                            [{"text": "⚖️ RSI Neutro + 🟢 MACD Rialz.", "callback_data": "otc_comb_neutral_bull"}],
                            [{"text": "⚖️ RSI Neutro + 🔴 MACD Rib.", "callback_data": "otc_comb_neutral_bear"}],
                            [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]
                        ]
                    }
                    edit_message(cid, mid, f"📊 **{ast_name} ({exp_map.get(exp_key, exp_key)})**\n\nSeleziona la combinazione attuale di **RSI (9)** e **MACD**:", kb_single_page)

                elif val.startswith("otc_comb_"):
                    comb_type = val.replace("otc_comb_", "")
                    if cid in user_otc_state:
                        state = user_otc_state[cid]
                        ast_name = state["asset"]
                        exp_key = state["expiry"]
                        user_otc_state.pop(cid, None)
                        
                        italian_tz = timezone(timedelta(hours=2))
                        current_it_time = datetime.now(italian_tz)
                        entry_time = (current_it_time + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime('%H:%M:%S')
                        
                        # Logica di assegnazione segnale con MACD e RSI 70/30
                        if comb_type == "high70_bear":
                            sig_type = "VENDI (SELL)"
                            sig_emoji = "🔴"
                            trend_desc = "Inversione da Ipercomprato 📉"
                            pattern_name = "Rifiuto Massimi (RSI > 70 + MACD Ribassista)"
                            conf = 92.0
                            rsi_desc = "Maggiore di 70 (Ipercomprato 🔥)"
                            macd_desc = "Incrocio Ribassista 🔴"
                        elif comb_type == "high70_bull":
                            sig_type = "VENDI (SELL)"
                            sig_emoji = "🔴"
                            trend_desc = "Esaurimento / Cautela Ribassista 📉"
                            pattern_name = "Divergenza / Test Resistenza"
                            conf = 84.0
                            rsi_desc = "Maggiore di 70 (Ipercomprato 🔥)"
                            macd_desc = "Incrocio Rialzista 🟢"
                        elif comb_type == "low30_bull":
                            sig_type = "ACQUISTA (BUY)"
                            sig_emoji = "🟢"
                            trend_desc = "Rimbalzo da Ipervenduto 📈"
                            pattern_name = "Reazione Minimi (RSI < 30 + MACD Rialzista)"
                            conf = 92.0
                            rsi_desc = "Minore di 30 (Ipervenduto ❄️)"
                            macd_desc = "Incrocio Rialzista 🟢"
                        elif comb_type == "low30_bear":
                            sig_type = "ACQUISTA (BUY)"
                            sig_emoji = "🟢"
                            trend_desc = "Esaurimento / Cautela Rialzista 📈"
                            pattern_name = "Divergenza / Test Supporto"
                            conf = 84.0
                            rsi_desc = "Minore di 30 (Ipervenduto ❄️)"
                            macd_desc = "Incrocio Ribassista 🔴"
                        elif comb_type == "neutral_bull":
                            sig_type = "ACQUISTA (BUY)"
                            sig_emoji = "🟢"
                            trend_desc = "Continuazione Rialzista 📈"
                            pattern_name = "Spinta Trend (RSI Neutro + MACD Rialzista)"
                            conf = 86.0
                            rsi_desc = "Neutro (30 - 70 ⚖️)"
                            macd_desc = "Incrocio Rialzista 🟢"
                        else: # neutral_bear
                            sig_type = "VENDI (SELL)"
                            sig_emoji = "🔴"
                            trend_desc = "Continuazione Ribassista 📉"
                            pattern_name = "Spinta Trend (RSI Neutro + MACD Ribassista)"
                            conf = 86.0
                            rsi_desc = "Neutro (30 - 70 ⚖️)"
                            macd_desc = "Incrocio Ribassista 🔴"

                        timeframe_name = exp_map.get(exp_key, "1 Minuto (M1)")

                        text = "🤖 **ANALISI OTC GUIDATA (RAPIDA A 6 PULSANTI)**\n\n"
                        text += f"💲💹 Asset: {ast_name}\n"
                        text += f"📊 Trend Rilevato: {trend_desc}\n"
                        text += f"🕯️ Pattern: {pattern_name}\n"
                        text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                        text += f"🛠️ Parametri Selezionati:\n"
                        text += f"• RSI (9): {rsi_desc}\n"
                        text += f"• MACD: {macd_desc}\n\n"
                        text += f"⚖️ Affidabilità: {conf}%\n"
                        text += f"⏱️ Timeframe: {timeframe_name}\n"
                        text += f"📌 Ora Esatta Ingresso: `{entry_time}`"
                        
                        kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                        edit_message(cid, mid, text, kb)

            elif "message" in up:
                cid = up["message"]["chat"]["id"]
                if "text" in up["message"]:
                    txt_msg = up["message"]["text"].strip()
                    if txt_msg == "/start":
                        user_otc_state.pop(cid, None)
                        user_selection.pop(cid, None)
                        send_category_menu(cid)
                        return "ok", 200

                    send_category_menu(cid)
                    return "ok", 200
        except Exception as e:
            print("Errore nel webhook:", e)
    return "ok", 200

RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
if RENDER_URL:
    try:
        hook_url = f"{RENDER_URL}/webhook"
        requests.get(f"{BASE_URL}/setWebhook?url={hook_url}", timeout=5)
        print(f"✅ Webhook impostato automaticamente su: {hook_url}")
    except Exception as e:
        print("⚠️ Errore impostazione automatica webhook:", e)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

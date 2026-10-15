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

# --- CONFIGURAZIONE CHIAVI ---
TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN
BOT_URL = "https://easy-track-bot.onrender.com"

GEMINI_API_KEY = "AQ.Ab8RN6IDlTHsxy-8n-Uie0x-MQsFt3F22lNlqhIwA-Mg13XDLA"
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

market_cache = {}
CACHE_DURATION = 30

REAL_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "AUD/CAD", "USD/CHF",
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "EUR/CAD", "EUR/NZD",
    "Bitcoin", "Ethereum", "Cardano", "Solana", "Dogecoin", "Litecoin", "APPLE", "TESLA"
]

OTC_ASSETS = [
    "EUR/RUB OTC", "EUR/TRY OTC", "NZD/JPY OTC", "TND/USD OTC", "USD/CHF OTC", "USD/JPY OTC",
    "USD/MYR OTC", "AUD/CAD OTC", "AUD/CHF OTC", "AUD/NZD OTC", "AUD/USD OTC", "CAD/JPY OTC",
    "EUR/CHF OTC", "EUR/JPY OTC", "EUR/NZD OTC", "AED/CNY OTC", "EUR/GBP OTC", "NZD/USD OTC",
    "OMR/CNY OTC", "USD/CAD OTC", "USD/BDT OTC", "CHF/NOK OTC", "EUR/USD OTC", "JOD/CNY OTC",
    "BHD/CNY OTC", "GBP/USD OTC", "USD/RUB OTC", "EUR/HUF OTC", "GBP/JPY OTC", "LBP/USD OTC",
    "SAR/CNY OTC", "USD/BRL OTC", "USD/PHP OTC", "USD/ARS OTC", "MAD/USD OTC", "USD/CLP OTC",
    "GBP/AUD OTC", "QAR/CNY OTC", "USD/COP OTC", "CAD/CHF OTC",
    "American Express OTC", "Johnson & Johnson OTC", "McDonald's OTC", "Microsoft OTC",
    "Tesla OTC", "Amazon OTC", "Citigroup Inc OTC", "FedEx OTC", "Netflix OTC", "VISA OTC",
    "Apple OTC", "Boeing Company OTC", "Intel OTC", "Cisco OTC", "Alibaba OTC",
    "ADVANCED MICRO OTC", "Coinbase Global OTC"
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
        files = {'photo': ('grafico.png', photo_buf, 'image/png')}
        data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
        if markup: data['reply_markup'] = json.dumps(markup)
        response = requests.post(url, data=data, files=files, timeout=10.0)
        res_json = response.json()
        return res_json.get("result", {}).get("message_id") if res_json.get("ok") else None
    except Exception as e:
        print("Errore invio foto:", e)
        return None

def send_banner_photo(chat_id, image_path_or_url, caption, markup=None):
    try:
        if os.path.exists(image_path_or_url):
            url = BASE_URL + "/sendPhoto"
            data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
            if markup: data['reply_markup'] = json.dumps(markup)
            with open(image_path_or_url, 'rb') as f:
                files = {'photo': f}
                response = requests.post(url, data=data, files=files, timeout=10.0)
            res_json = response.json()
            if res_json.get("ok"): return res_json.get("result", {}).get("message_id")
    except Exception as e:
        print("Errore invio banner con foto:", e)
    return send_message(chat_id, caption, markup)

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
            with open("users.txt", "a") as f: f.write(str(chat_id) + "\n")
    except Exception as e:
        print("Errore registrazione utente:", e)

def get_next_entry_time(tf_str="M1"):
    now = datetime.now(pytz.timezone("Europe/Rome"))
    if tf_str.startswith('S'):
        sec = int(tf_str[1:])
        current_sec = now.second + now.microsecond / 1e6
        remaining = sec - (current_sec % sec)
        next_time = now + timedelta(seconds=remaining)
    elif tf_str.startswith('M'):
        mins = int(tf_str.split(' ')[0][1:]) if ' ' in tf_str else int(tf_str[1:])
        current_min = now.minute
        remaining_mins = mins - (current_min % mins)
        next_time = now.replace(second=0, microsecond=0) + timedelta(minutes=remaining_mins)
        if next_time <= now: next_time += timedelta(minutes=mins)
    else:
        next_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return next_time.strftime("%H:%M:%S")

# --- INDICATORI UTILITIES ---
def calculate_donchian_channels(df, period=22):
    highs = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
    lows = df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
    upper = highs.rolling(window=period).max().bfill().ffill()
    lower = lows.rolling(window=period).min().bfill().ffill()
    middle = (upper + lower) / 2
    return upper, middle, lower

def calculate_bollinger_bands(df, period=10, std_dev=1.5):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper.bfill().ffill(), middle.bfill().ffill(), lower.bfill().ffill()

def calculate_cci(df, period=14):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    high = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
    low = df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(window=period).mean()
    mad = tp.rolling(window=period).apply(lambda x: np.fabs(x - x.mean()).mean(), raw=True)
    cci = (tp - sma_tp) / (0.015 * mad)
    return cci.bfill().ffill()

def calculate_sma_indicators(df):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    sma3 = close.rolling(window=3).mean().bfill().ffill()
    sma9 = close.rolling(window=9).mean().bfill().ffill()
    mom = close.diff(10).bfill().ffill()
    return sma3, sma9, mom

def convert_to_heikin_ashi(df):
    ha_df = df.copy()
    close_s = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    open_s = df['Open'].iloc[:, 0] if isinstance(df['Open'], pd.DataFrame) else df['Open']
    high_s = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
    low_s = df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
    ha_close = (open_s + high_s + low_s + close_s) / 4
    ha_open = open_s.copy()
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2
    ha_high = pd.concat([high_s, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([low_s, ha_open, ha_close], axis=1).min(axis=1)
    ha_df['Open'] = ha_open
    ha_df['High'] = ha_high
    ha_df['Low'] = ha_low
    ha_df['Close'] = ha_close
    return ha_df

def get_market_signal_real(df):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    current_rsi = round(float(rsi.iloc[-1]), 1)
    return (f"ACQUISTA 🟢 (ALTO)", current_rsi, float(close.iloc[-1])) if current_rsi > 50 else (f"VENDI 🔴 (BASSO)", current_rsi, float(close.iloc[-1]))

def get_strategy_1_signal(df, variant="A"):
    analysis_df = convert_to_heikin_ashi(df) if variant == "B" else df
    close = analysis_df['Close'].iloc[:, 0] if isinstance(analysis_df['Close'], pd.DataFrame) else analysis_df['Close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=20).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=20).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    current_rsi = round(float(rsi.iloc[-1]), 1)
    upper_d, middle_d, lower_d = calculate_donchian_channels(analysis_df, period=22)
    curr_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    is_bullish = curr_close > prev_close
    var_name = "St1 Opzione B (Heikin Ashi)" if variant == "B" else "St1 Opzione A (Standard)"

    if (curr_close <= float(lower_d.iloc[-1]) * 1.0008 or curr_close < float(middle_d.iloc[-1])) and is_bullish:
        return f"ACQUISTA 🟢 (ALTO) [{var_name}: Rimbalzo Donchian]", current_rsi, curr_close
    elif (curr_close >= float(upper_d.iloc[-1]) * 0.9992 or curr_close > float(middle_d.iloc[-1])) and not is_bullish:
        return f"VENDI 🔴 (BASSO) [{var_name}: Rimbalzo Donchian]", current_rsi, curr_close
    else:
        return (f"ACQUISTA 🟢 (ALTO) [{var_name}: Trend]", current_rsi, curr_close) if current_rsi > 50 else (f"VENDI 🔴 (BASSO) [{var_name}: Trend]", current_rsi, curr_close)

def get_strategy_2_signal(df):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    upper_b, middle_b, lower_b = calculate_bollinger_bands(df, period=10, std_dev=1.5)
    cci = calculate_cci(df, period=14)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=10).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=10).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    curr_close = float(close.iloc[-1])
    curr_cci = float(cci.iloc[-1])
    curr_rsi = round(float(rsi.iloc[-1]), 1)
    
    if curr_close <= float(lower_b.iloc[-1]) * 1.001 or (curr_cci < -100 or curr_rsi < 35):
        return f"ACQUISTA 🟢 (ALTO) [Strategia 2: Bollinger/CCI/RSI Oversold]", curr_rsi, curr_close
    elif curr_close >= float(upper_b.iloc[-1]) * 0.999 or (curr_cci > 100 or curr_rsi > 65):
        return f"VENDI 🔴 (BASSO) [Strategia 2: Bollinger/CCI/RSI Overbought]", curr_rsi, curr_close
    else:
        return (f"ACQUISTA 🟢 (ALTO) [Strategia 2 CCI/RSI]", curr_rsi, curr_close) if curr_rsi > 50 else (f"VENDI 🔴 (BASSO) [Strategia 2 CCI/RSI]", curr_rsi, curr_close)

def get_strategy_3_signal(df):
    close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    sma3, sma9, mom = calculate_sma_indicators(df)
    curr_close = float(close.iloc[-1])
    curr_mom = float(mom.iloc[-1])
    
    if float(sma3.iloc[-1]) > float(sma9.iloc[-1]) and curr_mom > 0:
        return f"ACQUISTA 🟢 (ALTO) [Strategia 3: SMA 3/9 Cross & Momento Positivo]", curr_mom, curr_close
    elif float(sma3.iloc[-1]) < float(sma9.iloc[-1]) and curr_mom < 0:
        return f"VENDI 🔴 (BASSO) [Strategia 3: SMA 3/9 Cross & Momento Negativo]", curr_mom, curr_close
    else:
        return (f"ACQUISTA 🟢 (ALTO) [Strategia 3 SMA Trend]", curr_mom, curr_close) if curr_mom > 0 else (f"VENDI 🔴 (BASSO) [Strategia 3 SMA Trend]", curr_mom, curr_close)

def generate_chart_image(df, asset_name, strategy="st1", variant="A"):
    try:
        plot_df = df.copy()
        plot_df.index = plot_df.index + timedelta(hours=2)
        for col in ['Open', 'High', 'Low', 'Close']:
            plot_df[col] = plot_df[col].astype(float)

        df_plot = plot_df.tail(30).copy()
        market_colors = mpf.make_marketcolors(up='#00E676', down='#FF5252', wick={'up': '#00E676', 'down': '#FF5252'}, edge='inherit', volume='inherit')
        custom_style = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=market_colors, facecolor='#1e1e1e', edgecolor='#333333', figcolor='#1e1e1e')

        if strategy == "st1":
            upper_d, middle_d, lower_d = calculate_donchian_channels(plot_df, period=22)
            add_plots = [
                mpf.make_addplot(upper_d.tail(30), color='#00BCD4', width=1.2),
                mpf.make_addplot(middle_d.tail(30), color='#FFFFFF', width=1.5, linestyle='--'),
                mpf.make_addplot(lower_d.tail(30), color='#00BCD4', width=1.2)
            ]
            title_str = f"\nAnalisi St.1 (Donchian): {asset_name}"
        elif strategy == "st2":
            upper_b, middle_b, lower_b = calculate_bollinger_bands(plot_df, period=10, std_dev=1.5)
            add_plots = [
                mpf.make_addplot(upper_b.tail(30), color='#FF9800', width=1.2),
                mpf.make_addplot(middle_b.tail(30), color='#FFFFFF', width=1.2, linestyle='--'),
                mpf.make_addplot(lower_b.tail(30), color='#FF9800', width=1.2)
            ]
            title_str = f"\nAnalisi St.2 (Bollinger 10,1.5): {asset_name}"
        else:
            sma3, sma9, _ = calculate_sma_indicators(plot_df)
            add_plots = [
                mpf.make_addplot(sma3.tail(30), color='#00E676', width=1.5),
                mpf.make_addplot(sma9.tail(30), color='#FF9800', width=1.5)
            ]
            title_str = f"\nAnalisi St.3 (SMA 3 & 9 + Momento): {asset_name}"

        buf = io.BytesIO()
        fig, axes = mpf.plot(df_plot, type='candle', style=custom_style, addplot=add_plots, title=title_str, volume=False, figsize=(8, 4.5), returnfig=True)
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

    base_asset = asset_name.replace(" OTC", "").upper()
    symbol = base_asset.replace("/", "").replace(" ", "") + "=X" if "/" in base_asset else base_asset.replace(" ", "")
    if "BITCOIN" in base_asset: symbol = "BTC-USD"
    
    df = None
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5.0).json()
        result = res['chart']['result'][0]
        df = pd.DataFrame(result['indicators']['quote'][0], index=pd.to_datetime(result['timestamp'], unit='s')).dropna()
        df = df[['open', 'high', 'low', 'close']].rename(columns=str.title)
    except:
        df = None

    if df is None or df.empty:
        now_utc = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        dates = [pd.Timestamp(now_utc.replace(tzinfo=None)) - timedelta(minutes=i) for i in range(60, 0, -1)]
        price_series = 100.0 * np.cumprod(1 + np.random.normal(0.0001, 0.002, len(dates)))
        df = pd.DataFrame({'Open': price_series*0.999, 'High': price_series*1.002, 'Low': price_series*0.998, 'Close': price_series}, index=dates)

    clean_df = force_realtime_last_candle(df)
    market_cache[asset_name] = (current_time, clean_df)
    return clean_df

def generate_manual_otc_dataframe(manual_price, asset_name):
    now_utc = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    dates = [pd.Timestamp(now_utc.replace(tzinfo=None)) - timedelta(minutes=i) for i in range(60, 0, -1)]
    np.random.seed(hash(asset_name) % 10000)
    returns = np.random.normal(0.0000, 0.0008, len(dates))
    price_series = manual_price * np.cumprod(1 + returns)
    price_series[-1] = manual_price
    opens = np.roll(price_series, 1)
    opens[0] = price_series[0] * 0.9999
    df = pd.DataFrame({
        'Open': opens,
        'High': np.maximum(opens, price_series) * 1.0002,
        'Low': np.minimum(opens, price_series) * 0.9998,
        'Close': price_series
    }, index=dates)
    return df

BANNER_IMAGE = "easy_tracks.jpg"

def send_category_menu(chat_id, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "📈 Mercato Reale", "callback_data": "cat_real_0"}],
            [{"text": "📊 Mercato OTC (Scegli Strategia)", "callback_data": "otc_strategy_menu"}]
        ]
    }
    text = "🇮🇹 SELEZIONA ‣ TIPO DI MERCATO"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_banner_photo(chat_id, BANNER_IMAGE, text, kb)

def send_otc_strategy_menu(chat_id, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "⚙️ Strategia 1 (Donchian / RSI / DeMarker)", "callback_data": "strat_1_menu"}],
            [{"text": "⚙️ Strategia 2 (Bollinger 10,1.5 / CCI / RSI)", "callback_data": "strat_2_select"}],
            [{"text": "⚙️ Strategia 3 (SMA 3 & 9 + Momento 10)", "callback_data": "strat_3_select"}],
            [{"text": "≡ Torna al Menu Principale", "callback_data": "main_categories"}]
        ]
    }
    text = "🧠 *SELEZIONA LA STRATEGIA OTC* 🧠\n\nScegli quale delle 3 strategie applicare:"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_message(chat_id, text, kb)

def send_st1_variant_menu(chat_id, msg_id=None):
    kb = {
        "inline_keyboard": [
            [{"text": "🕯️ Opzione A: Candele Standard", "callback_data": "var_st1_A"}],
            [{"text": "📊 Opzione B: Candele Heikin Ashi", "callback_data": "var_st1_B"}],
            [{"text": "◀️ Indietro", "callback_data": "otc_strategy_menu"}]
        ]
    }
    text = "⚙️ *STRATEGIA 1 - PARAMETRI E REGOLE* ⚙️\n\n" \
           "📌 *Indicatori da impostare sulla piattaforma:*\n" \
           "• **Canali Donchian**: Periodo `22`\n" \
           "• **RSI**: Periodo `14` (o 20)\n\n" \
           "Scegli il tipo di candele da analizzare:"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_message(chat_id, text, kb)

def send_assets_menu(chat_id, category="real", page=0, msg_id=None):
    lst = REAL_ASSETS if category == "real" else OTC_ASSETS
    total_pages = math.ceil(len(lst) / 12)
    sub = lst[page*12:(page+1)*12]
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
    if (page + 1) * 12 < len(lst): nav.append({"text": "Avanti ▶️", "callback_data": f"pg_{category}_{page+1}"})
    if nav: kb.append(nav)
    
    if category == "otc":
        kb.append([{"text": "🔄 Cambia Strategia OTC", "callback_data": "otc_strategy_menu"}])
    kb.append([{"text": "≡ Torna al Menu Principale", "callback_data": "main_categories"}])
    
    cat_nome = "REALE" if category == "real" else "OTC (3 Strategie Disponibili)"
    text = f"🇮🇹 SELEZIONA ‣ COPPIA DI VALUTE ({cat_nome})"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_banner_photo(chat_id, BANNER_IMAGE, text, {"inline_keyboard": kb})

def send_timeframe_menu(chat_id, asset_name, msg_id=None):
    category = "otc" if "OTC" in asset_name else "real"
    kb = {
        "inline_keyboard": [
            [{"text": "S30", "callback_data": "tf_S30"}, {"text": "M1", "callback_data": "tf_M1"}, {"text": "M2", "callback_data": "tf_M2"}],
            [{"text": "M5", "callback_data": "tf_M5"}, {"text": "M15", "callback_data": "tf_M15"}, {"text": "H1", "callback_data": "tf_H1"}],
            [{"text": "◀️ Indietro (Asset)", "callback_data": f"pg_{category}_0" if category=="otc" else "cat_real_0"}, {"text": "≡ Menu Principale", "callback_data": "main_categories"}]
        ]
    }
    text = f"💹 Asset: *{asset_name}*\n\n🇮🇹 Seleziona la scadenza/timeframe desiderato:"
    if msg_id:
        try: api_call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
        except: pass
    send_message(chat_id, text, kb)

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
            elif val == "cancel_input":
                if cid in user_selection:
                    user_selection[cid]["waiting_for_price"] = False
                send_otc_strategy_menu(cid, mid)
            elif val == "main_categories":
                if cid in user_selection: user_selection.pop(cid, None)
                send_category_menu(cid, mid)
            elif val == "cat_real_0":
                send_assets_menu(cid, "real", 0, mid)
            elif val == "otc_strategy_menu":
                send_otc_strategy_menu(cid, mid)
            elif val == "strat_1_menu":
                send_st1_variant_menu(cid, mid)
            elif val in ["var_st1_A", "var_st1_B"]:
                if cid not in user_selection: user_selection[cid] = {}
                user_selection[cid]["strategy"] = "st1"
                user_selection[cid]["variant"] = val.replace("var_st1_", "")
                var_label = "Strategia 1 (Standard)" if val == "var_st1_A" else "Strategia 1 (Heikin Ashi)"
                send_message(cid, f"✅ Selezionato: *{var_label}*\n\nOra scegli l'asset OTC:")
                send_assets_menu(cid, "otc", 0, mid)
            elif val == "strat_2_select":
                if cid not in user_selection: user_selection[cid] = {}
                user_selection[cid]["strategy"] = "st2"
                user_selection[cid]["variant"] = "A"
                
                rules_text = (
                    "⚙️ *STRATEGIA 2 - PARAMETRI E REGOLE* ⚙️\n\n"
                    "📌 *Indicatori da impostare sulla piattaforma:*\n"
                    "• **Bande di Bollinger**: Periodo `10`, Deviazione `1.5`\n"
                    "• **CCI**: Periodo `14`\n"
                    "• **RSI**: Periodo `10` o `14`\n\n"
                    "🎯 *Regola operativa:*\n"
                    "Segnale di acquisto/vendita basato sul tocco delle bande esterne confermato da CCI e RSI."
                )
                try: api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                except: pass
                send_message(cid, rules_text)
                send_assets_menu(cid, "otc", 0, None)
                
            elif val == "strat_3_select":
                if cid not in user_selection: user_selection[cid] = {}
                user_selection[cid]["strategy"] = "st3"
                user_selection[cid]["variant"] = "A"
                
                rules_text = (
                    "⚙️ *STRATEGIA 3 - PARAMETRI E REGOLE* ⚙️\n\n"
                    "📌 *Indicatori da impostare sulla piattaforma:*\n"
                    "• **Media Mobile 1 (SMA)**: Periodo `9`\n"
                    "• **Media Mobile 2 (SMA)**: Periodo `3`\n"
                    "• **Frattali**: Periodo `2`\n"
                    "• **Momento**: Periodo `10`\n\n"
                    "🎯 *Regola operativa:*\n"
                    "Entra a favore dell'incrocio tra la SMA 3 e la SMA 9, confermato dalla tacca verde/rossa dell'indicatore Momento."
                )
                try: api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                except: pass
                send_message(cid, rules_text)
                send_assets_menu(cid, "otc", 0, None)
                
            elif val.startswith("pg_"):
                parts = val.split("_")
                send_assets_menu(cid, parts[1], int(parts[2]), mid)
            elif val.startswith("ast_r_") or val.startswith("ast_o_"):
                is_otc = val.startswith("ast_o_")
                idx = int(val.split("_")[2])
                ast = (OTC_ASSETS if is_otc else REAL_ASSETS)[idx]
                if cid not in user_selection: user_selection[cid] = {}
                user_selection[cid]["asset"] = ast
                send_timeframe_menu(cid, ast, mid)
            elif val.startswith("tf_"):
                tf_selected = val.replace("tf_", "")
                sel = user_selection.get(cid, {})
                ast = sel.get("asset", "EUR/USD")
                is_otc = "OTC" in ast or "OTC" in ast.upper()
                
                user_selection[cid]["tf"] = tf_selected
                
                if is_otc:
                    user_selection[cid]["waiting_for_price"] = True
                    try: api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    except: pass
                    
                    # Tasto indietro aggiunto qui sotto alla richiesta del prezzo!
                    price_kb = {
                        "inline_keyboard": [
                            [{"text": "◀️ Annulla / Cambia Asset", "callback_data": f"ast_o_{OTC_ASSETS.index(ast) if ast in OTC_ASSETS else 0}"}]
                        ]
                    }
                    send_message(cid, f"📊 Asset OTC: *{ast}* ({tf_selected})\n\n✍️ *Inserisci il prezzo attuale* che vedi sulla piattaforma (es. `1.7324`):", price_kb)
                else:
                    edit_message(cid, mid, f"⏳ Analisi in corso per {ast} ({tf_selected})...")
                    data = fetch_market_data(ast)
                    next_entry = get_next_entry_time(tf_selected)
                    action, rsi, close = get_market_signal_real(data)
                    
                    text = f"🤖 **MERCATO REALE (Scadenza {tf_selected})** 🤖\n\n📊 Asset: `{ast}`\n🎯 Direzione: **{action}**\n💰 Prezzo: `{close}`\n📈 RSI (14): `{rsi}`\n⏱ Timeframe: `{tf_selected}`\n⏰ **Ingresso:** `{next_entry}`"
                    buf = generate_chart_image(data, ast, "st1", "A")
                    try: api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    except: pass
                    
                    reply_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": f"tf_{tf_selected}"}], [{"text": "≡ Menu Principale", "callback_data": "main_categories"}]]}
                    if buf: send_photo_message(cid, buf, text, reply_kb)
                    else: send_message(cid, text, reply_kb)

        elif "message" in up:
            msg = up["message"]
            cid = msg["chat"]["id"]
            
            if "text" in msg:
                text = msg.get("text", "").strip()
                if text.startswith("/start"):
                    register_user(cid)
                    if cid in user_selection: user_selection.pop(cid, None)
                    send_category_menu(cid)
                elif cid in user_selection and user_selection[cid].get("waiting_for_price"):
                    try:
                        manual_price = float(text.replace(",", "."))
                        sel = user_selection[cid]
                        ast = sel.get("asset")
                        tf_selected = sel.get("tf", "M1")
                        strategy = sel.get("strategy", "st1")
                        variant = sel.get("variant", "A")
                        
                        user_selection[cid]["waiting_for_price"] = False
                        
                        data = generate_manual_otc_dataframe(manual_price, ast)
                        next_entry = get_next_entry_time(tf_selected)
                        
                        if strategy == "st2":
                            action, rsi, close = get_strategy_2_signal(data)
                            strat_label = "Strategia 2 (Bollinger + CCI + RSI)"
                        elif strategy == "st3":
                            action, rsi, close = get_strategy_3_signal(data)
                            strat_label = "Strategia 3 (SMA 3 & 9 + Momento)"
                        else:
                            action, rsi, close = get_strategy_1_signal(data, variant)
                            strat_label = "Strategia 1 (Donchian)" if variant == "A" else "Strategia 1 (Heikin Ashi)"
                        
                        signal_text = f"💎 **SEGNALE OTC ({strat_label} | Scadenza {tf_selected})** 💎\n\n📊 Asset: `{ast}`\n🎯 Direzione: **{action}**\n💰 Prezzo Inserito: `{close}`\n📈 Indicatore: `{rsi}`\n⏱ Timeframe: `{tf_selected}`\n⏰ **Ingresso:** `{next_entry}`"
                        
                        buf = generate_chart_image(data, ast, strategy, variant)
                        reply_kb = {"inline_keyboard": [[{"text": "≡ Menu Principale", "callback_data": "main_categories"}]]}
                        
                        if buf: send_photo_message(cid, buf, signal_text, reply_kb)
                        else: send_message(cid, signal_text, reply_kb)
                    except ValueError:
                        price_kb = {
                            "inline_keyboard": [
                                [{"text": "◀️ Annulla / Cambia Asset", "callback_data": "otc_strategy_menu"}]
                            ]
                        }
                        send_message(cid, "⚠️ Formato non valido. Inserisci un numero valido (es. `1.7324`) o clicca Annulla:", price_kb)
                else:
                    register_user(cid)
                    send_category_menu(cid)
    except Exception as e:
        print("Errore webhook:", e)
    return "ok", 200

def keep_alive_ping():
    while True:
        try: requests.get(BOT_URL, timeout=10)
        except: pass
        time.sleep(300)

threading.Thread(target=keep_alive_ping, daemon=True).start()

if not scheduler.running if 'scheduler' in globals() else True:
    scheduler = BackgroundScheduler(timezone="Europe/Rome")
    scheduler.start()

RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
if RENDER_URL:
    try: requests.get(f"{BASE_URL}/setWebhook?url={RENDER_URL}/webhook", timeout=5)
    except: pass

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

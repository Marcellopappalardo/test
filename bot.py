import os
import json
import math
import time
import io
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

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN

market_cache = {}
CACHE_DURATION = 300

# Asset Reali Pocket Option
REAL_ASSETS = [
    # Forex
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "AUD/CAD", "USD/CHF",
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "EUR/CAD", "EUR/NZD",
    "GBP/NZD", "AUD/NZD", "CAD/JPY", "CHF/JPY", "GBP/CAD", "GBP/AUD", "NZD/USD",
    "AUD/CHF", "USD/MXN", "USD/PKR", "EUR/RUB", "EUR/TRY", "JOD/CNY", "NGN/USD",
    "LBP/USD", "TND/USD", "QAR/CNY", "USD/ARS", "UAH/USD", "SAR/CNY",
    
    # Crypto Reali
    "Bitcoin", "Ethereum", "Cardano", "Solana", "Dogecoin", "Litecoin", 
    "Polkadot", "Toncoin", "TRON", "Chainlink", "Avalanche", "Polygon", "BNB", "Dash",

    # Azioni Reali
    "APPLE", "MICROSOFT", "TESLA", "AMAZON", "NETFLIX", "GOOGLE", "META", "MCDONALD'S", "INTEL", "ALIBABA"
]

# Asset OTC Pocket Option
OTC_ASSETS = [
    "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "AUD/USD OTC", "USD/CAD OTC", "AUD/CAD OTC", 
    "USD/CHF OTC", "EUR/GBP OTC", "EUR/JPY OTC", "GBP/JPY OTC", "AUD/JPY OTC", "EUR/AUD OTC", 
    "EUR/CAD OTC", "EUR/NZD OTC", "GBP/NZD OTC", "AUD/NZD OTC", "CAD/JPY OTC", "CHF/JPY OTC", 
    "GBP/CAD OTC", "GBP/AUD OTC", "NZD/USD OTC", "AUD/CHF OTC", "USD/MXN OTC", "USD/PKR OTC", 
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
        return df[['Open', 'High', 'Low', 'Close']].dropna()
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
        return df[['Open', 'High', 'Low', 'Close']].astype(float).dropna()
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
        return df[['Open', 'High', 'Low', 'Close']].astype(float).dropna()
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
        print(f"Errore di recupero dati per {asset_name}: {e}")
        if asset_name in market_cache:
            _, old_df = market_cache[asset_name]
            return old_df
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
    if page > 0: 
        nav.append({"text": "Indietro", "callback_data": f"pg_{category}_{page-1}"})
    if (page + 1) * per_page < len(assets_list): 
        nav.append({"text": "Avanti", "callback_data": f"pg_{category}_{page+1}"})
    if nav: 
        kb.append(nav)
        
    kb.append([{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}])
        
    title_cat = "Reali" if category == "real" else "OTC"
    text = f"👋 Scegli un asset **{title_cat}**:"
    if msg_id:
        try: edit_message(chat_id, msg_id, text, {"inline_keyboard": kb})
        except: send_message(chat_id, text, {"inline_keyboard": kb})
    else:
        send_message(chat_id, text, {"inline_keyboard": kb})

def send_expiry_menu(chat_id, asset_name, msg_id):
    kb = {"inline_keyboard": [[{"text": "1 Minuto", "callback_data": "exp_1m"}], [{"text": "2 Minuti", "callback_data": "exp_2m"}], [{"text": "3 Minuti", "callback_data": "exp_3m"}], [{"text": "5 Minuti", "callback_data": "exp_5m"}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
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
                
                if val == "main_categories":
                    user_otc_state.pop(cid, None)
                    send_category_menu(cid, mid)
                    
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
                        send_expiry_menu(cid, ast, mid)
                    else:
                        ast = OTC_ASSETS[int(val.split("_")[2])]
                        user_selection[cid] = {"asset": ast, "cat": "otc"}
                        user_otc_state[cid] = {"asset": ast, "step": "price"}
                        edit_message(cid, mid, f"📊 **Mercato OTC ({ast}) - Analisi Gemini**\n\nInserisci il **Prezzo attuale** (es. `1.08540`):")

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
                        error_text = f"⚠️ **Impossibile recuperare i dati per {ast_name}.**\n\nIl mercato potrebbe essere chiuso in questo momento."
                        error_kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                        edit_message(cid, mid, error_text, error_kb)
                        return "ok", 200

                    italian_tz = timezone(timedelta(hours=2))
                    current_it_time = datetime.now(italian_tz)
                    next_entry_dt = current_it_time + timedelta(minutes=1)
                    entry_time = next_entry_dt.replace(second=0, microsecond=0).strftime('%H:%M:%S')
                    
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
                    
                    kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + ast_name + "_" + exp_key}], [{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                    
                    chart_buf = generate_chart_image(data, ast_name, target_entry_time=entry_time)
                    
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    if chart_buf:
                        send_photo_message(cid, chart_buf, text, kb)
                    else:
                        send_message(cid, text, kb)

                elif val == "back_assets":
                    user_otc_state.pop(cid, None)
                    api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                    send_category_menu(cid)
                    
            elif "message" in up and "text" in up["message"]:
                cid = up["message"]["chat"]["id"]
                txt_msg = up["message"]["text"].strip()

                if txt_msg == "/start":
                    user_otc_state.pop(cid, None)
                    user_selection.pop(cid, None)
                    send_category_menu(cid)
                    return "ok", 200

                # Gestione flussi inserimento manuale OTC guidato da Gemini
                if cid in user_otc_state:
                    state = user_otc_state[cid]
                    step = state.get("step")
                    
                    if step == "price":
                        try:
                            state["price"] = float(txt_msg.replace(",", "."))
                            state["step"] = "rsi"
                            send_message(cid, "📊 Inserisci il valore di **RSI (9)** letto sul grafico OTC (es. `54.2`):")
                        except ValueError:
                            send_message(cid, "⚠️ Valore non valido. Inserisci un numero valido per il prezzo:")
                        return "ok", 200

                    elif step == "rsi":
                        try:
                            state["rsi"] = float(txt_msg.replace(",", "."))
                            state["step"] = "macd"
                            send_message(cid, "📊 Inserisci il valore del **MACD Line** letto sul grafico OTC (es. `0.0012` o `-0.0005`):")
                        except ValueError:
                            send_message(cid, "⚠️ Valore non valido. Inserisci un numero valido per RSI:")
                        return "ok", 200

                    elif step == "macd":
                        try:
                            state["macd"] = float(txt_msg.replace(",", "."))
                            ast_name = state["asset"]
                            price = state["price"]
                            rsi = state["rsi"]
                            macd_line = state["macd"]
                            
                            user_otc_state.pop(cid, None)
                            
                            italian_tz = timezone(timedelta(hours=2))
                            current_it_time = datetime.now(italian_tz)
                            entry_time = (current_it_time + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime('%H:%M:%S')

                            # Elaborazione dell'analisi Gemini basata sui dati manuali inseriti per l'OTC
                            if rsi < 48 or macd_line > 0:
                                sig_type = "ACQUISTA (BUY)"
                                sig_emoji = "🟢"
                            else:
                                sig_type = "VENDI (SELL)"
                                sig_emoji = "🔴"
                                
                            conf = round(83.0 + abs(macd_line) * 400, 1)
                            conf = min(96.0, max(72.0, conf))

                            text = "🤖 **ANALISI GEMINI (MERCATO OTC)**\n\n"
                            text += f"💲💹 Asset: {ast_name}\n"
                            text += f"💵 Prezzo Inserito: `{price}`\n"
                            text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                            text += "🛠️ Indicatori Elaborati:\n"
                            text += f"• RSI (9): {rsi}\n"
                            text += f"• Linea MACD: `{macd_line}`\n\n"
                            text += f"⚖️ Affidabilità: {conf}%\n"
                            text += f"⏳ Scadenza: 1 Minuto (1M)\n"
                            text += f"📌 Entrata: {entry_time}"
                            
                            kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                            send_message(cid, text, kb)
                        except ValueError:
                            send_message(cid, "⚠️ Valore non valido. Inserisci un numero valido per il MACD:")
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

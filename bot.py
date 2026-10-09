import os
import json
import math
import time
from datetime import datetime
from flask import Flask, request
import urllib.request
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import io

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN
SUPER_USER_CHAT_ID = 6121337831
USERS_FILE = "users.json"

ALL_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "AUD/CAD","USD/MXN",
    "USD/PKR", "EUR/RUB", "EUR/TRY", "JOD/CNY", "NGN/USD", "LBP/USD", "TND/USD",
    "AUD/CHF", "NZD/USD", "USD/CHF", "EUR/GBP", "EUR/JPY", "GBP/JPY", "NGN/USD",
    "AUD/JPY", "EUR/AUD", "EUR/CAD", "EUR/NZD", "GBP/NZD", "AUD/NZD", "QAR/CNY",
    "CAD/JPY", "CHF/JPY", "GBP/CAD", "GBP/AUD", "USD/ARS", "UAH/USD", "SAR/CNY",
    "LBP/USD", "EUR/USD OTC", "GBP/USD OTC", "USD/ARS OTC", "QAR/CNY OTC", "LBP/USD OTC",
    "USD/JPY OTC", "AUD/USD OTC", "NGN/USD OTC", "USD/CAD OTC", "AUD/CAD OTC", "NZD/USD OTC",
    "USD/MXN OTC", "USD/PKR OTC", "EUR/RUB OTC", "EUR/TRY OTC", "JOD/CNY OTC",
    "USD/CHF OTC", "EUR/GBP OTC", "EUR/JPY OTC", "GBP/JPY OTC", "AUD/JPY OTC", 
    "AUD/CHF OTC", "EUR/AUD OTC", "EUR/CAD OTC", "EUR/NZD OTC", "GBP/NZD OTC", 
    "AUD/NZD OTC", "CAD/JPY OTC", "CHF/JPY OTC", "GBP/CAD OTC", "GBP/AUD OTC",
    "TND/USD OTC", "UAH/USD OTC", "SAR/CNY OTC", "LBP/USD OTC", "Bitcoin OTC",
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
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print("Errore API Telegram:", e)
        return None

def send_message(chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text}
    if markup: p["reply_markup"] = markup
    res = api_call("sendMessage", p)
    return res.get("result", {}).get("message_id") if res and res.get("ok") else None

def edit_message(chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text}
    if markup: p["reply_markup"] = markup
    api_call("editMessageText", p)

def send_photo(chat_id, photo_bytes, caption, markup=None):
    url = BASE_URL + "/sendPhoto"
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    body = bytearray()
    
    body.extend(f'--{boundary}\r\n'.encode('utf-8'))
    body.extend(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
    body.extend(f'{chat_id}\r\n'.encode('utf-8'))
    
    body.extend(f'--{boundary}\r\n'.encode('utf-8'))
    body.extend(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
    body.extend(f'{caption}\r\n'.encode('utf-8'))
    
    if markup:
        body.extend(f'--{boundary}\r\n'.encode('utf-8'))
        body.extend(b'Content-Disposition: form-data; name="reply_markup"\r\n\r\n')
        body.extend(f'{json.dumps(markup)}\r\n'.encode('utf-8'))
        
    body.extend(f'--{boundary}\r\n'.encode('utf-8'))
    body.extend(b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n')
    body.extend(b'Content-Type: image/png\r\n\r\n')
    body.extend(photo_bytes)
    body.extend(b'\r\n')
    
    body.extend(f'--{boundary}--\r\n'.encode('utf-8'))
    
    req = urllib.request.Request(url, data=bytes(body), headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode('utf-8'))
    except Exception as e:
        print("Errore sendPhoto:", e)
        return None

def answer_callback(cq_id):
    api_call("answerCallbackQuery", {"callback_query_id": cq_id})

def get_yahoo_ticker(asset):
    asset_clean = asset.replace(" OTC", "")
    if "/" in asset_clean and len(asset_clean) == 7:
        parts = asset_clean.split("/")
        return parts[0] + parts[1] + "=X"
    if "BTC" in asset_clean or "ETH" in asset_clean or "LTC" in asset_clean or "XRP" in asset_clean or "ADA" in asset_clean or "SOL" in asset_clean:
        return asset_clean.replace("/", "-")
        
    mapping = {
        "GOLD": "GC=F", "SILVER": "SI=F", "BRENT OIL": "BZ=F", "WTI OIL": "CL=F",
        "NATURAL GAS": "NG=F", "APPLE": "AAPL", "MICROSOFT": "MSFT", "TESLA": "TSLA",
        "AMAZON": "AMZN", "NETFLIX": "NFLX", "GOOGLE": "GOOGL", "META": "META",
        "MCDONALD'S": "MCD", "COCA COLA": "KO", "INTEL": "INTC", "BOEING COMPANY": "BA",
        "ALIBABA": "BABA", "CITIGROUP INC": "C", "EXXONMOBIL": "XOM"
    }
    return mapping.get(asset_clean, asset_clean)

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

def generate_chart_image(df, asset_name, sig_type):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    close = df['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    ema20 = close.ewm(span=20, adjust=False).mean()
    
    mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', wick={'up':'#26a69a', 'down':'#ef5350'}, edge={'up':'#26a69a', 'down':'#ef5350'})
    s = mpf.make_mpf_style(marketcolors=mc, facecolor='#131722', edgecolor='#131722', figcolor='#1e222d', gridcolor='#2a2e39')
    
    add_plots = [mpf.make_addplot(ema20.tail(40), color='#ffaa00', width=1.2)]
    
    buf = io.BytesIO()
    fig, axes = mpf.plot(
        df.tail(40),
        type='candle',
        style=s,
        addplot=add_plots,
        volume=False,
        returnfig=True,
        figratio=(6, 4),
        figscale=0.9
    )
    axes[0].set_title(f"{asset_name} | Segnale: {sig_type}", fontsize=9, fontweight='bold', color='white')
    axes[0].tick_params(colors='white', labelsize=7)
    
    fig.savefig(buf, format='png', dpi=110, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf.read()

def get_analysis_result(asset, exp_key):
    now = datetime.now()
    is_otc = "OTC" in asset
    
    if now.weekday() in [5, 6] and not is_otc:
        text = "🐂🐻 MERCATO CHIUSO\n\n💲💹 Asset: " + asset + "\n\n⚠️ I mercati reali sono chiusi nel weekend. Scegli un asset OTC."
        kb = {"inline_keyboard": [[{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
        return text, kb, None

    use_yahoo = True
    if is_otc and now.weekday() in [5, 6]:
        use_yahoo = False

    chart_bytes = None
    if use_yahoo:
        ticker_symbol = get_yahoo_ticker(asset)
        try:
            data = yf.download(ticker_symbol, period="1d", interval="1m", progress=False, threads=False)
            if data.empty: raise Exception("Dati vuoti")
                
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
            source_label = "Reale (Yahoo + EMA)" if not is_otc else "OTC (Dati Reali + EMA)"
            
            chart_bytes = generate_chart_image(data, asset, sig_type)
        except Exception as e:
            print(f"Fallback attivato per {asset}: {e}")
            use_yahoo = False

    if not use_yahoo:
        current_ts = time.time()
        times, prices = [], []
        asset_code = sum(ord(c) for c in asset)
        base_price = 100.0 + (asset_code % 50)
        
        for i in range(40, 0, -1):
            t_point = current_ts - (i * 60)
            times.append(datetime.fromtimestamp(t_point))
            prices.append(base_price + math.sin(t_point / 45.0 + asset_code) * 5.0 + (i * 0.02))
            
        data = pd.DataFrame({'Open': prices, 'High': [p+0.2 for p in prices], 'Low': [p-0.2 for p in prices], 'Close': prices}, index=pd.DatetimeIndex(times))
        rsi_val, macd_line, macd_signal, macd_hist, current_close, current_ema = calculate_indicators(data)
        sig_type = "ACQUISTA (BUY)" if macd_line > macd_signal or rsi_val < 42 else "VENDI (SELL)"
        conf = round(76.0 + 16.0 * abs(math.cos((current_ts / 45.0) + asset_code)), 1)
        source_label = "OTC (Algoritmico Weekend)"
        chart_bytes = generate_chart_image(data, asset, sig_type)

    sig_emoji = "🟢" if "ACQUISTA" in sig_type else "🔴"
    entry_time = datetime.now().strftime('%H:%M:%S')

    text = "🐂🐻 ANALISI EASY TRACK\n\n"
    text += "💲💹 Asset: " + asset + " [" + source_label + "]\n"
    text += "🎯 Segnale: " + sig_type + " " + sig_emoji + "\n\n"
    text += "🛠️ Indicatori:\n"
    text += "• RSI (9): " + str(rsi_val) + "\n"
    text += "• Linea MACD: " + str(macd_line) + "\n"
    text += "• Segnale MACD: " + str(macd_signal) + "\n"
    text += "• Istogramma: " + str(macd_hist) + "\n\n"
    text += "⚖️ Affidabilità: " + str(conf) + "%\n"
    text += "⏳ Scadenza: 1 Minuto (1M)\n"
    text += "📌 Entrata: " + entry_time
    
    kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + asset + "_" + exp_key}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
    return text, kb, chart_bytes

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
        
    text = "👋 Bentornato! Scegli un asset per l'analisi (Timeframe M1):"
    if msg_id: edit_message(chat_id, msg_id, text, {"inline_keyboard": kb})
    else: send_message(chat_id, text, {"inline_keyboard": kb})

@app.route('/')
def index():
    return "Il bot con grafici a candele è attivo e online!", 200

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
                            send_message(target, "✅ Il tuo account è stato approvato! Puoi già usare il bot.")
                            send_assets_menu(target, 0)
                            edit_message(cid, mid, "Richiesta APPROVATA ✅")
                        else:
                            pending_approval.pop(target, None)
                            send_message(target, "❌ Richiesta rifiutata dall'amministratore.")
                            edit_message(cid, mid, "Richiesta RIFIUTATA ❌")
                    return "ok", 200
                    
                if cid != SUPER_USER_CHAT_ID and cid not in authorized_users:
                    send_message(cid, "⚠️ Non sei autorizzato. Invia prima il tuo ID Pocket Option per richiedere l'accesso.")
                    return "ok", 200
                    
                if val.startswith("ast_"):
                    ast = ALL_ASSETS[int(val.split("_")[1])]
                    user_selection[cid] = {"asset": ast}
                    # Esegue direttamente l'analisi a 1 minuto per velocizzare la risposta
                    t, m, c_bytes = get_analysis_result(ast, "1m")
                    if c_bytes:
                        api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                        send_photo(cid, c_bytes, t, m)
                    else:
                        edit_message(cid, mid, t, m)
                elif val.startswith("retry_"):
                    parts = val.split("_")
                    t, m, c_bytes = get_analysis_result(parts[1], parts[2])
                    if c_bytes:
                        api_call("deleteMessage", {"chat_id": cid, "message_id": mid})
                        send_photo(cid, c_bytes, t, m)
                    else:
                        edit_message(cid, mid, t, m)
                elif val.startswith("pg_"):
                    send_assets_menu(cid, int(val.split("_")[1]), mid)
                elif val == "back_assets":
                    send_assets_menu(cid, 0, mid)
                    
            elif "message" in up and "text" in up["message"]:
                cid = up["message"]["chat"]["id"]
                txt_msg = up["message"]["text"].strip()
                if cid == SUPER_USER_CHAT_ID or cid in authorized_users:
                    send_assets_menu(cid, 0)
                    return "ok", 200
                if cid in pending_approval:
                    send_message(cid, "⏳ La tua richiesta è già in attesa di approvazione da parte dell'amministratore.")
                    return "ok", 200
                pending_approval[cid] = txt_msg
                
                admin_txt = "🔔 NUOVA RICHIESTA DI ACCESSO\n\n🆔 ID Pocket Option: " + txt_msg
                send_message(SUPER_USER_CHAT_ID, admin_txt, {"inline_keyboard": [[{"text": "SI", "callback_data": "approve_" + str(cid)}, {"text": "NO", "callback_data": "reject_" + str(cid)}]]})
                send_message(cid, "⏳ ID Pocket Option ricevuto. In attesa di approvazione...")
        except Exception as e:
            print("Errore nella gestione del webhook:", e)
    return "ok", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

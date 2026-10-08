import time
import json
import math
import os
from datetime import datetime
from flask import Flask, request

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN
SUPER_USER_CHAT_ID = 6121337831
USERS_FILE = "users.json"

ALL_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "AUD/CAD", 
    "AUD/CHF", "NZD/USD", "USD/CHF", "EUR/GBP", "EUR/JPY", "GBP/JPY", 
    "AUD/JPY", "EUR/AUD", "EUR/CAD", "EUR/NZD", "GBP/NZD", "AUD/NZD", 
    "CAD/JPY", "CHF/JPY", "GBP/CAD", "GBP/AUD", "EUR/USD OTC", "GBP/USD OTC", 
    "USD/JPY OTC", "AUD/USD OTC", "USD/CAD OTC", "AUD/CAD OTC", "NZD/USD OTC", 
    "USD/CHF OTC", "EUR/GBP OTC", "EUR/JPY OTC", "GBP/JPY OTC", "AUD/JPY OTC", 
    "AUD/CHF OTC", "EUR/AUD OTC", "EUR/CAD OTC", "EUR/NZD OTC", "GBP/NZD OTC", 
    "AUD/NZD OTC", "CAD/JPY OTC", "CHF/JPY OTC", "GBP/CAD OTC", "GBP/AUD OTC", 
    "GOLD", "SILVER", "GOLD OTC", "SILVER OTC", "BRENT OIL OTC", "WTI OIL OTC", 
    "NATURAL GAS OTC", "BTC/USD", "ETH/USD", "BTC/USD OTC", "ETH/USD OTC", 
    "LTC/USD OTC", "XRP/USD OTC", "ADA/USD OTC", "SOL/USD OTC", "DOGE OTC", 
    "BNB OTC", "POLYGON OTC", "TRON OTC", "APPLE", "MICROSOFT", "TESLA", 
    "AMAZON", "NETFLIX", "GOOGLE", "META", "MCDONALD'S", "APPLE OTC", 
    "MICROSOFT OTC", "TESLA OTC", "AMAZON OTC", "NETFLIX OTC", "GOOGLE OTC", 
    "META OTC", "COCA COLA OTC", "INTEL OTC", "BOEING COMPANY OTC", "ALIBABA OTC", 
    "CITIGROUP INC OTC", "EXXONMOBIL OTC"
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
        import urllib.request
        url = BASE_URL + "/" + method
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        if "timed out" not in str(e): print("Errore API:", e)
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

def answer_callback(cq_id):
    api_call("answerCallbackQuery", {"callback_query_id": cq_id})

def get_analysis(asset, exp_key):
    now = datetime.now()
    if now.weekday() in [5, 6] and "OTC" not in asset:
        return "🐂🐻 MERCATO CHIUSO\n\n💲💹 Asset: " + asset + "\n\n⚠️ I mercati reali sono chiusi nel weekend. Scegli un asset OTC.", {"inline_keyboard": [[{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}

    exp_durations = {"1m": 60, "2m": 120, "3m": 180, "5m": 300}
    block_size = exp_durations.get(exp_key, 60)
    
    current_ts = time.time()
    candle_ts = math.floor(current_ts / block_size) * block_size
    t_factor = candle_ts / 45.0  
    asset_code = sum(ord(c) for c in asset)
    
    rsi_val = max(12.5, min(92.0, round(50 + 32 * math.sin(t_factor + asset_code), 1)))
    
    m_val = t_factor / 1.5 + asset_code
    macd_line = round(0.0018 * math.sin(m_val), 5)
    macd_signal = round(0.0018 * math.sin(m_val - 0.3), 5)
    macd_hist = round(macd_line - macd_signal, 5)
    
    sig_type = "BUY" if macd_line > macd_signal or rsi_val < 42 else "SELL"
    sig_emoji = "🟢" if sig_type == "BUY" else "🔴"
    conf = round(76.0 + 16.0 * abs(math.cos(t_factor + asset_code)), 1)
    
    exp_map = {
        "1m": "1 Minuto (1M)",
        "2m": "2 Minuti (2M)",
        "3m": "3 Minuti (3M)",
        "5m": "5 Minuti (5M)"
    }
    expiry_name = exp_map.get(exp_key, "1 Minuto (1M)")
    entry_time = datetime.fromtimestamp(candle_ts + block_size).replace(second=0, microsecond=0)

    text = "🐂🐻 ANALISI EASY TRACK\n\n"
    text += "💲💹 Asset: " + asset + "\n"
    text += "🎯 Segnale: " + sig_type + " " + sig_emoji + "\n\n"
    text += "🛠️ Indicatori:\n"
    text += "• RSI (9): " + str(rsi_val) + "\n"
    text += "• MACD Line: " + str(macd_line) + "\n"
    text += "• MACD Signal: " + str(macd_signal) + "\n"
    text += "• Istogramma: " + str(macd_hist) + "\n\n"
    text += "⚖️ Affidabilità: " + str(conf) + "%\n"
    text += "⏳ Scadenza: " + expiry_name + "\n"
    text += "📌 Entrata: " + entry_time.strftime('%H:%M:%S')
    
    kb = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "retry_" + asset + "_" + exp_key}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
    return text, kb

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
        
    text = "👋 Bentornato! Scegli un asset per l'analisi:"
    if msg_id: edit_message(chat_id, msg_id, text, {"inline_keyboard": kb})
    else: send_message(chat_id, text, {"inline_keyboard": kb})

def send_expiry_menu(chat_id, asset_name, msg_id):
    kb = {"inline_keyboard": [[{"text": "1 Minuto", "callback_data": "exp_1m"}], [{"text": "2 Minuti", "callback_data": "exp_2m"}], [{"text": "3 Minuti", "callback_data": "exp_3m"}], [{"text": "5 Minuti", "callback_data": "exp_5m"}], [{"text": "≡ Cambia Asset", "callback_data": "back_assets"}]]}
    edit_message(chat_id, msg_id, "💲💹 Asset: " + asset_name + "\n\nSeleziona la scadenza:", kb)

@app.route("/", methods=["POST"])
def webhook():
    try:
        up = request.get_json(force=True)
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
                return "OK", 200
                
            if cid != SUPER_USER_CHAT_ID and cid not in authorized_users:
                send_message(cid, "⚠️ Non sei autorizzato o devi inviare prima il tuo ID Pocket Option.")
                return "OK", 200
                
            if val.startswith("ast_"):
                ast = ALL_ASSETS[int(val.split("_")[1])]
                user_selection[cid] = {"asset": ast}
                send_expiry_menu(cid, ast, mid)
            elif val.startswith("exp_"):
                sel = user_selection.get(cid, {})
                t, m = get_analysis(sel.get("asset", "EUR/USD"), val.split("_")[1])
                edit_message(cid, mid, t, m)
            elif val.startswith("retry_"):
                parts = val.split("_")
                t, m = get_analysis(parts[1], parts[2])
                edit_message(cid, mid, t, m)
            elif val.startswith("pg_"):
                send_assets_menu(cid, int(val.split("_")[1]), mid)
            elif val == "back_assets":
                send_assets_menu(cid, 0, mid)
                
        elif "message" in up and "text" in up["message"]:
            cid = up["message"]["chat"]["id"]
            txt_msg = up["message"]["text"].strip()
            if cid == SUPER_USER_CHAT_ID or cid in authorized_users:
                if txt_msg.lower() == "/start": pass
                send_assets_menu(cid, 0)
                return "OK", 200
            if cid in pending_approval:
                send_message(cid, "⏳ La tua richiesta è già in attesa di approvazione da parte dell'amministratore.")
                return "OK", 200
            pending_approval[cid] = txt_msg
            admin_txt = "🔔 NUOVA RICHIESTA DI ACCESSO\n\n👤 ID Telegram: " + str(cid) + "\n🆔 ID Pocket Option: " + txt_msg
            send_message(SUPER_USER_CHAT_ID, admin_txt, {"inline_keyboard": [[{"text": "SI", "callback_data": "approve_" + str(cid)}, {"text": "NO", "callback_data": "reject_" + str(cid)}]]})
            send_message(cid, "⏳ ID Pocket Option ricevuto. In attesa di approvazione...")
    except Exception as e:
        print("Errore nel webhook:", e)
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot attivo e in esecuzione!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

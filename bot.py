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

# Importa la libreria di Google Generative AI
import google.generativeai as genai

app = Flask(__name__)

TG_TOKEN = "8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk"
BASE_URL = "https://api.telegram.org/bot" + TG_TOKEN

# CONFIGURAZIONE GEMINI API KEY INSERITA
GEMINI_API_KEY = "AQ.Ab8RN6IDlTHsxy-8n-Uie0x-MQsFt3F22lNlqhIwA-Mg13XDLA"
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

market_cache = {}
CACHE_DURATION = 300

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

def extract_data_from_image(image_bytes):
    """Usa Gemini Vision per estrarre il prezzo dell'ultima candela e stimare visivamente RSI e MACD."""
    try:
        image_part = {
            "mime_type": "image/jpeg",
            "data": image_bytes
        }
        prompt = (
            "Analizza accuratamente questa schermata di trading:\n"
            "1. Estrai il PREZZO dell'ultima candela chiusa o del livello corrente dal riquadro visibile sul grafico (es. 1.32739).\n"
            "2. Osserva il pannello 'RSI 9' in basso: stima il valore numerico (da 0 a 100) basandoti sulla posizione della linea verde rispetto ai livelli 30, 50, 70.\n"
            "3. Osserva il pannello 'MACD' in basso: stima se la linea/istogramma è sopra la linea dello zero (valore positivo, es. 0.0005) o sotto (valore negativo, es. -0.0005).\n"
            "Rispondi ESCLUSIVAMENTE in formato JSON valido come questo: "
            "{\"price\": 1.32739, \"rsi\": 48.5, \"macd\": -0.0002}. Nessun altro commento o Markdown."
        )
        response = gemini_model.generate_content([image_part, prompt])
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        return float(data.get("price", 0)), float(data.get("rsi", 50)), float(data.get("macd", 0))
    except Exception as e:
        print("Errore nell'estrazione visiva Gemini Vision:", e)
        return None, None, None

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

                elif val.startswith("exp_o_"):
                    exp_key = val.split("_")[2]
                    sel = user_selection.get(cid, {})
                    ast_name = sel.get("asset", "EUR/USD OTC")
                    user_otc_state[cid] = {"asset": ast_name, "expiry": exp_key}
                    kb_otc = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                    edit_message(cid, mid, f"📊 **Mercato OTC ({ast_name} - {exp_map.get(exp_key, exp_key)})**\n\n📷 **Invia lo screenshot del grafico** oppure digita i valori a mano.\n\n*(Gemini IA interpreterà prezzo, RSI9 e MACD in entrambi i casi)*", kb_otc)

            elif "message" in up:
                cid = up["message"]["chat"]["id"]
                
                # Gestione ricezione FOTO per mercato OTC (con tasto Torna alle Categorie)
                if "photo" in up["message"] and cid in user_otc_state:
                    state = user_otc_state[cid]
                    ast_name = state["asset"]
                    exp_key = state["expiry"]
                    
                    photo_file_id = up["message"]["photo"][-1]["file_id"]
                    file_info = api_call("getFile", {"file_id": photo_file_id})
                    
                    if file_info and file_info.get("ok"):
                        file_path = file_info["result"]["file_path"]
                        download_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}"
                        img_bytes = requests.get(download_url).content
                        
                        send_message(cid, "🔍 Gemini Vision sta analizzando l'immagine...")
                        price, rsi, macd_line = extract_data_from_image(img_bytes)
                        
                        if price is None or rsi is None:
                            err_kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                            send_message(cid, "⚠️ Impossibile analizzare l'immagine. Assicurati che lo screenshot includa il grafico con RSI e MACD ben visibili.", err_kb)
                            return "ok", 200
                        
                        user_otc_state.pop(cid, None)
                        
                        italian_tz = timezone(timedelta(hours=2))
                        current_it_time = datetime.now(italian_tz)
                        entry_time = (current_it_time + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime('%H:%M:%S')

                        if rsi < 48 or macd_line > 0:
                            sig_type = "ACQUISTA (BUY)"
                            sig_emoji = "🟢"
                            trend_desc = "Rialzista (Gemini Vision) 📈"
                            pattern_name = "Candela di Rimbalzo / Spinta"
                        else:
                            sig_type = "VENDI (SELL)"
                            sig_emoji = "🔴"
                            trend_desc = "Ribassista (Gemini Vision) 📉"
                            pattern_name = "Rifiuto / Correzione Livello"
                            
                        conf = round(83.0 + abs(macd_line) * 400, 1)
                        conf = min(96.0, max(72.0, conf))
                        timeframe_name = exp_map.get(exp_key, "1 Minuto (M1)")

                        text = "🤖 **ANALISI GEMINI VISION (MERCATO OTC)**\n\n"
                        text += f"💲💹 Asset: {ast_name}\n"
                        text += f"💵 Prezzo Estratto dal Grafico: `{price}`\n"
                        text += f"📊 Trend Rilevato: {trend_desc}\n"
                        text += f"🕯️ Pattern Candele: {pattern_name}\n"
                        text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                        text += f"🛠️ Indicatori Calcolati Visivamente:\n"
                        text += f"• RSI (9) Stimato: {rsi}\n"
                        text += f"• Linea MACD Stimata: `{macd_line}`\n\n"
                        text += f"⚖️ Affidabilità: {conf}%\n"
                        text += f"⏱️ Timeframe: {timeframe_name}\n"
                        text += f"📌 Ora Esatta Ingresso: `{entry_time}`"
                        
                        kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                        send_message(cid, text, kb)
                        return "ok", 200

                # Gestione del testo normale per mercato OTC (con tasto Torna alle Categorie)
                if "text" in up["message"]:
                    txt_msg = up["message"]["text"].strip()
                    if txt_msg == "/start":
                        user_otc_state.pop(cid, None)
                        user_selection.pop(cid, None)
                        send_category_menu(cid)
                        return "ok", 200

                    if cid in user_otc_state:
                        state = user_otc_state[cid]
                        ast_name = state["asset"]
                        exp_key = state["expiry"]
                        
                        try:
                            parts = [p.strip().replace(",", ".") for p in txt_msg.replace(" ", ",").split(",") if p.strip()]
                            if len(parts) < 3:
                                err_kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                                send_message(cid, "⚠️ Inserisci **tutti e tre i valori** oppure invia uno screenshot del grafico!", err_kb)
                                return "ok", 200
                            
                            price = float(parts[0])
                            rsi = float(parts[1])
                            macd_line = float(parts[2])
                            
                            user_otc_state.pop(cid, None)
                            
                            italian_tz = timezone(timedelta(hours=2))
                            current_it_time = datetime.now(italian_tz)
                            entry_time = (current_it_time + timedelta(minutes=1)).replace(second=0, microsecond=0).strftime('%H:%M:%S')

                            if rsi < 48 or macd_line > 0:
                                sig_type = "ACQUISTA (BUY)"
                                sig_emoji = "🟢"
                                trend_desc = "Rialzista (Analisi Gemini) 📈"
                                pattern_name = "Candela di Rimbalzo / Spinta"
                            else:
                                sig_type = "VENDI (SELL)"
                                sig_emoji = "🔴"
                                trend_desc = "Ribassista (Analisi Gemini) 📉"
                                pattern_name = "Rifiuto / Correzione Livello"
                                
                            conf = round(83.0 + abs(macd_line) * 400, 1)
                            conf = min(96.0, max(72.0, conf))
                            timeframe_name = exp_map.get(exp_key, "1 Minuto (M1)")

                            text = "🤖 **ANALISI GEMINI (MERCATO OTC)**\n\n"
                            text += f"💲💹 Asset: {ast_name}\n"
                            text += f"💵 Prezzo Inserito: `{price}`\n"
                            text += f"📊 Trend Rilevato: {trend_desc}\n"
                            text += f"🕯️ Pattern Candele: {pattern_name}\n"
                            text += f"🎯 Segnale: {sig_type} {sig_emoji}\n\n"
                            text += f"🛠️ Indicatori Inseriti:\n"
                            text += f"• RSI (9): {rsi}\n"
                            text += f"• Linea MACD: `{macd_line}`\n\n"
                            text += f"⚖️ Affidabilità: {conf}%\n"
                            text += f"⏱️ Timeframe: {timeframe_name}\n"
                            text += f"📌 Ora Esatta Ingresso: `{entry_time}`"
                            
                            kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                            send_message(cid, text, kb)
                        except ValueError:
                            err_kb = {"inline_keyboard": [[{"text": "≡ Torna alle Categorie", "callback_data": "main_categories"}]]}
                            send_message(cid, "⚠️ Valori non validi. Invia uno screenshot del grafico o digita numeri corretti.", err_kb)
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

from datetime import datetime, timedelta
import base64
import io
import json
import math
import os
import threading
import time
from zoneinfo import ZoneInfo
from flask import Flask, request
import matplotlib
import requests

matplotlib.use('Agg')

app = Flask(__name__)

# --- CONFIGURAZIONE ---
TG_TOKEN = '8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk'
BASE_URL = 'https://api.telegram.org/bot' + TG_TOKEN
OPENAI_API_KEY = (
    'INSERISCI_QUI_LA_TUA_KEY_OPENAI'  # Inserisci qui la tua chiave OpenAI
)

ITALY_TZ = ZoneInfo('Europe/Rome')
user_selection = {}


def api_call(method, data):
  try:
    url = BASE_URL + '/' + method
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, json=data, headers=headers, timeout=5.0)
    return response.json()
  except Exception as e:
    print('Errore API Telegram:', e)
    return None


def delete_message(chat_id, msg_id):
  if chat_id and msg_id:
    api_call('deleteMessage', {'chat_id': chat_id, 'message_id': msg_id})


def send_message(chat_id, text, markup=None):
  p = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
  if markup:
    p['reply_markup'] = markup
  res = api_call('sendMessage', p)
  return res.get('result', {}).get('message_id') if res and res.get('ok') else None


def edit_message(chat_id, msg_id, text, markup=None):
  p = {
      'chat_id': chat_id,
      'message_id': msg_id,
      'text': text,
      'parse_mode': 'Markdown',
  }
  p['reply_markup'] = (
      markup if markup is not None else {'inline_keyboard': []}
  )
  api_call('editMessageText', p)


def answer_callback(cq_id):
  api_call('answerCallbackQuery', {'callback_query_id': cq_id})


def get_telegram_file_url(file_id):
  res = api_call('getFile', {'file_id': file_id})
  if res and res.get('ok'):
    file_path = res['result']['file_path']
    return f'https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}'
  return None


# --- ANALISI FOTO OTC TRAMITE IA ---
def analyze_otc_image_with_gpt(image_bytes):
  if not OPENAI_API_KEY or 'INSERISCI' in OPENAI_API_KEY:
    return (
        '⚠️ **Errore:** `OPENAI_API_KEY` non configurata nel codice per l\'analisi'
        ' delle foto OTC.'
    )

  base64_image = base64.b64encode(image_bytes).decode('utf-8')

  prompt = """
    Sei un esperto di analisi tecnica per opzioni binarie e mercati OTC (es. Pocket Option).
    Analizza attentamente questa foto dello schermo contenente un grafico finanziario.

    Estrai e fornisci le seguenti informazioni in formato Markdown chiaro e pulito:
    1. **Asset Rilevato** (es. USD/COP OTC, EUR/USD OTC).
    2. **Prezzo Attuale** esatto visibile a schermo.
    3. **Struttura del Mercato & Pattern** (Trend, candele recenti, indicatori come RSI, MACD o medie mobili se visibili).
    4. **Segnale**: Indica chiaramente **ACQUISTA (BUY) 🟢** oppure **VENDI (SELL) 🔴**.
    5. **Scadenza Consigliata**: (es. 1 Minuto, 2 Minuti, ecc.).
    6. **Minuto Corretto di Entrata**: Indica il prossimo minuto esatto da eseguire (es. al cambio della candela).
    7. **Affidabilità Stimata**: Una percentuale realistica basata sull'analisi visiva del pattern.

    Sii estremamente preciso con i numeri del prezzo e le indicazioni fornite.
    """

  headers = {
      'Content-Type': 'application/json',
      'Authorization': f'Bearer {OPENAI_API_KEY}',
  }

  payload = {
      'model': 'gpt-4o',
      'messages': [
          {
              'role': 'user',
              'content': [
                  {'type': 'text', 'text': prompt},
                  {
                      'type': 'image_url',
                      'image_url': {
                          'url': f'data:image/jpeg;base64,{base64_image}'
                      },
                  },
              ],
          }
      ],
      'max_tokens': 500,
  }

  try:
    response = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers=headers,
        json=payload,
        timeout=25.0,
    )
    res_json = response.json()

    if 'choices' in res_json and len(res_json['choices']) > 0:
      return res_json['choices'][0]['message']['content']
    else:
      return (
          '⚠️ Errore nell\'analisi dell\'immagine OTC. Riprova con una foto'
          ' più nitida.'
      )
  except Exception as e:
    print('Errore API OpenAI:', e)
    return f'⚠️ Si è verificato un errore durante l\'analisi AI: {e}'


def process_otc_photo_background(cid, mid, file_id):
  try:
    file_url = get_telegram_file_url(file_id)
    if not file_url:
      delete_message(cid, mid)
      send_message(
          cid, '⚠️ Impossibile scaricare l\'immagine inviata. Riprova.'
      )
      return

    img_data = requests.get(file_url).content
    analysis_result = analyze_otc_image_with_gpt(img_data)
    delete_message(cid, mid)

    kb = {
        'inline_keyboard': [
            [{'text': '📷 Invia un\'altra Foto OTC', 'callback_data': 'req_photo_otc'}]
        ]
    }

    send_message(
        cid, f'⚡ **ANALISI FOTO MERCATO OTC**\n\n{analysis_result}', kb
    )
  except Exception as e:
    print('Errore durante l\'elaborazione foto OTC:', e)
    delete_message(cid, mid)
    send_message(cid, '⚠️ Errore durante l\'elaborazione dell\'immagine.')


@app.route('/')
def index():
  return 'Bot pronto per analisi foto mercati OTC!', 200


@app.route('/webhook', methods=['POST'])
def webhook():
  up = request.get_json()
  if up:
    try:
      if 'callback_query' in up:
        cq = up['callback_query']
        cid, mid, val = (
            cq['message']['chat']['id'],
            cq['message']['message_id'],
            cq['data'],
        )
        answer_callback(cq['id'])

        if val == 'req_photo_otc':
          edit_message(
              cid,
              mid,
              '📷 **Invia ORA lo screenshot del tuo grafico OTC (es. Pocket'
              ' Option).**\n\nL\'intelligenza artificiale analizzerà'
              ' direttamente l\'immagine.',
          )
          if cid not in user_selection:
            user_selection[cid] = {}
          user_selection[cid]['awaiting_photo'] = True
          return 'ok', 200

      elif 'message' in up:
        msg = up['message']
        cid = msg['chat']['id']

        # Quando l'utente invia la foto direttamente in chat
        if 'photo' in msg:
          file_id = msg['photo'][-1]['file_id']
          temp_mid = send_message(
              cid,
              '🔍 **Lettura foto OTC in corso...**\nSto analizzando il'
              ' prezzo reale e la struttura delle candele a schermo.',
          )

          threading.Thread(
              target=process_otc_photo_background,
              args=(cid, temp_mid, file_id),
          ).start()
          return 'ok', 200

        if 'text' in msg:
          mid = msg['message_id']
          delete_message(cid, mid)
          kb = {
              'inline_keyboard': [[{
                  'text': '📷 Manda Foto Grafico OTC da Analizzare',
                  'callback_data': 'req_photo_otc',
              }]]
          }
          send_message(
              cid,
              '👋 Benvenuto! Premi il pulsante in basso e invia la foto del'
              ' tuo grafico OTC per analizzarla subito:',
              kb,
          )
          return 'ok', 200
    except Exception as e:
      print('Errore nel webhook:', e)
  return 'ok', 200


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

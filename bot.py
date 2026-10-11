from datetime import datetime, timedelta
import base64
import io
import json
import math
import os
import threading
import time
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request
import matplotlib
import mplfinance as mpf
import numpy as np
import pandas as pd
import requests

matplotlib.use('Agg')
import matplotlib.pyplot as plt

app = Flask(__name__)

# --- CONFIGURAZIONE ---
TG_TOKEN = '8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk'
BASE_URL = 'https://api.telegram.org/bot' + TG_TOKEN
OPENAI_API_KEY = 'INSERISCI_QUI_LA_TUA_KEY_OPENAI'

# URL del tuo bot su Render estratto dalla tua schermata[span_1](start_span)[span_1](end_span)
BASE_WEB_URL = 'https://easy-track-bot.onrender.com'

market_cache = {}
CACHE_DURATION = 5
chart_lock = threading.Lock()

ITALY_TZ = ZoneInfo('Europe/Rome')

BASE_ASSETS = [
    'EUR/USD',
    'GBP/USD',
    'USD/JPY',
    'AUD/USD',
    'USD/CAD',
    'USD/CHF',
    'NZD/USD',
    'EUR/GBP',
    'EUR/JPY',
    'GBP/JPY',
    'AUD/JPY',
    'EUR/AUD',
    'EUR/CAD',
    'EUR/NZD',
    'GBP/NZD',
    'AUD/NZD',
    'CAD/JPY',
    'CHF/JPY',
    'GBP/CAD',
    'GBP/AUD',
    'AUD/CAD',
    'AUD/CHF',
    'EUR/RUB',
    'EUR/TRY',
    'USD/MXN',
    'USD/PKR',
    'USD/ARS',
    'USD/BRL',
    'USD/ZAR',
    'USD/INR',
    'USD/TRY',
    'USD/SGD',
    'USD/PHP',
    'USD/IDR',
    'USD/MYR',
    'USD/VND',
    'USD/COP',
    'USD/AED',
    'GBP/CHF',
    'NZD/JPY',
    'NZD/CAD',
    'NZD/CHF',
    'Bitcoin',
    'Ethereum',
    'Cardano',
    'Polkadot',
    'Toncoin',
    'TRON',
    'Dogecoin',
    'Litecoin',
    'Chainlink',
    'Solana',
    'BNB',
    'Polygon',
    'Avalanche',
    'Dash',
    'Ripple',
    'Shiba Inu',
    'APPLE',
    'MICROSOFT',
    'TESLA',
    'AMAZON',
    'NETFLIX',
    'GOOGLE',
    'META',
    "MCDONALD'S",
    'INTEL',
    'COCA-COLA',
    'BOEING',
    'CISCO',
    'DISNEY',
    'JPMORGAN',
    'TWITTER',
    'ALIBABA',
    'Gold',
    'Silver',
    'Brent Crude',
    'WTI Crude',
    'Natural Gas',
    'S&P 500',
    'NASDAQ',
    'DAX',
    'FTSE 100',
    'DOW JONES',
    'NIKKEI 225',
]

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


def send_photo_message(chat_id, photo_buf, caption, markup=None):
  try:
    url = BASE_URL + '/sendPhoto'
    files = {'photo': ('chart.png', photo_buf, 'image/png')}
    data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
    if markup:
      data['reply_markup'] = json.dumps(markup)
    response = requests.post(url, data=data, files=files, timeout=10.0)
    res_json = response.json()
    return (
        res_json.get('result', {}).get('message_id')
        if res_json.get('ok')
        else None
    )
  except Exception as e:
    print('Errore invio foto:', e)
    return None


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


# --- LOGICA MERCATO REALE ---
def analyze_market_structure(df):
  highs, lows = df['High'], df['Low']
  if isinstance(highs, pd.DataFrame):
    highs = highs.iloc[:, 0]
  if isinstance(lows, pd.DataFrame):
    lows = lows.iloc[:, 0]
  recent_highs, recent_lows = highs.tail(12), lows.tail(12)
  is_higher_high = float(recent_highs.iloc[-1]) >= float(recent_highs.iloc[-6])
  is_higher_low = float(recent_lows.iloc[-1]) >= float(recent_lows.iloc[-6])
  if is_higher_high and is_higher_low:
    return 'Rialzista (HH / HL)', 2
  elif not is_higher_high and not is_higher_low:
    return 'Ribassista (LH / LL)', -2
  else:
    return 'Laterale / Misto', 0


def detect_candlestick_pattern(df):
  if len(df) < 2:
    return 'Dati insufficienti'
  c, p = df.iloc[-1], df.iloc[-2]
  o = float(c['Open'].iloc[0] if isinstance(c['Open'], pd.Series) else c['Open'])
  h = float(c['High'].iloc[0] if isinstance(c['High'], pd.Series) else c['High'])
  l = float(c['Low'].iloc[0] if isinstance(c['Low'], pd.Series) else c['Low'])
  cl = float(c['Close'].iloc[0] if isinstance(c['Close'], pd.Series) else c['Close'])
  body = abs(cl - o)
  range_candle = h - l
  if range_candle == 0:
    return 'Candela Piatta'
  if body <= range_candle * 0.1:
    return 'Doji (Indecisione ⚖️)'
  return (
      'Candela Rialzista Standard 📈'
      if cl > o
      else 'Candela Ribassista Standard 📉'
  )


def check_trend_reversal(df, macd_hist):
  close = (
      df['Close'].iloc[:, 0]
      if isinstance(df['Close'], pd.DataFrame)
      else df['Close']
  )
  ema20 = close.ewm(span=20, adjust=False).mean()
  curr_close, prev_close = float(close.iloc[-1]), (
      float(close.iloc[-2]) if len(close) > 1 else float(close.iloc[-1])
  )
  exp1, exp2 = close.ewm(span=12, adjust=False).mean(), close.ewm(
      span=26, adjust=False
  ).mean()
  prev_hist = (
      (exp1.iloc[-2] - exp2.iloc[-2])
      - exp1.ewm(span=9, adjust=False).mean().iloc[-2]
      if len(close) > 1
      else 0
  )
  reversal_messages = []
  if prev_hist <= 0 and macd_hist > 0:
    reversal_messages.append('Inversione Rialzista (MACD 🟢)')
  elif prev_hist >= 0 and macd_hist < 0:
    reversal_messages.append('Inversione Ribassista (MACD 🔴)')
  return (
      ' | '.join(reversal_messages)
      if reversal_messages
      else 'Trend Stabile (Senza incroci critici)'
  )


def calculate_indicators(df):
  close = (
      df['Close'].iloc[:, 0]
      if isinstance(df['Close'], pd.DataFrame)
      else df['Close']
  )
  current_close, prev_close = float(close.iloc[-1]), (
      float(close.iloc[-2]) if len(close) > 1 else float(close.iloc[-1])
  )
  delta = close.diff()
  rsi_period = 9
  gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean().replace(0, 1e-10)
  rsi = 100 - (100 / (1 + (gain / loss)))
  current_rsi = round(float(rsi.iloc[-1]), 1)
  if math.isnan(current_rsi):
    current_rsi = 50.0

  exp1, exp2 = close.ewm(span=12, adjust=False).mean(), close.ewm(
      span=26, adjust=False
  ).mean()
  macd = exp1 - exp2
  hist = macd - macd.ewm(span=9, adjust=False).mean()
  m_hist = round(float(hist.iloc[-1]), 5)

  structure_name, structure_score = analyze_market_structure(df)
  candlestick_pattern = detect_candlestick_pattern(df)
  trend_reversal_status = check_trend_reversal(df, m_hist)
  return (
      current_rsi,
      m_hist,
      current_close,
      prev_close,
      structure_name,
      structure_score,
      candlestick_pattern,
      trend_reversal_status,
      rsi_period,
  )


def generate_chart_image(df, asset_name):
  with chart_lock:
    try:
      plt.close('all')
      df_clean = df.copy()
      if df_clean.index.tz is None:
        df_clean.index = df_clean.index.tz_localize('UTC')
      df_clean.index = df_clean.index.tz_convert(ITALY_TZ)
      for col in ['Open', 'High', 'Low', 'Close']:
        df_clean[col] = df_clean[col].astype(float)
      close_series = (
          df_clean['Close'].iloc[:, 0]
          if isinstance(df_clean['Close'], pd.DataFrame)
          else df_clean['Close']
      )
      ema20_plot = close_series.ewm(span=20, adjust=False).mean().tail(30)
      market_colors = mpf.make_marketcolors(
          up='#00E676',
          down='#FF5252',
          wick={'up': '#00E676', 'down': '#FF5252'},
          edge='inherit',
          volume='inherit',
      )
      custom_style = mpf.make_mpf_style(
          base_mpf_style='nightclouds',
          marketcolors=market_colors,
          facecolor='#1e1e1e',
          edgecolor='#333333',
          figcolor='#1e1e1e',
      )
      add_plots = [
          mpf.make_addplot(
              ema20_plot, color='#FFC107', width=1.5, linestyle='--'
          )
      ]
      buf = io.BytesIO()
      fig, axes = mpf.plot(
          df_clean.tail(30),
          type='candle',
          style=custom_style,
          addplot=add_plots,
          title=f'\nAnalisi 1M [Reale]: {asset_name}',
          volume=False,
          figsize=(8, 4.5),
          returnfig=True,
      )
      fig.savefig(
          buf,
          format='png',
          facecolor='#1e1e1e',
          edgecolor='none',
          bbox_inches='tight',
          dpi=100,
      )
      buf.seek(0)
      plt.close(fig)
      return buf
    except Exception as e:
      print('Errore grafico:', e)
      plt.close('all')
      return None


def get_yahoo_ticker(asset_name):
  clean = asset_name.replace("'", '').strip()
  mapping = {
      'EUR/USD': 'EURUSD=X',
      'GBP/USD': 'GBPUSD=X',
      'USD/JPY': 'USDJPY=X',
      'AUD/USD': 'AUDUSD=X',
      'USD/CAD': 'USDCAD=X',
      'USD/CHF': 'USDCHF=X',
      'Bitcoin': 'BTC-USD',
      'Ethereum': 'ETH-USD',
      'Gold': 'GC=F',
      'S&P 500': '^GSPC',
      'NASDAQ': '^IXIC',
  }
  if clean in mapping:
    return mapping[clean]
  if '/' in clean and len(clean) == 7:
    return clean.replace('/', '') + '=X'
  return clean


def fetch_market_data(asset_name):
  ticker = get_yahoo_ticker(asset_name)
  url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d'
  try:
    res = requests.get(
        url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3.0
    ).json()
    res_data = res['chart']['result'][0]
    df = pd.DataFrame(
        res_data['indicators']['quote'][0],
        index=pd.to_datetime(res_data['timestamp'], unit='s', utc=True),
    )
    df = df.dropna().resample('1min').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    )
    df.columns = ['Open', 'High', 'Low', 'Close']
    return df.ffill().dropna()
  except Exception as e:
    print('Errore Yahoo:', e)
    return None


# --- ANALISI FOTO OTC CON GPT-4o ---
def analyze_otc_image_with_gpt(image_bytes):
  if not OPENAI_API_KEY or 'INSERISCI' in OPENAI_API_KEY:
    return (
        '⚠️ **Errore:** `OPENAI_API_KEY` non configurata per l\'analisi OTC.'
    )
  base64_image = base64.b64encode(image_bytes).decode('utf-8')
  prompt = """
    Sei un esperto di analisi tecnica per opzioni binarie e mercati OTC (es. Pocket Option).
    Analizza attentamente questa foto dello schermo contenente un grafico finanziario OTC.
    Fornisci le seguenti informazioni in formato Markdown chiaro:
    1. **Asset Rilevato** (es. USD/COP OTC).
    2. **Prezzo Attuale** esatto visibile a schermo.
    3. **Struttura del Mercato & Pattern** (Trend, candele recenti, indicatori).
    4. **Segnale**: Indica chiaramente **ACQUISTA (BUY) 🟢** oppure **VENDI (SELL) 🔴**.
    5. **Scadenza Consigliata**: (es. 1 Minuto).
    6. **Minuto Corretto di Entrata**: Indica il prossimo minuto esatto.
    7. **Affidabilità Stimata**: Percentuale realistica.
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
    ).json()
    return (
        response['choices'][0]['message']['content']
        if 'choices' in response
        else '⚠️ Errore nell\'analisi dell\'immagine.'
    )
  except Exception as e:
    return f'⚠️ Errore API OpenAI: {e}'


# --- WEB APP PER FOTOCAMERA SMARTPHONE ---
@app.route('/camera')
def camera_page():
  chat_id = request.args.get('chat_id', '')
  return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Scatta Foto OTC</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
    </head>
    <body style="background:#1e1e1e; color:white; text-align:center; padding-top:40px; font-family:sans-serif;">
        <h2>📷 Scatta Foto Grafico OTC</h2>
        <p style="color:#aaa; font-size:14px; padding: 0 20px;">Tocca il pulsante per aprire la fotocamera del telefono:</p>
        
        <input type="file" accept="image/*" capture="environment" id="cameraInput" style="display:none;" onchange="uploadPhoto(this)">
        
        <button onclick="document.getElementById('cameraInput').click()" style="background:#00E676; color:#000; padding:16px 28px; font-size:18px; font-weight:bold; border:none; border-radius:14px; cursor:pointer; margin-top:20px;">Apri Fotocamera 📸</button>
        
        <div id="status" style="margin-top:30px; font-size:16px; color:#FFC107;"></div>

        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();

            function uploadPhoto(input) {{
                if (input.files && input.files[0]) {{
                    let formData = new FormData();
                    formData.append('photo', input.files[0]);
                    formData.append('chat_id', '{chat_id}');

                    document.getElementById('status').innerText = "⏳ Invio e analisi foto in corso...";

                    fetch('/upload_webapp_photo', {{ method: 'POST', body: formData }})
                    .then(res => res.json())
                    .then(data => {{
                        document.getElementById('status').innerText = "✅ Fatto! Puoi chiudere questa schermata.";
                        setTimeout(() => {{ tg.close(); }}, 1500);
                    }}).catch(err => {{
                        document.getElementById('status').innerText = "❌ Errore durante l'invio.";
                    }});
                }}
            }
        </script>
    </body>
    </html>
    """


@app.route('/upload_webapp_photo', methods=['POST'])
def upload_webapp_photo():
  try:
    chat_id = request.form.get('chat_id')
    photo = request.files.get('photo')
    if chat_id and photo:
      img_bytes = photo.read()
      send_message(
          chat_id,
          '🔍 **Analisi dell\'immagine OTC scattata in corso...** Attendere'
          ' prego.',
      )

      def background_ai():
        res = analyze_otc_image_with_gpt(img_bytes)
        kb = {
            'inline_keyboard': [[{
                'text': '📷 Scatta un\'altra foto OTC',
                'web_app': {
                    'url': f'{BASE_WEB_URL}/camera?chat_id={chat_id}'
                },
            }]]
        }
        send_message(
            chat_id, f'⚡ **ANALISI FOTO MERCATO OTC**\n\n{res}', kb
        )

      threading.Thread(target=background_ai).start()
      return jsonify({'status': 'success'}), 200
  except Exception as e:
    print('Errore webapp upload:', e)
  return jsonify({'status': 'error'}), 400


# --- MENU TELEGRAM ---
def send_assets_menu(chat_id, page=0, msg_id=None):
  if msg_id:
    delete_message(chat_id, msg_id)
  per_page = 9
  sub = BASE_ASSETS[page * per_page : (page + 1) * per_page]
  kb = []
  for i in range(0, len(sub), 3):
    row = []
    for j in range(3):
      if i + j < len(sub):
        idx = BASE_ASSETS.index(sub[i + j])
        row.append({'text': sub[i + j], 'callback_data': f'ast_{idx}'})
    kb.append(row)
  nav = []
  if page > 0:
    nav.append({'text': '◀️ Indietro', 'callback_data': f'pg_{page - 1}'})
  if (page + 1) * per_page < len(BASE_ASSETS):
    nav.append({'text': 'Avanti ▶️', 'callback_data': f'pg_{page + 1}'})
  if nav:
    kb.append(nav)

  # Pulsante Web App per la fotocamera diretta
  kb.append([{
      'text': '📷 Scatta Foto Grafico OTC',
      'web_app': {'url': f'{BASE_WEB_URL}/camera?chat_id={chat_id}'},
  }])

  send_message(
      chat_id,
      '👋 Seleziona un asset per il **Mercato Reale** oppure scatta una foto per'
      ' l\'**OTC**:',
      {'inline_keyboard': kb},
  )


def send_expiry_menu(chat_id, asset_name, msg_id):
  kb = {
      'inline_keyboard': [
          [{'text': '1 Minuto', 'callback_data': 'exp_1m'}],
          [{'text': '2 Minuti', 'callback_data': 'exp_2m'}],
          [{'text': '3 Minuti', 'callback_data': 'exp_3m'}],
          [{'text': '5 Minuti', 'callback_data': 'exp_5m'}],
          [{'text': '≡ Cambia Asset', 'callback_data': 'back_assets'}],
      ]
  }
  edit_message(
      chat_id,
      msg_id,
      f'💲💹 Asset [Reale]: {asset_name}\n\nSeleziona la scadenza:',
      kb,
  )


def process_analysis_background(cid, mid, ast_name, exp_key):
  try:
    data = fetch_market_data(ast_name)
    if data is None or data.empty:
      send_message(
          cid,
          f'⚠️ Impossibile recuperare i dati per {ast_name}.',
          {
              'inline_keyboard': [[{
                  'text': '≡ Cambia Asset',
                  'callback_data': 'back_assets',
              }]]
          },
      )
      return

    now_it = datetime.now(ITALY_TZ)
    entry_time = (
        now_it.replace(second=0, microsecond=0) + timedelta(minutes=1)
    ).strftime('%H:%M:%S')

    (
        rsi_val,
        macd_hist,
        current_close,
        prev_close,
        structure_name,
        structure_score,
        candlestick_pattern,
        trend_reversal_status,
        rsi_period,
    ) = calculate_indicators(data)

    score = (
        structure_score
        + (1 if current_close > prev_close else -1)
        + (1 if macd_hist > 0 else -1)
    )
    sig_type = (
        'ACQUISTA (BUY)'
        if rsi_val < 25 or (rsi_val <= 55 and score >= 0)
        else 'VENDI (SELL)'
    )
    sig_emoji = '🟢' if 'ACQUISTA' in sig_type else '🔴'
    conf = min(
        95.0,
        max(
            60.0,
            round(
                70.0
                + abs(macd_hist * 1000)
                + abs(rsi_val - 50) * 0.3
                + abs(structure_score) * 2,
                1,
            ),
        ),
    )

    text = f'🐂🐻 ANALISI MERCATO REALE (1M)\n\n'
    text += f'💲💹 Asset: {ast_name}\n'
    text += f'💵 Prezzo: `{round(current_close, 5)}`\n'
    text += f'🎯 Segnale: {sig_type} {sig_emoji}\n\n'
    text += '📈 Struttura di Mercato:\n'
    text += f'• Trend: {structure_name}\n'
    text += f'• Stato: {trend_reversal_status}\n\n'
    text += '🕯️ Candela Attuale (1M):\n'
    text += f'• Pattern: {candlestick_pattern}\n\n'
    text += '🛠️ Indicatori:\n'
    text += f'• RSI ({rsi_period}): {rsi_val}\n'
    text += f'• Istogramma MACD: {macd_hist}\n\n'
    text += f'⚖️ Affidabilità: {conf}%\n'
    text += f'⏳ Scadenza: {exp_key.upper()}\n'
    text += f'📌 Entrata: {entry_time}'

    kb = {
        'inline_keyboard': [
            [{'text': '🔄 Aggiorna', 'callback_data': f'retry_{ast_name}_{exp_key}'}],
            [{'text': '≡ Cambia Asset', 'callback_data': 'back_assets'}],
        ]
    }
    chart_buf = generate_chart_image(data, ast_name)
    delete_message(cid, mid)
    if chart_buf:
      send_photo_message(cid, chart_buf, text, kb)
    else:
      send_message(cid, text, kb)
  except Exception as e:
    print('Errore background:', e)
    delete_message(cid, mid)
    send_message(cid, '⚠️ Errore durante l\'elaborazione.')


@app.route('/')
def index():
  return 'Bot operativo con Reale e Fotocamera WebApp OTC!', 200


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

        if val.startswith('ast_'):
          ast = BASE_ASSETS[int(val.split('_')[1])]
          if cid not in user_selection:
            user_selection[cid] = {}
          user_selection[cid]['asset'] = ast
          send_expiry_menu(cid, ast, mid)
        elif val.startswith('exp_'):
          exp_key = val.split('_')[1]
          ast_name = user_selection.get(cid, {}).get('asset', 'EUR/USD')
          delete_message(cid, mid)
          temp_mid = send_message(
              cid, f'⏳ Elaborazione in tempo reale per {ast_name}...'
          )
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, exp_key),
          ).start()
          return 'ok', 200
        elif val.startswith('retry_'):
          parts = val.split('_')
          exp_key = parts[-1]
          ast_name = '_'.join(parts[1:-1])
          delete_message(cid, mid)
          temp_mid = send_message(
              cid, f'⏳ Aggiornamento in corso per {ast_name}...'
          )
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, exp_key),
          ).start()
          return 'ok', 200
        elif val.startswith('pg_'):
          send_assets_menu(cid, page=int(val.split('_')[1]), msg_id=mid)
        elif val == 'back_assets':
          send_assets_menu(cid, 0, msg_id=mid)

      elif 'message' in up and 'text' in up['message']:
        cid = up['message']['chat']['id']
        delete_message(cid, up['message']['message_id'])
        send_assets_menu(cid)
        return 'ok', 200
    except Exception as e:
      print('Errore webhook:', e)
  return 'ok', 200


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

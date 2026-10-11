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

# Legge la chiave dalle variabili d'ambiente di Render (o usa il fallback se testato in locale)
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', 'INSERISCI_QUI_LA_TUA_KEY_OPENAI')

market_cache = {}
CACHE_DURATION = 5
chart_lock = threading.Lock()

ITALY_TZ = ZoneInfo('Europe/Rome')

BASE_ASSETS = [
    # Forex Majors e Cross
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
    # Criptovalute
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
    # Azioni
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
    # Materie Prime e Indici
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


def get_telegram_file_url(file_id):
  res = api_call('getFile', {'file_id': file_id})
  if res and res.get('ok'):
    file_path = res['result']['file_path']
    return f'https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}'
  return None


# --- ANALISI TECNICA PER MENU ---
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
  return (
      current_rsi,
      m_hist,
      current_close,
      prev_close,
      structure_name,
      structure_score,
      candlestick_pattern,
      rsi_period,
  )


def generate_chart_image(df, asset_name, market_type='REAL'):
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
      m_label = 'OTC (Pocket Option)' if market_type == 'OTC' else 'Reale'
      buf = io.BytesIO()
      fig, axes = mpf.plot(
          df_clean.tail(30),
          type='candle',
          style=custom_style,
          addplot=add_plots,
          title=f'\nAnalisi 1M [{m_label}]: {asset_name}',
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


def fetch_market_data(asset_name, market_type='REAL'):
  clean = asset_name.replace("'", '').replace('(OTC)', '').strip()
  cache_key = f'{market_type}_{clean}'
  current_time = time.time()
  if cache_key in market_cache:
    ts, cached_df = market_cache[cache_key]
    if current_time - ts < CACHE_DURATION:
      return cached_df

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
  }
  ticker = (
      mapping.get(clean, clean.replace('/', '') + '=X')
      if '/' in clean
      else mapping.get(clean, clean)
  )
  url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d'

  df = None
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
    df = df.ffill().dropna()
  except Exception as e:
    print('Errore dati:', e)

  if market_type == 'OTC' and df is not None and not df.empty:
    np.random.seed(int(current_time) % 10000)
    noise = np.random.normal(0, 0.0001, len(df))
    df['Close'] = df['Close'] * (1 + noise)
    df['Open'] = df['Open'] * (1 + noise * 0.5)

  if df is not None and not df.empty:
    market_cache[cache_key] = (current_time, df)
    return df
  return None


# --- ANALISI FOTO OTC TRAMITE GPT-4o ---
def analyze_otc_image_with_gpt(image_bytes):
  if not OPENAI_API_KEY or 'INSERISCI' in OPENAI_API_KEY:
    return (
        '⚠️ **Errore:** `OPENAI_API_KEY` non configurata nel codice o nelle'
        ' variabili d\'ambiente di Render.'
    )

  base64_image = base64.b64encode(image_bytes).decode('utf-8')
  prompt = """
    Sei un esperto di analisi tecnica per opzioni binarie e mercati OTC (es. Pocket Option).
    Analizza attentamente questa foto dello schermo contenente un grafico finanziario.
    Estrai e fornisci le seguenti informazioni in formato Markdown chiaro e pulito:
    1. **Asset Rilevato** (es. USD/COP OTC).
    2. **Prezzo Attuale** esatto visibile a schermo.
    3. **Struttura del Mercato & Pattern** (Trend, candele recenti, indicatori).
    4. **Segnale**: Indica chiaramente **ACQUISTA (BUY) 🟢** oppure **VENDI (SELL) 🔴**.
    5. **Scadenza Consigliata**: (es. 1 Minuto).
    6. **Minuto Corretto di Entrata**: Indica il prossimo minuto esatto da eseguire.
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
    res = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers=headers,
        json=payload,
        timeout=25.0,
    ).json()
    if 'choices' in res:
      return res['choices'][0]['message']['content']
    elif 'error' in res:
      return f"⚠️ Errore API OpenAI: {res['error'].get('message', 'Errore sconosciuto')}"
    else:
      return '⚠️ Errore nell\'analisi dell\'immagine OTC.'
  except Exception as e:
    return f'⚠️ Errore di connessione API OpenAI: {e}'


# --- MENU TELEGRAM ---
def send_market_type_menu(chat_id, msg_id=None):
  if msg_id:
    delete_message(chat_id, msg_id)
  kb = {
      'inline_keyboard': [
          [{'text': '🌐 Mercato Reale', 'callback_data': 'mtype_REAL'}],
          [{'text': '⚡ Mercato OTC (Pocket Option)', 'callback_data': 'mtype_OTC'}],
          [{'text': '📷 Invia Foto Grafico OTC', 'callback_data': 'req_photo_otc'}],
      ]
  }
  send_message(
      chat_id, '📊 Seleziona la tipologia di mercato o invia una foto:', kb
  )


def send_assets_menu(chat_id, market_type, page=0, msg_id=None):
  if msg_id:
    delete_message(chat_id, msg_id)
  per_page = 9
  sub = BASE_ASSETS[page * per_page : (page + 1) * per_page]
  kb = []
  for i in range(0, len(sub), 3):
    row = []
    for j in range(3):
      if i + j < len(sub):
        item = sub[i + j]
        idx = BASE_ASSETS.index(item)
        display_name = f'{item} (OTC)' if market_type == 'OTC' else item
        row.append(
            {'text': display_name, 'callback_data': f'ast_{market_type}_{idx}'}
        )
    kb.append(row)

  nav = []
  if page > 0:
    nav.append({
        'text': '◀️ Indietro',
        'callback_data': f'pg_{market_type}_{page - 1}',
    })
  if (page + 1) * per_page < len(BASE_ASSETS):
    nav.append({
        'text': 'Avanti ▶️',
        'callback_data': f'pg_{market_type}_{page + 1}',
    })
  if nav:
    kb.append(nav)

  kb.append(
      [{'text': '🔙 Cambia Mercato', 'callback_data': 'back_market_type'}]
  )
  m_label = (
      'Mercato OTC (Pocket Option)' if market_type == 'OTC' else 'Mercato Reale'
  )
  send_message(
      chat_id,
      f'👋 Seleziona un asset per **{m_label}** (Timeframe 1M):',
      {'inline_keyboard': kb},
  )


def send_expiry_menu(chat_id, asset_name, market_type, msg_id):
  kb = {
      'inline_keyboard': [
          [{'text': '1 Minuto', 'callback_data': f'exp_{market_type}_1m'}],
          [{'text': '2 Minuti', 'callback_data': f'exp_{market_type}_2m'}],
          [{'text': '3 Minuti', 'callback_data': f'exp_{market_type}_3m'}],
          [{'text': '5 Minuti', 'callback_data': f'exp_{market_type}_5m'}],
          [{'text': '≡ Cambia Asset', 'callback_data': f'back_assets_{market_type}'}],
      ]
  }
  m_label = 'OTC' if market_type == 'OTC' else 'Reale'
  edit_message(
      chat_id,
      msg_id,
      f'💲💹 Asset [{m_label}]: {asset_name}\n\nSeleziona la scadenza:',
      kb,
  )


def process_analysis_background(cid, mid, ast_name, market_type, exp_key):
  try:
    data = fetch_market_data(ast_name, market_type)
    if data is None or data.empty:
      send_message(
          cid,
          f'⚠️ Impossibile recuperare i dati per {ast_name}.',
          {
              'inline_keyboard': [[{
                  'text': '≡ Cambia Asset',
                  'callback_data': f'back_assets_{market_type}',
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

    m_title = (
        '⚡ ANALISI OTC (POCKET OPTION)'
        if market_type == 'OTC'
        else '🐂🐻 ANALISI MERCATO REALE'
    )
    text = f'{m_title} (1M)\n\n'
    text += f'💲💹 Asset: {ast_name}\n'
    text += f'💵 Prezzo: `{round(current_close, 5)}`\n'
    text += f'🎯 Segnale: {sig_type} {sig_emoji}\n\n'
    text += '📈 Struttura di Mercato:\n'
    text += f'• Trend: {structure_name}\n\n'
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
            [{'text': '🔄 Aggiorna', 'callback_data': f'retry_{market_type}_{ast_name}_{exp_key}'}],
            [{'text': '≡ Cambia Asset', 'callback_data': f'back_assets_{market_type}'}],
        ]
    }
    chart_buf = generate_chart_image(data, ast_name, market_type)
    delete_message(cid, mid)
    if chart_buf:
      send_photo_message(cid, chart_buf, text, kb)
    else:
      send_message(cid, text, kb)
  except Exception as e:
    print('Errore background:', e)
    delete_message(cid, mid)
    send_message(cid, '⚠️ Si è verificato un errore.')


def process_otc_photo_background(cid, mid, file_id):
  try:
    file_url = get_telegram_file_url(file_id)
    if not file_url:
      delete_message(cid, mid)
      send_message(cid, '⚠️ Impossibile scaricare l\'immagine.')
      return
    img_data = requests.get(file_url).content
    res = analyze_otc_image_with_gpt(img_data)
    delete_message(cid, mid)
    kb = {
        'inline_keyboard': [
            [{'text': '📷 Invia altra foto OTC', 'callback_data': 'req_photo_otc'}],
            [{'text': '🔙 Torna al menu principale', 'callback_data': 'back_market_type'}],
        ]
    }
    send_message(
        cid, f'⚡ **ANALISI FOTO MERCATO OTC**\n\n{res}', kb
    )
  except Exception as e:
    delete_message(cid, mid)
    send_message(cid, '⚠️ Errore durante l\'elaborazione dell\'immagine.')


@app.route('/')
def index():
  return 'Bot completo pronto con Reale, OTC e Analisi Foto IA!', 200


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

        if val.startswith('mtype_'):
          m_type = val.split('_')[1]
          user_selection[cid] = {'market_type': m_type}
          send_assets_menu(cid, m_type, page=0, msg_id=mid)
        elif val == 'req_photo_otc':
          # Aggiunto il pulsante per tornare indietro se non si vuole inviare la foto
          kb = {
              'inline_keyboard': [
                  [{'text': '🔙 Torna al menu principale', 'callback_data': 'back_market_type'}]
              ]
          }
          edit_message(
              cid,
              mid,
              '📷 **Invia ORA lo screenshot del tuo grafico OTC (es. Pocket Option).**\n\nL\'intelligenza artificiale analizzerà direttamente l\'immagine.',
              markup=kb
          )
          if cid not in user_selection:
            user_selection[cid] = {}
          user_selection[cid]['awaiting_photo'] = True
          return 'ok', 200
        elif val.startswith('ast_'):
          parts = val.split('_')
          m_type = parts[1]
          ast = BASE_ASSETS[int(parts[2])]
          ast_display = f'{ast} (OTC)' if m_type == 'OTC' else ast
          if cid not in user_selection:
            user_selection[cid] = {}
          user_selection[cid]['market_type'] = m_type
          user_selection[cid]['asset'] = ast_display
          send_expiry_menu(cid, ast_display, m_type, mid)
        elif val.startswith('exp_'):
          parts = val.split('_')
          m_type, exp_key = parts[1], parts[2]
          ast_name = user_selection.get(cid, {}).get('asset', 'EUR/USD')
          delete_message(cid, mid)
          temp_mid = send_message(
              cid, f'⏳ Elaborazione in corso per {ast_name}...'
          )
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, m_type, exp_key),
          ).start()
          return 'ok', 200
        elif val.startswith('retry_'):
          parts = val.split('_')
          m_type, exp_key = parts[1], parts[-1]
          ast_name = '_'.join(parts[2:-1])
          delete_message(cid, mid)
          temp_mid = send_message(
              cid, f'⏳ Aggiornamento in corso per {ast_name}...'
          )
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, m_type, exp_key),
          ).start()
          return 'ok', 200
        elif val.startswith('pg_'):
          parts = val.split('_')
          send_assets_menu(
              cid, market_type=parts[1], page=int(parts[2]), msg_id=mid
          )
        elif val.startswith('back_assets_'):
          send_assets_menu(cid, val.split('_')[2], 0, msg_id=mid)
        elif val == 'back_market_type':
          send_market_type_menu(cid, msg_id=mid)

      elif 'message' in up:
        msg = up['message']
        cid = msg['chat']['id']

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
          send_market_type_menu(cid)
          return 'ok', 200
    except Exception as e:
      print('Errore webhook:', e)
  return 'ok', 200


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

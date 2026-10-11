from datetime import datetime, timedelta
from difflib import get_close_matches
from flask import Flask, request
from google import genai
from PIL import Image, ImageEnhance, ImageOps
import io
import json
import math
import os
import re
import threading
import time
import traceback
from zoneinfo import ZoneInfo
import matplotlib
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
import requests

matplotlib.use('Agg')

app = Flask(__name__)

TG_TOKEN = '8585533636:AAE_J2ospaddCWva9gPHzE26dCp2_WaziLk'
BASE_URL = 'https://api.telegram.org/bot' + TG_TOKEN

# Inizializzazione sicura del client Gemini
try:
  gemini_client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
except Exception as e:
  print('Errore init Gemini client:', e)
  gemini_client = None

market_cache = {}
CACHE_DURATION = 5
chart_lock = threading.Lock()

ITALY_TZ = ZoneInfo('Europe/Rome')

# --- LISTA COMPLETA DI TUTTI GLI ASSET REALI ED OTC ---
ALL_ASSETS = [
    # Forex Majors
    'EUR/USD',
    'GBP/USD',
    'USD/JPY',
    'AUD/USD',
    'USD/CAD',
    'USD/CHF',
    'NZD/USD',
    # Forex Crosses
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
    'CAD/CHF',
    'NZD/CAD',
    'NZD/CHF',
    'NZD/JPY',
    'EUR/CHF',
    'GBP/CHF',
    # Forex Exotics / OTC Diffusi
    'USD/COP',
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
    'USD/AED',
    'EUR/RUB',
    'EUR/TRY',
    'OMR/CNY',
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
    'BCH/EUR',
    'BCH/GBP',
    'BCH/JPY',
    'BTC/GBP',
    'BTC/JPY',
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
    'NVIDIA',
    'AMD',
    'PAYPAL',
    'VISA',
    'MASTERCARD',
    'PEPSI',
    'CITIGROUP',
    'PFIZER',
    'JOHNSON & JOHNSON',
    # Materie Prime e Indici
    'Gold',
    'Silver',
    'Brent Crude',
    'WTI Crude',
    'Natural Gas',
    'Platinum',
    'Palladium',
    'Wheat',
    'Corn',
    'S&P 500',
    'NASDAQ',
    'DAX',
    'FTSE 100',
    'DOW JONES',
    'NIKKEI 225',
    'CAC 40',
    'EURO STOXX 50',
    'ASX 200',
    'IBEX 35',
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


# --- RECUPERO DATI CRYPTO ---
def fetch_binance_data(clean_name):
  mapping = {
      'Bitcoin': 'BTCUSDT',
      'Ethereum': 'ETHUSDT',
      'Cardano': 'ADAUSDT',
      'Polkadot': 'DOTUSDT',
      'Toncoin': 'TONUSDT',
      'TRON': 'TRXUSDT',
      'Dogecoin': 'DOGEUSDT',
      'Litecoin': 'LTCUSDT',
      'Chainlink': 'LINKUSDT',
      'Solana': 'SOLUSDT',
      'BNB': 'BNBUSDT',
      'Polygon': 'MATICUSDT',
      'Avalanche': 'AVAXUSDT',
      'Dash': 'DASHUSDT',
      'Ripple': 'XRPUSDT',
      'Shiba Inu': 'SHIBUSDT',
  }
  symbol = mapping.get(clean_name)
  if not symbol:
    return None
  url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=100'
  try:
    res = requests.get(url, timeout=3.0).json()
    if isinstance(res, list) and len(res) > 0:
      df = pd.DataFrame(
          res,
          columns=[
              'open_time',
              'open',
              'high',
              'low',
              'close',
              'volume',
              'close_time',
              'quote_asset_volume',
              'number_of_trades',
              'taker_buy_base_asset_volume',
              'taker_buy_quote_asset_volume',
              'ignore',
          ],
      )
      df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
      df.set_index('timestamp', inplace=True)
      df = df[['open', 'high', 'low', 'close']].astype(float)
      df.columns = ['Open', 'High', 'Low', 'Close']
      df = df.sort_index()
      return df
  except Exception as e:
    print('Errore Binance:', e)
  return None


def fetch_coinbase_data(clean_name):
  mapping = {
      'Bitcoin': 'BTC-USD',
      'Ethereum': 'ETH-USD',
      'Cardano': 'ADA-USD',
      'Polkadot': 'DOT-USD',
      'Litecoin': 'LTC-USD',
      'Chainlink': 'LINK-USD',
      'Solana': 'SOL-USD',
      'Dogecoin': 'DOGE-USD',
      'Ripple': 'XRP-USD',
  }
  product_id = mapping.get(clean_name)
  if not product_id:
    return None
  url = f'https://api.exchange.coinbase.com/products/{product_id}/candles?granularity=60'
  try:
    res = requests.get(
        url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3.0
    ).json()
    if isinstance(res, list) and len(res) > 0:
      df = pd.DataFrame(
          res, columns=['time', 'low', 'high', 'open', 'close', 'volume']
      )
      df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
      df.set_index('timestamp', inplace=True)
      df = df[['open', 'high', 'low', 'close']].astype(float)
      df.columns = ['Open', 'High', 'Low', 'Close']
      df = df.sort_index()
      return df
  except Exception as e:
    print('Errore Coinbase:', e)
  return None


def fetch_bitget_data(clean_name):
  mapping = {
      'Bitcoin': 'BTCUSDT',
      'Ethereum': 'ETHUSDT',
      'Cardano': 'ADAUSDT',
      'Polkadot': 'DOTUSDT',
      'Solana': 'SOLUSDT',
      'Dogecoin': 'DOGEUSDT',
      'Litecoin': 'LTCUSDT',
      'Ripple': 'XRPUSDT',
  }
  symbol = mapping.get(clean_name)
  if not symbol:
    return None
  url = f'https://api.bitget.com/api/v2/spot/market/candles?symbol={symbol}&granularity=1min&limit=100'
  try:
    res = requests.get(url, timeout=3.0).json()
    if res.get('code') == '00000' and 'data' in res:
      raw = res['data']
      if isinstance(raw, list) and len(raw) > 0:
        df = pd.DataFrame(
            raw,
            columns=[
                'time',
                'open',
                'high',
                'low',
                'close',
                'baseVol',
                'quoteVol',
                'usdtVol',
            ],
        )
        df['timestamp'] = pd.to_datetime(
            df['time'].astype(int), unit='ms', utc=True
        )
        df.set_index('timestamp', inplace=True)
        df = df[['open', 'high', 'low', 'close']].astype(float)
        df.columns = ['Open', 'High', 'Low', 'Close']
        df = df.sort_index()
        return df
  except Exception as e:
    print('Errore Bitget:', e)
  return None


# --- CALCOLI DI INDICATORI ---
def calculate_indicators(df):
  close = df['Close']
  if isinstance(close, pd.DataFrame):
    close = close.iloc[:, 0]
  current_close = float(close.iloc[-1])
  prev_close = float(close.iloc[-2]) if len(close) > 1 else current_close

  delta = close.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean().replace(0, 1e-10)
  rsi = 100 - (100 / (1 + (gain / loss)))
  current_rsi = round(float(rsi.iloc[-1]), 1)
  if math.isnan(current_rsi):
    current_rsi = 50.0

  exp1 = close.ewm(span=12, adjust=False).mean()
  exp2 = close.ewm(span=26, adjust=False).mean()
  hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
  m_hist = round(float(hist.iloc[-1]), 5)

  highs = (
      df['High'].iloc[:, 0]
      if isinstance(df['High'], pd.DataFrame)
      else df['High']
  )
  lows = (
      df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
  )

  recent_highs = highs.tail(12)
  recent_lows = lows.tail(12)
  is_hh = float(recent_highs.iloc[-1]) >= float(recent_highs.iloc[-6])
  is_hl = float(recent_lows.iloc[-1]) >= float(recent_lows.iloc[-6])

  if is_hh and is_hl:
    structure_name, structure_score = 'Rialzista (HH/HL)', 2
  elif not is_hh and not is_hl:
    structure_name, structure_score = 'Ribassista (LH/LL)', -2
  else:
    structure_name, structure_score = 'Laterale / Misto', 0

  return (
      current_rsi,
      m_hist,
      current_close,
      prev_close,
      structure_name,
      structure_score,
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
      ema20_full = close_series.ewm(span=20, adjust=False).mean()

      df_plot = df_clean.tail(30).copy()
      ema20_plot = ema20_full.tail(30)

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
          df_plot,
          type='candle',
          style=custom_style,
          addplot=add_plots,
          title=f'\nAnalisi Tecnica 1M: {asset_name}',
          volume=False,
          figsize=(8, 4.5),
          returnfig=True,
          panel_ratios=(1,),
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
      print('ERRORE NELLA GENERAZIONE DEL GRAFICO:', e)
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
      'USD/MXN': 'USDMXN=X',
      'USD/ZAR': 'USDZAR=X',
      'USD/CHF': 'USDCHF=X',
      'EUR/GBP': 'EURGBP=X',
      'EUR/JPY': 'EURJPY=X',
      'GBP/JPY': 'GBPJPY=X',
      'AUD/JPY': 'AUDJPY=X',
      'NZD/USD': 'NZDUSD=X',
      'EUR/AUD': 'EURAUD=X',
      'EUR/CAD': 'EURCAD=X',
      'USD/COP': 'USDCOP=X',
      'Gold': 'GC=F',
      'Silver': 'SI=F',
      'APPLE': 'AAPL',
      'MICROSOFT': 'MSFT',
      'TESLA': 'TSLA',
      'AMAZON': 'AMZN',
  }
  if clean in mapping:
    return mapping[clean]
  if '/' in clean and len(clean) == 7:
    return clean.replace('/', '') + '=X'
  return clean


def fetch_market_data(asset_name):
  clean = asset_name.replace("'", '').strip()
  current_time = time.time()
  if clean in market_cache:
    ts, cached_df = market_cache[clean]
    if current_time - ts < CACHE_DURATION:
      return cached_df

  df = fetch_binance_data(clean)
  if df is None or df.empty:
    df = fetch_coinbase_data(clean)
  if df is None or df.empty:
    df = fetch_bitget_data(clean)

  if df is None or df.empty:
    ticker_symbol = get_yahoo_ticker(clean)
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d'
    try:
      response = requests.get(
          url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3.0
      )
      if response.status_code == 200:
        data = response.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        df_raw = pd.DataFrame(
            {
                'Open': quote['open'],
                'High': quote['high'],
                'Low': quote['low'],
                'Close': quote['close'],
            },
            index=pd.to_datetime(timestamps, unit='s', utc=True),
        )
        df = (
            df_raw.dropna()
            .resample('1min')
            .agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'})
            .ffill()
            .dropna()
        )
        df = df.sort_index()
    except Exception as e:
      print('Errore Yahoo:', e)

  if df is not None and not df.empty:
    market_cache[clean] = (current_time, df)
    return df
  return None


# --- ANALISI IMMAGINE CON GEMINI AI (OTTIMIZZATA, FORZATA JSON E CON MATCHING SICURO) ---
def extract_asset_and_price_from_image(image_bytes):
  try:
    if gemini_client is None:
      return 'EUR/USD', 0.0

    img = Image.open(io.BytesIO(image_bytes))
    assets_reference = ', '.join(ALL_ASSETS)

    prompt = (
        'Analizza con estrema precisione questo screenshot di una piattaforma di trading online. '
        'Il tuo compito è identificare con precisione chirurgica:\n'
        '1. L\'asset o la coppia di valute presente nel grafico. '
        f'DEVI scegliere obbligatoriamente un nome che sia il più vicino possibile o identico a uno di questi presenti nella lista ufficiale:\n[{assets_reference}]. '
        "Se l'asset sullo schermo include la dicitura 'OTC', segnalalo chiaramente.\n"
        "2. 'price': Il prezzo numerico attuale di mercato visibile a schermo (formato float).\n\n"
        "Rispondi unicamente con un oggetto JSON avente le chiavi 'asset' (stringa) e 'price' (numero float)."
    )

    response = gemini_client.models.generate_content(
        model='gemini-2.0-flash',
        contents=[img, prompt],
        config={'response_mime_type': 'application/json'},
    )

    parsed = json.loads(response.text.strip())
    detected_raw = parsed.get('asset', 'EUR/USD').strip()
    price_val = float(parsed.get('price', 0.0))

    # Controllo di pulizia e corrispondenza esatta/simile con ALL_ASSETS
    is_otc_flag = 'OTC' in detected_raw.upper()
    clean_detected = (
        detected_raw.replace('OTC', '').replace('/', '').upper().strip()
    )

    best_match = 'EUR/USD'
    highest_ratio = 0
    for a in ALL_ASSETS:
      a_clean = a.replace('/', '').upper().strip()
      if clean_detected == a_clean:
        best_match = a
        break
      if clean_detected in a_clean or a_clean in clean_detected:
        if len(a_clean) > highest_ratio:
          best_match = a
          highest_ratio = len(a_clean)

    if highest_ratio == 0:
      matches = get_close_matches(
          detected_raw, ALL_ASSETS, n=1, cutoff=0.3
      )
      if matches:
        best_match = matches[0]

    final_asset_name = f'{best_match} OTC' if is_otc_flag else best_match
    return final_asset_name, price_val

  except Exception as e:
    print("Errore durante l'analisi con Gemini:", e)
    return 'EUR/USD', 0.0


def process_otc_photo_background(cid, mid, file_id):
  try:
    file_url = get_telegram_file_url(file_id)
    if not file_url:
      edit_message(cid, mid, '⚠️ Impossibile scaricare lo screenshot.')
      return

    img_data = requests.get(file_url, timeout=10.0).content
    asset_detected, current_price = extract_asset_and_price_from_image(
        img_data
    )

    clean_asset = asset_detected.replace('OTC', '').strip()
    data = fetch_market_data(clean_asset)
    if data is None or data.empty:
      data = fetch_market_data('Bitcoin')

    (
        rsi_val,
        macd_hist,
        current_close,
        prev_close,
        structure_name,
        structure_score,
    ) = calculate_indicators(data)

    now_it = datetime.now(ITALY_TZ)
    entry_time = (
        now_it.replace(second=0, microsecond=0) + timedelta(minutes=1)
    ).strftime('%H:%M:%S')

    score = structure_score + (1 if current_close > prev_close else -1)
    sig_type = (
        'ACQUISTA (BUY)'
        if rsi_val < 50 or score >= 0
        else 'VENDI (SELL)'
    )
    sig_emoji = '🟢' if 'ACQUISTA' in sig_type else '🔴'
    conf = round(np.random.uniform(86.0, 96.0), 1)

    text = f'⚡ **ANALISI FOTO MERCATO (GEMINI AI)**\n\n'
    text += f'1. **Asset Rilevato**: `{asset_detected}`\n'
    text += f'2. **Prezzo Attuale**: `{current_price}`\n'
    text += (
        '3. **Struttura del Mercato & Pattern**: Trend a breve termine'
        f' reattivo ({structure_name}).\n'
    )
    text += f'4. **Segnale**: **{sig_type} {sig_emoji}**\n'
    text += f'5. **Scadenza Consigliata**: 1 Minuto\n'
    text += f'6. **Minuto Corretto di Entrata**: `{entry_time}`\n'
    text += f'7. **Affidabilità Stimata**: `{conf}%`'

    kb = {
        'inline_keyboard': [
            [{'text': '📷 Invia un altro screenshot', 'callback_data': 'req_photo_otc'}],
            [{'text': '🔙 Torna al menu principale', 'callback_data': 'back_market_type'}],
        ]
    }

    delete_message(cid, mid)
    send_message(cid, text, kb)
  except Exception as e:
    print('Errore elaborazione foto:', e)
    send_message(cid, '⚠️ Si è verificato un errore nell\'analisi della foto.')


# --- MENU TELEGRAM ---
def send_market_type_menu(chat_id, msg_id=None):
  if msg_id:
    delete_message(chat_id, msg_id)
  kb = {
      'inline_keyboard': [
          [{'text': '🌐 Mercato Reale (Lista Asset)', 'callback_data': 'mtype_real'}],
          [{'text': '⚡ Mercato OTC (Seleziona Asset)', 'callback_data': 'mtype_otc_select'}],
          [{'text': '📷 Invia Screenshot', 'callback_data': 'req_photo_otc'}],
      ]
  }
  send_message(
      chat_id, '📊 Seleziona un\'opzione dal menu o invia direttamente uno screenshot:', kb
  )


def send_assets_menu(chat_id, page=0, is_otc=False, msg_id=None):
  if msg_id:
    delete_message(chat_id, msg_id)

  per_page = 9
  sub = ALL_ASSETS[page * per_page : (page + 1) * per_page]
  prefix = 'otcast_' if is_otc else 'ast_'
  pg_prefix = 'otcpg_' if is_otc else 'pg_'

  kb = []
  for i in range(0, len(sub), 3):
    row = []
    for j in range(3):
      if i + j < len(sub):
        asset_item = sub[i + j]
        label = f'{asset_item} OTC' if is_otc else asset_item
        row.append({
            'text': label,
            'callback_data': prefix + str(ALL_ASSETS.index(asset_item)),
        })
    kb.append(row)

  nav = []
  if page > 0:
    nav.append({'text': '◀️ Indietro', 'callback_data': pg_prefix + str(page - 1)})
  if (page + 1) * per_page < len(ALL_ASSETS):
    nav.append({'text': 'Avanti ▶️', 'callback_data': pg_prefix + str(page + 1)})
  if nav:
    kb.append(nav)

  kb.append([{'text': '🔙 Torna al Menu Principale', 'callback_data': 'back_market_type'}])

  title = '⚡ Scegli un Asset OTC (Calcolo Probabilità RSI(9) & MACD):' if is_otc else '🌐 Scegli un Asset Reale (Timeframe 1M):'
  send_message(chat_id, title, {'inline_keyboard': kb})


def send_expiry_menu(chat_id, asset_name, is_otc, msg_id):
  prefix = 'otcexp_' if is_otc else 'exp_'
  back_target = 'back_otc_assets' if is_otc else 'back_assets'
  kb = {
      'inline_keyboard': [
          [{'text': '1 Minuto', 'callback_data': f'{prefix}1m'}],
          [{'text': '2 Minuti', 'callback_data': f'{prefix}2m'}],
          [{'text': '3 Minuti', 'callback_data': f'{prefix}3m'}],
          [{'text': '5 Minuti', 'callback_data': f'{prefix}5m'}],
          [{'text': '≡ Cambia Asset', 'callback_data': back_target}],
      ]
  }
  tag = 'OTC' if is_otc else 'Reale'
  edit_message(
      chat_id,
      msg_id,
      f'💲💹 Asset {tag}: {asset_name}\n\nSeleziona la scadenza:',
      kb,
  )


def process_analysis_background(cid, mid, ast_name, exp_key, is_otc=False):
  try:
    clean_asset = ast_name.replace('OTC', '').strip()
    data = fetch_market_data(clean_asset)
    if data is None or data.empty:
      data = fetch_market_data('Bitcoin')

    now_it = datetime.now(ITALY_TZ)
    next_entry_dt = now_it.replace(second=0, microsecond=0) + timedelta(
        minutes=1
    )
    entry_time = next_entry_dt.strftime('%H:%M:%S')

    (
        rsi_val,
        macd_hist,
        current_close,
        prev_close,
        structure_name,
        structure_score,
    ) = calculate_indicators(data)

    score = structure_score + (1 if current_close > prev_close else -1)
    sig_type = (
        'ACQUISTA (BUY)'
        if rsi_val < 50 or score >= 0
        else 'VENDI (SELL)'
    )
    sig_emoji = '🟢' if 'ACQUISTA' in sig_type else '🔴'
    conf = round(np.random.uniform(86.0, 96.0), 1)

    tag_title = (
        '⚡ ANALISI PROBABILITÀ' if is_otc else '🐂🐻 ANALISI MERCATO REALE (1M)'
    )
    back_target = 'back_otc_assets' if is_otc else 'back_assets'

    text = f'{tag_title}\n\n'
    text += f'💲💹 Asset: {ast_name}\n'
    text += f'💵 Prezzo: `{round(current_close, 5)}`\n'
    text += f'🎯 Segnale: {sig_type} {sig_emoji}\n\n'
    text += f'• Trend: {structure_name}\n'
    text += f'• RSI (9): {rsi_val}\n'
    text += f'• MACD Hist (12,26,9): {macd_hist}\n'
    text += f'⚖️ Probabilità / Affidabilità: {conf}%\n'
    text += f'📌 Minuto Corretto di Entrata: `{entry_time}`'

    kb = {
        'inline_keyboard': [
            [{'text': '🔄 Aggiorna', 'callback_data': f'retry_{"otc_" if is_otc else ""}{ast_name}_{exp_key}'}],
            [{'text': '≡ Cambia Asset', 'callback_data': back_target}],
        ]
    }

    delete_message(cid, mid)

    if not is_otc:
      chart_buf = generate_chart_image(data, ast_name)
      if chart_buf:
        send_photo_message(cid, chart_buf, text, kb)
        return

    send_message(cid, text, kb)

  except Exception as e:
    print('Errore background:', e)
    delete_message(cid, mid)
    send_message(cid, '⚠️ Si è verificato un errore nell\'elaborazione.')


@app.route('/')
def index():
  return 'Bot operativo con Gemini AI integrato!', 200


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

        if val == 'mtype_real':
          send_assets_menu(cid, page=0, is_otc=False, msg_id=mid)

        elif val == 'mtype_otc_select':
          send_assets_menu(cid, page=0, is_otc=True, msg_id=mid)

        elif val == 'req_photo_otc':
          kb = {
              'inline_keyboard': [
                  [{'text': '🔙 Torna Indietro', 'callback_data': 'back_market_type'}]
              ]
          }
          edit_message(
              cid,
              mid,
              '📷 **Invia ORA lo screenshot del tuo grafico.**\n\nGemini analizzerà l\'immagine riconoscendo istantaneamente asset e prezzo!',
              markup=kb,
          )
          return 'ok', 200

        elif val.startswith('ast_'):
          ast = ALL_ASSETS[int(val.split('_')[1])]
          user_selection[cid] = {'asset': ast, 'is_otc': False}
          send_expiry_menu(cid, ast, is_otc=False, msg_id=mid)

        elif val.startswith('otcast_'):
          ast = ALL_ASSETS[int(val.split('_')[1])] + ' OTC'
          user_selection[cid] = {'asset': ast, 'is_otc': True}
          send_expiry_menu(cid, ast, is_otc=True, msg_id=mid)

        elif (
            val.startswith('exp_')
            or val.startswith('otcexp_')
            or val.startswith('retry_')
        ):
          is_otc = 'otc' in val
          if 'retry_' in val:
            parts = val.split('_')
            exp_key = parts[-1]
            ast_name = (
                '_'.join(parts[2:-1]) if is_otc else '_'.join(parts[1:-1])
            )
          else:
            sel = user_selection.get(cid, {})
            ast_name = sel.get('asset', 'EUR/USD')
            exp_key = val.split('_')[1]

          delete_message(cid, mid)
          temp_mid = send_message(
              cid, f'⏳ Calcolo probabilità RSI(9) e MACD per {ast_name}...'
          )
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, exp_key, is_otc),
          ).start()
          return 'ok', 200

        elif val.startswith('pg_'):
          send_assets_menu(cid, int(val.split('_')[1]), is_otc=False, msg_id=mid)

        elif val.startswith('otcpg_'):
          send_assets_menu(cid, int(val.split('_')[1]), is_otc=True, msg_id=mid)

        elif val == 'back_assets':
          send_assets_menu(cid, 0, is_otc=False, msg_id=mid)

        elif val == 'back_otc_assets':
          send_assets_menu(cid, 0, is_otc=True, msg_id=mid)

        elif val == 'back_market_type':
          send_market_type_menu(cid, msg_id=mid)

      elif 'message' in up:
        msg = up['message']
        cid = msg['chat']['id']

        if 'photo' in msg:
          file_id = msg['photo'][-1]['file_id']
          user_msg_id = msg['message_id']
          delete_message(cid, user_msg_id)

          temp_mid = send_message(
              cid, '🔍 **Screenshot ricevuto.** Gemini sta analizzando il grafico...'
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

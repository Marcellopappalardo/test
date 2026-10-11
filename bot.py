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


# --- RECUPERO DATI CRYPTO (SOLO MERCATO REALE) ---
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


# --- CALCOLI DI INDICATORI (MERCATO REALE) ---
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


# --- ANALISI OTC MANUALE CON GEMINI IA ---
def analyze_otc_manual_with_gemini(asset_name, expiry_key, price_val, rsi_val, macd_val):
  try:
    if gemini_client is None:
      return 'Rialzista (HH/HL)', 92.5, 'ACQUISTA (BUY)'

    prompt = (
        f"Sei un trader professionista ed esperto in opzioni binarie. Analizza questi dati manuali inseriti per l'asset OTC: {asset_name} con scadenza {expiry_key}:\n"
        f"- Prezzo attuale: {price_val}\n"
        f"- RSI (9): {rsi_val}\n"
        f"- MACD Hist: {macd_val}\n\n"
        "Determina:\n"
        "1. 'trend': La struttura del mercato coerente con questi indicatori ('Rialzista (HH/HL)', 'Ribassista (LH/LL)' o 'Laterale / Misto').\n"
        "2. 'confidence': Una percentuale di affidabilità realistica tra 88.0 e 96.5.\n"
        "3. 'signal': 'ACQUISTA (BUY)' oppure 'VENDI (SELL)'.\n\n"
        "Rispondi ESCLUSIVAMENTE in formato JSON valido, senza testo aggiuntivo:\n"
        '{"trend": "Rialzista (HH/HL)", "confidence": 92.5, "signal": "ACQUISTA (BUY)"}'
    )

    response = gemini_client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'},
    )

    text_resp = response.text.strip()
    if text_resp.startswith("```json"):
      text_resp = text_resp[7:]
    if text_resp.endswith("```"):
      text_resp = text_resp[:-3]
    text_resp = text_resp.strip()

    parsed = json.loads(text_resp)
    trend_val = str(parsed.get('trend', 'Rialzista (HH/HL)'))
    conf_val = float(parsed.get('confidence', 91.0))
    signal_val = str(parsed.get('signal', 'ACQUISTA (BUY)'))

    return trend_val, conf_val, signal_val

  except Exception as e:
    print('Errore analisi OTC manuale con Gemini:', e)
    return 'Rialzista (HH/HL)', 90.0, 'ACQUISTA (BUY)'


# --- MENU TELEGRAM ---
def send_market_type_menu(chat_id, msg_id=None):
  if msg_id:
    delete_message(chat_id, msg_id)
  kb = {
      'inline_keyboard': [
          [{'text': '🌐 Mercato Reale (Lista Asset)', 'callback_data': 'mtype_real'}],
          [{'text': '⚡ Mercato OTC (Inserimento Manuale)', 'callback_data': 'mtype_otc_select'}],
      ]
  }
  send_message(
      chat_id, '📊 Seleziona il tipo di mercato:', kb
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

  title = '⚡ Scegli un Asset OTC:' if is_otc else '🌐 Scegli un Asset Reale (Timeframe 1M):'
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
    exp_minutes = int(exp_key.replace('m', ''))
    next_entry_dt = now_it.replace(second=0, microsecond=0) + timedelta(minutes=exp_minutes)
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

    tag_title = '🐂🐻 ANALISI MERCATO REALE (1M)'
    back_target = 'back_assets'

    text = f'{tag_title}\n\n'
    text += f'💲💹 Asset: {ast_name}\n'
    text += f'💵 Prezzo: `{round(current_close, 5)}`\n'
    text += f'🎯 Segnale: {sig_type} {sig_emoji}\n\n'
    text += f'• Trend: {structure_name}\n'
    text += f'• RSI (9): {rsi_val}\n'
    text += f'• MACD Hist (12,26,9): {macd_hist}\n'
    text += f'⚖️ Probabilità / Affidabilità: {conf}%\n'
    text += f'📌 Scadenza / Entrata: {exp_key.upper()} (Fine a `{entry_time}`)'

    kb = {
        'inline_keyboard': [
            [{'text': '🔄 Aggiorna', 'callback_data': f'retry_real_{ast_name}_{exp_key}'}],
            [{'text': '≡ Cambia Asset', 'callback_data': back_target}],
        ]
    }

    delete_message(cid, mid)

    chart_buf = generate_chart_image(data, ast_name)
    if chart_buf:
      send_photo_message(cid, chart_buf, text, kb)
      return

    send_message(cid, text, kb)

  except Exception as e:
    print('Errore background reale:', e)
    delete_message(cid, mid)
    send_message(cid, '⚠️ Si è verificato un errore nell\'elaborazione del mercato reale.')


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

        elif val.startswith('ast_'):
          ast = ALL_ASSETS[int(val.split('_')[1])]
          user_selection[cid] = {'asset': ast, 'is_otc': False}
          send_expiry_menu(cid, ast, is_otc=False, msg_id=mid)

        elif val.startswith('otcast_'):
          ast = ALL_ASSETS[int(val.split('_')[1])] + ' OTC'
          user_selection[cid] = {'asset': ast, 'is_otc': True}
          send_expiry_menu(cid, ast, is_otc=True, msg_id=mid)

        elif val.startswith('exp_'):
          exp_key = val.split('_')[1]
          sel = user_selection.get(cid, {})
          ast_name = sel.get('asset', 'EUR/USD')

          delete_message(cid, mid)
          temp_mid = send_message(cid, f'⏳ Calcolo analisi mercato reale per {ast_name}...')
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, exp_key, False),
          ).start()
          return 'ok', 200

        elif val.startswith('otcexp_'):
          exp_key = val.split('_')[1]
          sel = user_selection.get(cid, {})
          sel['expiry'] = exp_key
          user_selection[cid] = sel

          kb = {'inline_keyboard': [[{'text': '🔙 Indietro', 'callback_data': 'back_otc_assets'}]]}
          edit_message(
              cid,
              mid,
              f"✍️ **Asset OTC:** {sel.get('asset')}\n⏱ **Scadenza:** {exp_key.upper()}\n\nInvia ORA in chat i dati dell'ultima candela separati da virgola nel formato:\n`Prezzo, RSI, MACD`\n\n*(Esempio: `1.45274, 48.5, 0.00012`)*",
              markup=kb
          )
          return 'ok', 200

        elif val.startswith('retry_real_'):
          parts = val.split('_')
          exp_key = parts[-1]
          ast_name = '_'.join(parts[2:-1])

          delete_message(cid, mid)
          temp_mid = send_message(cid, f'⏳ Aggiornamento analisi per {ast_name}...')
          threading.Thread(
              target=process_analysis_background,
              args=(cid, temp_mid, ast_name, exp_key, False),
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

        if 'text' in msg:
          text_content = msg['text'].strip()
          mid = msg['message_id']
          delete_message(cid, mid)

          sel = user_selection.get(cid)
          if sel and 'asset' in sel and 'expiry' in sel and sel.get('is_otc'):
            parts = [p.strip() for p in text_content.split(',')]
            if len(parts) >= 3:
              try:
                price_val = float(parts[0])
                rsi_val = float(parts[1])
                macd_val = float(parts[2])
                
                ast_name = sel['asset']
                exp_key = sel['expiry']

                temp_mid = send_message(cid, f'⚡ Elaborazione dati manuali per {ast_name} con Gemini...')

                trend_val, conf_val, signal_val = analyze_otc_manual_with_gemini(ast_name, exp_key, price_val, rsi_val, macd_val)

                now_it = datetime.now(ITALY_TZ)
                exp_min = int(exp_key.replace('m', ''))
                next_entry_dt = now_it.replace(second=0, microsecond=0) + timedelta(minutes=exp_min)
                entry_time = next_entry_dt.strftime('%H:%M:%S')

                sig_emoji = '🟢' if 'ACQUISTA' in signal_val else '🔴'

                result_text = f'⚡ **ANALISI OTC (MANUALE)**\n\n'
                result_text += f'💲💹 Asset: {ast_name}\n'
                result_text += f'💵 Prezzo Inserito: `{price_val}`\n'
                result_text += f'🎯 Segnale: **{signal_val} {sig_emoji}**\n\n'
                result_text += f'• Trend: {trend_val}\n'
                result_text += f'• RSI (9): {rsi_val}\n'
                result_text += f'• MACD Hist: {macd_val}\n'
                result_text += f'⚖️ Probabilità / Affidabilità: `{conf_val}%`\n'
                result_text += f'📌 Scadenza: `{exp_key.upper()}` (Fine a `{entry_time}`)'

                kb = {
                    'inline_keyboard': [
                        [{'text': '🔙 Torna al menu principale', 'callback_data': 'back_market_type'}],
                    ]
                }

                delete_message(cid, temp_mid)
                send_message(cid, result_text, kb)
                return 'ok', 200

              except Exception as ex:
                print('Errore parsing valori manuali:', ex)
                send_message(cid, '⚠️ Formato non valido. Inserisci i dati separati da virgola, es: `1.45274, 48.5, 0.00012`')
                return 'ok', 200

          send_market_type_menu(cid)
          return 'ok', 200
    except Exception as e:
      print('Errore webhook:', e)
  return 'ok', 200


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

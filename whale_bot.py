TELEGRAM_TOKEN = "8779897859:AAEhhq2C93tCD030HvzPn7gLZzV0unacbwc"
CHAT_ID = "1139482362"
import json
import logging
import threading
import time
from collections import defaultdict
import websocket
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

# ==========================================
# 1. AYARLAR
# ==========================================
TELEGRAM_TOKEN = "8779897859:AAEhhq2C93tCD030HvzPn7gLZzV0unacbwc"  # BotFather token'ınızı kontrol edin
CHAT_ID = "1139482362"  # Kendi Chat ID'nizi yazın

SYMBOLS = ["mtlusdt", "btcusdt", "ethusdt", "solusdt"]

TP_PERCENT = 1.5   # %1.5 Kar Al
SL_PERCENT = 0.8   # %0.8 Stop Loss

# TEST İÇİN DÜŞÜK EŞİKLER (Sinyali hemen görmek için)
MIN_VOL_THRESHOLD_USD = 5000   # $5.000 hacim
DOMINANCE_RATIO = 0.51          # %51 dominans
SIGNAL_COOLDOWN_SEC = 30        # 30 saniye cooldown

trade_buffer = defaultdict(list)
last_signal_time = defaultdict(float)
active_positions = {}

telegram_app = None
logging.basicConfig(level=logging.INFO)

# ==========================================
# 2. HACİM VE DİNLENME MEKANİZMASI
# ==========================================
def process_trades(symbol):
    now = time.time()
    trade_buffer[symbol] = [t for t in trade_buffer[symbol] if now - t['time'] <= 60]
    
    buy_volume = sum(t['usd'] for t in trade_buffer[symbol] if not t['is_sell'])
    sell_volume = sum(t['usd'] for t in trade_buffer[symbol] if t['is_sell'])
    total_volume = buy_volume + sell_volume

    if total_volume < MIN_VOL_THRESHOLD_USD:
        return

    last_price = trade_buffer[symbol][-1]['price']
    
    # Erken Kapatma Kontrolü
    if symbol in active_positions:
        current_side = active_positions[symbol]
        
        if current_side == "LONG" and (sell_volume / total_volume >= DOMINANCE_RATIO):
            del active_positions[symbol]
            if telegram_app and telegram_app.loop:
                telegram_app.loop.create_task(
                    send_exit_signal(symbol.upper(), "🔴 POZİSYONU KAPAT (EXIT)", last_price, "Ters yönde (Satış) güçlü balina hacmi algılandı!")
                )
            return

        elif current_side == "SHORT" and (buy_volume / total_volume >= DOMINANCE_RATIO):
            del active_positions[symbol]
            if telegram_app and telegram_app.loop:
                telegram_app.loop.create_task(
                    send_exit_signal(symbol.upper(), "🟢 POZİSYONU KAPAT (EXIT)", last_price, "Ters yönde (Alış) güçlü balina hacmi algılandı!")
                )
            return

    # Giriş Sinyali Kontrolü
    if now - last_signal_time[symbol] < SIGNAL_COOLDOWN_SEC:
        return

    signal_type = None
    tp_price = 0.0
    sl_price = 0.0

    if buy_volume / total_volume >= DOMINANCE_RATIO:
        signal_type = "🟢 BUY (LONG) SİNYALİ"
        active_positions[symbol] = "LONG"
        tp_price = last_price * (1 + TP_PERCENT / 100)
        sl_price = last_price * (1 - SL_PERCENT / 100)
        reason = f"Balinalar son 1 dk içinde **${buy_volume:,.0f}** alım yaptı! (%{buy_volume/total_volume*100:.0f} Alıcı Dominansı)"

    elif sell_volume / total_volume >= DOMINANCE_RATIO:
        signal_type = "🔴 SELL (SHORT) SİNYALİ"
        active_positions[symbol] = "SHORT"
        tp_price = last_price * (1 - TP_PERCENT / 100)
        sl_price = last_price * (1 + SL_PERCENT / 100)
        reason = f"Balinalar son 1 dk içinde **${sell_volume:,.0f}** satış yaptı! (%{sell_volume/total_volume*100:.0f} Satıcı Dominansı)"

    if signal_type:
        last_signal_time[symbol] = now
        if telegram_app and telegram_app.loop:
            telegram_app.loop.create_task(
                send_entry_signal(symbol.upper(), signal_type, last_price, tp_price, sl_price, reason)
            )

def on_message(ws, message):
    try:
        data = json.loads(message)
        if 'data' in data:
            data = data['data']
            
        symbol = data['s'].lower()
        price = float(data['p'])
        quantity = float(data['q'])
        usd_val = price * quantity
        is_sell = data['m']

        if usd_val >= 1000:  # $1.000 üzeri işlemleri takip et
            trade_buffer[symbol].append({
                'time': time.time(),
                'usd': usd_val,
                'is_sell': is_sell,
                'price': price
            })
            process_trades(symbol)
    except Exception as e:
        logging.error(f"Message process error: {e}")

def start_multi_websocket():
    streams = "/".join([f"{s}@aggTrade" for s in SYMBOLS])
    socket_url = f"wss://fstream.binance.com/stream?streams={streams}"
    ws = websocket.WebSocketApp(socket_url, on_message=on_message)
    ws.run_forever()

# ==========================================
# 3. TELEGRAM MESAJLARI
# ==========================================
async def send_entry_signal(symbol: str, signal_type: str, entry: float, tp: float, sl: float, reason: str):
    text = (
        f"🚨 **YENİ İŞLEM SİNYALİ: {symbol}**\n\n"
        f"**Yön:** {signal_type}\n"
        f"**Giriş Fiyatı:** ${entry:,.4f}\n\n"
        f"🎯 **Kar Al (TP):** ${tp:,.4f} (+%{TP_PERCENT})\n"
        f"🛑 **Stop Loss (SL):** ${sl:,.4f} (-%{SL_PERCENT})\n\n"
        f"**Gerekçe:** {reason}"
    )
    binance_link = f"https://www.binance.com/en/futures/{symbol}"
    keyboard = [[InlineKeyboardButton(f"📱 {symbol} İşlemini Binance'te Aç", url=binance_link)]]
    await telegram_app.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def send_exit_signal(symbol: str, signal_type: str, price: float, reason: str):
    text = (
        f"⚠️ **POZİSYON KAPATMA UYARISI: {symbol}**\n\n"
        f"**Aksiyon:** {signal_type}\n"
        f"**Anlık Fiyat:** ${price:,.4f}\n\n"
        f"**Neden:** {reason}\n"
        f"👉 *Lütfen Binance üzerinden pozisyonunuzu kontrol edin veya kapatın.*"
    )
    binance_link = f"https://www.binance.com/en/futures/{symbol}"
    keyboard = [[InlineKeyboardButton(f"📱 {symbol} Ekranına Git", url=binance_link)]]
    await telegram_app.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ==========================================
# 4. BAŞLATICI
# ==========================================
def main():
    global telegram_app
    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()

    ws_thread = threading.Thread(target=start_multi_websocket, daemon=True)
    ws_thread.start()

    print("Balina Sinyal Botu Aktif... İzlenenler:", SYMBOLS)
    telegram_app.run_polling()

if __name__ == "__main__":
    main()

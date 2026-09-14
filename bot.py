# -*- coding: utf-8 -*-
"""
WB Jewelry Monitor — полное демо для собеседования.
Меню + ключевые показатели: маржа, ROI, прибыль, скидка, рейтинг.
"""
import configparser
import json
import os
import logging

import telebot
from telebot import types
import gspread
from google.oauth2.service_account import Credentials

# ==================== ЛОГИ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================

def get_settings(section, key):
    config = configparser.ConfigParser()
    config.read('settings.ini', encoding='utf-8')
    return config.get(section, key)

def get_float(section, key):
    return float(get_settings(section, key))

bot = telebot.TeleBot(token=get_settings('TELEGRAM', 'token'))

# ==================== GOOGLE SHEETS ====================

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def get_sheet():
    if 'GOOGLE_CREDENTIALS' in os.environ:
        creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(
        get_settings('GOOGLE', 'spreadsheet_id')
    ).worksheet(get_settings('GOOGLE', 'worksheet_name'))

def load_products():
    try:
        return get_sheet().get_all_records()
    except Exception as e:
        logger.error(f'Ошибка загрузки: {e}')
        return []

# ==================== КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ ====================

def calc_metrics(p):
    """Считает ключевые показатели по одному SKU."""
    try:
        initial = float(p.get('initial_price', 0) or 0)
        final = float(p.get('final_price', 0) or 0)
        rating = float(p.get('rating', 0) or 0)
        reviews = int(p.get('review_count', 0) or 0)
        
        if final <= 0:
            return None
        
        # Комиссия WB
        commission = round(final * get_float('ECONOMICS', 'commission_pct') / 100, 2)
        # Себестоимость
        cost = round(initial * get_float('ECONOMICS', 'cost_pct') / 100, 2)
        # Логистика + хранение
        logistics = get_float('ECONOMICS', 'logistics')
        storage = get_float('ECONOMICS', 'storage')
        
        # Прибыль
        profit = round(final - commission - logistics - storage - cost, 2)
        # Маржа %
        margin = round(profit / final * 100, 1)
        # ROI %
        roi = round(profit / cost * 100, 1) if cost > 0 else 0
        # Скидка %
        discount = round((initial - final) / initial * 100, 1) if initial > 0 else 0
        
        return {
            'commission': commission,
            'cost': cost,
            'profit': profit,
            'margin': margin,
            'roi': roi,
            'discount': discount,
            'rating': rating,
            'reviews': reviews,
        }
    except (ValueError, TypeError):
        return None

def enrich(products):
    """Добавляет показатели к каждому товару."""
    result = []
    for p in products:
        m = calc_metrics(p)
        if m:
            p.update(m)
            result.append(p)
    return result

# ==================== ТОПЫ ====================

def top_selling(products, n=10):
    """Самые продаваемые — по числу отзывов."""
    return sorted(products, key=lambda p: p.get('reviews', 0), reverse=True)[:n]

def top_rated(products, n=10):
    """Топ по рейтингу (10+ отзывов)."""
    filtered = [p for p in products if p.get('reviews', 0) >= 10]
    return sorted(filtered, key=lambda p: p.get('rating', 0), reverse=True)[:n]

def top_margin(products, n=10):
    """Топ по марже."""
    return sorted(products, key=lambda p: p.get('margin', 0), reverse=True)[:n]

def top_roi(products, n=10):
    """Топ по ROI."""
    return sorted(products, key=lambda p: p.get('roi', 0), reverse=True)[:n]

def problems(products):
    """Проблемные SKU."""
    margin_th = get_float('ALERTS', 'margin_threshold')
    rating_th = get_float('ALERTS', 'rating_threshold')
    discount_th = get_float('ALERTS', 'discount_threshold')
    
    low_margin = [p for p in products if p.get('margin', 0) < margin_th]
    low_rating = [p for p in products if p.get('rating', 0) < rating_th and p.get('reviews', 0) > 100]
    high_discount = [p for p in products if p.get('discount', 0) > discount_th]
    
    return {
        'low_margin': low_margin,
        'low_rating': low_rating,
        'high_discount': high_discount,
    }

def brand_summary(products):
    """Сводка по брендам."""
    brands = {}
    for p in products:
        b = p.get('brand', '—') or '—'
        brands.setdefault(b, []).append(p)
    
    result = []
    for brand, items in brands.items():
        margins = [i.get('margin', 0) for i in items]
        result.append({
            'brand': brand,
            'count': len(items),
            'avg_margin': round(sum(margins) / len(margins), 1) if margins else 0,
        })
    return sorted(result, key=lambda x: x['avg_margin'], reverse=True)

# ==================== КЛАВИАТУРЫ ====================

def main_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🔥 Самые продаваемые", callback_data="top_selling"),
        types.InlineKeyboardButton("⭐ Топ по рейтингу", callback_data="top_rated"),
        types.InlineKeyboardButton("💰 Топ по марже", callback_data="top_margin"),
        types.InlineKeyboardButton("📈 Топ по ROI", callback_data="top_roi"),
        types.InlineKeyboardButton("📊 Сводка по брендам", callback_data="brands"),
        types.InlineKeyboardButton("💹 Общая экономика", callback_data="economy"),
        types.InlineKeyboardButton("🚨 Проблемные SKU", callback_data="problems"),
    )
    return kb

def back_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="main"))
    return kb

# ==================== ЭКРАНЫ ====================

def screen_main(chat_id, msg_id=None):
    text = (
        "💎 <b>WB Jewelry Monitor</b>\n\n"
        "Демо-бот для анализа товаров WB.\n"
        "Данные — из Google Таблицы.\n"
        "Ключевые показатели: маржа, ROI, прибыль, скидка.\n\n"
        "Выберите отчёт:"
    )
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=main_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=main_kb())

def format_product_row(i, p):
    """Форматирует строку товара с показателями."""
    name = str(p.get('name', '—'))[:35]
    margin = p.get('margin', '—')
    profit = p.get('profit', '—')
    rating = p.get('rating', '—')
    reviews = p.get('reviews', 0)
    return (
        f"{i}. <b>{name}</b>\n"
        f"   ★{rating} | {reviews} отз. | маржа {margin}% | прибыль {profit} ₽\n"
    )

def screen_top_selling(chat_id, msg_id=None):
    products = enrich(load_products())
    top = top_selling(products, 10)
    
    text = "🔥 <b>Самые продаваемые</b>\n<i>(по числу отзывов)</i>\n\n"
    for i, p in enumerate(top, 1):
        text += format_product_row(i, p)
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

def screen_top_rated(chat_id, msg_id=None):
    products = enrich(load_products())
    top = top_rated(products, 10)
    
    text = "⭐ <b>Топ по рейтингу</b>\n<i>(при 10+ отзывах)</i>\n\n"
    for i, p in enumerate(top, 1):
        text += format_product_row(i, p)
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

def screen_top_margin(chat_id, msg_id=None):
    products = enrich(load_products())
    top = top_margin(products, 10)
    
    text = "💰 <b>Топ по марже</b>\n\n"
    for i, p in enumerate(top, 1):
        text += format_product_row(i, p)
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

def screen_top_roi(chat_id, msg_id=None):
    products = enrich(load_products())
    top = top_roi(products, 10)
    
    text = "📈 <b>Топ по ROI</b>\n<i>(окупаемость себестоимости)</i>\n\n"
    for i, p in enumerate(top, 1):
        name = str(p.get('name', '—'))[:35]
        roi = p.get('roi', '—')
        margin = p.get('margin', '—')
        profit = p.get('profit', '—')
        text += f"{i}. <b>{name}</b>\n"
        text += f"   ROI {roi}% | маржа {margin}% | прибыль {profit} ₽\n"
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

def screen_brands(chat_id, msg_id=None):
    products = enrich(load_products())
    summary = brand_summary(products)
    
    text = "📊 <b>Сводка по брендам</b>\n\n"
    for i, b in enumerate(summary[:15], 1):
        text += f"{i}. <b>{b['brand']}</b>\n"
        text += f"   SKU: {b['count']} | средняя маржа: {b['avg_margin']}%\n"
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

def screen_economy(chat_id, msg_id=None):
    products = enrich(load_products())
    
    if not products:
        text = "❌ Нет данных"
    else:
        total = len(products)
        profitable = sum(1 for p in products if p.get('profit', 0) > 0)
        unprofitable = total - profitable
        avg_margin = round(sum(p.get('margin', 0) for p in products) / total, 1)
        avg_roi = round(sum(p.get('roi', 0) for p in products) / total, 1)
        total_profit = round(sum(p.get('profit', 0) for p in products), 2)
        
        text = (
            f"💹 <b>Общая экономика</b>\n\n"
            f"<b>Ключевые показатели:</b>\n"
            f"• Всего SKU: <b>{total}</b>\n"
            f"• Прибыльных: <b>{profitable}</b>\n"
            f"• Убыточных: <b>{unprofitable}</b>\n\n"
            f"• Средняя маржа: <b>{avg_margin}%</b>\n"
            f"• Средний ROI: <b>{avg_roi}%</b>\n"
            f"• Суммарная прибыль: <b>{total_profit} ₽</b>\n\n"
            f"<i>Параметры расчёта:</i>\n"
            f"• Комиссия WB: {get_settings('ECONOMICS', 'commission_pct')}%\n"
            f"• Логистика: {get_settings('ECONOMICS', 'logistics')} ₽\n"
            f"• Хранение: {get_settings('ECONOMICS', 'storage')} ₽\n"
            f"• Себестоимость: {get_settings('ECONOMICS', 'cost_pct')}%"
        )
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

def screen_problems(chat_id, msg_id=None):
    products = enrich(load_products())
    pr = problems(products)
    
    total = sum(len(v) for v in pr.values())
    
    if total == 0:
        text = "✅ <b>Проблем не найдено</b>\n\nВсе SKU в норме."
    else:
        text = f"🚨 <b>Проблемные SKU</b> (всего: {total})\n\n"
        
        if pr['low_margin']:
            text += f"<b>💸 Низкая маржа</b> ({len(pr['low_margin'])}):\n"
            for p in pr['low_margin'][:5]:
                name = str(p.get('name', '—'))[:30]
                text += f"• {name}: {p.get('margin')}%\n"
            if len(pr['low_margin']) > 5:
                text += f"  ...и ещё {len(pr['low_margin']) - 5}\n"
            text += "\n"
        
        if pr['low_rating']:
            text += f"<b>⭐ Низкий рейтинг</b> ({len(pr['low_rating'])}):\n"
            for p in pr['low_rating'][:5]:
                name = str(p.get('name', '—'))[:30]
                text += f"• {name}: ★{p.get('rating')} ({p.get('reviews')} отз.)\n"
            if len(pr['low_rating']) > 5:
                text += f"  ...и ещё {len(pr['low_rating']) - 5}\n"
            text += "\n"
        
        if pr['high_discount']:
            text += f"<b>🔥 Большая скидка</b> ({len(pr['high_discount'])}):\n"
            for p in pr['high_discount'][:5]:
                name = str(p.get('name', '—'))[:30]
                text += f"• {name}: -{p.get('discount')}%\n"
            if len(pr['high_discount']) > 5:
                text += f"  ...и ещё {len(pr['high_discount']) - 5}\n"
    
    if msg_id:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode='html', reply_markup=back_kb())
    else:
        bot.send_message(chat_id, text, parse_mode='html', reply_markup=back_kb())

# ==================== ОБРАБОТЧИКИ ====================

@bot.message_handler(commands=['start'])
def cmd_start(message):
    screen_main(message.chat.id)

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    
    try:
        if call.data == "main":
            screen_main(chat_id, msg_id)
        elif call.data == "top_selling":
            screen_top_selling(chat_id, msg_id)
        elif call.data == "top_rated":
            screen_top_rated(chat_id, msg_id)
        elif call.data == "top_margin":
            screen_top_margin(chat_id, msg_id)
        elif call.data == "top_roi":
            screen_top_roi(chat_id, msg_id)
        elif call.data == "brands":
            screen_brands(chat_id, msg_id)
        elif call.data == "economy":
            screen_economy(chat_id, msg_id)
        elif call.data == "problems":
            screen_problems(chat_id, msg_id)
        else:
            bot.answer_callback_query(call.id, "Неизвестное действие")
            return
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f'Callback error: {e}')
        bot.answer_callback_query(call.id, f"Ошибка: {e}")

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    screen_main(message.chat.id)

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    logger.info('=== WB Jewelry Monitor запущен ===')
    bot.infinity_polling()

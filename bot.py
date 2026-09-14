# -*- coding: utf-8 -*-
"""
WB Jewelry Monitor — Telegram-бот для селлеров бижутерии на Wildberries.
Работает на открытых API WB (без кабинета продавца).
"""
import configparser
import json
import os
import time
import traceback
from datetime import datetime

import requests
from loguru import logger
import telebot

# ==================== НАСТРОЙКИ ====================

logger.add('log.log', format="{time} {level} {message}", level="INFO")

def get_settings(section, key):
    config = configparser.ConfigParser()
    config.read('settings.ini', encoding='utf-8')
    return config.get(section, key)

def get_settings_int(section, key):
    return int(get_settings(section, key))

bot = telebot.TeleBot(token=get_settings('TELEGRAM', 'token'))

# ==================== КОНСТАНТЫ WB ====================

# Маппинг городов (dest из WB API)
CITY_MAPPING = {
    "MSK": -445298,      # Москва
    "SPB": -1181900,     # Санкт-Петербург
    "EKB": -5818883,     # Екатеринбург
    "KZN": -2133462,     # Казань
    "KRY": 12358058,     # Краснодар
    "NSK": -364763,      # Новосибирск
}

# ==================== РАБОТА С API WB ====================

def search_wb(query, city="MSK", page=1):
    """
    Поиск товаров на Wildberries (публичный API).
    Возвращает список товаров.
    """
    dest = CITY_MAPPING.get(city.upper(), CITY_MAPPING["MSK"])
    
    url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
    params = {
        'appType': 1,
        'curr': 'rub',
        'dest': dest,
        'query': query,
        'resultset': 'catalog',
        'sort': 'popular',
        'spp': 30,
        'page': page,
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'ru-RU,ru;q=0.9',
    }
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get('data', {}).get('products', [])
    except Exception as e:
        logger.error(f'Ошибка поиска WB: {e}')
        return []

def find_position(article, query, city="MSK"):
    """Находит позицию артикула в поиске WB."""
    products = search_wb(query, city)
    for i, p in enumerate(products, 1):
        if str(p.get('id')) == str(article):
            return i
    return None

def get_product_info(article):
    """Получает информацию о товаре по артикулу (публичный API)."""
    url = f"https://card.wb.ru/cards/list?nm={article}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        products = data.get('data', {}).get('products', [])
        if products:
            return products[0]
        return None
    except Exception as e:
        logger.error(f'Ошибка получения товара: {e}')
        return None

# ==================== АНАЛИЗ ====================

def analyze_competitor_prices(article, query, city="MSK"):
    """
    Анализирует цены конкурентов в выдаче по запросу.
    Возвращает статистику.
    """
    products = search_wb(query, city)
    if not products:
        return None
    
    # Находим свой товар
    my_product = None
    my_pos = None
    for i, p in enumerate(products, 1):
        if str(p.get('id')) == str(article):
            my_product = p
            my_pos = i
            break
    
    if not my_product:
        return None
    
    # Собираем цены конкурентов
    prices = []
    for p in products:
        sale_price = p.get('salePriceU', 0) / 100
        if sale_price > 0:
            prices.append(sale_price)
    
    prices.sort()
    
    my_price = my_product.get('salePriceU', 0) / 100
    avg_price = sum(prices) / len(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0
    
    # Позиция по цене
    price_position = sum(1 for p in prices if p < my_price) + 1
    
    return {
        'my_price': round(my_price, 2),
        'my_position': my_pos,
        'avg_price': round(avg_price, 2),
        'min_price': round(min_price, 2),
        'max_price': round(max_price, 2),
        'price_position': price_position,
        'total_products': len(prices),
        'competitors': [
            {
                'id': p.get('id'),
                'brand': p.get('brand', '—'),
                'price': round(p.get('salePriceU', 0) / 100, 2),
                'rating': p.get('reviewRating', 0),
            }
            for p in products[:10]
        ]
    }

# ==================== TELEGRAM-БОТ ====================

@bot.message_handler(commands=['start'])
def cmd_start(message):
    """Приветствие и список команд."""
    bot.send_message(
        message.chat.id,
        "💎 <b>WB Jewelry Monitor</b>\n\n"
        "Бот для селлеров бижутерии на Wildberries.\n"
        "Работает на <b>открытых API</b> — без кабинета продавца.\n\n"
        "<b>Команды:</b>\n"
        "/position [артикул] [запрос] — позиция в поиске\n"
        "/price [артикул] [запрос] — анализ цен конкурентов\n"
        "/product [артикул] — информация о товаре\n"
        "/help — справка\n\n"
        "<i>Пример: /position 247759689 шапка бини</i>",
        parse_mode='html'
    )

@bot.message_handler(commands=['help'])
def cmd_help(message):
    """Справка по командам."""
    bot.send_message(
        message.chat.id,
        "📖 <b>Справка</b>\n\n"
        "<b>/position [артикул] [запрос]</b>\n"
        "Показывает позицию вашего SKU в поиске WB.\n"
        "Алерт, если позиция ниже порога.\n\n"
        "<b>/price [артикул] [запрос]</b>\n"
        "Сравнивает вашу цену с конкурентами:\n"
        "• Средняя, минимальная, максимальная цена\n"
        "• Ваше место по цене\n"
        "• Топ-10 конкурентов\n\n"
        "<b>/product [артикул]</b>\n"
        "Информация о товаре: название, бренд, цена, рейтинг.\n\n"
        "<i>Запрос — это поисковая фраза, по которой вы продвигаетесь.</i>",
        parse_mode='html'
    )

@bot.message_handler(commands=['position'])
def cmd_position(message):
    """Поиск позиции в выдаче."""
    args = message.text.split(maxsplit=2)
    
    if len(args) < 3:
        bot.send_message(
            message.chat.id,
            "⚠️ Укажите артикул и запрос.\n"
            "Пример: <code>/position 247759689 шапка бини</code>",
            parse_mode='html'
        )
        return
    
    article = args[1].strip()
    query = args[2].strip()
    
    bot.send_message(message.chat.id, f"🔍 Ищу позицию для SKU <b>{article}</b> по запросу «{query}»...", parse_mode='html')
    
    try:
        pos = find_position(article, query)
        
        if pos is None:
            bot.send_message(
                message.chat.id,
                f"❌ SKU <b>{article}</b> не найден в первых 100 товарах по запросу «{query}».\n"
                f"Возможно, товар на низких позициях или запрос непопулярный.",
                parse_mode='html'
            )
            return
        
        threshold = get_settings_int('ALERTS', 'position_threshold')
        
        # Формируем сообщение
        if pos <= 10:
            emoji = "🟢"
            status = "Отличная позиция!"
        elif pos <= threshold:
            emoji = "🟡"
            status = "Хорошая позиция"
        else:
            emoji = "🔴"
            status = f"⚠️ Позиция ниже порога ({threshold})"
        
        msg = (
            f"{emoji} <b>Позиция: {pos}</b>\n\n"
            f"SKU: <code>{article}</code>\n"
            f"Запрос: «{query}»\n"
            f"Статус: {status}"
        )
        
        bot.send_message(message.chat.id, msg, parse_mode='html')
        
    except Exception as e:
        logger.error(f'Ошибка в /position: {e}\n{traceback.format_exc()}')
        bot.send_message(message.chat.id, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['price'])
def cmd_price(message):
    """Анализ цен конкурентов."""
    args = message.text.split(maxsplit=2)
    
    if len(args) < 3:
        bot.send_message(
            message.chat.id,
            "⚠️ Укажите артикул и запрос.\n"
            "Пример: <code>/price 247759689 шапка бини</code>",
            parse_mode='html'
        )
        return
    
    article = args[1].strip()
    query = args[2].strip()
    
    bot.send_message(message.chat.id, f"📊 Анализирую цены по запросу «{query}»...", parse_mode='html')
    
    try:
        result = analyze_competitor_prices(article, query)
        
        if not result:
            bot.send_message(
                message.chat.id,
                f"❌ Не удалось найти SKU <b>{article}</b> в выдаче по запросу «{query}».",
                parse_mode='html'
            )
            return
        
        # Формируем сообщение
        msg = (
            f"💎 <b>Анализ цен</b>\n\n"
            f"<b>Ваш товар:</b>\n"
            f"SKU: <code>{article}</code>\n"
            f"Позиция: <b>{result['my_position']}</b>\n"
            f"Цена: <b>{result['my_price']} ₽</b>\n"
            f"Место по цене: <b>{result['price_position']}</b> из {result['total_products']}\n\n"
            f"<b>Рынок:</b>\n"
            f"Средняя: <b>{result['avg_price']} ₽</b>\n"
            f"Минимум: <b>{result['min_price']} ₽</b>\n"
            f"Максимум: <b>{result['max_price']} ₽</b>\n\n"
            f"<b>Топ-10 конкурентов:</b>\n"
        )
        
        for i, c in enumerate(result['competitors'], 1):
            msg += f"{i}. {c['brand']} — {c['price']} ₽ (★{c['rating']})\n"
        
        bot.send_message(message.chat.id, msg, parse_mode='html')
        
    except Exception as e:
        logger.error(f'Ошибка в /price: {e}\n{traceback.format_exc()}')
        bot.send_message(message.chat.id, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['product'])
def cmd_product(message):
    """Информация о товаре."""
    args = message.text.split()
    
    if len(args) < 2:
        bot.send_message(
            message.chat.id,
            "⚠️ Укажите артикул.\n"
            "Пример: <code>/product 247759689</code>",
            parse_mode='html'
        )
        return
    
    article = args[1].strip()
    
    bot.send_message(message.chat.id, f"🔍 Ищу информацию о SKU <b>{article}</b>...", parse_mode='html')
    
    try:
        product = get_product_info(article)
        
        if not product:
            bot.send_message(message.chat.id, f"❌ Товар <b>{article}</b> не найден.", parse_mode='html')
            return
        
        name = product.get('name', '—')
        brand = product.get('brand', '—')
        price = product.get('salePriceU', 0) / 100
        rating = product.get('reviewRating', 0)
        reviews = product.get('feedbacks', 0)
        
        msg = (
            f"📦 <b>Товар</b>\n\n"
            f"SKU: <code>{article}</code>\n"
            f"Название: <b>{name}</b>\n"
            f"Бренд: <b>{brand}</b>\n"
            f"Цена: <b>{price} ₽</b>\n"
            f"Рейтинг: <b>★{rating}</b> ({reviews} отзывов)"
        )
        
        bot.send_message(message.chat.id, msg, parse_mode='html')
        
    except Exception as e:
        logger.error(f'Ошибка в /product: {e}\n{traceback.format_exc()}')
        bot.send_message(message.chat.id, f"⚠️ Ошибка: {e}")

@bot.message_handler(func=lambda m: True)
def echo_all(message):
    """Ответ на неизвестные команды."""
    bot.send_message(
        message.chat.id,
        "🤖 Неизвестная команда.\n"
        "Используйте /help для списка команд."
    )

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    logger.info('=== WB Jewelry Monitor запущен ===')
    bot.infinity_polling()
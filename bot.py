# -*- coding: utf-8 -*-
"""
WB Demo — Telegram-бот мониторинга FBS.
Читает токен и настройки из переменных окружения (для BotHost).

Переменные окружения:
- TELEGRAM_BOT_TOKEN — токен от @BotFather
- ADMIN_ID — твой chat_id (узнать: @userinfobot)
- DATABASE_URL — строка подключения к PostgreSQL
"""
import os
import telebot
from telebot import types
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import threading
import time
import traceback

# ==================== НАСТРОЙКИ ИЗ ENV ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:1234@localhost:5432/wb_demo"
)
PENALTY_RUB = 500
TARGET_OVERDUE_PCT = 2.0
ALERT_INTERVAL_MIN = 30
DAILY_HOUR = 9

if not TOKEN:
    print("❌ Не задан TELEGRAM_BOT_TOKEN в переменных окружения!")
    exit(1)

engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")


# ==================== БД ====================
def q(sql, params=None):
    with engine.connect() as c:
        return pd.read_sql(text(sql), c, params=params or {})


def scalar(sql, params=None):
    with engine.connect() as c:
        return c.execute(text(sql), params or {}).scalar()


def fmt(n):
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)


def fmt_money(n):
    return f"{fmt(n)} ₽"


# ==================== КЛАВИАТУРЫ ====================
def reply_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("📊 Сводка"), types.KeyboardButton("🚨 Горящие"))
    kb.add(types.KeyboardButton("📉 Просрочки"), types.KeyboardButton("🔥 Топ SKU"))
    kb.add(types.KeyboardButton("💰 Деньги"), types.KeyboardButton("📦 Склад"))
    kb.add(types.KeyboardButton("🔄 Возвраты"), types.KeyboardButton("👥 Клиенты"))
    kb.add(types.KeyboardButton("📍 ПВЗ"), types.KeyboardButton("💾 Экономия"))
    kb.add(types.KeyboardButton("📅 По дням"), types.KeyboardButton("❓ Помощь"))
    return kb


def back_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 В меню", callback_data="back"))
    return kb


# ==================== МЕТРИКИ ====================
def get_metrics():
    row = q("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE assembled_at IS NOT NULL) AS assembled,
            COUNT(*) FILTER (WHERE assembled_at > deadline) AS overdue,
            COUNT(*) FILTER (WHERE assembled_at IS NULL AND deadline < NOW()) AS burning,
            COUNT(*) FILTER (WHERE status = 'new') AS new_orders,
            COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
            COUNT(*) FILTER (WHERE status = 'returned') AS returned
        FROM orders
    """).iloc[0]
    total = int(row["total"])
    assembled = int(row["assembled"])
    overdue = int(row["overdue"])
    pct = round(100.0 * overdue / assembled, 2) if assembled else 0.0
    penalty = overdue * PENALTY_RUB
    target_count = assembled * TARGET_OVERDUE_PCT / 100.0
    saved_month = max(0, overdue - target_count) * PENALTY_RUB
    return {
        "total": total, "assembled": assembled, "overdue": overdue,
        "pct": pct, "penalty": penalty,
        "saved_month": saved_month, "saved_year": saved_month * 12,
        "burning": int(row["burning"]), "new": int(row["new_orders"]),
        "cancelled": int(row["cancelled"]), "returned": int(row["returned"]),
    }


# ==================== КОМАНДЫ ====================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(message):
    try:
        m = get_metrics()
        txt = (
            "💎 <b>WB Demo — мониторинг FBS</b>\n\n"
            f"Заказов: <b>{fmt(m['total'])}</b>\n"
            f"Просрочек: <b>{fmt(m['overdue'])}</b> ({m['pct']}%)\n"
            f"Горящих сейчас: <b>{fmt(m['burning'])}</b>\n\n"
            "Кнопки меню — внизу экрана."
        )
        bot.send_message(message.chat.id, txt, reply_markup=reply_menu())
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Ошибка: <code>{e}</code>")


@bot.message_handler(commands=["help"])
def cmd_help(message):
    txt = (
        "📖 <b>Помощь</b>\n\n"
        "<b>📊 Сводка</b> — ключевые метрики\n"
        "<b>🚨 Горящие</b> — заказы с истёкшим дедлайном\n"
        "<b>📉 Просрочки</b> — по ПВЗ\n"
        "<b>🔥 Топ SKU</b> — топ-10 по % просрочек\n"
        "<b>📅 По дням</b> — динамика за 14 дней\n"
        "<b>💰 Деньги</b> — выручка, маржа, штрафы\n"
        "<b>📦 Склад</b> — дефицит\n"
        "<b>🔄 Возвраты</b> — доля и причины\n"
        "<b>👥 Клиенты</b> — сегменты, регионы\n"
        "<b>📍 ПВЗ</b> — рейтинг и загрузка\n"
        "<b>💾 Экономия</b> — потенциал\n\n"
        "Команды: /start /menu /help /daily /burning /overdue /money"
    )
    bot.send_message(message.chat.id, txt, reply_markup=reply_menu())


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    try:
        handlers = {
            "daily": show_daily, "burning": show_burning,
            "overdue": show_overdue, "sku": show_sku,
            "daily_chart": show_daily_chart, "money": show_money,
            "stock": show_stock, "returns": show_returns,
            "customers": show_customers, "pvz": show_pvz,
            "saved": show_saved,
        }
        if call.data == "back":
            m = get_metrics()
            bot.edit_message_text(
                f"📋 <b>Главное меню</b>\n\nПросрочек: <b>{fmt(m['overdue'])}</b> ({m['pct']}%)",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
            )
        elif call.data == "help":
            cmd_help(call.message)
        elif call.data in handlers:
            handlers[call.data](call.message)
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка")
        bot.send_message(call.message.chat.id, f"⚠️ <code>{e}</code>")


@bot.message_handler(func=lambda m: m.text in (
    "📊 Сводка", "🚨 Горящие", "📉 Просрочки", "🔥 Топ SKU",
    "💰 Деньги", "📦 Склад", "🔄 Возвраты", "👥 Клиенты",
    "📍 ПВЗ", "💾 Экономия", "📅 По дням", "❓ Помощь",
))
def on_reply_button(message):
    try:
        mapping = {
            "📊 Сводка": show_daily, "🚨 Горящие": show_burning,
            "📉 Просрочки": show_overdue, "🔥 Топ SKU": show_sku,
            "💰 Деньги": show_money, "📦 Склад": show_stock,
            "🔄 Возвраты": show_returns, "👥 Клиенты": show_customers,
            "📍 ПВЗ": show_pvz, "💾 Экономия": show_saved,
            "📅 По дням": show_daily_chart, "❓ Помощь": cmd_help,
        }
        fn = mapping.get(message.text)
        if fn:
            fn(message)
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Ошибка: <code>{e}</code>")


@bot.message_handler(commands=["daily"])
def c_daily(m): show_daily(m)

@bot.message_handler(commands=["burning"])
def c_burning(m): show_burning(m)

@bot.message_handler(commands=["overdue"])
def c_overdue(m): show_overdue(m)

@bot.message_handler(commands=["money"])
def c_money(m): show_money(m)


# ==================== ЭКРАНЫ ====================
def show_daily(message):
    m = get_metrics()
    txt = (
        "📊 <b>Ключевые метрики</b>\n\n"
        f"Всего заказов: <b>{fmt(m['total'])}</b>\n"
        f"Собрано: <b>{fmt(m['assembled'])}</b>\n"
        f"Просрочено: <b>{fmt(m['overdue'])}</b> ({m['pct']}%)\n"
        f"Горящих сейчас: <b>{fmt(m['burning'])}</b>\n\n"
        f"Новых: {fmt(m['new'])}\n"
        f"Отменено: {fmt(m['cancelled'])}\n"
        f"Возвратов: {fmt(m['returned'])}\n\n"
        f"Штрафы/мес: <b>{fmt_money(m['penalty'])}</b>\n"
        f"Цель: {TARGET_OVERDUE_PCT}% просрочек"
    )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_burning(message):
    df = q("""
        SELECT o.wb_order_id, s.sku_code, o.pvz, o.deadline,
               round((EXTRACT(EPOCH FROM (NOW() - o.deadline))/3600.0)::numeric, 1) AS hours_late
        FROM orders o
        LEFT JOIN skus s ON s.sku_id = o.sku_id
        WHERE o.assembled_at IS NULL AND o.deadline < NOW()
        ORDER BY o.deadline
        LIMIT 25
    """)
    if df.empty:
        bot.send_message(message.chat.id, "✅ Горящих заказов нет", reply_markup=back_menu())
        return
    txt = f"🚨 <b>Горящие заказы ({len(df)})</b>\n\n"
    for _, r in df.iterrows():
        txt += (
            f"• <code>{r['wb_order_id']}</code> / {r['sku_code'] or '—'}\n"
            f"   {r['pvz']} · опоздание <b>{r['hours_late']} ч</b>\n"
        )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_overdue(message):
    df = q("""
        SELECT pvz,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE assembled_at > deadline) AS overdue,
               round((100.0 * COUNT(*) FILTER (WHERE assembled_at > deadline)
                     / NULLIF(COUNT(*), 0))::numeric, 2) AS pct
        FROM orders WHERE assembled_at IS NOT NULL
        GROUP BY pvz ORDER BY pct DESC
    """)
    txt = "📉 <b>Просрочки по ПВЗ</b>\n\n"
    for _, r in df.iterrows():
        emo = "🔴" if r["pct"] >= 15 else ("🟡" if r["pct"] >= 10 else "🟢")
        txt += (
            f"{emo} <b>{r['pvz']}</b>\n"
            f"   Всего: {int(r['total'])} · Просрочек: <b>{int(r['overdue'])}</b> ({r['pct']}%)\n\n"
        )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_sku(message):
    df = q("""
        SELECT s.sku_code, s.category,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE o.assembled_at > o.deadline) AS overdue,
               round((100.0 * COUNT(*) FILTER (WHERE o.assembled_at > o.deadline)
                     / NULLIF(COUNT(*), 0))::numeric, 2) AS pct
        FROM orders o JOIN skus s ON s.sku_id = o.sku_id
        WHERE o.assembled_at IS NOT NULL
        GROUP BY s.sku_code, s.category
        HAVING COUNT(*) >= 10 ORDER BY pct DESC LIMIT 10
    """)
    txt = "🔥 <b>Топ-10 SKU по % просрочек</b>\n\n"
    for i, (_, r) in enumerate(df.iterrows(), 1):
        txt += (
            f"{i}. <code>{r['sku_code']}</code> — {r['category']}\n"
            f"   <b>{r['pct']}%</b> ({int(r['overdue'])}/{int(r['total'])})\n\n"
        )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_daily_chart(message):
    df = q("""
        SELECT DATE(created_at) AS day, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE assembled_at > deadline) AS overdue
        FROM orders WHERE assembled_at IS NOT NULL
          AND created_at >= NOW() - INTERVAL '14 days'
        GROUP BY DATE(created_at) ORDER BY day
    """)
    if df.empty:
        bot.send_message(message.chat.id, "Нет данных", reply_markup=back_menu())
        return
    txt = "📅 <b>Последние 14 дней</b>\n\n<pre>"
    for _, r in df.iterrows():
        bar = "█" * min(int(r["overdue"] / 2) + 1, 25)
        txt += f"{str(r['day'])[5:]}  {int(r['overdue']):>3}  {bar}\n"
    txt += "</pre>"
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_money(message):
    df = q("""
        SELECT COALESCE(SUM(revenue), 0) AS revenue,
               COALESCE(SUM(commission), 0) AS commission,
               COALESCE(SUM(logistics_cost), 0) AS logistics,
               COALESCE(SUM(ads_cost), 0) AS ads,
               COALESCE(SUM(penalty), 0) AS penalty,
               COALESCE(SUM(profit), 0) AS profit
        FROM finance
    """).iloc[0]
    margin = round(100.0 * df["profit"] / df["revenue"], 2) if df["revenue"] else 0
    txt = (
        "💰 <b>Деньги</b>\n\n"
        f"Выручка: <b>{fmt_money(df['revenue'])}</b>\n"
        f"Комиссия WB: {fmt_money(df['commission'])}\n"
        f"Логистика: {fmt_money(df['logistics'])}\n"
        f"Реклама: {fmt_money(df['ads'])}\n"
        f"Штрафы: {fmt_money(df['penalty'])}\n\n"
        f"Прибыль: <b>{fmt_money(df['profit'])}</b>\n"
        f"Маржа: <b>{margin}%</b>"
    )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_stock(message):
    df = q("""
        SELECT s.sku_code, s.category,
               COALESCE(SUM(st.qty), 0) AS stock,
               COUNT(DISTINCT o.order_id) AS orders_pending
        FROM skus s
        LEFT JOIN stocks st ON st.sku_id = s.sku_id
        LEFT JOIN orders o ON o.sku_id = s.sku_id
            AND o.assembled_at IS NULL AND o.status = 'new'
        GROUP BY s.sku_code, s.category
        HAVING COUNT(DISTINCT o.order_id) > 0 OR COALESCE(SUM(st.qty), 0) = 0
        ORDER BY orders_pending DESC, stock ASC LIMIT 15
    """)
    if df.empty:
        bot.send_message(message.chat.id, "✅ Дефицита нет", reply_markup=back_menu())
        return
    txt = "📦 <b>Дефицит / ожидаемые заказы</b>\n\n"
    for _, r in df.iterrows():
        txt += (
            f"• <code>{r['sku_code']}</code> ({r['category']})\n"
            f"   Остаток: <b>{int(r['stock'])}</b> · Ждут: {int(r['orders_pending'])}\n"
        )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_returns(message):
    m = q("""
        SELECT (SELECT COUNT(*) FROM orders) AS total,
               (SELECT COUNT(*) FROM returns) AS returns
    """).iloc[0]
    pct = round(100.0 * m["returns"] / m["total"], 2) if m["total"] else 0
    top = q("SELECT reason, COUNT(*) AS cnt FROM returns GROUP BY reason ORDER BY cnt DESC")
    txt = (
        "🔄 <b>Возвраты</b>\n\n"
        f"Всего: <b>{fmt(m['returns'])}</b> из {fmt(m['total'])} ({pct}%)\n\n"
        "<b>Причины:</b>\n"
    )
    for _, r in top.iterrows():
        txt += f"• {r['reason']}: {int(r['cnt'])}\n"
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_customers(message):
    seg = q("SELECT segment, COUNT(*) AS cnt FROM customers GROUP BY segment ORDER BY cnt DESC")
    reg = q("SELECT region, COUNT(*) AS cnt FROM customers GROUP BY region ORDER BY cnt DESC")
    txt = "👥 <b>Клиенты</b>\n\n<b>Сегменты:</b>\n"
    for _, r in seg.iterrows():
        txt += f"• {r['segment']}: {int(r['cnt'])}\n"
    txt += "\n<b>Регионы:</b>\n"
    for _, r in reg.iterrows():
        txt += f"• {r['region']}: {int(r['cnt'])}\n"
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_pvz(message):
    df = q("""
        SELECT pvz, region, rating, reviews, avg_delivery_hours, load_pct
        FROM pvz_ratings ORDER BY rating DESC
    """)
    txt = "📍 <b>ПВЗ</b>\n\n"
    for _, r in df.iterrows():
        emo = "🟢" if r["rating"] >= 4.5 else ("🟡" if r["rating"] >= 4.0 else "🔴")
        txt += (
            f"{emo} <b>{r['pvz']}</b> ({r['region']})\n"
            f"   Рейтинг: {r['rating']} · Отзывов: {int(r['reviews'])}\n"
            f"   Доставка: {r['avg_delivery_hours']} ч · Загрузка: {r['load_pct']}%\n\n"
        )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


def show_saved(message):
    m = get_metrics()
    txt = (
        "💾 <b>Потенциал экономии</b>\n\n"
        f"Сейчас просрочек: <b>{fmt(m['overdue'])}</b> ({m['pct']}%)\n"
        f"Штрафы: <b>{fmt_money(m['penalty'])}</b>\n\n"
        f"Цель: <b>{TARGET_OVERDUE_PCT}%</b>\n"
        f"Экономия/мес: <b>{fmt_money(m['saved_month'])}</b>\n"
        f"Экономия/год: <b>{fmt_money(m['saved_year'])}</b>\n\n"
        "Способ: автоматические напоминания сборщикам\n"
        "за 2 часа до дедлайна + эскалация менеджеру."
    )
    bot.send_message(message.chat.id, txt, reply_markup=back_menu())


# ==================== ФОНОВЫЕ АЛЕРТЫ ====================
_last_alert = {"burning": None, "overdue_pct": None, "returns_pct": None}


def send_admin(text):
    if not ADMIN_ID:
        return
    try:
        bot.send_message(ADMIN_ID, text)
    except Exception:
        pass


def check_alerts():
    try:
        m = get_metrics()
        if m["burning"] > 10:
            key = ("burning", m["burning"])
            if _last_alert["burning"] != key:
                send_admin(f"🚨 <b>Алерт: {m['burning']} горящих</b>\n\nОткрой /burning")
                _last_alert["burning"] = key
        else:
            _last_alert["burning"] = None

        if m["pct"] > 12:
            if _last_alert["overdue_pct"] != round(m["pct"], 1):
                send_admin(f"📉 <b>Алерт: просрочки {m['pct']}%</b>\n\nШтрафы: {fmt_money(m['penalty'])}")
                _last_alert["overdue_pct"] = round(m["pct"], 1)
        else:
            _last_alert["overdue_pct"] = None

        ret_pct = round(100.0 * m["returned"] / m["total"], 2) if m["total"] else 0
        if ret_pct > 18:
            if _last_alert["returns_pct"] != round(ret_pct, 1):
                send_admin(f"🔄 <b>Алерт: возвраты {ret_pct}%</b>")
                _last_alert["returns_pct"] = round(ret_pct, 1)
        else:
            _last_alert["returns_pct"] = None
    except Exception as e:
        print(f"[alert] {e}")


def scheduler_loop():
    last_daily_date = None
    while True:
        try:
            now = datetime.now()
            check_alerts()
            if now.hour == DAILY_HOUR and last_daily_date != now.date():
                m = get_metrics()
                send_admin(
                    f"☀️ <b>Утренняя сводка {now.strftime('%d.%m.%Y')}</b>\n\n"
                    f"Заказов: {fmt(m['total'])}\n"
                    f"Просрочек: <b>{fmt(m['overdue'])}</b> ({m['pct']}%)\n"
                    f"Горящих: <b>{fmt(m['burning'])}</b>\n"
                    f"Штрафы: {fmt_money(m['penalty'])}"
                )
                last_daily_date = now.date()
        except Exception as e:
            print(f"[scheduler] {e}")
        time.sleep(ALERT_INTERVAL_MIN * 60)


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("=" * 60)
    print("WB Demo Bot — запуск")
    print("=" * 60)

    try:
        n = scalar("SELECT COUNT(*) FROM orders")
        print(f"✅ БД подключена. Заказов: {n}")
    except Exception as e:
        print(f"❌ Ошибка БД: {e}")
        print("   Проверь DATABASE_URL")
        exit(1)

    if ADMIN_ID:
        print(f"✅ ADMIN_ID: {ADMIN_ID}")
    else:
        print("⚠️ ADMIN_ID не задан")

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    print(f"✅ Планировщик запущен")
    print("📱 Открой Telegram и напиши /start")
    print("=" * 60)

    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"[polling] error: {e}")
            time.sleep(5)

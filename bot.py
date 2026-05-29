"""
Telegram бот для химчистки — поиск клиентов и история заказов.
Бот: @toptozazakaz_bot
"""
import logging
import sqlite3
from datetime import datetime, date

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ─── Настройки ────────────────────────────────────────────────────────────────
BOT_TOKEN = "8821892651:AAHRegPFRHwHKJJO147UrF5lN6cz6MKtAjI"   # ← получить у @BotFather
DB_FILE   = "clients.db"

# Цены за услуги (сом)
PRICES = {"km": 12, "os": 40, "sk": 30, "ps": 70}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Состояния диалогов ───────────────────────────────────────────────────────
SEARCH_INPUT = 10
ADD_PHONE, ADD_ADDR, ADD_DATE, ADD_KM, ADD_OS, ADD_SK, ADD_PS, ADD_COMMENT = range(20, 28)

# ─── База данных ──────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            phone   TEXT NOT NULL,
            addr    TEXT DEFAULT '',
            date    TEXT DEFAULT '',
            itogo   REAL DEFAULT 0,
            km      REAL DEFAULT 0,
            os      INTEGER DEFAULT 0,
            sk      REAL DEFAULT 0,
            ps      INTEGER DEFAULT 0,
            comment TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_phone ON orders(phone);
    """)
    conn.commit()
    conn.close()

def search_by_phone(query: str):
    conn = get_conn()
    q = f"%{query.replace('+', '')}%"
    rows = conn.execute(
        "SELECT * FROM orders WHERE phone LIKE ? ORDER BY date DESC", (q,)
    ).fetchall()
    conn.close()
    return rows

def get_client_history(phone: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orders WHERE phone=? ORDER BY date DESC", (phone,)
    ).fetchall()
    conn.close()
    return rows

def add_order(phone, addr, date_str, itogo, km, os_, sk, ps, comment):
    conn = get_conn()
    conn.execute(
        "INSERT INTO orders (phone,addr,date,itogo,km,os,sk,ps,comment) VALUES (?,?,?,?,?,?,?,?,?)",
        (phone, addr, date_str, itogo, km, os_, sk, ps, comment)
    )
    conn.commit()
    conn.close()

def db_total_count():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    return n

# ─── Форматирование ───────────────────────────────────────────────────────────
def fmt(v):
    return f"{v:,.0f}".replace(",", " ") if v else "0"

def fmt_tags(row):
    tags = []
    if row["km"]: tags.append(f"🟦 Ковёр {row['km']}м²")
    if row["os"]: tags.append(f"🟩 Одеяло {row['os']}шт")
    if row["sk"]: tags.append(f"🟨 Шторы {row['sk']}кг")
    if row["ps"]: tags.append(f"🟥 Плед {row['ps']}шт")
    return "  ".join(tags) if tags else ""

def build_client_card(rows) -> str:
    phone = rows[0]["phone"]
    addrs = list(dict.fromkeys(r["addr"] for r in rows if r["addr"]))
    total = sum(r["itogo"] or 0 for r in rows)
    dates = sorted(r["date"] for r in rows if r["date"])

    freq = "—"
    if len(dates) > 1:
        try:
            gaps = [(datetime.strptime(dates[i], "%Y-%m-%d") - datetime.strptime(dates[i-1], "%Y-%m-%d")).days
                    for i in range(1, len(dates))]
            avg = round(sum(gaps) / len(gaps))
            if avg < 14:    freq = f"каждые {avg} дн."
            elif avg < 60:  freq = f"каждые {avg//7} нед."
            elif avg < 365: freq = f"каждые {avg//30} мес."
            else:            freq = f"раз в {avg//365} год"
        except:
            pass

    return (
        f"📞 *+{phone}*\n"
        f"🏠 {addrs[0] if addrs else '—'}\n\n"
        f"📦 Заказов:    *{len(rows)}*\n"
        f"💰 Сумма:      *{fmt(total)} сом*\n"
        f"🔄 Частота:    {freq}\n"
        f"📅 Первый:     {dates[0] if dates else '—'}\n"
        f"📅 Последний:  {dates[-1] if dates else '—'}"
    )

def build_order_list(rows, limit=10) -> str:
    lines = [f"*📋 История ({len(rows)} заказов):*\n"]
    for r in list(rows)[:limit]:
        line = f"`{r['date'] or '—'}` — *{fmt(r['itogo'])} сом*"
        tags = fmt_tags(r)
        if tags: line += f"\n   {tags}"
        if r["addr"]: line += f"\n   📍 {r['addr']}"
        if r["comment"]: line += f"\n   💬 {r['comment']}"
        lines.append(line + "\n")
    if len(rows) > limit:
        lines.append(f"_...ещё {len(rows)-limit} заказов_")
    return "\n".join(lines)

# ─── Меню ─────────────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup(
    [["🔍 Найти клиента", "➕ Добавить заказ"], ["📊 Статистика"]],
    resize_keyboard=True,
)

# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🧺 *Химчистка — база клиентов*\n\nВ базе: *{db_total_count():,}* записей",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )

# ─── Статистика ───────────────────────────────────────────────────────────────
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    total_ord = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    total_sum = conn.execute("SELECT COALESCE(SUM(itogo),0) FROM orders").fetchone()[0]
    uniq      = conn.execute("SELECT COUNT(DISTINCT phone) FROM orders").fetchone()[0]
    top5      = conn.execute("""
        SELECT phone, COUNT(*) cnt, SUM(itogo) total
        FROM orders GROUP BY phone ORDER BY total DESC LIMIT 5
    """).fetchall()
    conn.close()

    top_lines = "\n".join(
        f"  {i+1}. +{r['phone']} — {fmt(r['total'])} сом ({r['cnt']} зак.)"
        for i, r in enumerate(top5)
    )
    avg = total_sum / total_ord if total_ord else 0
    await update.message.reply_text(
        f"📊 *Статистика базы*\n\n"
        f"📦 Всего заказов: *{total_ord:,}*\n"
        f"👤 Уник. клиентов: *{uniq:,}*\n"
        f"💰 Общая выручка: *{fmt(total_sum)} сом*\n"
        f"📈 Средний чек: *{fmt(avg)} сом*\n\n"
        f"🏆 *Топ-5 по сумме:*\n{top_lines}",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )

# ─── Поиск ────────────────────────────────────────────────────────────────────
async def search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 Введите номер телефона (или часть номера):",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SEARCH_INPUT

async def search_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().replace("+", "").replace(" ", "")
    if len(query) < 3:
        await update.message.reply_text("⚠️ Минимум 3 цифры. Попробуйте ещё раз:")
        return SEARCH_INPUT

    rows = search_by_phone(query)
    if not rows:
        await update.message.reply_text(f"❌ Клиент «{query}» не найден.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    phones = list(dict.fromkeys(r["phone"] for r in rows))

    if len(phones) == 1:
        await _show_client_msg(update.message, phones[0])
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(f"📞 +{p}", callback_data=f"cl:{p}")] for p in phones[:10]]
    await update.message.reply_text(
        f"Найдено: *{len(phones)}* клиентов. Выберите:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns),
    )
    return ConversationHandler.END

async def search_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KB)
    return ConversationHandler.END

async def on_client_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    phone = q.data.split(":", 1)[1]
    rows = get_client_history(phone)
    if not rows:
        await q.edit_message_text("❌ Данные не найдены.")
        return
    card = build_client_card(rows)
    hist = build_order_list(rows)
    await q.edit_message_text(card, parse_mode="Markdown")
    await q.message.reply_text(hist, parse_mode="Markdown", reply_markup=MAIN_KB)

async def _show_client_msg(message, phone: str):
    rows = get_client_history(phone)
    if not rows:
        await message.reply_text("❌ Клиент не найден.", reply_markup=MAIN_KB)
        return
    await message.reply_text(build_client_card(rows), parse_mode="Markdown")
    await message.reply_text(build_order_list(rows), parse_mode="Markdown", reply_markup=MAIN_KB)

# ─── Добавить заказ ───────────────────────────────────────────────────────────
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "➕ *Новый заказ — Шаг 1/7*\n\n📞 Телефон клиента:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_PHONE

async def a_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    p = update.message.text.strip().replace("+", "").replace(" ", "")
    if len(p) < 5:
        await update.message.reply_text("⚠️ Слишком короткий номер. Введите ещё раз:")
        return ADD_PHONE
    ctx.user_data["phone"] = p
    ex = get_client_history(p)
    if ex:
        await update.message.reply_text(
            f"ℹ️ Клиент *+{p}* уже в базе: {len(ex)} зак., {fmt(sum(r['itogo'] or 0 for r in ex))} сом",
            parse_mode="Markdown",
        )
    await update.message.reply_text("🏠 *Шаг 2/7* — Адрес (или /skip):", parse_mode="Markdown")
    return ADD_ADDR

async def a_addr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["addr"] = update.message.text.strip()
    await update.message.reply_text(f"📅 *Шаг 3/7* — Дата `ГГГГ-ММ-ДД` (или /skip = сегодня):", parse_mode="Markdown")
    return ADD_DATE

async def a_addr_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["addr"] = ""
    await update.message.reply_text(f"📅 *Шаг 3/7* — Дата `ГГГГ-ММ-ДД` (или /skip = сегодня):", parse_mode="Markdown")
    return ADD_DATE

async def a_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = update.message.text.strip()
    try:
        datetime.strptime(d, "%Y-%m-%d")
        ctx.user_data["date"] = d
    except:
        await update.message.reply_text("⚠️ Формат: `2025-01-15`", parse_mode="Markdown")
        return ADD_DATE
    await update.message.reply_text("🟦 *Шаг 4/7* — Ковёр м² (или /skip):", parse_mode="Markdown")
    return ADD_KM

async def a_date_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["date"] = str(date.today())
    await update.message.reply_text("🟦 *Шаг 4/7* — Ковёр м² (или /skip):", parse_mode="Markdown")
    return ADD_KM

async def _num(update, ctx, key, cast, next_state, next_msg):
    try:
        ctx.user_data[key] = cast(update.message.text.strip())
    except:
        await update.message.reply_text("⚠️ Введите число:")
        return next_state - 1  # retry same state
    await update.message.reply_text(next_msg, parse_mode="Markdown")
    return next_state

async def a_km(update, ctx):      return await _num(update, ctx, "km", float, ADD_OS, "🟩 *Шаг 5/7* — Одеяло шт (или /skip):")
async def a_km_skip(update, ctx): ctx.user_data["km"]=0; await update.message.reply_text("🟩 *Шаг 5/7* — Одеяло шт (или /skip):", parse_mode="Markdown"); return ADD_OS
async def a_os(update, ctx):      return await _num(update, ctx, "os", int,   ADD_SK, "🟨 *Шаг 6/7* — Шторы кг (или /skip):")
async def a_os_skip(update, ctx): ctx.user_data["os"]=0; await update.message.reply_text("🟨 *Шаг 6/7* — Шторы кг (или /skip):", parse_mode="Markdown"); return ADD_SK
async def a_sk(update, ctx):      return await _num(update, ctx, "sk", float, ADD_PS, "🟥 *Шаг 7/7* — Плед шт (или /skip):")
async def a_sk_skip(update, ctx): ctx.user_data["sk"]=0; await update.message.reply_text("🟥 *Шаг 7/7* — Плед шт (или /skip):", parse_mode="Markdown"); return ADD_PS
async def a_ps(update, ctx):      return await _num(update, ctx, "ps", int,   ADD_COMMENT, "💬 Комментарий (или /skip):")
async def a_ps_skip(update, ctx): ctx.user_data["ps"]=0; await update.message.reply_text("💬 Комментарий (или /skip):"); return ADD_COMMENT

async def a_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["comment"] = update.message.text.strip()
    return await _do_save(update, ctx)

async def a_comment_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["comment"] = ""
    return await _do_save(update, ctx)

async def _do_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.user_data
    km, os_, sk, ps = d.get("km",0), d.get("os",0), d.get("sk",0), d.get("ps",0)
    itogo = km*PRICES["km"] + os_*PRICES["os"] + sk*PRICES["sk"] + ps*PRICES["ps"]
    add_order(d.get("phone",""), d.get("addr",""), d.get("date", str(date.today())),
              itogo, km, os_, sk, ps, d.get("comment",""))

    tags = []
    if km:  tags.append(f"🟦 Ковёр {km}м²")
    if os_: tags.append(f"🟩 Одеяло {os_}шт")
    if sk:  tags.append(f"🟨 Шторы {sk}кг")
    if ps:  tags.append(f"🟥 Плед {ps}шт")

    await update.message.reply_text(
        f"✅ *Заказ сохранён!*\n\n"
        f"📞 +{d.get('phone')}\n"
        f"📅 {d.get('date')}\n"
        f"{'  '.join(tags) or '—'}\n"
        f"💰 *Итого: {fmt(itogo)} сом*",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )
    return ConversationHandler.END

async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ─── Запуск ───────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 Найти клиента$"), search_start)],
        states={SEARCH_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_do)]},
        fallbacks=[CommandHandler("cancel", search_cancel)],
    )

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить заказ$"), add_start)],
        states={
            ADD_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, a_phone)],
            ADD_ADDR:    [CommandHandler("skip", a_addr_skip),    MessageHandler(filters.TEXT & ~filters.COMMAND, a_addr)],
            ADD_DATE:    [CommandHandler("skip", a_date_skip),    MessageHandler(filters.TEXT & ~filters.COMMAND, a_date)],
            ADD_KM:      [CommandHandler("skip", a_km_skip),      MessageHandler(filters.TEXT & ~filters.COMMAND, a_km)],
            ADD_OS:      [CommandHandler("skip", a_os_skip),      MessageHandler(filters.TEXT & ~filters.COMMAND, a_os)],
            ADD_SK:      [CommandHandler("skip", a_sk_skip),      MessageHandler(filters.TEXT & ~filters.COMMAND, a_sk)],
            ADD_PS:      [CommandHandler("skip", a_ps_skip),      MessageHandler(filters.TEXT & ~filters.COMMAND, a_ps)],
            ADD_COMMENT: [CommandHandler("skip", a_comment_skip), MessageHandler(filters.TEXT & ~filters.COMMAND, a_comment)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), stats))
    app.add_handler(CallbackQueryHandler(on_client_button, pattern=r"^cl:"))
    app.add_handler(search_conv)
    app.add_handler(add_conv)

    logger.info("🧺 Бот @toptozazakaz_bot запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

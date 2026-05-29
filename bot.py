"""
Telegram бот для химчистки — @toptozazakaz_bot
Структура Excel:
A=Дата, B=Телефон+Адрес, C=Қолин адад, D=м/кв, E=маблаг ковёр,
F=Одеяло адад, G=маблаг одеяло, H=Парда кг, I=маблаг парда,
J=Курпача маблаг, K=Итого умуми
"""
import logging, io, re, os
import psycopg2
import psycopg2.extras
from datetime import datetime, date

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PRICES    = {"km": 12, "os": 50, "sk": 30, "ps": 70}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

SEARCH_INPUT = 10
ADD_PHONE, ADD_ADDR, ADD_DATE, ADD_KM, ADD_OS, ADD_SK, ADD_PS, ADD_COMMENT = range(20, 28)
EDIT_VALUE = 40

# ─── БД ───────────────────────────────────────────────────────────────────────
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            phone TEXT NOT NULL, addr TEXT DEFAULT '',
            date TEXT DEFAULT '', itogo REAL DEFAULT 0,
            km REAL DEFAULT 0, os INTEGER DEFAULT 0,
            sk REAL DEFAULT 0, ps INTEGER DEFAULT 0,
            comment TEXT DEFAULT ''
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_phone ON orders(phone)")
    conn.commit(); cur.close(); conn.close()

def search_by_phone(query):
    conn = get_conn(); cur = dict_cursor(conn)
    cur.execute("SELECT * FROM orders WHERE phone LIKE %s ORDER BY date DESC",
                        (f"%{query.replace('+','')}%",))
    rows = cur.fetchall(); cur.close(); conn.close(); return rows

def search_by_addr(query):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM orders WHERE addr LIKE ? ORDER BY date DESC",
                        (f"%{query}%",)).fetchall()
    conn.close(); return rows

def search_any(query):
    conn = get_conn(); cur = dict_cursor(conn)
    q = f"%{query.replace('+','')}%"
    cur.execute("SELECT * FROM orders WHERE phone LIKE %s OR addr LIKE %s ORDER BY date DESC",
        (q, f"%{query}%"))
    rows = cur.fetchall(); cur.close(); conn.close(); return rows

def get_client_history(phone):
    conn = get_conn(); cur = dict_cursor(conn)
    cur.execute("SELECT * FROM orders WHERE phone=%s ORDER BY date DESC", (phone,))
    rows = cur.fetchall(); cur.close(); conn.close(); return rows

def get_order_by_id(oid):
    conn = get_conn(); cur = dict_cursor(conn)
    cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
    row = cur.fetchone(); cur.close(); conn.close(); return row

def add_order(phone, addr, date_str, itogo, km, os_, sk, ps, comment):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO orders (phone,addr,date,itogo,km,os,sk,ps,comment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (phone, addr, date_str, itogo, km, os_, sk, ps, comment))
    conn.commit(); cur.close(); conn.close()

def delete_order(oid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE id=%s", (oid,))
    conn.commit(); cur.close(); conn.close()

def update_order_field(oid, field, value):
    if field not in {"addr","date","km","os","sk","ps","comment","itogo"}: return
    conn = get_conn(); cur = conn.cursor()
    cur.execute(f"UPDATE orders SET {field}=%s WHERE id=%s", (value, oid))
    conn.commit(); cur.close(); conn.close()

def db_count():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM orders")
    n = cur.fetchone()[0]; cur.close(); conn.close(); return n

def get_all_orders():
    conn = get_conn(); cur = dict_cursor(conn)
    cur.execute("SELECT * FROM orders ORDER BY date DESC")
    rows = cur.fetchall(); cur.close(); conn.close(); return rows

# ─── Парсинг телефон+адрес из одной ячейки ───────────────────────────────────
def parse_phone_addr(cell_value):
    """
    Ячейка вида: '906669329 Гулбута 1' или '077171881 Рахими 12-28'
    Первое слово = телефон (9 цифр), остальное = адрес
    """
    s = str(cell_value).strip()
    # Найти первое число (телефон) — цифры в начале или после пробела
    m = re.match(r'^(\d{6,12})\s*(.*)', s)
    if m:
        phone = m.group(1).lstrip('0') if len(m.group(1)) > 9 else m.group(1)
        addr  = m.group(2).strip()
        return phone, addr
    return s.replace(" ",""), ""

# ─── Парсинг даты ─────────────────────────────────────────────────────────────
def parse_date(cell_value):
    """
    Форматы: '01,05AM', '01.05AM', '2025-01-05', datetime объект
    '01,05AM' → год текущий, месяц 05, день 01
    """
    if cell_value is None: return ""
    if isinstance(cell_value, (datetime,)): return cell_value.strftime("%Y-%m-%d")
    s = str(cell_value).strip()
    # Формат 01,05AM или 01,05НБ или 01.05AM
    m = re.match(r'^(\d{1,2})[,.](\d{2})', s)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = datetime.now().year
        try: return f"{year}-{month:02d}-{day:02d}"
        except: pass
    # Стандартные форматы
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try: return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except: pass
    return s

# ─── Форматирование ───────────────────────────────────────────────────────────
def fmt(v): return f"{v:,.0f}".replace(",", " ") if v else "0"

def fmt_tags(row):
    t = []
    if row["km"]: t.append(f"🟦{row['km']}м²")
    if row["os"]: t.append(f"🟩{row['os']}шт")
    if row["sk"]: t.append(f"🟨{row['sk']}кг")
    if row["ps"]: t.append(f"🟥{row['ps']}шт")
    return "  ".join(t) if t else ""

def build_card(rows):
    phone = rows[0]["phone"]
    addrs = list(dict.fromkeys(r["addr"] for r in rows if r["addr"]))
    total = sum(r["itogo"] or 0 for r in rows)
    dates = sorted(r["date"] for r in rows if r["date"])
    freq = "—"
    if len(dates) > 1:
        try:
            gaps = [(datetime.strptime(dates[i],"%Y-%m-%d")-datetime.strptime(dates[i-1],"%Y-%m-%d")).days for i in range(1,len(dates))]
            avg = round(sum(gaps)/len(gaps))
            freq = f"каждые {avg} дн." if avg<14 else f"каждые {avg//7} нед." if avg<60 else f"каждые {avg//30} мес." if avg<365 else f"раз в {avg//365} год"
        except: pass
    return (f"📞 *+{phone}*\n🏠 {addrs[0] if addrs else '—'}\n\n"
            f"📦 Заказов: *{len(rows)}*\n💰 Сумма: *{fmt(total)} сом*\n"
            f"🔄 Частота: {freq}\n📅 Первый: {dates[0] if dates else '—'}\n📅 Последний: {dates[-1] if dates else '—'}")

def build_history(rows, limit=8):
    lines = [f"*📋 История ({len(rows)} заказов):*\n"]
    for r in list(rows)[:limit]:
        line = f"`{r['date'] or '—'}` — *{fmt(r['itogo'])} сом*  `#{r['id']}`"
        tags = fmt_tags(r)
        if tags: line += f"\n   {tags}"
        if r["addr"]: line += f"\n   📍 {r['addr']}"
        if r["comment"]: line += f"\n   💬 {r['comment']}"
        lines.append(line + "\n")
    if len(rows) > limit:
        lines.append(f"_...ещё {len(rows)-limit} заказов_\n")
    lines.append("✏️ `/edit ID` — редактировать\n🗑 `/del ID` — удалить")
    return "\n".join(lines)

# ─── Меню ─────────────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup(
    [["🔍 Найти клиента", "➕ Добавить заказ"],
     ["📊 Статистика",    "📤 Экспорт Excel"],
     ["📥 Загрузить Excel", "🌐 Открыть сайт"],
     ["🤖 ИИ-помощник"]],
    resize_keyboard=True,
)

# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update, ctx):
    await update.message.reply_text(
        f"🧺 *Химчистка — база клиентов*\n\nВ базе: *{db_count():,}* записей\n\n"
        f"📥 Отправьте файл `.xlsx` чтобы загрузить новые заказы",
        parse_mode="Markdown", reply_markup=MAIN_KB)

# ─── Статистика ───────────────────────────────────────────────────────────────
async def stats(update, ctx):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM orders"); total_ord = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(itogo),0) FROM orders"); total_sum = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT phone) FROM orders"); uniq = cur.fetchone()[0]
    cur2 = dict_cursor(conn)
    cur2.execute("SELECT phone,COUNT(*) cnt,SUM(itogo) total FROM orders GROUP BY phone ORDER BY total DESC LIMIT 5")
    top5 = cur2.fetchall()
    cur.close(); cur2.close(); conn.close()
    top_lines = "\n".join(f"  {i+1}. +{r['phone']} — {fmt(r['total'])} сом ({r['cnt']} зак.)" for i,r in enumerate(top5))
    avg = total_sum/total_ord if total_ord else 0
    await update.message.reply_text(
        f"📊 *Статистика*\n\n📦 Заказов: *{total_ord:,}*\n👤 Клиентов: *{uniq:,}*\n"
        f"💰 Выручка: *{fmt(total_sum)} сом*\n📈 Средний чек: *{fmt(avg)} сом*\n\n🏆 *Топ-5:*\n{top_lines}",
        parse_mode="Markdown", reply_markup=MAIN_KB)

# ─── Экспорт Excel ────────────────────────────────────────────────────────────
async def export_excel(update, ctx):
    await update.message.reply_text("⏳ Готовлю файл...")
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    rows = get_all_orders()
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "База"
    headers = ["Санаи","Телефон+Адрес","Қолин адад","м/кв","маблаг","Одеяло адад","маблаг","Парда кг","маблаг","Курпача маблаг","Итого"]
    fill = PatternFill("solid", fgColor="1E3A5F")
    font = Font(bold=True, color="FFFFFF")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = fill; cell.font = font
        cell.alignment = Alignment(horizontal="center")
    for r in rows:
        phone_addr = f"{r['phone']} {r['addr']}".strip()
        ws.append([r["date"], phone_addr,
                   r["km"] or "", r["km"] or "", r["km"]*PRICES["km"] if r["km"] else "",
                   r["os"] or "", r["os"]*PRICES["os"] if r["os"] else "",
                   r["sk"] or "", r["sk"]*PRICES["sk"] if r["sk"] else "",
                   r["ps"]*PRICES["ps"] if r["ps"] else "",
                   r["itogo"]])
    for i, w in enumerate([12,35,10,10,12,10,12,10,12,12,14], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    await update.message.reply_document(document=buf, filename=f"Himchistka_{date.today()}.xlsx",
        caption=f"📊 Экспорт: *{len(rows):,}* записей", parse_mode="Markdown")

# ─── Загрузить Excel ──────────────────────────────────────────────────────────
async def upload_excel_prompt(update, ctx):
    await update.message.reply_text(
        "📥 *Загрузка Excel*\n\n"
        "Отправьте файл `.xlsx` в формате вашей химчистки:\n\n"
        "`A` — Санаи (дата: `01,05AM`)\n"
        "`B` — Телефон + Адрес (`906669329 Гулбута 1`)\n"
        "`C` — Қолин адад\n"
        "`D` — м/кв\n"
        "`E` — маблаг ковёр\n"
        "`F` — Одеяло адад\n"
        "`G` — маблаг одеяло\n"
        "`H` — Парда кг\n"
        "`I` — маблаг парда\n"
        "`J` — Курпача маблаг\n"
        "`K` — Итого умуми",
        parse_mode="Markdown")

async def handle_document(update, ctx):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".xlsx"):
        await update.message.reply_text("⚠️ Пожалуйста отправьте файл .xlsx")
        return

    await update.message.reply_text("⏳ Читаю файл...")
    import openpyxl

    file = await ctx.bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    try:
        wb = openpyxl.load_workbook(buf, data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка чтения: {e}")
        return

    added = 0; skipped = 0; dupes = 0
    conn = get_conn()
    batch = []

    for row in all_rows:
        if not row or len(row) < 2: skipped += 1; continue

        # Дата — колонка A
        dt = parse_date(row[0]) if len(row) > 0 else ""

        # Пропустить строки-заголовки и итоговые строки
        cell_b = str(row[1]).strip() if row[1] else ""
        if not cell_b or cell_b.lower() in ("санаи овардан","супорид","итого","жами","б/м",""):
            skipped += 1; continue
        # Пропустить если в B нет цифр (заголовок)
        if not re.search(r'\d{6,}', cell_b):
            skipped += 1; continue

        # Телефон + адрес — колонка B
        phone, addr = parse_phone_addr(cell_b)
        if not phone or len(phone) < 6: skipped += 1; continue

        def n(i):
            try: return float(row[i]) if i < len(row) and row[i] is not None and str(row[i]).strip() not in ("","0","0.0","0,00") else 0
            except: return 0

        # C=адад ковёр, D=м/кв, E=маблаг ковёр
        km    = n(3)   # м/кв (колонка D)
        # F=адад одеяло, G=маблаг одеяло
        os_   = int(n(5))  # колонка F
        # H=кг парда, I=маблаг
        sk    = n(7)   # кг (колонка H)
        # J=маблаг курпача (колонка J)
        ps_mabl = n(9)
        ps    = int(ps_mabl // PRICES["ps"]) if ps_mabl > 0 else 0

        # K=итого (колонка K)
        itogo = n(10) if len(row) > 10 else n(4) + n(6) + n(8) + ps_mabl

        if itogo == 0:
            itogo = n(4) + n(6) + n(8) + ps_mabl

        # Дубли
        cur_check = conn.cursor()
        cur_check.execute(
            "SELECT 1 FROM orders WHERE phone=%s AND date=%s AND itogo=%s",
            (phone, dt, itogo))
        exists = cur_check.fetchone()
        cur_check.close()
        if exists: dupes += 1; continue

        batch.append((phone, addr, dt, itogo, km, os_, sk, ps, ""))
        added += 1

    if batch:
        cur_ins = conn.cursor()
        psycopg2.extras.execute_batch(cur_ins,
            "INSERT INTO orders (phone,addr,date,itogo,km,os,sk,ps,comment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            batch)
        conn.commit()
        cur_ins.close()
    conn.close()

    await update.message.reply_text(
        f"✅ *Импорт завершён!*\n\n"
        f"➕ Добавлено: *{added}*\n"
        f"🔄 Дубли пропущены: *{dupes}*\n"
        f"⚠️ Пропущено строк: *{skipped}*\n\n"
        f"📦 Всего в базе: *{db_count():,}*",
        parse_mode="Markdown", reply_markup=MAIN_KB)

# ─── Поиск ────────────────────────────────────────────────────────────────────
async def search_start(update, ctx):
    await update.message.reply_text("🔍 Введите телефон или адрес клиента:", reply_markup=ReplyKeyboardRemove())
    return SEARCH_INPUT

async def search_do(update, ctx):
    query = update.message.text.strip()
    if len(query) < 3:
        await update.message.reply_text("⚠️ Минимум 3 символа:")
        return SEARCH_INPUT

    rows = search_any(query)
    if not rows:
        await update.message.reply_text(f"❌ Ничего не найдено по запросу «{query}».", reply_markup=MAIN_KB)
        return ConversationHandler.END

    phones = list(dict.fromkeys(r["phone"] for r in rows))
    if len(phones) == 1:
        await _show_client(update.message, phones[0])
        return ConversationHandler.END

    # Показать список с адресами
    btns = []
    seen = set()
    for r in rows[:10]:
        if r["phone"] in seen: continue
        seen.add(r["phone"])
        label = f"📞 +{r['phone']}"
        if r["addr"]: label += f" — {r['addr'][:20]}"
        btns.append([InlineKeyboardButton(label, callback_data=f"cl:{r['phone']}")])

    await update.message.reply_text(
        f"Найдено: *{len(phones)}* клиентов:", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns))
    return ConversationHandler.END

async def search_cancel(update, ctx):
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KB); return ConversationHandler.END

async def on_client_btn(update, ctx):
    q = update.callback_query; await q.answer()
    phone = q.data.split(":",1)[1]
    rows = get_client_history(phone)
    if not rows: await q.edit_message_text("❌ Не найдено."); return
    await q.edit_message_text(build_card(rows), parse_mode="Markdown")
    await q.message.reply_text(build_history(rows), parse_mode="Markdown", reply_markup=MAIN_KB)

async def _show_client(message, phone):
    rows = get_client_history(phone)
    if not rows: await message.reply_text("❌ Не найден.", reply_markup=MAIN_KB); return
    await message.reply_text(build_card(rows), parse_mode="Markdown")
    await message.reply_text(build_history(rows), parse_mode="Markdown", reply_markup=MAIN_KB)

# ─── Удалить ──────────────────────────────────────────────────────────────────
async def del_order(update, ctx):
    try: oid = int(ctx.args[0])
    except: await update.message.reply_text("❌ `/del 123`", parse_mode="Markdown"); return
    row = get_order_by_id(oid)
    if not row: await update.message.reply_text(f"❌ Заказ #{oid} не найден."); return
    btns = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Удалить", callback_data=f"delok:{oid}"),
        InlineKeyboardButton("❌ Отмена",  callback_data="delno")]])
    await update.message.reply_text(
        f"⚠️ Удалить *#{oid}*?\n📞 +{row['phone']} | 📅 {row['date']} | 💰 {fmt(row['itogo'])} сом",
        parse_mode="Markdown", reply_markup=btns)

async def del_ok(update, ctx):
    q = update.callback_query; await q.answer()
    delete_order(int(q.data.split(":",1)[1]))
    await q.edit_message_text(f"✅ Удалён.")

async def del_no(update, ctx):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("Отменено.")

# ─── Редактировать ────────────────────────────────────────────────────────────
async def edit_order(update, ctx):
    try: oid = int(ctx.args[0])
    except: await update.message.reply_text("❌ `/edit 123`", parse_mode="Markdown"); return
    row = get_order_by_id(oid)
    if not row: await update.message.reply_text(f"❌ Заказ #{oid} не найден."); return
    ctx.user_data["edit_id"] = oid
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Адрес",      callback_data="ef:addr"),
         InlineKeyboardButton("📅 Дата",        callback_data="ef:date")],
        [InlineKeyboardButton("🟦 Қолин м²",   callback_data="ef:km"),
         InlineKeyboardButton("🟩 Одеяло шт",  callback_data="ef:os")],
        [InlineKeyboardButton("🟨 Парда кг",   callback_data="ef:sk"),
         InlineKeyboardButton("🟥 Курпача шт", callback_data="ef:ps")],
        [InlineKeyboardButton("💬 Комментарий", callback_data="ef:comment")],
    ])
    await update.message.reply_text(
        f"✏️ Редактирование *#{oid}*\n📞 +{row['phone']} | 📅 {row['date']} | 💰 {fmt(row['itogo'])} сом\n\nЧто изменить?",
        parse_mode="Markdown", reply_markup=btns)

async def edit_field(update, ctx):
    q = update.callback_query; await q.answer()
    field = q.data.split(":",1)[1]
    names = {"addr":"Адрес","date":"Дата (ГГГГ-ММ-ДД)","km":"Қолин м²","os":"Одеяло шт","sk":"Парда кг","ps":"Курпача шт","comment":"Комментарий"}
    ctx.user_data["edit_field"] = field
    await q.edit_message_text(f"✏️ Введите новое значение для *{names.get(field,field)}*:", parse_mode="Markdown")
    return EDIT_VALUE

async def edit_value(update, ctx):
    oid = ctx.user_data.get("edit_id")
    field = ctx.user_data.get("edit_field")
    val = update.message.text.strip()
    try:
        if field in ("km","sk"): val = float(val)
        elif field in ("os","ps"): val = int(val)
    except: await update.message.reply_text("⚠️ Введите число:"); return EDIT_VALUE
    update_order_field(oid, field, val)
    if field in ("km","os","sk","ps"):
        row = get_order_by_id(oid)
        if row:
            new_itogo = row["km"]*PRICES["km"]+row["os"]*PRICES["os"]+row["sk"]*PRICES["sk"]+row["ps"]*PRICES["ps"]
            update_order_field(oid, "itogo", new_itogo)
    await update.message.reply_text(f"✅ Заказ *#{oid}* обновлён!", parse_mode="Markdown", reply_markup=MAIN_KB)
    return ConversationHandler.END

async def edit_cancel(update, ctx):
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KB); return ConversationHandler.END

# ─── Добавить заказ ───────────────────────────────────────────────────────────
async def add_start(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("➕ *Шаг 1/7* — Телефон клиента:", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return ADD_PHONE

async def a_phone(update, ctx):
    p = update.message.text.strip().replace("+","").replace(" ","")
    if len(p)<5: await update.message.reply_text("⚠️ Короткий номер:"); return ADD_PHONE
    ctx.user_data["phone"] = p
    ex = get_client_history(p)
    if ex: await update.message.reply_text(f"ℹ️ *+{p}* уже в базе: {len(ex)} зак., {fmt(sum(r['itogo'] or 0 for r in ex))} сом", parse_mode="Markdown")
    await update.message.reply_text("🏠 *Шаг 2/7* — Адрес (или /skip):", parse_mode="Markdown"); return ADD_ADDR

async def a_addr(u,c):      c.user_data["addr"]=u.message.text.strip(); await u.message.reply_text("📅 *Шаг 3/7* — Дата `ГГГГ-ММ-ДД` (или /skip):", parse_mode="Markdown"); return ADD_DATE
async def a_addr_skip(u,c): c.user_data["addr"]=""; await u.message.reply_text("📅 *Шаг 3/7* — Дата `ГГГГ-ММ-ДД` (или /skip):", parse_mode="Markdown"); return ADD_DATE

async def a_date(update, ctx):
    d = update.message.text.strip()
    try: datetime.strptime(d,"%Y-%m-%d"); ctx.user_data["date"]=d
    except: await update.message.reply_text("⚠️ Формат: `2025-01-15`", parse_mode="Markdown"); return ADD_DATE
    await update.message.reply_text("🟦 *Шаг 4/7* — Қолин м² (или /skip):", parse_mode="Markdown"); return ADD_KM
async def a_date_skip(u,c): c.user_data["date"]=str(date.today()); await u.message.reply_text("🟦 *Шаг 4/7* — Қолин м² (или /skip):", parse_mode="Markdown"); return ADD_KM

async def _num(u, c, key, cast, ns, msg):
    try: c.user_data[key]=cast(u.message.text.strip())
    except: await u.message.reply_text("⚠️ Введите число:"); return ns
    await u.message.reply_text(msg, parse_mode="Markdown"); return ns+1

async def a_km(u,c):      return await _num(u,c,"km",float,ADD_KM,"🟩 *Шаг 5/7* — Одеяло шт (или /skip):")
async def a_km_skip(u,c): c.user_data["km"]=0; await u.message.reply_text("🟩 *Шаг 5/7* — Одеяло шт (или /skip):", parse_mode="Markdown"); return ADD_OS
async def a_os(u,c):      return await _num(u,c,"os",int,ADD_OS,"🟨 *Шаг 6/7* — Парда кг (или /skip):")
async def a_os_skip(u,c): c.user_data["os"]=0; await u.message.reply_text("🟨 *Шаг 6/7* — Парда кг (или /skip):", parse_mode="Markdown"); return ADD_SK
async def a_sk(u,c):      return await _num(u,c,"sk",float,ADD_SK,"🟥 *Шаг 7/7* — Курпача шт (или /skip):")
async def a_sk_skip(u,c): c.user_data["sk"]=0; await u.message.reply_text("🟥 *Шаг 7/7* — Курпача шт (или /skip):", parse_mode="Markdown"); return ADD_PS
async def a_ps(u,c):      return await _num(u,c,"ps",int,ADD_PS,"💬 Комментарий (или /skip):")
async def a_ps_skip(u,c): c.user_data["ps"]=0; await u.message.reply_text("💬 Комментарий (или /skip):"); return ADD_COMMENT

async def a_comment(u,c):      c.user_data["comment"]=u.message.text.strip(); return await _save(u,c)
async def a_comment_skip(u,c): c.user_data["comment"]=""; return await _save(u,c)

async def _save(update, ctx):
    d=ctx.user_data
    km,os_,sk,ps=d.get("km",0),d.get("os",0),d.get("sk",0),d.get("ps",0)
    itogo=km*PRICES["km"]+os_*PRICES["os"]+sk*PRICES["sk"]+ps*PRICES["ps"]
    add_order(d.get("phone",""),d.get("addr",""),d.get("date",str(date.today())),itogo,km,os_,sk,ps,d.get("comment",""))
    tags=[]
    if km: tags.append(f"🟦{km}м²")
    if os_: tags.append(f"🟩{os_}шт")
    if sk: tags.append(f"🟨{sk}кг")
    if ps: tags.append(f"🟥{ps}шт")
    await update.message.reply_text(
        f"✅ *Заказ сохранён!*\n📞 +{d.get('phone')} | 📅 {d.get('date')}\n{'  '.join(tags) or '—'}\n💰 *{fmt(itogo)} сом*",
        parse_mode="Markdown", reply_markup=MAIN_KB)
    return ConversationHandler.END

async def add_cancel(update, ctx):
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB); return ConversationHandler.END

# ─── Запуск ───────────────────────────────────────────────────────────────────

async def open_site(update, ctx):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    await update.message.reply_text(
        "🌐 Открыть сайт химчистки:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🧺 Открыть", url="https://himchistka-web-production.up.railway.app")
        ]]))


async def ai_helper(update, ctx):
    ctx.user_data["ai_mode"] = True
    await update.message.reply_text(
        "🤖 Задайте вопрос о вашей базе:\n\n"
        "• Сколько заработали в мае?\n"
        "• Какой клиент самый частый?\n"
        "• Топ 5 клиентов по сумме?\n"
        "• Сравни январь и февраль",
        reply_markup=ReplyKeyboardRemove())

async def ai_answer(update, ctx):
    if not ctx.user_data.get("ai_mode"): return
    ctx.user_data["ai_mode"] = False
    question = update.message.text
    await update.message.reply_text("⏳ Думаю...")
    try:
        import httpx
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(itogo),0) FROM orders"); total_cnt, total_sum = cur.fetchone()
        cur.execute("SELECT COUNT(DISTINCT phone) FROM orders"); uniq = cur.fetchone()[0]
        cur.execute("SELECT phone, COUNT(*) cnt, SUM(itogo) total FROM orders GROUP BY phone ORDER BY total DESC LIMIT 5"); top5 = cur.fetchall()
        cur.execute("SELECT substr(date,1,7) as m, COUNT(*), SUM(itogo) FROM orders WHERE date != \'\' GROUP BY m ORDER BY m DESC LIMIT 12"); monthly = cur.fetchall()
        cur.close(); conn.close()
        top5_text = "\n".join([f"+{r[0]}: {r[1]} зак., {float(r[2]):.0f} сом" for r in top5])
        monthly_text = "\n".join([f"{r[0]}: {r[1]} зак., {float(r[2]):.0f} сом" for r in monthly if r[0]])
        context = f"""База данных химчистки:
Всего заказов: {total_cnt}, Клиентов: {uniq}, Выручка: {float(total_sum):.0f} сом
Топ-5: {top5_text}
По месяцам: {monthly_text}"""
        resp = httpx.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ.get("ANTHROPIC_API_KEY",""),
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                  "messages": [{"role": "user", "content": f"{context}\n\nВопрос: {question}\nОтветь коротко на русском."}]},
            timeout=30)
        answer = resp.json()["content"][0]["text"]
        await update.message.reply_text(f"🤖 {answer}", reply_markup=MAIN_KB)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_KB)


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
            ADD_ADDR:    [CommandHandler("skip", a_addr_skip), MessageHandler(filters.TEXT & ~filters.COMMAND, a_addr)],
            ADD_DATE:    [CommandHandler("skip", a_date_skip), MessageHandler(filters.TEXT & ~filters.COMMAND, a_date)],
            ADD_KM:      [CommandHandler("skip", a_km_skip),   MessageHandler(filters.TEXT & ~filters.COMMAND, a_km)],
            ADD_OS:      [CommandHandler("skip", a_os_skip),   MessageHandler(filters.TEXT & ~filters.COMMAND, a_os)],
            ADD_SK:      [CommandHandler("skip", a_sk_skip),   MessageHandler(filters.TEXT & ~filters.COMMAND, a_sk)],
            ADD_PS:      [CommandHandler("skip", a_ps_skip),   MessageHandler(filters.TEXT & ~filters.COMMAND, a_ps)],
            ADD_COMMENT: [CommandHandler("skip", a_comment_skip), MessageHandler(filters.TEXT & ~filters.COMMAND, a_comment)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_field, pattern=r"^ef:")],
        states={EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)]},
        fallbacks=[CommandHandler("cancel", edit_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🤖 ИИ-помощник$"), ai_helper))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_answer))
    app.add_handler(MessageHandler(filters.Regex("^🤖 ИИ-помощник$"), ai_helper))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, ai_answer))
    app.add_handler(MessageHandler(filters.Regex("^🌐 Открыть сайт$"), open_site))
    app.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), stats))
    app.add_handler(MessageHandler(filters.Regex("^📤 Экспорт Excel$"), export_excel))
    app.add_handler(MessageHandler(filters.Regex("^📥 Загрузить Excel$"), upload_excel_prompt))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(on_client_btn, pattern=r"^cl:"))
    app.add_handler(CallbackQueryHandler(del_ok, pattern=r"^delok:"))
    app.add_handler(CallbackQueryHandler(del_no, pattern=r"^delno$"))
    app.add_handler(CommandHandler("del", del_order))
    app.add_handler(CommandHandler("edit", edit_order))
    app.add_handler(search_conv)
    app.add_handler(add_conv)
    app.add_handler(edit_conv)

    logger.info("🧺 Бот @toptozazakaz_bot запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()


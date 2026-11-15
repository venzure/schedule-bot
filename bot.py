import os
import sys
import time
import json
import datetime
import traceback
import subprocess
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "user_subgroups.json")
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
CRASH_FILE = os.path.join(BASE_DIR, "crash_reason.txt")
PID_FILE = os.path.join(BASE_DIR, "bot.pid")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")

class SafeLogger:
    def __init__(self, filename):
        self.filename = filename
    def write(self, message):
        if not message:
            return
        try:
            with open(self.filename, "a", encoding="utf-8") as f:
                f.write(message)
        except Exception:
            pass
    def flush(self):
        pass

if sys.stdout is None or not hasattr(sys.stdout, 'write'):
    sys.stdout = SafeLogger(LOG_FILE)
if sys.stderr is None or not hasattr(sys.stderr, 'write'):
    sys.stderr = SafeLogger(LOG_FILE)

import logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8")

# Proxy is only used if specified in environment (not needed in European/US cloud)
PROXY_URL = os.environ.get("PROXY_URL")
if PROXY_URL:
    os.environ["HTTP_PROXY"] = PROXY_URL
    os.environ["HTTPS_PROXY"] = PROXY_URL
    os.environ["ALL_PROXY"] = PROXY_URL


# --- Koyeb Health Check Web Server ---
def _start_koyeb_health_server():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Telegram Bot is running! OK")
        def log_message(self, format, *args):
            pass # Silent
    port = int(os.environ.get("PORT", 8000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Health server error: {e}")

threading.Thread(target=_start_koyeb_health_server, daemon=True).start()
# -------------------------------------

import telebot
from telebot import types, apihelper
apihelper.proxy = {"https": PROXY_URL, "http": PROXY_URL}

TOKEN = "9230975748:AAE4YKJGKfiFIGLvlMHKSbtg-UjM4VEKRlQ"
bot = telebot.TeleBot(TOKEN)

def ensure_single_instance():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                subprocess.run(["taskkill", "/F", "/PID", str(old_pid)], capture_output=True)
                time.sleep(0.5)
        except Exception:
            pass
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

DAYS_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default or {}

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

user_subgroups = load_json(USERS_FILE, {})

def get_user_subgroup(user_id):
    return user_subgroups.get(str(user_id), 2)

def set_user_subgroup(user_id, sub):
    user_subgroups[str(user_id)] = int(sub)
    save_json(USERS_FILE, user_subgroups)

def get_academic_week(dt=None):
    now = dt or datetime.datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    sept_1 = datetime.datetime(now.year if now.month >= 9 else now.year - 1, 9, 1)
    sept_1_week = sept_1.isocalendar()[1]
    if iso_week >= sept_1_week:
        acad_week = (iso_week - sept_1_week) + 1
    else:
        acad_week = (52 - sept_1_week + iso_week) + 1
    is_chislitel = (acad_week % 2 != 0)
    key = "chislitel" if is_chislitel else "znamenatel"
    name = "Числитель" if is_chislitel else "Знаменатель"
    return acad_week, key, name

def get_day_pairs(subgroup, week_key, weekday):
    data = load_json(SCHEDULE_FILE)
    sub_str = str(subgroup)
    subgroups = data.get("subgroups", {})
    sub_data = subgroups.get(sub_str, subgroups.get("2", {}))
    week_days = sub_data.get(week_key, {})
    return week_days.get(str(weekday), {})

def get_current_pair_status(user_id):
    data = load_json(SCHEDULE_FILE)
    bells = data.get("bells", [])
    sub = get_user_subgroup(user_id)
    now = datetime.datetime.now()
    weekday = now.weekday()
    current_minutes = now.hour * 60 + now.minute
    day_name = DAYS_NAMES[weekday]
    acad_week, current_key, current_name = get_academic_week(now)
    today_pairs = get_day_pairs(sub, current_key, weekday)

    # 1. Выходной день
    if weekday in [5, 6] or not today_pairs:
        mon_days = 7 - weekday if weekday in [5, 6] else 1
        next_dt = now + datetime.timedelta(days=mon_days)
        next_week, next_key, next_wname = get_academic_week(next_dt)
        next_pairs = get_day_pairs(sub, next_key, next_dt.weekday())
        first_p = next_pairs.get("1") if next_pairs else None

        text = f"🎉 **СЕГОДНЯ ВЫХОДНОЙ!**\n"
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📅 {day_name} • Пар в расписании нет, можно отдыхать! 😎\n\n"
        if first_p:
            text += f"🔔 **В понедельник ({next_wname}) 1-я пара в 12:10:**\n"
            text += f"👉 **{first_p.get('name')}** (ауд. **{first_p.get('room')}**)\n"
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"👤 Твоя подгруппа: **{sub}-я**"
        return text

    # 2. Идет ли пара прямо сейчас
    for bell in bells:
        num = bell["num"]
        sh, sm = bell["start"]
        eh, em = bell["end"]
        start_min = sh * 60 + sm
        end_min = eh * 60 + em

        if start_min <= current_minutes <= end_min:
            left = end_min - current_minutes
            pair = today_pairs.get(str(num))
            time_str = f"{sh:02d}:{sm:02d} — {eh:02d}:{em:02d}"

            if pair:
                name = pair.get("name", "Предмет")
                room = pair.get("room", "")
                ptype = pair.get("type", "")
                teacher = pair.get("teacher", "")

                text = f"🟢 **СЕЙЧАС ИДЁТ: {num}-я ПАРА**\n"
                text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
                text += f"📚 **{name}**\n"
                text += f"📍 АУДИТОРИЯ: **{room}**\n"
                text += f"📝 _{ptype}_\n"
                if teacher:
                    text += f"👨‍🏫 {teacher}\n"
                text += f"⏱ Время: **{time_str}**\n"
                text += f"⏳ До звонка с пары: **{left} мин.**\n"
            else:
                text = f"🟢 **СЕЙЧАС {num}-я ПАРА (ОКНО)**\n"
                text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
                text += f"☕ У твоей подгруппы сейчас пары нет (окно).\n"
                text += f"⏳ До конца пары: **{left} мин.**\n"

            # Следующая пара
            next_num = num + 1
            if str(next_num) in today_pairs:
                np = today_pairs[str(next_num)]
                nbell = [b for b in bells if b["num"] == next_num][0]
                text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
                text += f"⏭ **Следующая ({next_num}-я) пара в {nbell['start'][0]:02d}:{nbell['start'][1]:02d}:**\n"
                text += f"👉 **{np.get('name')}** (ауд. **{np.get('room')}**)"
            return text

    # 3. Перемена между парами
    for i in range(len(bells) - 1):
        eh, em = bells[i]["end"]
        next_num = bells[i+1]["num"]
        nsh, nsm = bells[i+1]["start"]
        break_start = eh * 60 + em
        break_end = nsh * 60 + nsm

        if break_start < current_minutes < break_end:
            left_break = break_end - current_minutes
            next_pair = today_pairs.get(str(next_num))
            text = f"☕ **СЕЙЧАС ПЕРЕМЕНА!** (10 мин)\n"
            text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
            text += f"⏳ До звонка осталось: **{left_break} мин.**\n\n"
            if next_pair:
                text += f"🔔 **В {nsh:02d}:{nsm:02d} НАЧНЕТСЯ {next_num}-я ПАРА:**\n"
                text += f"📚 **{next_pair.get('name')}**\n"
                text += f"📍 АУДИТОРИЯ: **{next_pair.get('room')}**\n"
                text += f"📝 _{next_pair.get('type')}_\n"
                if next_pair.get("teacher"):
                    text += f"👨‍🏫 {next_pair.get('teacher')}\n"
            else:
                text += f"🔔 Следующая ({next_num}-я) пара: Окно (пары нет)\n"
            return text

    # 4. Утро (до начала первой пары)
    first_bell = bells[0]
    first_start = first_bell["start"][0] * 60 + first_bell["start"][1]
    if current_minutes < first_start:
        left_to_start = first_start - current_minutes
        h = left_to_start // 60
        m = left_to_start % 60
        left_str = f"{h} ч. {m} мин." if h > 0 else f"{m} мин."

        text = f"🌅 **ДО НАЧАЛА ЗАНЯТИЙ: {left_str}**\n"
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # Поиск первой активной пары сегодня
        found_first = False
        for b in bells:
            num_s = str(b["num"])
            if num_s in today_pairs:
                p = today_pairs[num_s]
                text += f"🔔 **В {b['start'][0]:02d}:{b['start'][1]:02d} начнется {num_s}-я пара:**\n"
                text += f"📚 **{p.get('name')}**\n"
                text += f"📍 АУДИТОРИЯ: **{p.get('room')}**\n"
                text += f"📝 _{p.get('type')}_\n"
                if p.get("teacher"):
                    text += f"👨‍🏫 {p.get('teacher')}\n"
                found_first = True
                break

        if not found_first:
            text += f"🎉 Пар на сегодня не запланировано!\n"

        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📅 Сегодня: **{day_name}** ({current_name}, {sub}-я подгруппа)"
        return text

    # 5. Вечер (пары на сегодня закончились)
    tomorrow_dt = now + datetime.timedelta(days=1)
    t_weekday = tomorrow_dt.weekday()
    t_week, t_key, t_wname = get_academic_week(tomorrow_dt)
    t_day_name = DAYS_NAMES[t_weekday]
    t_pairs = get_day_pairs(sub, t_key, t_weekday) if t_weekday < 5 else {}

    text = f"🏁 **ВСЕ ПАРЫ НА СЕГОДНЯ ЗАКОНЧИЛИСЬ!**\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"Сегодня всё отучились, отличного отдыха! 😎\n\n"

    if t_pairs:
        for b in bells:
            num_s = str(b["num"])
            if num_s in t_pairs:
                tp = t_pairs[num_s]
                text += f"🌅 **ЗАВТРА ({t_day_name}, {t_wname}):**\n"
                text += f"🔔 {num_s}-я пара в **{b['start'][0]:02d}:{b['start'][1]:02d}**:\n"
                text += f"📚 **{tp.get('name')}** (ауд. **{tp.get('room')}**)\n"
                break
    else:
        text += f"🎉 **Завтра ({t_day_name}) — выходной!** Пар нет."

    return text

def format_day_schedule(user_id, target_dt, is_today=True):
    data = load_json(SCHEDULE_FILE)
    bells = data.get("bells", [])
    sub = get_user_subgroup(user_id)
    now = datetime.datetime.now()
    current_minutes = now.hour * 60 + now.minute if is_today else -1
    weekday = target_dt.weekday()
    day_name = DAYS_NAMES[weekday]
    acad_week, week_key, week_name = get_academic_week(target_dt)
    pairs = get_day_pairs(sub, week_key, weekday)

    title = "РАСПИСАНИЕ НА СЕГОДНЯ" if is_today else "РАСПИСАНИЕ НА ЗАВТРА"
    text = f"📅 **{title} ({day_name}, {target_dt.strftime('%d.%m')})**\n"
    text += f"🔖 **{week_name}** | 👤 **{sub}-я подгруппа**\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if weekday in [5, 6] or not pairs:
        text += f"🎉 **В этот день пар нет, выходной!** 😎"
        return text

    for bell in bells:
        num = str(bell["num"])
        sh, sm = bell["start"]
        eh, em = bell["end"]
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        time_str = f"{sh:02d}:{sm:02d} — {eh:02d}:{em:02d}"

        if num not in pairs:
            continue

        p = pairs[num]
        pname = p.get("name", "")
        proom = p.get("room", "")
        ptype = p.get("type", "")
        pteacher = p.get("teacher", "")

        type_icon = "📖" if "Лекция" in ptype else ("🔬" if "Лабораторная" in ptype else ("✍️" if "Практическ" in ptype else "👥"))

        if is_today:
            if current_minutes > end_min:
                status_badge = "⚪ `[Завершена ✅]`"
            elif start_min <= current_minutes <= end_min:
                left = end_min - current_minutes
                status_badge = f"🟢 **`▶️ СЕЙЧАС ИДЁТ`** _(осталось {left} мин)_"
            elif current_minutes < start_min and (current_minutes >= start_min - 15 or not any(b['start'][0]*60+b['start'][1] <= current_minutes <= b['end'][0]*60+b['end'][1] for b in bells)):
                left_to = start_min - current_minutes
                status_badge = f"🟡 **`⏳ СЛЕДУЮЩАЯ`** _(через {left_to} мин)_"
            else:
                status_badge = "🔵"
        else:
            status_badge = "🔹"

        text += f"{status_badge} **{num}-я пара** ({time_str})\n"
        text += f"   {type_icon} **{pname}**\n"
        text += f"   📍 Ауд: **{proom}** | _{ptype}_\n"
        if pteacher:
            text += f"   👨‍🏫 {pteacher}\n"
        text += "\n"

    return text.strip()

def get_week_schedule(user_id, week_key=None):
    data = load_json(SCHEDULE_FILE)
    group_name = data.get("group", "БАС.5-26-1 (И,О)")
    bells = data.get("bells", [])
    sub = get_user_subgroup(user_id)
    acad_week, current_key, current_name = get_academic_week()

    if not week_key:
        week_key = current_key

    sub_str = str(sub)
    subgroups = data.get("subgroups", {})
    sub_data = subgroups.get(sub_str, subgroups.get("2", {}))
    days = sub_data.get(week_key, {})
    target_name = "Числитель" if week_key == "chislitel" else "Знаменатель"

    now = datetime.datetime.now()
    cur_weekday = now.weekday()

    text = f"🗓 **РАСПИСАНИЕ НА НЕДЕЛЮ — {target_name.upper()}**\n"
    text += f"🎓 Группа: **{group_name}** | 👤 Подгруппа: **{sub}-я**\n"
    text += f"📍 Текущая неделя: **{current_name}** ({acad_week}-я учебная)\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for w in range(6):
        dname = DAYS_NAMES[w]
        day_pairs = days.get(str(w), {})
        is_today = (w == cur_weekday and week_key == current_key)
        
        day_prefix = "👉 📌 " if is_today else "📌 "
        day_suffix = " ◀️ СЕГОДНЯ" if is_today else ""
        
        text += f"{day_prefix}**{dname.upper()}{day_suffix}:**\n"
        if not day_pairs:
            text += "  _Пар нет (выходной)_\n\n"
            continue

        for bell in bells:
            num = str(bell["num"])
            if num in day_pairs:
                sh, sm = bell["start"]
                pair = day_pairs[num]
                name = pair.get("name", "")
                room = pair.get("room", "")
                ptype = pair.get("type", "")
                text += f"  • **{num} пара** ({sh:02d}:{sm:02d}): {name} (ауд. **{room}**, _{ptype}_)\n"
        text += "\n"

    return text

def make_main_keyboard(user_id):
    sub = get_user_subgroup(user_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_now = types.KeyboardButton("⚡ Какая сейчас пара?")
    btn_today = types.KeyboardButton("📋 На сегодня")
    btn_tomorrow = types.KeyboardButton("🌅 На завтра")
    btn_week = types.KeyboardButton("🗓 Вся неделя")
    btn_sub = types.KeyboardButton(f"👥 Подгруппа: {sub}-я")
    
    markup.row(btn_now)
    markup.row(btn_today, btn_tomorrow)
    markup.row(btn_week, btn_sub)
    return markup

def make_subgroup_inline(current_sub=2):
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton(("✅ " if current_sub == 1 else "") + "1️⃣ 1-я подгруппа", callback_data="set_sub_1")
    btn2 = types.InlineKeyboardButton(("✅ " if current_sub == 2 else "") + "2️⃣ 2-я подгруппа", callback_data="set_sub_2")
    markup.row(btn1, btn2)
    return markup

def make_week_switch_inline(current_selected="chislitel"):
    markup = types.InlineKeyboardMarkup()
    btn_ch = types.InlineKeyboardButton(("✅ " if current_selected == "chislitel" else "") + "1️⃣ Числитель", callback_data="show_week_chislitel")
    btn_zn = types.InlineKeyboardButton(("✅ " if current_selected == "znamenatel" else "") + "2️⃣ Знаменатель", callback_data="show_week_znamenatel")
    markup.row(btn_ch, btn_zn)
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    uid = message.chat.id
    sub = get_user_subgroup(uid)
    data = load_json(SCHEDULE_FILE)
    group = data.get("group", "БАС.5-26-1(И,О)")
    acad_week, current_key, current_name = get_academic_week()

    # Сразу отправляем статус текущей пары!
    status_text = get_current_pair_status(uid)

    welcome_text = (
        f"👋 Привет! Твоё расписание для группы **{group}**:\n"
        f"👤 Подгруппа: **{sub}-я** | 🔖 **{current_name}** ({acad_week}-я неделя)\n\n"
        f"{status_text}"
    )

    bot.send_message(
        uid,
        welcome_text,
        reply_markup=make_main_keyboard(uid),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda msg: msg.text in ["⚡ Какая сейчас пара?", "⏰ Какая сейчас пара?", "/now", "сейчас", "пара", "какая пара"])
def handle_current_pair(message):
    uid = message.chat.id
    text = get_current_pair_status(uid)
    bot.send_message(uid, text, parse_mode="Markdown", reply_markup=make_main_keyboard(uid))

@bot.message_handler(func=lambda msg: msg.text in ["📋 На сегодня", "📋 Расписание на сегодня", "/today", "сегодня"])
def handle_today(message):
    uid = message.chat.id
    text = format_day_schedule(uid, datetime.datetime.now(), is_today=True)
    bot.send_message(uid, text, parse_mode="Markdown", reply_markup=make_main_keyboard(uid))

@bot.message_handler(func=lambda msg: msg.text in ["🌅 На завтра", "/tomorrow", "завтра"])
def handle_tomorrow(message):
    uid = message.chat.id
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    text = format_day_schedule(uid, tomorrow, is_today=False)
    bot.send_message(uid, text, parse_mode="Markdown", reply_markup=make_main_keyboard(uid))

@bot.message_handler(func=lambda msg: msg.text in ["🗓 Вся неделя", "/week", "неделя"])
def handle_week(message):
    uid = message.chat.id
    _, current_key, _ = get_academic_week()
    text = get_week_schedule(uid, current_key)
    bot.send_message(uid, text, parse_mode="Markdown", reply_markup=make_week_switch_inline(current_key))

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_week_"))
def callback_switch_week(call):
    uid = call.message.chat.id
    week_key = call.data.replace("show_week_", "")
    text = get_week_schedule(uid, week_key)
    try:
        bot.edit_message_text(
            text,
            chat_id=uid,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=make_week_switch_inline(week_key)
        )
    except Exception:
        pass
    target = "Числитель" if week_key == "chislitel" else "Знаменатель"
    bot.answer_callback_query(call.id, f"Показан {target}")

@bot.message_handler(func=lambda msg: msg.text and "Подгруппа:" in msg.text)
def handle_change_sub(message):
    uid = message.chat.id
    sub = get_user_subgroup(uid)
    bot.send_message(uid, f"Сейчас выбрана **{sub}-я подгруппа**.\nНажми, чтобы переключить:", reply_markup=make_subgroup_inline(sub), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_sub_"))
def callback_set_sub(call):
    uid = call.message.chat.id
    sub = int(call.data.replace("set_sub_", ""))
    set_user_subgroup(uid, sub)

    bot.answer_callback_query(call.id, f"Выбрана {sub}-я подгруппа!")
    bot.edit_message_text(
        f"✅ Подгруппа переключена на **{sub}-ю**!",
        chat_id=uid,
        message_id=call.message.message_id,
        parse_mode="Markdown"
    )
    status_text = get_current_pair_status(uid)
    bot.send_message(uid, f"Расписание обновлено для {sub}-й подгруппы:\n\n{status_text}", reply_markup=make_main_keyboard(uid), parse_mode="Markdown")

if __name__ == "__main__":
    ensure_single_instance()

    if "pythonw" not in sys.executable.lower() and sys.stdin and hasattr(sys.stdin, 'isatty') and sys.stdin.isatty():
        print("=" * 60)
        print("  Telegram Schedule Bot @paravottut_bot")
        print("  Группа: БАС.5-26-1(И,О)")
        print("  Бот запущен и работает! Не закрывайте это окно.")
        print("=" * 60)

    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            try:
                with open(CRASH_FILE, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now()}] Polling Error: {e}\n{traceback.format_exc()}\n")
            except Exception:
                pass
            time.sleep(3)

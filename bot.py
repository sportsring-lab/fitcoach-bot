#!/usr/bin/env python3
"""
ФитКоуч PRO - Telegram бот (OpenAI version)
"""
ADMIN_ID = 1307723730
import logging
import json
import os
import io
import psycopg2
import psycopg2.extras
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

# ===== НАСТРОЙКИ =====

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# Необязательно: если используешь прокси/совместимый шлюз
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")

# Основная модель для ответов
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

# Модель для транскрибации
TRANSCRIBE_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")

SYSTEM_PROMPT = """Ты — ФитКоуч PRO, профессиональный AI-эксперт по похудению, питанию и физической активности.

═══ РАСХОД КАЛОРИЙ С УЧЁТОМ РАБОТЫ ═══
Всегда используй формулу TDEE = БМ × Коэффициент:

- Офисная/сидячая работа: × 1.2
- Лёгкий физ. труд (продавец, официант): × 1.375
- Умеренный физ. труд (строитель, водитель): × 1.55
- Тяжёлый физ. труд (грузчик, разнорабочий): × 1.725
- Экстремальный труд (шахтёр, спортсмен): × 1.9
Плюс добавляй +5-10% за каждые 3 тренировки в неделю.
ВСЕГДА уточняй профессию пользователя при расчёте нормы калорий, если она не указана!

═══ КОНТРОЛЬ ВЕСА ═══
При получении нового веса — анализируй динамику:
📊 Было: Xкг → Стало: Xкг
Изменение: -/+Xкг за N дней
Темп похудения: ✅ Нормальный (0.5-1кг/нед) / ⚠️ Медленный / 🚀 Слишком быстрый
Рекомендация: [конкретный совет]

═══ СПИСОК ПРОДУКТОВ ═══
При запросе списка продуктов:
1. Учитывай предпочтения и антипатии пользователя из профиля
2. Разбивай по категориям с количеством
3. Добавляй план питания на 3 дня из этих продуктов

═══ КБЖУ ═══
Формат: Калории: X ккал | Б: Xг | Ж: Xг | У: Xг

Отвечай по-русски.
Конкретные цифры, без воды.
Используй эмодзи.
"""

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===== OPENAI CLIENT =====

client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    client_kwargs["base_url"] = OPENAI_BASE_URL

openai_client = OpenAI(**client_kwargs)


def ask_openai(messages: list[dict], max_tokens: int = 1024) -> str:
    """
    Унифицированный вызов OpenAI для текстовых ответов.
    Используем Chat Completions API с system + history/messages.
    """
    response = openai_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        max_completion_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


# ===== БАЗА ДАННЫХ (PostgreSQL) =====

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Создаём таблицу users если не существует"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fitcoach_users (
                    user_id BIGINT PRIMARY KEY,
                    history JSONB NOT NULL DEFAULT '[]',
                    profile JSONB NOT NULL DEFAULT '{}',
                    weight_log JSONB NOT NULL DEFAULT '[]',
                    preferences JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()


def get_user(user_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fitcoach_users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if row:
                return {
                    "history": row["history"],
                    "profile": row["profile"],
                    "weight_log": row["weight_log"],
                    "preferences": row["preferences"],
                }

            cur.execute("""
                INSERT INTO fitcoach_users (user_id, history, profile, weight_log, preferences)
                VALUES (%s, '[]', '{}', '[]', '{}')
            """, (user_id,))
            conn.commit()
            return {"history": [], "profile": {}, "weight_log": [], "preferences": {}}


def update_user(user_id: int, user_data: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO fitcoach_users (user_id, history, profile, weight_log, preferences, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    history = EXCLUDED.history,
                    profile = EXCLUDED.profile,
                    weight_log = EXCLUDED.weight_log,
                    preferences = EXCLUDED.preferences,
                    updated_at = NOW()
            """, (
                user_id,
                json.dumps(user_data.get("history", []), ensure_ascii=False),
                json.dumps(user_data.get("profile", {}), ensure_ascii=False),
                json.dumps(user_data.get("weight_log", []), ensure_ascii=False),
                json.dumps(user_data.get("preferences", {}), ensure_ascii=False),
            ))
            conn.commit()


def get_all_users_with_weight() -> list:
    """Для планировщика напоминаний"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, weight_log FROM fitcoach_users WHERE jsonb_array_length(weight_log) > 0"
            )
            return cur.fetchall()


# ===== ВСПОМОГАТЕЛЬНОЕ =====

def split_long_text(text: str, chunk_size: int = 4000) -> list[str]:
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


async def send_long_message(msg, text: str):
    parts = split_long_text(text, 4000)
    for part in parts:
        await msg.reply_text(part)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Команды ФитКоуч PRO:*\n\n"
        "⚖️ `/weight 80.5` или `/ves 80.5` — записать вес\n"
        "📊 `/history` или `/istoriya` — история веса\n"
        "🎯 `/goal 70` или `/tsel 70` — целевой вес\n"
        "🛒 `/grocery` или `/produkty` — список на неделю\n"
        "⚡ `/tdee` — расчёт суточного расхода\n"
        "👤 `/profile` или `/profil` — профиль\n"
        "🥗 `/preferences` — пищевые предпочтения\n\n"
        "🎤 *Голосовые* — отправь голосовое, распознаю и отвечу!\n\n"
        "Или просто пиши вопрос — я отвечу! 💬\n\n"
        "Пример профиля:\n"
        "`/profil ves=80 rost=178 vozrast=30 pol=m rabota=ofis trenirovki=3`",
        parse_mode="Markdown"
    )


# ===== КОМАНДЫ =====
async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬  Напиши свой вопрос или отзыв — я передам его в поддержку 👇"
    )
    context.user_data["support_mode"] = True
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    user["history"] = []
    update_user(user_id, user)

    keyboard = [
       [ InlineKeyboardButton("💬  Поддержка", callback_data="cmd_support")]
        [
            InlineKeyboardButton("⚖️ Записать вес", callback_data="cmd_weight"),
            InlineKeyboardButton("🛒 Список продуктов", callback_data="cmd_grocery"),
        ],
        [
            InlineKeyboardButton("⚡ Мой расход калорий", callback_data="cmd_tdee"),
            InlineKeyboardButton("👤 Мой профиль", callback_data="cmd_profile"),
        ],
        [
            InlineKeyboardButton("📊 История веса", callback_data="cmd_weight_history"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🏋️ *ФитКоуч PRO* — твой AI-эксперт по питанию!\n\n"
        "Функции:\n"
        "⚖️ Еженедельный контроль веса с аналитикой\n"
        "🛒 Список продуктов под твои предпочтения\n"
        "⚡ Расчёт TDEE с учётом вида работы\n"
        "🎤 Распознавание голосовых сообщений\n\n"
        "Пиши текстом, отправляй голосовые или выбери действие!\n"
        "Справка: /help",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await process_weight(update, context, " ".join(context.args))
    else:
        await update.message.reply_text(
            "⚖️ *Запись веса*\n\n"
            "Напиши свой текущий вес:\n"
            "Пример: `/weight 80.5`\n\n"
            "Я сравню с предыдущим и дам анализ! 📊",
            parse_mode="Markdown"
        )


async def process_weight(update, context, weight_str):
    user_id = update.effective_user.id
    try:
        weight = float(weight_str.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "⚠️ Укажи вес числом, например: `/weight 80.5`",
            parse_mode="Markdown"
        )
        return

    user = get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    prev_entries = user.get("weight_log", [])

    user["weight_log"].append({"date": today, "weight": weight})
    update_user(user_id, user)

    if not prev_entries:
        await update.message.reply_text(
            f"⚖️ Стартовый вес записан: *{weight} кг*\n"
            f"📅 Дата: {today}\n\n"
            "Отлично! Теперь записывай вес каждую неделю, и я буду отслеживать твой прогресс 📈\n\n"
            "💡 Укажи цель командой `/goal 70`",
            parse_mode="Markdown"
        )
        return

    prev = prev_entries[-1]
    prev_weight = prev["weight"]
    prev_date = prev["date"]
    days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(prev_date, "%Y-%m-%d")).days
    diff = weight - prev_weight
    goal_weight = user.get("profile", {}).get("goal_weight")

    prompt = (
        f"Пользователь записал новый вес.\n"
        f"Предыдущий: {prev_weight} кг ({prev_date})\n"
        f"Текущий: {weight} кг ({today})\n"
        f"Прошло дней: {days}\n"
        f"Изменение: {diff:+.1f} кг\n"
        f"Цель по весу: {goal_weight or 'не указана'} кг\n\n"
        "Дай КРАТКИЙ мотивирующий недельный отчёт (4-6 строк):\n"
        "- Оцени темп (нормальный/медленный/слишком быстрый)\n"
        "- 1-2 конкретных совета что скорректировать\n"
        "- Мотивация\n"
        "Используй формат из инструкции."
    )

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = ask_openai(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Error in process_weight: {e}")
        await update.message.reply_text("⚠️ Ошибка при анализе веса. Попробуй снова.")


async def grocery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    profile = user.get("profile", {})
    prefs = user.get("preferences", {})

    kcal = profile.get("target_kcal", 1800)
    goal = profile.get("goal", "похудение")
    likes = prefs.get("likes", "не указано")
    dislikes = prefs.get("dislikes", "нет")

    msg = update.message or (update.callback_query.message if update.callback_query else None)

    await msg.reply_text("🛒 Составляю список продуктов на неделю... ⏳")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    prompt = (
        "Составь список продуктов на 1 неделю.\n"
        f"Цель: {goal}\n"
        f"Суточная норма калорий: {kcal} ккал\n"
        f"Любит: {likes}\n"
        f"Не ест / аллергии: {dislikes}\n\n"
        "Формат:\n"
        "Разбей по категориям (Белки / Овощи и фрукты / Крупы / Молочное / Жиры / Прочее)\n"
        "Укажи количество каждого продукта\n"
        "В конце — план питания на 3 дня из этих продуктов (завтрак/обед/ужин)"
    )

    try:
        text = ask_openai(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
        await send_long_message(msg, text)
    except Exception as e:
        logger.error(f"Error in grocery_command: {e}")
        await msg.reply_text("⚠️ Не удалось составить список продуктов.")


async def tdee_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    profile = user.get("profile", {})

    msg = update.message or (update.callback_query.message if update.callback_query else None)

    if not profile.get("weight") or not profile.get("height") or not profile.get("age"):
        await msg.reply_text(
            "📋 Для расчёта нужен твой профиль!\n\n"
            "Заполни данные командами:\n"
            "`/profile weight=80 height=178 age=30 gender=м`\n\n"
            "Пример:\n`/profile weight=75 height=165 age=25 gender=ж`",
            parse_mode="Markdown"
        )
        return

    await msg.reply_text("⚡ Рассчитываю твой суточный расход... ⏳")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    w = profile["weight"]
    h = profile["height"]
    a = profile["age"]
    gender = profile.get("gender", "м")
    work = profile.get("work_type", "офис")
    trainings = profile.get("trainings_per_week", 3)

    prompt = (
        "Рассчитай TDEE (суточный расход калорий) для:\n"
        f"Пол: {'мужчина' if gender == 'м' else 'женщина'}\n"
        f"Возраст: {a} лет\n"
        f"Вес: {w} кг\n"
        f"Рост: {h} см\n"
        f"Вид работы: {work}\n"
        f"Тренировок в неделю: {trainings}\n\n"
        "Покажи:\n"
        "1. Формулу расчёта БМ (Миффлин-Сан Жеор)\n"
        "2. Коэффициент активности и почему такой\n"
        "3. Итоговый TDEE\n"
        "4. Норма калорий для похудения (−500 ккал)\n"
        "5. Норма БЖУ для похудения\n"
        "6. Конкретный совет под этот профиль"
    )

    try:
        reply = ask_openai(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        await msg.reply_text(reply)
    except Exception as e:
        logger.error(f"Error in tdee_command: {e}")
        await msg.reply_text("⚠️ Не удалось рассчитать TDEE.")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    msg = update.message or (update.callback_query.message if update.callback_query else None)

    if not context.args:
        profile = user.get("profile", {})
        prefs = user.get("preferences", {})
        text = (
            "👤 *Твой профиль:*\n\n"
            f"⚖️ Вес: {profile.get('weight', 'не указан')} кг\n"
            f"📏 Рост: {profile.get('height', 'не указан')} см\n"
            f"🎂 Возраст: {profile.get('age', 'не указан')} лет\n"
            f"👤 Пол: {profile.get('gender', 'не указан')}\n"
            f"💼 Работа: {profile.get('work_type', 'не указана')}\n"
            f"🏋️ Тренировок/нед: {profile.get('trainings_per_week', 'не указано')}\n"
            f"🎯 Цель: {profile.get('goal', 'не указана')}\n"
            f"⚖️ Целевой вес: {profile.get('goal_weight', 'не указан')} кг\n"
            f"🔥 Норма ккал: {profile.get('target_kcal', 'не рассчитана')}\n\n"
            f"🥗 Предпочтения: {prefs.get('likes', 'не указаны')}\n"
            f"🚫 Не ест: {prefs.get('dislikes', 'не указано')}\n\n"
            "Обнови командой:\n"
            "`/profile weight=80 height=178 age=30 gender=м work=офис trainings=3`\n\n"
            "Предпочтения:\n"
            "`/preferences likes=курица,рис,яйца dislikes=морепродукты`"
        )
        await msg.reply_text(text, parse_mode="Markdown")
        return

    profile = user.get("profile", {})
    for arg in context.args:
        if "=" in arg:
            key, val = arg.split("=", 1)
            key_map = {
                "вес": "weight", "рост": "height", "возраст": "age",
                "пол": "gender", "работа": "work_type", "тренировки": "trainings_per_week",
                "цель": "goal", "целевой_вес": "goal_weight", "ккал": "target_kcal",

                "ves": "weight", "rost": "height", "vozrast": "age",
                "pol": "gender", "rabota": "work_type", "trenirovki": "trainings_per_week",
                "tsel": "goal", "tselevoy_ves": "goal_weight", "kkal": "target_kcal",

                "weight": "weight", "height": "height", "age": "age",
                "gender": "gender", "work": "work_type", "trainings": "trainings_per_week",
                "goal": "goal", "goal_weight": "goal_weight", "kcal": "target_kcal",
            }

            if key in key_map:
                mapped = key_map[key]
                try:
                    if mapped in ["weight", "height", "goal_weight", "target_kcal"]:
                        profile[mapped] = float(val)
                    elif mapped in ["age", "trainings_per_week"]:
                        profile[mapped] = int(val)
                    else:
                        profile[mapped] = val
                except ValueError:
                    profile[mapped] = val

    user["profile"] = profile
    update_user(user_id, user)

    await msg.reply_text(
        "✅ Профиль обновлён!\n\n"
        f"Вес: {profile.get('weight', '?')} кг | Рост: {profile.get('height', '?')} см | "
        f"Возраст: {profile.get('age', '?')} | Работа: {profile.get('work_type', '?')}\n\n"
        "Теперь можешь:\n"
        "• `/tdee` — рассчитать суточный расход\n"
        "• `/grocery` — список продуктов на неделю",
        parse_mode="Markdown"
    )


async def preferences_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not context.args:
        await update.message.reply_text(
            "🥗 Укажи предпочтения в еде:\n\n"
            "`/preferences likes=курица,рис,яйца dislikes=морепродукты,лактоза`\n\n"
            "Это поможет мне составить идеальный список продуктов!",
            parse_mode="Markdown"
        )
        return

    prefs = user.get("preferences", {})
    for arg in context.args:
        if "=" in arg:
            key, val = arg.split("=", 1)
            if key in ["люблю", "likes"]:
                prefs["likes"] = val.replace(",", ", ")
            elif key in ["нелюблю", "dislikes"]:
                prefs["dislikes"] = val.replace(",", ", ")

    user["preferences"] = prefs
    update_user(user_id, user)

    await update.message.reply_text(
        f"✅ Предпочтения сохранены!\n\n"
        f"🥗 Люблю: {prefs.get('likes', '—')}\n"
        f"🚫 Не ем: {prefs.get('dislikes', '—')}\n\n"
        "Теперь команда `/grocery` составит список именно под тебя!",
        parse_mode="Markdown"
    )


async def weight_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    log = user.get("weight_log", [])

    msg = update.message or (update.callback_query.message if update.callback_query else None)

    if not log:
        await msg.reply_text(
            "📊 История веса пуста.\n\n"
            "Начни записывать: `/weight 80.5`",
            parse_mode="Markdown"
        )
        return

    text = "📊 *История веса:*\n\n"
    for i, entry in enumerate(reversed(log[-10:])):
        idx = len(log) - 1 - i
        prev = log[idx - 1] if idx > 0 else None
        if prev:
            diff = entry["weight"] - prev["weight"]
            arrow = "▼" if diff < 0 else "▲" if diff > 0 else "→"
            diff_text = f" {arrow} {abs(diff):.1f}кг"
        else:
            diff_text = " (старт)"
        text += f"📅 {entry['date']}: *{entry['weight']} кг*{diff_text}\n"

    if len(log) >= 2:
        total = log[-1]["weight"] - log[0]["weight"]
        text += f"\n{'✅' if total < 0 else '📈'} Всего: *{total:+.1f} кг* за весь период"

    goal = user.get("profile", {}).get("goal_weight")
    if goal:
        to_go = log[-1]["weight"] - goal
        text += f"\n🎯 До цели ({goal} кг): *{to_go:.1f} кг*"

    await msg.reply_text(text, parse_mode="Markdown")


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not context.args:
        await update.message.reply_text(
            "🎯 Укажи целевой вес:\n`/goal 70`",
            parse_mode="Markdown"
        )
        return

    try:
        goal = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "⚠️ Укажи вес числом, например: `/goal 70`",
            parse_mode="Markdown"
        )
        return

    profile = user.get("profile", {})
    profile["goal_weight"] = goal
    user["profile"] = profile
    update_user(user_id, user)

    current = profile.get("weight")
    if current:
        to_go = current - goal
        await update.message.reply_text(
            f"🎯 Цель установлена: *{goal} кг*\n\n"
            f"Осталось до цели: *{to_go:.1f} кг*\n\n"
            "Продолжай записывать вес командой `/weight`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"🎯 Цель установлена: *{goal} кг*\n\n"
            "Запиши свой текущий вес командой `/weight 80.5`",
            parse_mode="Markdown"
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cmd_weight":
        await query.message.reply_text(
            "⚖️ Запиши вес командой:\n`/weight 80.5`",
            parse_mode="Markdown"
        )
    elif query.data == "cmd_grocery":
        await grocery_command(update, context)
    elif query.data == "cmd_tdee":
        await tdee_command(update, context)
    elif query.data == "cmd_profile":
        await profile_command(update, context)
    elif query.data == "cmd_weight_history":
        await weight_history_command(update, context)
    elif query.data == "cmd_support":
        await query.message.reply_text("💬  Напиши свой вопрос — я передам его в поддержку")
    context.user_data["support_mode"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    user = get_user(user_id)

    # Проверяем — это запись веса? (просто число)
    try:
        weight = float(text.replace(",", ".").replace("кг", "").strip())
        if 30 < weight < 300:
            await process_weight(update, context, str(weight))
            return
    except ValueError:
        pass

    profile = user.get("profile", {})
    prefs = user.get("preferences", {})

    context_str = ""
    if profile:
        context_str = (
            f"\n[Профиль пользователя: вес={profile.get('weight','?')}кг, "
            f"рост={profile.get('height','?')}см, возраст={profile.get('age','?')}, "
            f"работа={profile.get('work_type','?')}, "
            f"тренировки={profile.get('trainings_per_week','?')}/нед, "
            f"предпочтения={prefs.get('likes','нет')}, "
            f"не ест={prefs.get('dislikes','нет')}]\n"
        )

    history = user.get("history", [])
    history.append({"role": "user", "content": context_str + text})
    if len(history) > 20:
        history = history[-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = ask_openai(messages=history, max_tokens=1024)

        history.append({"role": "assistant", "content": reply})
        user["history"] = history
        update_user(user_id, user)

        await send_long_message(update.message, reply)

    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text("⚠️ Ошибка при обращении к AI. Попробуй снова.")

if context.user_data.get("support_mode"):
    user_id = update.effective_user.id
    text = update.message.text

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩  Сообщение от пользователя {user_id}:\n\n{text}"
    )

    await update.message.reply_text(
        "✅ Сообщение отправлено! Мы ответим тебе в ближайшее время."
    )

    context.user_data["support_mode"] = False
    return
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Распознавание голосовых сообщений через OpenAI"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice = update.message.voice
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)
        buf.name = "voice.ogg"

        transcription = openai_client.audio.transcriptions.create(
            model=TRANSCRIBE_MODEL,
            file=buf,
            language="ru",
        )
        text = transcription.text.strip()

        if not text:
            await update.message.reply_text("⚠️ Не удалось распознать речь. Попробуй снова.")
            return

        logger.info(f"Voice transcribed for user {update.effective_user.id}: {text[:80]}")
        await update.message.reply_text(f"🎤 *Распознано:* _{text}_", parse_mode="Markdown")

        update.message.text = text
        await handle_message(update, context)

    except Exception as e:
        logger.error(f"Voice recognition error: {e}")
        await update.message.reply_text(
            "⚠️ Не удалось распознать голосовое сообщение.\n"
            "Попробуй написать текстом."
        )


async def weekly_weight_reminder(context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_users_with_weight()
    for row in rows:
        uid = row["user_id"]
        log = row["weight_log"]
        if not log:
            continue

        last_date = datetime.strptime(log[-1]["date"], "%Y-%m-%d")
        if (datetime.now() - last_date).days >= 7:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        "⚖️ Привет! Прошла неделя — время записать вес!\n\n"
                        f"Последний раз ты записывал {log[-1]['weight']} кг ({log[-1]['date']})\n\n"
                        "Напиши просто число, например: `82.3`"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Could not send reminder to {uid}: {e}")


def main():
    logger.info("🚀 ФитКоуч PRO запускается…")

    init_db()
    logger.info("✅ База данных инициализирована")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("weight", weight_command))
    app.add_handler(CommandHandler("ves", weight_command))

    app.add_handler(CommandHandler("grocery", grocery_command))
    app.add_handler(CommandHandler("produkty", grocery_command))

    app.add_handler(CommandHandler("tdee", tdee_command))

    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("profil", profile_command))

    app.add_handler(CommandHandler("preferences", preferences_command))
    app.add_handler(CommandHandler("predpochteniya", preferences_command))

    app.add_handler(CommandHandler("history", weight_history_command))
    app.add_handler(CommandHandler("istoriya", weight_history_command))

    app.add_handler(CommandHandler("goal", goal_command))
    app.add_handler(CommandHandler("tsel", goal_command))

    # Кнопки, текстовые и голосовые сообщения
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Напоминания
    job_queue = app.job_queue
    if job_queue is not None:
        job_queue.run_repeating(weekly_weight_reminder, interval=86400, first=3600)
        logger.info("✅ Планировщик напоминаний активирован")
    else:
        logger.warning("⚠️ JobQueue недоступен — напоминания о весе отключены")

    logger.info("✅ Бот запущен и ожидает сообщений")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

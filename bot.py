#!/usr/bin/env python3
"""
ФитКоуч PRO - Telegram бот на OpenAI
"""

import logging
import json
import os
import io
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
Application, CommandHandler, MessageHandler,
CallbackQueryHandler, filters, ContextTypes
)
from openai import OpenAI

# ===== НАСТРОЙКИ =====

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = "gpt-4o-mini"

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """Ты — ФитКоуч PRO, профессиональный AI-эксперт по похудению, питанию и физической активности.

═══ РАСХОД КАЛОРИЙ С УЧЁТОМ РАБОТЫ ═══
Всегда используй формулу TDEE = БМ × Коэффициент:

- Офисная/сидячая работа: × 1.2
- Лёгкий физ. труд (продавец, официант): × 1.375
- Умеренный физ. труд (строитель, водитель): × 1.55
- Тяжёлый физ. труд (грузчик, разнорабочий): × 1.725
- Экстремальный труд (шахтёр, спортсмен): × 1.9

Плюс добавляй +5-10% за каждые 3 тренировки в неделю.
ВСЕГДА уточняй профессию пользователя при расчёте нормы калорий!

═══ КОНТРОЛЬ ВЕСА ═══
При получении нового веса — анализируй динамику:
📊 Было: Xкг → Стало: Xкг
Изменение: -/+Xкг за N дней
Темп похудения: ✅ Нормальный (0.5-1кг/нед) / ⚠️ Медленный / 🚀 Слишком быстрый
Рекомендация: [конкретный совет]

═══ СПИСОК ПРОДУКТОВ ═══
При запросе списка продуктов:

1. Учитывай предпочтения и антипатии пользователя из профиля
1. Разбивай по категориям с количеством
1. Добавляй план питания на 3 дня из этих продуктов

═══ КБЖУ ═══
Формат: Калории: X ккал | Б: Xг | Ж: Xг | У: Xг

Отвечай по-русски. Конкретные цифры, без воды. Используй эмодзи."""

logging.basicConfig(
format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== ХРАНИЛИЩЕ В ПАМЯТИ =====

# При рестарте данные сбрасываются. Для постоянного хранения нужна БД.

USERS = {}

def get_user (user_id: int) -> dict:
uid = str (user_id)
if uid not in USERS:
USERS[uid] = {
"history": [],
"profile": {},
"weight_log": [],
"preferences": {},
}
return USERS[uid]

def update_user(user_id: int, user_data: dict):
USERS[str(user_id)] = user_data

# ===== КОМАНДЫ =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)
user["history"] = []
update_user(user_id, user)

```
keyboard = [
    [InlineKeyboardButton("⚖️ Записать вес", callback_data="cmd_weight"),
     InlineKeyboardButton("🛒 Список продуктов", callback_data="cmd_grocery")],
    [InlineKeyboardButton("⚡ Мой расход калорий", callback_data="cmd_tdee"),
     InlineKeyboardButton("👤 Мой профиль", callback_data="cmd_profile")],
    [InlineKeyboardButton("📊 История веса", callback_data="cmd_weight_history")],
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
```

async def process_weight(update, context, weight_str):
user_id = update.effective_user.id
try:
weight = float(weight_str.replace(",", "."))
except ValueError:
await update.message.reply_text("⚠️ Укажи вес числом, например: `/ves 80.5`", parse_mode="Markdown")
return

```
user = get_user(user_id)
today = datetime.now().strftime("%Y-%m-%d")
prev_entries = user.get("weight_log", [])

user["weight_log"].append({"date": today, "weight": weight})
update_user(user_id, user)

if not prev_entries:
    await update.message.reply_text(
        f"⚖️ Стартовый вес записан: *{weight} кг*\n"
        f"📅 Дата: {today}\n\n"
        f"Отлично! Записывай вес каждую неделю — буду отслеживать прогресс 📈\n\n"
        f"💡 Укажи цель командой `/tsel 70`",
        parse_mode="Markdown"
    )
    return

prev = prev_entries[-1]
days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(prev["date"], "%Y-%m-%d")).days
diff = weight - prev["weight"]
goal_weight = user.get("profile", {}).get("goal_weight")

prompt = (
    f"Пользователь записал новый вес.\n"
    f"Предыдущий: {prev['weight']}кг ({prev['date']})\n"
    f"Текущий: {weight}кг ({today})\n"
    f"Прошло дней: {days}\n"
    f"Изменение: {diff:+.1f}кг\n"
    f"Цель: {goal_weight or 'не указана'}кг\n\n"
    f"Дай КРАТКИЙ мотивирующий отчёт (4-6 строк): оцени темп, дай 1-2 совета, мотивируй."
)

await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
response = client.chat.completions.create(
    model=MODEL, max_tokens=500,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
)
await update.message.reply_text(response.choices[0].message.content)
```

async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
if context.args:
await process_weight(update, context, " ".join(context.args))
else:
await update.message.reply_text(
"⚖️ Напиши вес:\n`/ves 80.5`",
parse_mode="Markdown"
)

async def grocery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)
profile = user.get("profile", {})
prefs = user.get("preferences", {})

```
msg = update.message or (update.callback_query.message if update.callback_query else None)
await msg.reply_text("🛒 Составляю список продуктов... ⏳")
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

prompt = (
    f"Составь список продуктов на 1 неделю.\n"
    f"Цель: {profile.get('goal', 'похудение')}\n"
    f"Норма калорий: {profile.get('target_kcal', 1800)} ккал\n"
    f"Любит: {prefs.get('likes', 'не указано')}\n"
    f"Не ест: {prefs.get('dislikes', 'нет')}\n\n"
    f"Разбей по категориям, укажи количество, добавь план питания на 3 дня."
)

response = client.chat.completions.create(
    model=MODEL, max_tokens=1200,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
)
text = response.choices[0].message.content
if len(text) > 4000:
    for part in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await msg.reply_text(part)
else:
    await msg.reply_text(text)
```

async def tdee_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)
profile = user.get("profile", {})
msg = update.message or (update.callback_query.message if update.callback_query else None)

```
if not profile.get("weight") or not profile.get("height") or not profile.get("age"):
    await msg.reply_text(
        "📋 Сначала заполни профиль:\n`/profil ves=80 rost=178 vozrast=30 pol=m rabota=voditel trenirovki=3`",
        parse_mode="Markdown"
    )
    return

await msg.reply_text("⚡ Рассчитываю... ⏳")
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

prompt = (
    f"Рассчитай TDEE для:\n"
    f"Пол: {'мужчина' if profile.get('gender','м') == 'м' else 'женщина'}\n"
    f"Возраст: {profile['age']} лет, Вес: {profile['weight']} кг, Рост: {profile['height']} см\n"
    f"Работа: {profile.get('work_type','офис')}, Тренировок/нед: {profile.get('trainings_per_week',3)}\n\n"
    f"Покажи: формулу БМ, коэффициент активности, итоговый TDEE, норму для похудения (-500 ккал), БЖУ, совет."
)

response = client.chat.completions.create(
    model=MODEL, max_tokens=800,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
)
await msg.reply_text(response.choices[0].message.content)
```

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)
msg = update.message or (update.callback_query.message if update.callback_query else None)

```
if not context.args:
    profile = user.get("profile", {})
    prefs = user.get("preferences", {})
    await msg.reply_text(
        "👤 *Твой профиль:*\n\n"
        f"⚖️ Вес: {profile.get('weight','—')} кг\n"
        f"📏 Рост: {profile.get('height','—')} см\n"
        f"🎂 Возраст: {profile.get('age','—')} лет\n"
        f"👤 Пол: {profile.get('gender','—')}\n"
        f"💼 Работа: {profile.get('work_type','—')}\n"
        f"🏋️ Тренировок/нед: {profile.get('trainings_per_week','—')}\n"
        f"🎯 Цель: {profile.get('goal','—')}\n"
        f"⚖️ Целевой вес: {profile.get('goal_weight','—')} кг\n\n"
        f"🥗 Люблю: {prefs.get('likes','—')}\n"
        f"🚫 Не ем: {prefs.get('dislikes','—')}\n\n"
        "Обнови:\n`/profil ves=80 rost=178 vozrast=30 pol=m rabota=voditel trenirovki=3`",
        parse_mode="Markdown"
    )
    return

profile = user.get("profile", {})
key_map = {
    "ves": "weight", "rost": "height", "vozrast": "age",
    "pol": "gender", "rabota": "work_type", "trenirovki": "trainings_per_week",
    "tsel": "goal", "tselevoy_ves": "goal_weight", "kkal": "target_kcal",
    "weight": "weight", "height": "height", "age": "age",
    "gender": "gender", "work": "work_type", "trainings": "trainings_per_week",
}
for arg in context.args:
    if "=" in arg:
        key, val = arg.split("=", 1)
        if key in key_map:
            mapped = key_map[key]
            try:
                profile[mapped] = float(val) if mapped in ["weight", "height", "goal_weight", "target_kcal"] else (
                    int(val) if mapped in ["age", "trainings_per_week"] else val
                )
            except ValueError:
                profile[mapped] = val

user["profile"] = profile
update_user(user_id, user)
await msg.reply_text(
    f"✅ Профиль обновлён!\n"
    f"Вес: {profile.get('weight','?')} кг | Рост: {profile.get('height','?')} см | "
    f"Возраст: {profile.get('age','?')} | Работа: {profile.get('work_type','?')}",
    parse_mode="Markdown"
)
```

async def preferences_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)

```
if not context.args:
    await update.message.reply_text(
        "🥗 Укажи предпочтения:\n`/prefs lyublyu=kurica,ris nelublyu=moreprodukty`",
        parse_mode="Markdown"
    )
    return

prefs = user.get("preferences", {})
for arg in context.args:
    if "=" in arg:
        key, val = arg.split("=", 1)
        if key in ["lyublyu", "likes"]:
            prefs["likes"] = val.replace(",", ", ")
        elif key in ["nelublyu", "dislikes"]:
            prefs["dislikes"] = val.replace(",", ", ")

user["preferences"] = prefs
update_user(user_id, user)
await update.message.reply_text(
    f"✅ Сохранено!\n🥗 Люблю: {prefs.get('likes','—')}\n🚫 Не ем: {prefs.get('dislikes','—')}"
)
```

async def weight_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)
log = user.get("weight_log", [])
msg = update.message or (update.callback_query.message if update.callback_query else None)

```
if not log:
    await msg.reply_text("📊 История пуста. Начни: `/ves 80.5`", parse_mode="Markdown")
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
    text += f"\n{'✅' if total < 0 else '📈'} Всего: *{total:+.1f} кг*"

goal = user.get("profile", {}).get("goal_weight")
if goal:
    to_go = log[-1]["weight"] - goal
    text += f"\n🎯 До цели ({goal}кг): *{to_go:.1f} кг*"

await msg.reply_text(text, parse_mode="Markdown")
```

async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
user = get_user(user_id)

```
if not context.args:
    await update.message.reply_text("🎯 Укажи цель: `/tsel 70`", parse_mode="Markdown")
    return

try:
    goal = float(context.args[0].replace(",", "."))
except ValueError:
    await update.message.reply_text("⚠️ Укажи числом: `/tsel 70`", parse_mode="Markdown")
    return

profile = user.get("profile", {})
profile["goal_weight"] = goal
user["profile"] = profile
update_user(user_id, user)

current = profile.get("weight")
if current:
    to_go = current - goal
    await update.message.reply_text(f"🎯 Цель: *{goal} кг*\nОсталось: *{to_go:.1f} кг*", parse_mode="Markdown")
else:
    await update.message.reply_text(f"🎯 Цель: *{goal} кг*\nЗапиши вес: `/ves 80.5`", parse_mode="Markdown")
```

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.callback_query
await query.answer()

```
if query.data == "cmd_weight":
    await query.message.reply_text("⚖️ Запиши вес:\n`/ves 80.5`", parse_mode="Markdown")
elif query.data == "cmd_grocery":
    await grocery_command(update, context)
elif query.data == "cmd_tdee":
    await tdee_command(update, context)
elif query.data == "cmd_profile":
    await profile_command(update, context)
elif query.data == "cmd_weight_history":
    await weight_history_command(update, context)
```

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
try:
tg_file = await context.bot.get_file(update.message.voice.file_id)
buf = io.BytesIO()
await tg_file.download_to_memory(buf)
buf.seek(0)
buf.name = "voice.ogg"

```
    transcription = client.audio.transcriptions.create(
        model="whisper-1", file=buf, language="ru"
    )
    text = transcription.text.strip()

    if not text:
        await update.message.reply_text("⚠️ Не удалось распознать. Напиши текстом.")
        return

    await update.message.reply_text(f"🎤 *Распознано:* _{text}_", parse_mode="Markdown")
    update.message.text = text
    await handle_message(update, context)

except Exception as e:
    logger.error(f"Voice error: {e}")
    await update.message.reply_text("⚠️ Ошибка голосового. Напиши текстом.")
```

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
user_id = update.effective_user.id
text = update.message.text
user = get_user(user_id)

```
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
        f"[Профиль: вес={profile.get('weight','?')}кг, рост={profile.get('height','?')}см, "
        f"возраст={profile.get('age','?')}, работа={profile.get('work_type','?')}, "
        f"тренировки={profile.get('trainings_per_week','?')}/нед, "
        f"любит={prefs.get('likes','нет')}, не ест={prefs.get('dislikes','нет')}]\n"
    )

history = user.get("history", [])
history.append({"role": "user", "content": context_str + text})
if len(history) > 20:
    history = history[-20:]

await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

try:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    response = client.chat.completions.create(model=MODEL, max_tokens=1024, messages=messages)
    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    user["history"] = history
    update_user(user_id, user)

    if len(reply) > 4096:
        for part in [reply[i:i+4096] for i in range(0, len(reply), 4096)]:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(reply)

except Exception as e:
    logger.error(f"Message error: {e}")
    await update.message.reply_text("⚠️ Ошибка. Попробуй снова.")
```

def main():
logger.info("🚀 ФитКоуч PRO запускается…")
app = Application.builder().token(TELEGRAM_TOKEN).build()

```
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("ves", weight_command))
app.add_handler(CommandHandler("weight", weight_command))
app.add_handler(CommandHandler("produkty", grocery_command))
app.add_handler(CommandHandler("grocery", grocery_command))
app.add_handler(CommandHandler("tdee", tdee_command))
app.add_handler(CommandHandler("profil", profile_command))
app.add_handler(CommandHandler("profile", profile_command))
app.add_handler(CommandHandler("prefs", preferences_command))
app.add_handler(CommandHandler("istoriya", weight_history_command))
app.add_handler(CommandHandler("history", weight_history_command))
app.add_handler(CommandHandler("tsel", goal_command))
app.add_handler(CommandHandler("goal", goal_command))
app.add_handler(CommandHandler("help", lambda u, c: u.message.reply_text(
    "📖 *Команды:*\n\n"
    "⚖️ `/ves 80.5` — записать вес\n"
    "🎯 `/tsel 70` — целевой вес\n"
    "📊 `/istoriya` — история веса\n"
    "🛒 `/produkty` — список продуктов\n"
    "⚡ `/tdee` — расход калорий\n"
    "👤 `/profil` — профиль\n"
    "🥗 `/prefs` — предпочтения\n\n"
    "🎤 Голосовые сообщения — поддерживаются!\n\n"
    "Пример профиля:\n"
    "`/profil ves=80 rost=178 vozrast=30 pol=m rabota=voditel trenirovki=3`",
    parse_mode="Markdown"
)))

app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

logger.info("✅ Бот запущен!")
app.run_polling(allowed_updates=Update.ALL_TYPES)
```

if __name__ == "__main__":
main()

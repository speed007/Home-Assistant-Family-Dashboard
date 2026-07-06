import os
import logging
import json
import requests
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import db

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT_RAW = os.getenv("MQTT_PORT")
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

_REQUIRED = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
    "MQTT_BROKER": MQTT_BROKER,
    "MQTT_PORT": MQTT_PORT_RAW,
    "MQTT_USER": MQTT_USER,
    "MQTT_PASS": MQTT_PASS,
}

def _check_required_env():
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        raise RuntimeError(
            "🚫 Missing required .env variable(s): " + ", ".join(missing)
        )

_check_required_env()
MQTT_PORT = int(MQTT_PORT_RAW)

# Path to external meal configuration file
MEAL_PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meal_plan.json")

def load_weekly_meal_plan():
    try:
        if os.path.exists(MEAL_PLAN_PATH):
            with open(MEAL_PLAN_PATH, "r", encoding="utf-8") as f:
                logger.info("🍽️ Successfully loaded external weekly meal plan configuration.")
                return json.load(f)
        else:
            logger.warning("⚠️ meal_plan.json not found! Falling back to empty menu defaults.")
            return {}
    except Exception as e:
        logger.error(f"❌ Failed to parse meal_plan.json: {e}")
        return {}

WEEKLY_MEAL_PLAN = load_weekly_meal_plan()


def parse_uk_date(date_str: str) -> str | None:
    cleaned = date_str.replace('.', '-').replace('/', '-')
    match = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2,4})$", cleaned)
    if not match:
        return None
    day, month, year = match.groups()
    if len(year) == 2:
        year = f"20{year}"
    try:
        validated_date = datetime(int(year), int(month), int(day))
        return validated_date.strftime("%Y-%m-%d")
    except ValueError:
        return None


def trigger_ha_note_event(text: str, author: str):
    if not HA_URL or not HA_TOKEN:
        return
    try:
        url = f"{HA_URL}/api/events/telegram_note_posted"
        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        payload = {"message": text, "sender": author}
        requests.post(url, headers=headers, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Failed to forward event packet to HA: {e}")


def publish_to_dashboard(topic: str, payload_dict: dict):
    try:
        client = mqtt.Client()
        if MQTT_USER and MQTT_PASS:
            client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        client.publish(topic, json.dumps(payload_dict), qos=1, retain=True)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        logger.error(f"MQTT Publish sequence failure on topic {topic}: {e}")


def publish_shopping():
    publish_to_dashboard("home/dashboard/shopping_list", {"items": db.get_shopping()})

def publish_meals():
    publish_to_dashboard("home/dashboard/meal_plan", {"meals": db.get_meals()})

def publish_notes():
    publish_to_dashboard("home/dashboard/daily_notes", {"notes": db.get_daily_notes()})

def publish_appointments():
    publish_to_dashboard("home/dashboard/manual_appointments", {"events": db.get_appointments()})


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Family Hub Group Bot Online!\n\n"
        "🔒 *Strict Mode Active:* The bot will ignore normal group chat. "
        "It only triggers when messages explicitly begin with family system keywords.\n\n"
        "🛒 *Shopping:* `need milk`, `buy apples`, `add to grocery eggs`\n"
        "📋 *Notes:* `note lock back door`, `memo fix tap`, `sticky grab keys`\n"
        "📅 *Schedules:* `schedule dentist on 12/07`, `appt 15/07 MOT`\n"
        "🍽️ *Meals:* `menu monday burgers`, `eat friday pizza`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_text = update.message.text.strip()
        user_name = update.message.from_user.first_name or "Family Member"
        low_text = raw_text.lower().strip()

        db.prune_expired_appointments()

        # --- GLOBAL EXPLICIT SYSTEM COMMANDS ---
        if low_text in [
            "shopping done", "been to shopping", "done shopping", "clear shopping",
            "cleared shopping", "finished shopping", "emptied shopping", "clear shopping list"
        ]:
            db.clear_shopping()
            await update.message.reply_text("🛒 Shopping list completely cleared!")
            publish_shopping()
            return

        if low_text in ["clear menu", "clear meal plan", "reset menu", "delete menu"]:
            with db._connect() as conn:
                conn.execute("DELETE FROM meal_overrides")
                conn.commit()
            await update.message.reply_text("🍽️ Meal overrides cleared! Reverted to default rotation schedule.")
            publish_meals()
            return

        if low_text in ["clear notes", "clear sticky", "delete notes", "clear notes stack"]:
            db.clear_daily_notes()
            await update.message.reply_text("📋 Notes stack cleared.")
            publish_notes()
            return

        if low_text in ["clear appointments", "clear calendar", "clear schedule"]:
            db.clear_appointments()
            await update.message.reply_text("📅 All manual calendar entries wiped.")
            publish_appointments()
            return

        # --- EXPLICIT DISPLAY COMMANDS ---
        if re.match(r"^(list|view|show|get)\s+(shop|item|grocer)", low_text) or low_text in ["whats on the list", "what are we buying", "what's on the list"]:
            items = db.get_shopping()
            msg = "🛒 *Current Shopping List:*\n" + ("_Empty_" if not items else "\n".join(f"• {i}" for i in items))
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if low_text in ["list notes", "view notes", "notes", "show notes", "sticky notes", "memos"]:
            current_notes = db.get_daily_notes()
            msg = "📋 *Active Family Notes:*\n" + ("_No notes_" if not current_notes else "\n".join(f"*{n['index']}•* {n['text']}" for n in current_notes))
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if re.match(r"^(list|view|show)\s+(appt|calendar|event|sched)", low_text) or low_text in ["whats on today", "any appointments", "schedule", "calendar"]:
            appts = db.get_appointments()
            if not appts:
                await update.message.reply_text("📅 No manually tracked appointments found.")
            else:
                msg = "📅 *Manual Calendar Events:*\n"
                for a in appts:
                    when = f"[{a['date']} {a['time'] or ''}]" if a['date'] else "[Unscheduled]"
                    msg += f"*{a['index']}•* {when} {a['title']}\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if re.match(r"^(list|view|show)\s+(meal|menu|food|dinner)", low_text) or low_text in ["meals", "whats for dinner", "what's for dinner", "menu", "food", "meal plan"]:
            current_overrides = db.get_meals()
            now_dt = datetime.now()
            tom_dt = now_dt + timedelta(days=1)
            day_today = now_dt.strftime("%A").lower()
            day_tomorrow = tom_dt.strftime("%A").lower()

            if "today" in current_overrides:
                today_display = f"⚠️ *Override:* {current_overrides['today']}"
            elif day_today in current_overrides:
                today_display = f"⚠️ *Override ({day_today.capitalize()}):* {current_overrides[day_today]}"
            else:
                today_display = "\n".join(f"• {m}" for m in WEEKLY_MEAL_PLAN.get(day_today, ["None configured"]))

            if "tomorrow" in current_overrides:
                tomorrow_display = f"⚠️ *Override:* {current_overrides['tomorrow']}"
            elif day_tomorrow in current_overrides:
                tomorrow_display = f"⚠️ *Override ({day_tomorrow.capitalize()}):* {current_overrides[day_tomorrow]}"
            else:
                tomorrow_display = "\n".join(f"• {m}" for m in WEEKLY_MEAL_PLAN.get(day_tomorrow, ["None configured"]))

            msg = f"🍽️ *Family Menu Outlook*\n\n📅 *TODAY:* \n{today_display}\n\n📅 *TOMORROW:*\n{tomorrow_display}"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return


        # --- STRICT REGEX MATCHERS ---
        # Added structural lookahead \s+ to require trailing context text so empty commands or chat phrases don't trip it.
        note_match = re.match(r"^(?:note|sticky|remind|remember|memo)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        buy_match = re.match(r"^(?:buy|add\s+to\s+shopping\s+list|add\s+to\s+shopping|add\s+to\s+grocery|add\s+to\s+groceries|add|get|shop|need|want)(?:\s+some|\s+to|\s+more)?\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        put_on_list_match = re.match(r"^put\b[,\s]+(.+)\s+on\s+(?:the\s+)?list", raw_text, re.IGNORECASE)
        remove_match = re.match(r"^(?:remove|delete|cancel|drop|bought)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        meal_match = re.match(r"^(?:meal|dinner|food|menu|eat)\s+(today|tomorrow|monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|saturday|sat|sunday|sun)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        appt_match = re.match(r"^(?:appt|appointment|book|schedule|event|calendar)\b[,\s]+(.+)", raw_text, re.IGNORECASE)

        # --- EXECUTION ACTIONS ROUTER ---

        # 1. Active Notes Stack Trigger
        if note_match:
            note_content = note_match.group(1).strip()
            db.add_daily_note(note_content, user_name)
            await update.message.reply_text(f"📋 Note posted: \"{note_content}\"")
            trigger_ha_note_event(note_content, user_name)
            publish_notes()
            return

        # 2. Meal Rotation Overwrite Override Trigger
        elif meal_match:
            day_target = meal_match.group(1).lower()
            meal_content = meal_match.group(2).strip()
            day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
            if day_target in day_map:
                day_target = day_map[day_target]

            db.set_meal(day_target, meal_content)
            await update.message.reply_text(f"🍽️ Meal updated for {day_target.capitalize()}: \"{meal_content}\"")
            publish_meals()
            return

        # 3. Dynamic Targeted Removals Engine
        elif remove_match:
            remaining = remove_match.group(1).strip()

            # Sub-parsing target logic block (e.g. "remove appt 3" or "delete note 1")
            appt_rem = re.match(r"^(?:appt|appointment|book|event|schedule|calendar)\b[,\s]+(.+)", remaining, re.IGNORECASE)
            if appt_rem:
                target = appt_rem.group(1).strip()
                if target.isdigit():
                    target_idx = int(target)
                    if db.delete_appointment_by_index(target_idx):
                        await update.message.reply_text(f"🗑️ Removed appointment #{target_idx}.")
                        publish_appointments()
                    else:
                        await update.message.reply_text(f"❓ Appointment #{target_idx} not found.")
                else:
                    if db.delete_appointment_by_text(target):
                        await update.message.reply_text(f"🗑️ Removed appointment matching: \"{target}\"")
                        publish_appointments()
                    else:
                        await update.message.reply_text(f"❓ No match found for: '{target}'.")
                return

            note_rem = re.match(r"^(?:note|sticky|memo)\b[,\s]+(.+)", remaining, re.IGNORECASE)
            if note_rem:
                raw_target = note_rem.group(1).strip()
                if raw_target.isdigit():
                    target_idx = int(raw_target)
                    if db.delete_note_by_index(target_idx):
                        await update.message.reply_text(f"🗑️ Deleted note #{target_idx}.")
                        publish_notes()
                    else:
                        await update.message.reply_text(f"❓ Note #{target_idx} doesn't exist.")
                else:
                    if db.delete_note_by_text(raw_target):
                        await update.message.reply_text("🗑️ Deleted note matching phrase.")
                        publish_notes()
                    else:
                        await update.message.reply_text("❓ Note phrase not found.")
                return

            # Baseline removal fallback targeted at shopping list
            if db.delete_shopping_item(remaining):
                await update.message.reply_text(f"🗑️ Removed '{remaining}' from shopping list.")
                publish_shopping()
            else:
                await update.message.reply_text(f"❓ '{remaining}' is not on the shopping list.")
            return

        # 4. Schedule Entry Manual Calendar Additions Trigger
        elif appt_match:
            rest = appt_match.group(1).strip()
            parts = rest.split(maxsplit=2)
            date_val, time_val, title_val = None, None, None

            if len(parts) == 3:
                parsed_iso = parse_uk_date(parts[0])
                if parsed_iso:
                    date_val, time_val, title_val = parsed_iso, parts[1], parts[2]
                else:
                    title_val = rest
            elif len(parts) == 2:
                parsed_iso = parse_uk_date(parts[0])
                if parsed_iso:
                    date_val, title_val = parsed_iso, parts[1]
                else:
                    title_val = rest
            else:
                title_val = rest

            db.add_appointment(title_val, date=date_val, time=time_val)
            publish_appointments()
            display_when = f"on {date_val}" if date_val else "unscheduled"
            if time_val:
                display_when += f" at {time_val}"
            await update.message.reply_text(f"📅 Appointment added ({display_when}): \"{title_val}\"")
            return

        # 5. Smart Grocery Target List Trigger
        item_to_add = None
        
        # Check structural formatting symbols first (multi-line lists)
        if raw_text.startswith(('-', '*', '▫️', '•')):
            item_to_add = re.sub(r"^[-\*▫️•]\s*", "", raw_text).strip()
        elif buy_match:
            item_to_add = buy_match.group(1).strip()
        elif put_on_list_match:
            item_to_add = put_on_list_match.group(1).strip()

        if item_to_add:
            if db.add_shopping(item_to_add):
                await update.message.reply_text(f"🛒 Added '{item_to_add}' to shopping list.")
                publish_shopping()
            else:
                await update.message.reply_text(f"❌ '{item_to_add}' is already on the list!")
            return

        # --- SILENT CASCADE DROP ---
        # If the text falls through to this point, it is treated as normal family conversation.
        # It gets dropped quietly without executing database actions or responding.
        logger.info(f"💬 Ignored group chat conversation line: '{raw_text}'")

    except Exception as e:
        logger.exception(f"Unhandled system trace exception during handling: {e}")


def main():
    db.init_db()
    logger.info(f"💾 SQLite database ready at {db.DB_PATH}")
    db.prune_expired_appointments()

    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 System boot secured. Telegram Dispatch Bot listening in strict command filter mode...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
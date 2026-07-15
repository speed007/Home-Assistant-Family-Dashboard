import os
import sys
import logging
import json
import requests
import re
import signal
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
HA_CALENDAR_ENTITY = os.getenv("HA_CALENDAR_ENTITY", "calendar.family_events")

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
        logger.critical(
            "Missing required .env variable(s): %s — check your .env file against .env-example.",
            ", ".join(missing),
        )
        raise SystemExit(1)


_check_required_env()

try:
    MQTT_PORT = int(MQTT_PORT_RAW)
except ValueError:
    logger.critical(f"MQTT_PORT must be a number, got: {MQTT_PORT_RAW!r}")
    raise SystemExit(1)

MEAL_PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meal_plan.json")


_WEEKLY_MEAL_PLAN_CACHE = None


def load_weekly_meal_plan():
    global _WEEKLY_MEAL_PLAN_CACHE
    if _WEEKLY_MEAL_PLAN_CACHE is not None:
        return _WEEKLY_MEAL_PLAN_CACHE
    try:
        if os.path.exists(MEAL_PLAN_PATH):
            with open(MEAL_PLAN_PATH, "r", encoding="utf-8") as f:
                _WEEKLY_MEAL_PLAN_CACHE = json.load(f)
                logger.info("Successfully loaded external weekly meal plan configuration.")
                return _WEEKLY_MEAL_PLAN_CACHE
        else:
            logger.warning(
                "meal_plan.json not found at %s! Falling back to empty menu defaults. "
                "Copy meal_plan.json-example to meal_plan.json and mount it (see docker-compose.yml).",
                MEAL_PLAN_PATH,
            )
            return {}
    except Exception as e:
        logger.error(f"Failed to parse meal_plan.json: {e}")
        return {}


# ---------- MQTT client (long-lived) ----------

_mqtt_client = None


def _init_mqtt():
    global _mqtt_client
    _mqtt_client = mqtt.Client()
    if MQTT_USER and MQTT_PASS:
        _mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    try:
        _mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        _mqtt_client.loop_start()
        logger.info("MQTT client connected and loop started.")
    except Exception as e:
        logger.error(f"MQTT initial connection failed: {e}")


def _stop_mqtt():
    global _mqtt_client
    if _mqtt_client:
        try:
            _mqtt_client.loop_stop()
            _mqtt_client.disconnect()
            logger.info("MQTT client disconnected.")
        except Exception as e:
            logger.error(f"MQTT disconnect error: {e}")


def publish_to_dashboard(topic: str, payload_dict: dict):
    global _mqtt_client
    if _mqtt_client is None or not _mqtt_client.is_connected():
        logger.warning(f"MQTT client not connected — cannot publish to {topic}")
        return
    try:
        _mqtt_client.publish(topic, json.dumps(payload_dict), qos=1, retain=True)
    except Exception as e:
        logger.error(f"MQTT publish failure on topic {topic}: {e}")


# ---------- Signal handling ----------

def _signal_handler(sig, frame):
    logger.info(f"Received signal {sig} — shutting down gracefully...")
    _stop_mqtt()
    sys.exit(0)


# ---------- HA helpers ----------

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
        logger.debug("HA_URL/HA_TOKEN not set — skipping HA note-forward event.")
        return
    try:
        url = f"{HA_URL}/api/events/telegram_note_posted"
        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        payload = {"message": text, "sender": author}
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        if response.status_code in (200, 201):
            logger.info(f"Dispatched event to HA for author: {author}")
        else:
            logger.warning(f"HA note-forward returned status {response.status_code}: {response.text[:200]}")
    except Exception as e:
        logger.error(f"Failed to forward event packet to HA: {e}")


def sync_shopping_to_ha(item: str, action: str):
    if not HA_URL or not HA_TOKEN:
        logger.debug("HA_URL/HA_TOKEN not set — skipping HA shopping list sync.")
        return
    if action not in ("add", "remove"):
        logger.error(f"sync_shopping_to_ha called with invalid action: {action!r}")
        return
    service = "add_item" if action == "add" else "remove_item"
    try:
        url = f"{HA_URL}/api/services/shopping_list/{service}"
        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        payload = {"name": item}
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        if response.status_code in (200, 201):
            logger.info(f"Synced '{item}' ({action}) to HA shopping list.")
        else:
            logger.warning(
                f"HA shopping_list.{service} returned {response.status_code}: {response.text[:200]}"
            )
    except Exception as e:
        logger.error(f"Failed to sync shopping item '{item}' to HA: {e}")


def _parse_time_to_24h(time_str: str) -> str | None:
    cleaned = time_str.strip().lower().replace(" ", "")
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", cleaned)
    if not match:
        return None
    hour_s, minute_s, meridiem = match.groups()
    hour, minute = int(hour_s), int(minute_s) if minute_s else 0
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def push_appointment_to_ha_calendar(title: str, date: str | None, time: str | None):
    if not HA_URL or not HA_TOKEN:
        logger.debug("HA_URL/HA_TOKEN not set — skipping HA calendar push.")
        return
    if not date:
        logger.info(
            f"Appointment '{title}' has no date — skipping HA calendar push "
            "(Alexa day-before/hour-before reminders need a concrete date)."
        )
        return

    try:
        payload = {"entity_id": HA_CALENDAR_ENTITY, "summary": title}
        if time:
            time_24h = _parse_time_to_24h(time)
            if time_24h:
                start_dt = f"{date}T{time_24h}:00"
                end_dt = (datetime.fromisoformat(start_dt) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
                payload["start_date_time"] = start_dt
                payload["end_date_time"] = end_dt
            else:
                logger.warning(f"Couldn't parse time '{time}' for HA calendar push — creating as all-day instead.")
                payload["start_date"] = date
                payload["end_date"] = date
        else:
            payload["start_date"] = date
            payload["end_date"] = date

        url = f"{HA_URL}/api/services/calendar/create_event"
        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        if response.status_code in (200, 201):
            logger.info(f"Pushed '{title}' to HA calendar ({HA_CALENDAR_ENTITY}).")
        else:
            logger.warning(
                f"HA calendar.create_event returned {response.status_code}: {response.text[:200]}"
            )
    except Exception as e:
        logger.error(f"Failed to push appointment '{title}' to HA calendar: {e}")


# ---------- Publishing helpers ----------

def publish_shopping():
    publish_to_dashboard("home/dashboard/shopping_list", {"items": db.get_shopping()})


def publish_meals():
    publish_to_dashboard("home/dashboard/meal_plan", {"meals": db.get_meals()})


def publish_notes():
    publish_to_dashboard("home/dashboard/daily_notes", {"notes": db.get_daily_notes()})


def publish_appointments():
    publish_to_dashboard("home/dashboard/manual_appointments", {"events": db.get_appointments()})


# ---------- Telegram handlers ----------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "Family Hub Group Bot Online!\n\n"
        "Strict Mode Active: The bot will ignore normal group chat. "
        "It only triggers when messages explicitly begin with family system keywords.\n\n"
        "Shopping: `need milk`, `buy apples`, `add to grocery eggs`\n"
        "Notes: `note lock back door`, `memo fix tap`, `sticky grab keys`\n"
        "Schedules: `schedule dentist 12/07 3pm`, `appt 15/07 MOT`\n"
        "Meals: `menu monday burgers`, `eat friday pizza`\n\n"
        "_Every command must be the first word(s) of the message — the bot "
        "does not scan mid-sentence for these keywords._"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_text = update.message.text.strip()
        user_name = update.message.from_user.first_name or "Family Member"
        low_text = raw_text.lower().strip()

        WEEKLY_MEAL_PLAN = load_weekly_meal_plan()

        db.prune_expired_appointments()

        if low_text in [
            "shopping done", "been to shopping", "done shopping", "clear shopping",
            "cleared shopping", "finished shopping", "emptied shopping", "clear shopping list"
        ]:
            db.clear_shopping()
            await update.message.reply_text(
                "Shopping list completely cleared! "
                + ("(Note: this doesn't clear HA's Alexa shopping list — clear that separately if needed.)"
                   if HA_URL and HA_TOKEN else "")
            )
            publish_shopping()
            return

        if low_text in ["clear menu", "clear meal plan", "reset menu", "delete menu"]:
            db.clear_meals()
            await update.message.reply_text("Meal overrides cleared! Reverted to default rotation schedule.")
            publish_meals()
            return

        if low_text in ["clear notes", "clear sticky", "delete notes", "clear notes stack"]:
            db.clear_daily_notes()
            await update.message.reply_text("Notes stack cleared.")
            publish_notes()
            return

        if low_text in ["clear appointments", "clear calendar", "clear schedule"]:
            db.clear_appointments()
            await update.message.reply_text("All manual calendar entries wiped.")
            publish_appointments()
            return

        if re.match(r"^(list|view|show|get)\s+(shop|item|grocer)", low_text) or low_text in ["whats on the list", "what are we buying", "what's on the list"]:
            items = db.get_shopping()
            msg = "Current Shopping List:\n" + ("_Empty_" if not items else "\n".join(f"- {i}" for i in items))
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if low_text in ["list notes", "view notes", "notes", "show notes", "sticky notes", "memos"]:
            current_notes = db.get_daily_notes()
            msg = "Active Family Notes:\n" + ("_No notes_" if not current_notes else "\n".join(
                f"*{n['index']}.* {n['text']}" + (f" (by {n['author']})" if n.get('author') else "")
                for n in current_notes
            ))
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if re.match(r"^(list|view|show)\s+(appt|calendar|event|sched)", low_text) or low_text in ["whats on today", "any appointments", "schedule", "calendar"]:
            appts = db.get_appointments()
            if not appts:
                await update.message.reply_text("No manually tracked appointments found.")
            else:
                msg = "Manual Calendar Events:\n"
                for a in appts:
                    when = f"[{a['date']} {a['time'] or ''}]" if a['date'] else "[Unscheduled]"
                    msg += f"*{a['index']}.* {when} {a['title']}\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if re.match(r"^(list|view|show)\s+(meal|menu|food|dinner)", low_text) or low_text in ["meals", "whats for dinner", "what's for dinner", "menu", "food", "meal plan"]:
            current_overrides = db.get_meals()
            now_dt = datetime.now()
            tom_dt = now_dt + timedelta(days=1)
            day_today = now_dt.strftime("%A").lower()
            day_tomorrow = tom_dt.strftime("%A").lower()

            if "today" in current_overrides:
                today_display = f"Override: {current_overrides['today']}"
            elif day_today in current_overrides:
                today_display = f"Override ({day_today.capitalize()}): {current_overrides[day_today]}"
            else:
                today_display = "\n".join(f"- {m}" for m in WEEKLY_MEAL_PLAN.get(day_today, ["None configured"]))

            if "tomorrow" in current_overrides:
                tomorrow_display = f"Override: {current_overrides['tomorrow']}"
            elif day_tomorrow in current_overrides:
                tomorrow_display = f"Override ({day_tomorrow.capitalize()}): {current_overrides[day_tomorrow]}"
            else:
                tomorrow_display = "\n".join(f"- {m}" for m in WEEKLY_MEAL_PLAN.get(day_tomorrow, ["None configured"]))

            msg = f"Family Menu Outlook\n\nTODAY:\n{today_display}\n\nTOMORROW:\n{tomorrow_display}"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        note_match = re.match(r"^(?:note|sticky|remind|remember|memo)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        buy_match = re.match(r"^(?:buy|add\s+to\s+shopping\s+list|add\s+to\s+shopping|add\s+to\s+grocery|add\s+to\s+groceries|add|get|shop|need|want)(?:\s+some|\s+to|\s+more)?\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        put_on_list_match = re.match(r"^put\b[,\s]+(.+)\s+on\s+(?:the\s+)?list", raw_text, re.IGNORECASE)
        remove_match = re.match(r"^(?:remove|delete|cancel|drop|bought)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        meal_match = re.match(r"^(?:meal|dinner|food|menu|eat)\s+(today|tomorrow|monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|saturday|sat|sunday|sun)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        appt_match = re.match(r"^(?:appt|appointment|book|schedule|event|calendar)\b[,\s]+(.+)", raw_text, re.IGNORECASE)

        if note_match:
            note_content = note_match.group(1).strip()
            db.add_daily_note(note_content, user_name)
            await update.message.reply_text(f"Note posted: \"{note_content}\"")
            trigger_ha_note_event(note_content, user_name)
            publish_notes()
            return

        elif meal_match:
            day_target = meal_match.group(1).lower()
            meal_content = meal_match.group(2).strip()
            day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
            if day_target in day_map:
                day_target = day_map[day_target]

            db.set_meal(day_target, meal_content)
            await update.message.reply_text(f"Meal updated for {day_target.capitalize()}: \"{meal_content}\"")
            publish_meals()
            return

        elif remove_match:
            remaining = remove_match.group(1).strip()

            appt_rem = re.match(r"^(?:appt|appointment|book|event|schedule|calendar)\b[,\s]+(.+)", remaining, re.IGNORECASE)
            if appt_rem:
                target = appt_rem.group(1).strip()
                if target.isdigit():
                    target_idx = int(target)
                    if db.delete_appointment_by_index(target_idx):
                        await update.message.reply_text(f"Removed appointment #{target_idx}.")
                        publish_appointments()
                    else:
                        await update.message.reply_text(f"Appointment #{target_idx} not found.")
                else:
                    if db.delete_appointment_by_text(target):
                        await update.message.reply_text(f"Removed appointment matching: \"{target}\"")
                        publish_appointments()
                    else:
                        await update.message.reply_text(f"No match found for: '{target}'.")
                return

            note_rem = re.match(r"^(?:note|sticky|memo)\b[,\s]+(.+)", remaining, re.IGNORECASE)
            if note_rem:
                raw_target = note_rem.group(1).strip()
                if raw_target.isdigit():
                    target_idx = int(raw_target)
                    if db.delete_note_by_index(target_idx):
                        await update.message.reply_text(f"Deleted note #{target_idx}.")
                        publish_notes()
                    else:
                        await update.message.reply_text(f"Note #{target_idx} doesn't exist.")
                else:
                    if db.delete_note_by_text(raw_target):
                        await update.message.reply_text("Deleted note matching phrase.")
                        publish_notes()
                    else:
                        await update.message.reply_text("Note phrase not found.")
                return

            if db.delete_shopping_item(remaining):
                await update.message.reply_text(f"Removed '{remaining}' from shopping list.")
                publish_shopping()
                sync_shopping_to_ha(remaining, "remove")
            else:
                await update.message.reply_text(f"'{remaining}' is not on the shopping list.")
            return

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
            push_appointment_to_ha_calendar(title_val, date_val, time_val)
            display_when = f"on {date_val}" if date_val else "unscheduled"
            if time_val:
                display_when += f" at {time_val}"
            await update.message.reply_text(f"Appointment added ({display_when}): \"{title_val}\"")
            return

        item_to_add = None

        if raw_text.startswith(('-', '*', '\u25ab', '\u2022')):
            item_to_add = re.sub(r"^[-\*\u25ab\u2022]\s*", "", raw_text).strip()
        elif buy_match:
            item_to_add = buy_match.group(1).strip()
        elif put_on_list_match:
            item_to_add = put_on_list_match.group(1).strip()

        if item_to_add:
            if db.add_shopping(item_to_add):
                await update.message.reply_text(f"Added '{item_to_add}' to shopping list.")
                publish_shopping()
                sync_shopping_to_ha(item_to_add, "add")
            else:
                await update.message.reply_text(f"'{item_to_add}' is already on the list!")
            return

        logger.info(f"Ignored group chat conversation line: '{raw_text}'")

    except Exception as e:
        logger.exception(f"Unhandled system trace exception during handling: {e}")
        try:
            await update.message.reply_text("Something went wrong processing that message. Check the bot logs.")
        except Exception:
            pass


async def _periodic_cleanup(context: ContextTypes.DEFAULT_TYPE):
    db.prune_expired_appointments()
    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()


def main():
    db.init_db()
    logger.info(f"SQLite database ready at {db.DB_PATH}")
    db.prune_expired_appointments()

    _init_mqtt()

    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(_periodic_cleanup, interval=900, first=300)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    logger.info("System boot secured. Telegram Dispatch Bot listening in strict command filter mode...")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Polling failed — is another instance already running with the same token? {e}")
    finally:
        _stop_mqtt()


if __name__ == '__main__':
    main()

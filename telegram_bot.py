import asyncio
import os
import sys
import logging
import json
import html as html_lib
import random
import requests
import re
import signal
import threading
from datetime import datetime, timedelta, timezone, time
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
from telegram import Update
from telegram.error import TelegramError, TimedOut, NetworkError, RetryAfter
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import db
import recipes as recipe_lib

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
HA_CALENDAR_ENTITY = os.getenv("HA_CALENDAR_ENTITY", "calendar.telegram")

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
    _mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
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
        _mqtt_client.publish(topic, json.dumps(payload_dict), qos=0, retain=True)
    except Exception as e:
        logger.error(f"MQTT publish failure on topic {topic}: {e}")


# ---------- Signal handling ----------

def _background_ha_call(func, *args):
    threading.Thread(target=func, args=args, daemon=True).start()

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


async def _reply(update: Update, text: str, parse_mode: str | None = None) -> object | None:
    """Send a reply, retrying transient network failures.

    Returns the sent message, or None if Telegram could not be reached after
    retries. Never raises, so a failed confirmation doesn't abort an action
    that already succeeded.
    """
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            msg = await update.message.reply_text(text, parse_mode=parse_mode)
            try:
                db.add_bot_message(msg.message_id, msg.chat.id)
            except Exception:
                pass
            return msg
        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 1)
            last_exc = e
        except (TimedOut, NetworkError) as e:
            last_exc = e
            await asyncio.sleep(1.5 * (attempt + 1))
        except TelegramError as e:
            logger.error(f"Telegram reply rejected: {e}")
            return None
    logger.error(f"Telegram reply failed after retries: {last_exc}")
    return None


def _signal_handler(sig, frame):
    logger.info(f"Received signal {sig} — shutting down gracefully...")
    _stop_mqtt()
    sys.exit(0)


# ---------- HA helpers ----------

def parse_uk_date(date_str: str) -> str | None:
    cleaned = date_str.replace('.', '-').replace('/', '-')
    match = re.match(r"^(\d{1,2})-(\d{1,2})(?:-(\d{2,4}))?$", cleaned)
    if not match:
        return None
    day, month, year = match.groups()
    if year is None:
        year = str(datetime.now().year)
    elif len(year) == 2:
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


_PREP_WORDS = {
    "for", "with", "on", "at", "about", "regarding", "by", "in", "from",
    "to", "the", "a", "an", "of", "this", "next",
}

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _strip_tail(word: str) -> str:
    return word.strip(".,;:!?()\"'")


def _next_weekday_date(day_idx: int) -> str:
    today = datetime.now().date()
    days_ahead = (day_idx - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _date_from_day_month(day: int, month: int) -> str | None:
    today = datetime.now().date()
    year = today.year
    try:
        d = datetime(year, month, day).date()
    except ValueError:
        return None
    if d < today:
        try:
            d = datetime(year + 1, month, day).date()
        except ValueError:
            return None
    return d.strftime("%Y-%m-%d")


def _scan_time(token: str, next_token: str | None):
    candidates = []
    if next_token is not None and _strip_tail(next_token).lower() in ("am", "pm"):
        candidates.append((token + _strip_tail(next_token).lower(), True))
    if re.search(r"[:]|am|pm", token.lower()):
        candidates.append((token, False))
    for text, consumed in candidates:
        pt = _parse_time_to_24h(text)
        if pt:
            return pt, consumed
    return None, False


def _parse_appointment_fields(rest: str):
    tokens = rest.split()
    date_val = None
    time_val = None
    title_parts = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        low = _strip_tail(tok).lower()

        pt, consumed_next = _scan_time(tok, tokens[i + 1] if i + 1 < n else None)
        if pt:
            if time_val is None:
                time_val = pt
            if consumed_next:
                i += 1
            i += 1
            continue

        if date_val is None:
            pd = parse_uk_date(tok)
            if pd:
                date_val = pd
                i += 1
                continue

        if date_val is None and low in ("today", "tonight"):
            date_val = datetime.now().date().strftime("%Y-%m-%d")
            i += 1
            continue
        if date_val is None and low in ("tomorrow", "tmrw", "tmw"):
            date_val = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
            i += 1
            continue
        if date_val is None and low in _WEEKDAYS:
            date_val = _next_weekday_date(_WEEKDAYS[low])
            i += 1
            continue

        if date_val is None:
            day_match = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?$", low)
            if day_match and i + 1 < n:
                nxt = _strip_tail(tokens[i + 1]).lower()
                if nxt in _MONTHS:
                    d = _date_from_day_month(int(day_match.group(1)), _MONTHS[nxt])
                    if d:
                        date_val = d
                        i += 2
                        continue

        if date_val is None and low in _MONTHS and i + 1 < n:
            nxt = _strip_tail(tokens[i + 1]).lower()
            day_match = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?$", nxt)
            if day_match:
                d = _date_from_day_month(int(day_match.group(1)), _MONTHS[low])
                if d:
                    date_val = d
                    i += 2
                    continue

        if low in _PREP_WORDS:
            i += 1
            continue

        title_parts.append(tok)
        i += 1

    title = " ".join(title_parts).strip() or rest.strip()
    return date_val, time_val, title


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


# ---------- Menu display helpers ----------

_WEEKDAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DAY_ALIASES = {
    "mon": "monday", "monday": "monday",
    "tue": "tuesday", "tues": "tuesday", "tuesday": "tuesday",
    "wed": "wednesday", "weds": "wednesday", "wednesday": "wednesday",
    "thu": "thursday", "thur": "thursday", "thurs": "thursday", "thursday": "thursday",
    "fri": "friday", "friday": "friday",
    "sat": "saturday", "saturday": "saturday",
    "sun": "sunday", "sunday": "sunday",
}

_WEEK_SPECS = {
    "week", "the week", "weekly", "all", "all week", "full", "full week",
    "whole week", "entire week", "week ahead", "this week", "next week",
    "7 days", "seven days",
}


def _parse_menu_days(spec: str):
    """Resolve a menu day selection into 'default', 'week', or a day list.

    Returns None when the spec is not a recognisable day selection, so the
    caller can fall through to the meal-override (set) handler.
    """
    spec = (spec or "").strip().lower()
    spec = re.sub(r"\s+(?:please|pls|plz)\s*$", "", spec).strip()
    spec = re.sub(r"^(?:the|for)\s+", "", spec, count=1).strip()
    if spec == "":
        return "default"
    if spec in _WEEK_SPECS:
        return "week"
    parts = [p.strip() for p in re.split(r"\s*(?:,|/|&|\+|\band\b)\s*", spec) if p.strip()]
    days = []
    for part in parts:
        if part in ("today", "tomorrow"):
            days.append(part)
        elif part in _DAY_ALIASES:
            days.append(_DAY_ALIASES[part])
        else:
            return None
    return days or None


def _menu_body(weekday, overrides, weekly, today_name, tomorrow_name):
    override = None
    if weekday == today_name and "today" in overrides:
        override = overrides["today"]
    elif weekday == tomorrow_name and "tomorrow" in overrides:
        override = overrides["tomorrow"]
    elif weekday in overrides:
        override = overrides[weekday]
    if override:
        return f"Override: {override}"
    meals = weekly.get(weekday) or ["None configured"]
    return "\n".join(f"- {m}" for m in meals)


def _menu_view_message(low_text, overrides, weekly, now_dt):
    """Return a formatted menu reply when the text is a menu-view request.

    Returns None when the text is not a menu view (e.g. a meal override like
    "menu monday burgers") so the caller can fall through to other handlers.
    """
    text = low_text.strip()

    verb = re.match(r"^(?:list|view|show|get)\s+(.+)$", text)
    if verb:
        text = verb.group(1).strip()

    if text in ("meals", "meal", "menu", "food", "dinner", "meal plan",
                "whats for dinner", "what's for dinner", "whats for tea"):
        spec = ""
    else:
        m = re.match(r"^(?:meal|menu|food|dinner|meals|dinners)\b\s*(.*)$", text)
        if m:
            rest = m.group(1).strip()
            if rest.startswith("for "):
                rest = rest[4:].strip()
            spec = rest
        else:
            m = re.match(
                r"^(?:full|whole|entire|weekly)\s+(?:week\s+)?"
                r"(?:meal plan|menu|meals|food|dinner)\b\s*(.*)$",
                text,
            )
            if m:
                spec = m.group(1).strip() or "week"
            else:
                return None

    days = _parse_menu_days(spec)
    if days is None:
        return None

    today_name = now_dt.strftime("%A").lower()
    tomorrow_name = (now_dt + timedelta(days=1)).strftime("%A").lower()

    def resolve(label):
        if label == "today":
            return "Today", today_name
        if label == "tomorrow":
            return "Tomorrow", tomorrow_name
        return label.capitalize(), label

    if days == "default":
        entries = [resolve("today"), resolve("tomorrow")]
        title = "Family Menu Outlook"
    elif days == "week":
        entries = [(d.capitalize(), d) for d in _WEEKDAY_ORDER]
        title = "Family Menu Outlook — Full Week"
    else:
        entries = [resolve(d) for d in days]
        title = "Family Menu Outlook"

    blocks = [
        f"{heading.upper()}:\n{_menu_body(weekday, overrides, weekly, today_name, tomorrow_name)}"
        for heading, weekday in entries
    ]
    return title + "\n\n" + "\n\n".join(blocks)


# ---------- Recipe suggestion helpers ----------

RECIPE_SUGGEST_COUNT = int(os.getenv("RECIPE_SUGGEST_COUNT", "3"))
RECIPE_REPEAT_DAYS = int(os.getenv("RECIPE_REPEAT_DAYS", "28"))
RECIPE_CATALOG_MAX_AGE_DAYS = int(os.getenv("RECIPE_CATALOG_MAX_AGE_DAYS", "7"))


def _esc(text) -> str:
    """Escape text for Telegram HTML (keeps apostrophes literal)."""
    return html_lib.escape(str(text or ""), quote=False)

_SUGGEST_TRIGGERS = sorted([
    "what should i cook for dinner", "what should i make for dinner",
    "what should we cook for dinner", "what should we make for dinner",
    "what should i cook", "what should i make",
    "what should we cook", "what should we make",
    "give me a recipe", "give me some recipes", "give me recipe",
    "suggest something to cook", "suggest something to make",
    "suggest a recipe", "suggest some recipes", "suggest recipes",
    "something to cook", "something to make", "something for dinner",
    "dinner suggestion", "meal suggestion", "cook suggestion",
    "dinner idea", "cook idea", "meal idea", "food idea", "recipe idea",
    "surprise me", "recommend", "suggest", "recipes", "recipe",
], key=len, reverse=True)

_CUISINE_ALIASES = recipe_lib.CUISINE_ALIASES

_TAG_ALIASES = {
    "vegetarian": "vegetarian", "veggie": "vegetarian",
    "vegan": "vegan",
    "gluten free": "gluten-free", "gluten-free": "gluten-free",
    "low carb": "low-carb", "low-carb": "low-carb",
    "dairy free": "dairy-free", "dairy-free": "dairy-free",
}


def _parse_recipe_filters(spec: str) -> dict:
    spec = (spec or "").lower().strip()
    cuisine = None
    tags: list[str] = []
    max_minutes = None

    for alias in sorted(_CUISINE_ALIASES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", spec):
            cuisine = _CUISINE_ALIASES[alias]
            break

    for alias, slug in _TAG_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", spec) and slug not in tags:
            tags.append(slug)

    m = re.search(r"(?:under|less than|within|below|max|<)\s*(\d+)\s*(?:min|mins|minutes)?", spec)
    if m:
        max_minutes = int(m.group(1))
    elif re.search(r"\b(quick|fast|speedy|easy)\b", spec):
        max_minutes = 30

    return {
        "cuisine": cuisine,
        "tags": tags,
        "max_minutes": max_minutes,
        "spec": spec,
    }


def _parse_recipe_request(low_text: str):
    """Return filter dict when the text is a recipe-suggestion request, else None."""
    text = low_text.strip().strip("?!. ").strip()
    for trig in _SUGGEST_TRIGGERS:
        if text == trig:
            return _parse_recipe_filters("")
        if text.startswith(trig) and text[len(trig):len(trig) + 1] in (" ", ",", ":", "-"):
            spec = text[len(trig):].strip(" ,:.-")
            return _parse_recipe_filters(spec)
    return None


def _recipe_suggestion_message(filters: dict) -> str:
    recipe_lib.ensure_catalog(max_age_days=RECIPE_CATALOG_MAX_AGE_DAYS)

    cuisine = filters.get("cuisine")
    tags = filters.get("tags") or []
    max_minutes = filters.get("max_minutes")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECIPE_REPEAT_DAYS)).isoformat(
        timespec="seconds"
    )

    eligible = db.get_recipes(
        cuisine=cuisine, tags=tags,
        max_minutes=max_minutes, eligible_since=cutoff,
    )

    picks: list[dict] = []
    if len(eligible) >= RECIPE_SUGGEST_COUNT:
        picks = random.sample(eligible, RECIPE_SUGGEST_COUNT)
    else:
        picks = list(eligible)
        chosen = {(p["source"], p["slug"]) for p in picks}
        fallback = db.get_recipes(
            cuisine=cuisine, tags=tags,
            max_minutes=max_minutes, least_recent=True,
        )
        for r in fallback:
            key = (r["source"], r["slug"])
            if key in chosen:
                continue
            picks.append(r)
            chosen.add(key)
            if len(picks) >= RECIPE_SUGGEST_COUNT:
                break

    if not picks:
        return (
            "No recipes matched that request. "
            "Try a cuisine (e.g. `suggest indian`), a tag (e.g. `suggest vegetarian`), "
            "or `suggest quick` for something fast."
        )

    db.mark_recipes_offered([(p["source"], p["slug"]) for p in picks])

    filter_bits = []
    if cuisine:
        filter_bits.append(cuisine.replace("-", " ").title())
    filter_bits.extend(tags)
    if max_minutes:
        filter_bits.append(f"≤{max_minutes} min")
    suffix = f" ({', '.join(filter_bits)})" if filter_bits else ""

    lines = [f"🍽️ <b>Dinner ideas</b>{_esc(suffix)}"]
    for i, r in enumerate(picks, 1):
        meta = []
        if r.get("minutes"):
            meta.append(f"{r['minutes']} min")
        if r.get("calories"):
            meta.append(f"{r['calories']} cal")
        if r.get("protein"):
            meta.append(f"{r['protein']}g protein")
        if r.get("difficulty"):
            meta.append(r["difficulty"])
        cuisine_display = r.get("cuisine") or ""
        tag_line = f" — {_esc(cuisine_display)}" if cuisine_display else ""
        lines.append("")
        lines.append(f"{i}. <b>{_esc(r['title'])}</b>{tag_line}")
        if meta:
            lines.append("   " + " · ".join(meta))
        desc = (r.get("description") or "").strip()
        if len(desc) > 200:
            desc = desc[:197].rstrip() + "..."
        if desc:
            lines.append("   " + _esc(desc))
        lines.append(f'   <a href="{r["url"]}">View recipe</a>')

    lines.append("")
    lines.append(f"<i>Fresh picks — none of these repeat for {RECIPE_REPEAT_DAYS} days.</i>")
    lines.append("Reply <code>menu today &lt;name&gt;</code> to set one as today's meal.")
    return "\n".join(lines)


# ---------- Telegram handlers ----------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "Family Hub Group Bot Online!\n\n"
        "Strict Mode Active: The bot will ignore normal group chat. "
        "It only triggers when messages explicitly begin with family system keywords.\n\n"
        "Shopping: `need milk`, `buy apples`, `add to grocery eggs`\n"
        "Notes: `note lock back door`, `memo fix tap`, `sticky grab keys`\n"
        "Schedules: `schedule dentist 12/07 3pm`, `appt 15/07 MOT`\n"
        "Meals: `menu monday burgers`, `eat friday pizza`, `add lamb curry to wednesday menu`\n"
        "Menu view: `menu`, `menu for the week`, `menu for monday and tuesday`\n"
        "Recipe ideas: `suggest dinner`, `suggest indian`, `suggest vegetarian`, `suggest quick`\n\n"
        "_Every command must be the first word(s) of the message — the bot "
        "does not scan mid-sentence for these keywords._"
    )
    await _reply(update,welcome_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_text = update.message.text.strip()
        user_name = update.message.from_user.first_name or "Family Member"
        low_text = raw_text.lower().strip()

        WEEKLY_MEAL_PLAN = load_weekly_meal_plan()

        db.prune_expired_appointments()

        if "\n" in raw_text:
            lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
            if len(lines) > 1:
                add_re = re.compile(
                    r"^(?:buy|add\s+to\s+shopping\s+list|add\s+to\s+shopping|add\s+to\s+grocery|add\s+to\s+groceries|add|get|shop|need|want|grab|pick\s+up|require|fetch|purchase)(?:\s+some|\s+to|\s+more)?\b[,\s]+(.+)",
                    re.IGNORECASE,
                )
                first_match = add_re.match(lines[0])
                if first_match:
                    add_matches = [first_match.group(1).strip()]
                    for line in lines[1:]:
                        m = add_re.match(line)
                        if m:
                            add_matches.append(m.group(1).strip())
                        else:
                            add_matches.append(line)
                    added, dupes = [], []
                    for item in add_matches:
                        if db.add_shopping(item):
                            added.append(item)
                        else:
                            dupes.append(item)
                    if added:
                        publish_shopping()
                        for item in added:
                            _background_ha_call(sync_shopping_to_ha, item, "add")
                    parts = []
                    if added:
                        parts.append("Added: " + ", ".join(f"'{a}'" for a in added))
                    if dupes:
                        parts.append("Already on list: " + ", ".join(f"'{d}'" for d in dupes))
                    await _reply(update, "\n".join(parts) if parts else "No new items added.")
                    return

        if low_text in [
            "shopping done", "been to shopping", "done shopping", "clear shopping",
            "cleared shopping", "finished shopping", "emptied shopping", "clear shopping list",
            "reset shopping", "wipe list", "erase list",
        ]:
            db.clear_shopping()
            await _reply(update,
                "Shopping list completely cleared! "
                + ("(Note: this doesn't clear HA's Alexa shopping list — clear that separately if needed.)"
                   if HA_URL and HA_TOKEN else "")
            )
            publish_shopping()
            return

        if low_text in ["clear menu", "clear meal plan", "reset menu", "delete menu"]:
            db.clear_meals()
            await _reply(update,"Meal overrides cleared! Reverted to default rotation schedule.")
            publish_meals()
            return

        if low_text in ["clear notes", "clear sticky", "delete notes", "clear notes stack"]:
            db.clear_daily_notes()
            await _reply(update,"Notes stack cleared.")
            publish_notes()
            return

        if low_text in ["clear appointments", "clear calendar", "clear schedule"]:
            db.clear_appointments()
            await _reply(update,"All manual calendar entries wiped.")
            publish_appointments()
            return

        if re.match(r"^(list|view|show|get)\s+(shop|item|grocer)", low_text) or low_text in ["whats on the list", "what are we buying", "what's on the list"]:
            items = db.get_shopping()
            msg = "Current Shopping List:\n" + ("_Empty_" if not items else "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1)))
            await _reply(update,msg, parse_mode="Markdown")
            return

        if low_text in ["list notes", "view notes", "notes", "show notes", "sticky notes", "memos"]:
            current_notes = db.get_daily_notes()
            msg = "Active Family Notes:\n" + ("_No notes_" if not current_notes else "\n".join(
                f"*{n['index']}.* {n['text']}" + (f" (by {n['author']})" if n.get('author') else "")
                for n in current_notes
            ))
            await _reply(update,msg, parse_mode="Markdown")
            return

        if re.match(r"^(list|view|show)\s+(appt|calendar|event|sched)", low_text) or low_text in ["whats on today", "any appointments", "schedule", "calendar"]:
            appts = db.get_appointments()
            if not appts:
                await _reply(update,"No manually tracked appointments found.")
            else:
                msg = "Manual Calendar Events:\n"
                for a in appts:
                    when = f"[{a['date']} {a['time'] or ''}]" if a['date'] else "[Unscheduled]"
                    msg += f"*{a['index']}.* {when} {a['title']}\n"
                await _reply(update,msg, parse_mode="Markdown")
            return

        menu_view = _menu_view_message(low_text, db.get_meals(), WEEKLY_MEAL_PLAN, datetime.now())
        if menu_view is not None:
            await _reply(update, menu_view, parse_mode="Markdown")
            return

        recipe_filters = _parse_recipe_request(low_text)
        if recipe_filters is not None:
            suggestion = await asyncio.to_thread(_recipe_suggestion_message, recipe_filters)
            await _reply(update, suggestion, parse_mode="HTML")
            return

        note_match = re.match(r"^(?:note|sticky|remind|remember|memo|jot|write|save|pin)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        buy_match = re.match(r"^(?:buy|add\s+to\s+shopping\s+list|add\s+to\s+shopping|add\s+to\s+grocery|add\s+to\s+groceries|add|get|shop|need|want|grab|pick\s+up|require|fetch|purchase)(?:\s+some|\s+to|\s+more)?\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        put_on_list_match = re.match(r"^put\b[,\s]+(.+)\s+on\s+(?:the\s+)?list", raw_text, re.IGNORECASE)
        remove_match = re.match(r"^(?:remove|delete|cancel|drop|bought|erase|scrub|toss|dump|clear|forget|uncheck)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        meal_match = re.match(r"^(?:meal|dinner|food|menu|eat)\s+(today|tomorrow|monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|saturday|sat|sunday|sun)\b[,\s]+(.+)", raw_text, re.IGNORECASE)
        # Natural phrasing: "add lamb curry to Wednesday menu", "set wednesday to X",
        # "put curry on friday", "make monday pasta".
        _day = r"(today|tomorrow|monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|saturday|sat|sunday|sun)"
        meal_set_natural = re.match(
            rf"^(?:add|put|set|change|make)\s+(.+?)\s+(?:to|for|on|as)\s+(?:the\s+|my\s+)?{_day}(?:'s)?(?:\s+(?:menu|dinner|lunch|supper|meal|plan))?\s*$",
            raw_text, re.IGNORECASE,
        )
        meal_set_day_first = re.match(
            rf"^(?:set|change|make)\s+(?:the\s+)?{_day}(?:'s)?\s*(?:menu|dinner|lunch|supper|meal|plan)?\s*(?:to\s+|as\s+)?(.+)$",
            raw_text, re.IGNORECASE,
        )
        appt_match = re.match(r"^(?:(?:add(?:ed)?|new|set|create|make)\s+)?(?:appointment|appt|book(?:ing)?|schedule|event|calendar|plan|reminder|meeting)\b[,\s]+(.+)", raw_text, re.IGNORECASE)

        if note_match:
            note_content = note_match.group(1).strip()
            db.add_daily_note(note_content, user_name)
            await _reply(update,f"Note posted: \"{note_content}\"")
            _background_ha_call(trigger_ha_note_event, note_content, user_name)
            publish_notes()
            return

        elif meal_match:
            day_target = meal_match.group(1).lower()
            meal_content = meal_match.group(2).strip()
            day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
            if day_target in day_map:
                day_target = day_map[day_target]

            db.set_meal(day_target, meal_content)
            await _reply(update,f"Meal updated for {day_target.capitalize()}: \"{meal_content}\"")
            publish_meals()
            return

        elif meal_set_natural or meal_set_day_first:
            if meal_set_natural:
                meal_content = meal_set_natural.group(1).strip()
                day_target = meal_set_natural.group(2).lower()
            else:
                day_target = meal_set_day_first.group(1).lower()
                meal_content = meal_set_day_first.group(2).strip()
            day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
            day_target = day_map.get(day_target, day_target)

            db.set_meal(day_target, meal_content)
            await _reply(update,f"Meal updated for {day_target.capitalize()}: \"{meal_content}\"")
            publish_meals()
            return

        elif remove_match:
            remaining = remove_match.group(1).strip()

            appt_rem = re.match(r"^(?:appointment|appt|book(?:ing)?|event|schedule|calendar|plan|reminder|meeting)\b[,\s]+(.+)", remaining, re.IGNORECASE)
            if appt_rem:
                target = appt_rem.group(1).strip()
                target = re.sub(r"\s+(?:in|from|on)\s+(?:the\s+)?shopping\s+list$", "", target, flags=re.IGNORECASE).strip()
                if target.isdigit():
                    target_idx = int(target)
                    if db.delete_appointment_by_index(target_idx):
                        await _reply(update,f"Removed appointment #{target_idx}.")
                        publish_appointments()
                    else:
                        await _reply(update,f"Appointment #{target_idx} not found.")
                elif len(target) < 3:
                    await _reply(update,f"Text too short ({len(target)} chars) — use the appointment number to delete.")
                else:
                    if db.delete_appointment_by_text(target):
                        await _reply(update,f"Removed appointment matching: \"{target}\"")
                        publish_appointments()
                    else:
                        await _reply(update,f"No match found for: '{target}'.")
                return

            note_rem = re.match(r"^(?:note|sticky|memo|pin|jot)\b[,\s]+(.+)", remaining, re.IGNORECASE)
            if note_rem:
                raw_target = note_rem.group(1).strip()
                if raw_target.isdigit():
                    target_idx = int(raw_target)
                    if db.delete_note_by_index(target_idx):
                        await _reply(update,f"Deleted note #{target_idx}.")
                        publish_notes()
                    else:
                        await _reply(update,f"Note #{target_idx} doesn't exist.")
                elif len(raw_target) < 3:
                    await _reply(update,f"Text too short ({len(raw_target)} chars) — use the note number to delete.")
                else:
                    if db.delete_note_by_text(raw_target):
                        await _reply(update,"Deleted note matching phrase.")
                        publish_notes()
                    else:
                        await _reply(update,"Note phrase not found.")
                return

            if remaining.isdigit():
                target_idx = int(remaining)
                if db.delete_shopping_item_by_index(target_idx):
                    await _reply(update,f"Removed item #{target_idx} from shopping list.")
                    publish_shopping()
                else:
                    await _reply(update,f"Item #{target_idx} not found.")
            elif db.delete_shopping_item(remaining):
                await _reply(update,f"Removed '{remaining}' from shopping list.")
                publish_shopping()
                _background_ha_call(sync_shopping_to_ha, remaining, "remove")
            else:
                await _reply(update,f"'{remaining}' is not on the shopping list.")
            return

        elif appt_match:
            rest = appt_match.group(1).strip()
            rest = re.sub(r"^(?:for|with|on|at|about|regarding)\s+", "", rest, flags=re.IGNORECASE).strip()

            date_val, time_val, title_val = _parse_appointment_fields(rest)

            db.add_appointment(title_val, date=date_val, time=time_val)
            display_when = f"on {date_val}" if date_val else "unscheduled"
            if time_val:
                display_when += f" at {time_val}"
            await _reply(update,f"Appointment added ({display_when}): \"{title_val}\"")
            publish_appointments()
            _background_ha_call(push_appointment_to_ha_calendar, title_val, date_val, time_val)
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
                await _reply(update,f"Added '{item_to_add}' to shopping list.")
                publish_shopping()
                _background_ha_call(sync_shopping_to_ha, item_to_add, "add")
            else:
                await _reply(update,f"'{item_to_add}' is already on the list!")
            return

        logger.info(f"Ignored group chat conversation line: '{raw_text}'")

    except Exception as e:
        logger.exception(f"Unhandled system trace exception during handling: {e}")
        try:
            await _reply(update,"Something went wrong processing that message. Check the bot logs.")
        except Exception:
            pass


async def _periodic_cleanup(context: ContextTypes.DEFAULT_TYPE):
    db.prune_expired_appointments()
    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()


async def _cleanup_old_messages(context: ContextTypes.DEFAULT_TYPE):
    old = db.get_old_bot_messages(days=3)
    for row in old:
        if row.get("pinned"):
            logger.info(f"Skipping pinned message #{row['message_id']}")
            continue
        try:
            await context.bot.delete_message(chat_id=row["chat_id"], message_id=row["message_id"])
            db.delete_bot_message_record(row["id"])
            logger.info(f"Deleted old message #{row['message_id']}")
        except Exception as e:
            err = str(e).lower()
            if "message to delete not found" in err or "message is too old" in err:
                db.delete_bot_message_record(row["id"])
            elif "chat_admin_required" in err or "not enough rights" in err:
                logger.warning(f"Cannot delete message #{row['message_id']}: bot lacks delete permission")
                db.delete_bot_message_record(row["id"])
            else:
                logger.debug(f"Could not delete message #{row['message_id']}: {e}")


async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        try:
            db.add_bot_message(update.message.message_id, update.message.chat.id)
        except Exception:
            pass


async def track_pinned_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pinned = update.message.pinned_message if update.message else None
    if not pinned:
        return
    try:
        db.mark_message_pinned(pinned.message_id, update.message.chat.id)
        logger.info(f"Message #{pinned.message_id} marked pinned — cleanup will skip it")
    except Exception as e:
        logger.debug(f"Could not mark pinned message #{pinned.message_id}: {e}")


def main():
    db.init_db()
    logger.info(f"SQLite database ready at {db.DB_PATH}")
    db.prune_expired_appointments()

    _init_mqtt()

    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()

    # Pre-warm the halal recipe catalogue in the background so the first
    # "suggest dinner" request is instant and startup isn't blocked.
    threading.Thread(
        target=recipe_lib.ensure_catalog,
        kwargs={"max_age_days": RECIPE_CATALOG_MAX_AGE_DAYS},
        daemon=True,
    ).start()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(10)
        .read_timeout(10)
        .write_timeout(10)
        .pool_timeout(5)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.PINNED_MESSAGE, track_pinned_message))
    app.add_handler(MessageHandler(filters.ALL, track_message))
    app.job_queue.run_repeating(_periodic_cleanup, interval=900, first=300)
    app.job_queue.run_daily(_cleanup_old_messages, time=time(hour=4, minute=0))

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

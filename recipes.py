"""
Halal recipe catalogue helper for the Family Hub Telegram bot.

Scrapes the public recipe index at https://www.halalmealplan.com/recipes
and stores a parsed catalogue in SQLite so suggestions can be served without
re-fetching on every request. The catalogue is refreshed periodically.
"""

import html
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import requests

import db

logger = logging.getLogger(__name__)

_FETCH_LOCK = threading.Lock()

RECIPES_URL = "https://www.halalmealplan.com/recipes"
SITE_BASE = "https://www.halalmealplan.com"
USER_AGENT = (
    "FamilyDashboardBot/1.0 "
    "(+https://github.com/speed007/Home-Assistant-Family-Dashboard)"
)

CUISINE_NAMES = {
    "indian": "Indian",
    "middle-eastern": "Middle Eastern",
    "mediterranean": "Mediterranean",
    "pakistani": "Pakistani",
    "turkish": "Turkish",
    "malaysian": "Malaysian",
    "african": "African",
    "korean": "Korean",
    "caribbean": "Caribbean",
    "central-asian": "Central Asian",
    "american-fusion": "American",
    "levantine": "Levantine",
    "european": "European",
}

_KNOWN_TAGS = ["vegetarian", "vegan", "gluten-free", "low-carb", "dairy-free"]


def _clean(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _first(pattern: str, block: str, group: int = 1, cast=None):
    m = re.search(pattern, block, re.S)
    if not m:
        return None
    value = m.group(group).strip()
    if cast:
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None
    return value


def parse_recipes(raw_html: str) -> list[dict]:
    """Parse the recipe index HTML into a list of recipe dicts."""
    recipes: list[dict] = []
    parts = raw_html.split('<a class="group block" href="')

    for seg in parts[1:]:
        href_end = seg.find('"')
        if href_end == -1:
            continue
        href = seg[:href_end]
        if not href.startswith("/recipes/"):
            continue

        block_end = seg.find("</a>")
        block = seg if block_end == -1 else seg[:block_end]

        path_bits = href.strip("/").split("/")
        cuisine_slug = path_bits[1] if len(path_bits) >= 2 else ""
        slug = path_bits[-1]

        title = _first(r"<h3[^>]*>(.*?)</h3>", block)
        if not title:
            continue

        tags = [t for t in _KNOWN_TAGS if re.search(
            r">\s*" + re.escape(t) + r"\s*</span>", block)]

        recipes.append({
            "slug": slug,
            "title": title,
            "cuisine": CUISINE_NAMES.get(cuisine_slug, cuisine_slug.replace("-", " ").title()),
            "cuisine_slug": cuisine_slug,
            "url": SITE_BASE + href,
            "tags": ",".join(tags),
            "minutes": _first(r">(\d+)<!-- -->\s*min<", block, cast=int),
            "servings": _first(r"lucide-users.*?<span>(\d+)</span>", block, cast=int),
            "calories": _first(r">(\d+)<!-- -->\s*cal<", block, cast=int),
            "protein": _first(r">(\d+)<!-- -->g protein<", block, cast=int),
            "difficulty": _first(r">(Easy|Medium|Hard)</span>", block),
            "description": _first(r'<p class="mt-1[^"]*">(.*?)</p>', block),
        })

    return recipes


def fetch_catalog() -> list[dict]:
    """Fetch and parse the live recipe index."""
    resp = requests.get(RECIPES_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return parse_recipes(resp.text)


def _catalog_is_fresh(max_age_days: int) -> bool:
    meta = db.get_recipe_meta()
    if not meta["fetched_at"] or meta["count"] == 0:
        return False
    stamp = meta["fetched_at"]
    if stamp.endswith("Z"):
        stamp = stamp[:-1]
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched < timedelta(days=max_age_days)


def ensure_catalog(max_age_days: int = 7, force: bool = False) -> int:
    """Refresh the stored catalogue when stale or missing.

    Returns the number of recipes currently stored. Network failures fall back
    to whatever is already cached so suggestions keep working offline.
    """
    if not force and _catalog_is_fresh(max_age_days):
        return db.get_recipe_meta()["count"]

    with _FETCH_LOCK:
        # Another thread may have refreshed while we waited for the lock.
        if not force and _catalog_is_fresh(max_age_days):
            return db.get_recipe_meta()["count"]
        try:
            items = fetch_catalog()
        except Exception:
            logger.exception("Recipe catalogue fetch failed — using cached data")
            return db.get_recipe_meta()["count"]

        if items:
            db.save_recipes(items)
            logger.info("Recipe catalogue refreshed with %d recipes", len(items))
        else:
            logger.warning("Recipe catalogue fetch returned no recipes")
    return db.get_recipe_meta()["count"]

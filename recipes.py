"""
Multi-source halal recipe catalogue for the Family Hub Telegram bot.

Scrapes/fetches recipes from supported sites into SQLite so suggestions can
be served without re-fetching on every request. The catalogue is refreshed
periodically and every recipe tracks when it was last offered.

Sources
-------
- halalmealplan: static Next.js index page listing every recipe.
- amiraspantry: WordPress + WP Recipe Maker REST API (only recipes tagged
  as halal are imported).
"""

import html
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import db

logger = logging.getLogger(__name__)

_FETCH_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_last_attempt = 0.0
_REFRESH_COOLDOWN = 900  # don't retry a failed source more than every 15 min

USER_AGENT = (
    "FamilyDashboardBot/1.0 "
    "(+https://github.com/speed007/Home-Assistant-Family-Dashboard)"
)

# ------------------------------------------------------------------ Sources

HALALMEALPLAN_URL = "https://www.halalmealplan.com/recipes"
HALALMEALPLAN_BASE = "https://www.halalmealplan.com"

AMIRA_API = "https://amiraspantry.com/wp-json/wp/v2/wprm_recipe"
AMIRA_COURSE_API = "https://amiraspantry.com/wp-json/wp/v2/wprm_course"
AMIRA_HALAL_TERM = 8870  # wprm_suitablefordiet term id for "Halal"
AMIRA_PER_PAGE = 100
AMIRA_MEAL_COURSE_SLUGS = ("main-course", "dinner", "lunch", "soup")

SOURCE_NAMES = {
    "halalmealplan": "Halal Meal Plan",
    "amiraspantry": "Amira's Pantry",
}

SOURCE_ALIASES = {
    "halal": "halalmealplan",
    "halalmealplan": "halalmealplan",
    "halal meal plan": "halalmealplan",
    "halalmealplan.com": "halalmealplan",
    "amira": "amiraspantry",
    "amiras": "amiraspantry",
    "amira's": "amiraspantry",
    "amiraspantry": "amiraspantry",
    "amira's pantry": "amiraspantry",
    "amiras pantry": "amiraspantry",
    "amira pantry": "amiraspantry",
    "amiraspantry.com": "amiraspantry",
}

# ----------------------------------------------------- Cuisine normalisation

HALALMEALPLAN_CUISINES = {
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

# Maps amiraspantry cuisine slugs onto a shared canonical slug space.
AMIRA_CUISINE_CANON = {
    "middle-east": "middle-eastern",
    "middle-eastern": "middle-eastern",
    "middle-eastern-mediterranean": "mediterranean",
    "arabic": "middle-eastern",
    "lebanese": "middle-eastern",
    "palestinian": "middle-eastern",
    "syrian": "middle-eastern",
    "yemeni": "middle-eastern",
    "armenian": "middle-eastern",
    "levant": "levantine",
    "mediterranean": "mediterranean",
    "mediterranean-american": "mediterranean",
    "american": "american-fusion",
    "american-fusion": "american-fusion",
    "mexican": "mexican",
    "mexican-american": "mexican",
    "italian": "italian",
    "italian-american": "italian",
    "greek": "greek",
    "greek-american": "greek",
    "french": "french",
    "english": "english",
    "british": "british",
    "european": "european",
    "russian": "european",
    "swiss": "european",
    "austria": "european",
    "western": "european",
    "indian": "indian",
    "pakistani": "pakistani",
    "turkish": "turkish",
    "malaysian": "malaysian",
    "korean": "korean",
    "asian": "asian",
    "asian-american": "asian",
    "japanese": "japanese",
    "chinese": "chinese",
    "egyptian": "egyptian",
    "moroccan": "moroccan",
    "north-african": "african",
    "libyan-cuisine": "african",
    "djibouti": "african",
    "latin-american": "latin-american",
}

# User-facing cuisine words -> canonical slug (used by the bot to parse filters).
CUISINE_ALIASES = {
    "indian": "indian",
    "middle eastern": "middle-eastern",
    "middle-eastern": "middle-eastern",
    "middle east": "middle-eastern",
    "arabic": "middle-eastern",
    "lebanese": "middle-eastern",
    "syrian": "middle-eastern",
    "palestinian": "middle-eastern",
    "yemeni": "middle-eastern",
    "mediterranean": "mediterranean",
    "pakistani": "pakistani",
    "turkish": "turkish",
    "malaysian": "malaysian",
    "african": "african",
    "egyptian": "egyptian",
    "moroccan": "moroccan",
    "korean": "korean",
    "caribbean": "caribbean",
    "central asian": "central-asian",
    "central-asian": "central-asian",
    "american": "american-fusion",
    "american fusion": "american-fusion",
    "levantine": "levantine",
    "levant": "levantine",
    "european": "european",
    "italian": "italian",
    "greek": "greek",
    "french": "french",
    "english": "english",
    "british": "british",
    "mexican": "mexican",
    "asian": "asian",
    "japanese": "japanese",
    "chinese": "chinese",
}

_DIET_TAG_MAP = {
    "vegetariandiet": "vegetarian",
    "vegandiet": "vegan",
    "glutenfreediet": "gluten-free",
}

_KNOWN_HALAL_TAGS = ["vegetarian", "vegan", "gluten-free", "low-carb", "dairy-free"]


# ------------------------------------------------------------------ Helpers

def _clean(text) -> str:
    if not text:
        return ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


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


# ------------------------------------------------------------ halalmealplan

def parse_halalmealplan(raw_html: str) -> list[dict]:
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

        tags = [t for t in _KNOWN_HALAL_TAGS
                if re.search(r">\s*" + re.escape(t) + r"\s*</span>", block)]

        recipes.append({
            "source": "halalmealplan",
            "slug": slug,
            "title": title,
            "cuisine": HALALMEALPLAN_CUISINES.get(
                cuisine_slug, cuisine_slug.replace("-", " ").title()),
            "cuisine_slug": cuisine_slug,
            "url": HALALMEALPLAN_BASE + href,
            "tags": ",".join(tags),
            "minutes": _first(r">(\d+)<!-- -->\s*min<", block, cast=int),
            "servings": _first(r"lucide-users.*?<span>(\d+)</span>", block, cast=int),
            "calories": _first(r">(\d+)<!-- -->\s*cal<", block, cast=int),
            "protein": _first(r">(\d+)<!-- -->g protein<", block, cast=int),
            "difficulty": _first(r">(Easy|Medium|Hard)</span>", block),
            "description": _first(r'<p class="mt-1[^"]*">(.*?)</p>', block),
        })

    return recipes


def fetch_halalmealplan() -> list[dict]:
    resp = requests.get(HALALMEALPLAN_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return parse_halalmealplan(resp.text)


# ------------------------------------------------------------- amiraspantry

def _amira_meal_course_ids() -> str:
    """Resolve the REST ids for Amira's meal-type courses (for server filtering)."""
    try:
        resp = requests.get(
            AMIRA_COURSE_API,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            params={"per_page": 100, "_fields": "id,slug"},
        )
        resp.raise_for_status()
        by_slug = {t.get("slug"): t.get("id") for t in resp.json() if isinstance(t, dict)}
        return ",".join(str(by_slug[s]) for s in AMIRA_MEAL_COURSE_SLUGS if s in by_slug)
    except Exception:
        logger.warning("Could not resolve Amira meal course ids — fetching all halal recipes")
        return ""


def fetch_amiraspantry() -> list[dict]:
    recipes: list[dict] = []
    course_param = _amira_meal_course_ids()
    meal_courses = set(AMIRA_MEAL_COURSE_SLUGS)
    page = 1
    while True:
        params = {
            "per_page": AMIRA_PER_PAGE,
            "page": page,
            "wprm_suitablefordiet": AMIRA_HALAL_TERM,
            "_fields": "slug,link,title,recipe",
        }
        if course_param:
            params["wprm_course"] = course_param
        resp = requests.get(
            AMIRA_API,
            headers={"User-Agent": USER_AGENT},
            timeout=40,
            params=params,
        )
        if resp.status_code == 400 and page > 1:
            break  # past the last page
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break

        for item in batch:
            rec = item.get("recipe") or {}
            link = item.get("link") or ""
            slug = link.rstrip("/").split("/")[-1] or item.get("slug")
            if not slug:
                continue

            tags_obj = rec.get("tags") or {}

            # Keep only meal-type courses so desserts/sauces/roundups are excluded.
            course_slugs = {
                t.get("slug") for t in (tags_obj.get("course") or [])
            }
            if not (course_slugs & meal_courses):
                continue

            cuisine_terms = tags_obj.get("cuisine") or []
            cuisine_name = cuisine_terms[0].get("name") if cuisine_terms else None
            raw_cuisine = cuisine_terms[0].get("slug") if cuisine_terms else ""
            cuisine_slug = AMIRA_CUISINE_CANON.get(raw_cuisine, raw_cuisine or None)

            diet = tags_obj.get("suitablefordiet") or []
            group_tags = sorted({
                _DIET_TAG_MAP[t.get("slug")]
                for t in diet
                if t.get("slug") in _DIET_TAG_MAP
            })

            nutrition = rec.get("nutrition") or {}
            minutes = rec.get("total_time") or (
                (_int(rec.get("prep_time")) or 0) + (_int(rec.get("cook_time")) or 0)
            ) or None

            recipes.append({
                "source": "amiraspantry",
                "slug": slug,
                "title": rec.get("name") or _clean((item.get("title") or {}).get("rendered")),
                "cuisine": cuisine_name,
                "cuisine_slug": cuisine_slug,
                "url": link,
                "tags": ",".join(group_tags),
                "minutes": _int(minutes),
                "servings": _int(rec.get("servings")),
                "calories": _int(nutrition.get("calories")),
                "protein": _int(nutrition.get("protein")),
                "difficulty": None,
                "description": _clean(rec.get("summary")),
            })

        if len(batch) < AMIRA_PER_PAGE:
            break
        page += 1

    return recipes


SOURCES = {
    "halalmealplan": fetch_halalmealplan,
    "amiraspantry": fetch_amiraspantry,
}


# ------------------------------------------------------------------ Catalog

def fetch_catalog(sources: list[str] | None = None):
    """Fetch every requested source.

    Returns (items, succeeded) where succeeded is a list of (source, keep_slugs)
    for sources that returned at least one recipe.
    """
    targets = sources or list(SOURCES)
    all_recipes: list[dict] = []
    succeeded: list[tuple[str, set[str]]] = []
    for name in targets:
        fetcher = SOURCES.get(name)
        if not fetcher:
            continue
        try:
            items = fetcher()
            logger.info("Fetched %d recipes from %s", len(items), name)
            all_recipes.extend(items)
            if items:
                succeeded.append((name, {r["slug"] for r in items}))
        except Exception:
            logger.exception("Failed to fetch recipes from %s", name)
    return all_recipes, succeeded


def _catalog_state(max_age_days: int, sources: list[str] | None):
    """Return (meta, is_fresh). Fresh means every expected source is present
    and the catalogue was fetched within max_age_days."""
    meta = db.get_recipe_meta()
    if not meta["fetched_at"] or meta["count"] == 0:
        return meta, False

    expected = set(sources or SOURCES.keys())
    present = set((meta.get("sources") or {}).keys())
    if not expected.issubset(present):
        return meta, False

    stamp = meta["fetched_at"]
    if stamp.endswith("Z"):
        stamp = stamp[:-1]
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return meta, False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    fresh = datetime.now(timezone.utc) - fetched < timedelta(days=max_age_days)
    return meta, fresh


def ensure_catalog(max_age_days: int = 7, force: bool = False,
                   sources: list[str] | None = None) -> int:
    """Refresh the stored catalogue when stale, missing a source, or forced.

    Returns the number of recipes currently stored. Network failures fall back
    to whatever is already cached so suggestions keep working offline.
    """
    global _last_attempt

    meta, fresh = _catalog_state(max_age_days, sources)
    if fresh and not force:
        return meta["count"]

    # If a source is missing because a recent fetch failed, don't hammer it.
    with _STATE_LOCK:
        if (not force and meta["count"] > 0
                and time.time() - _last_attempt < _REFRESH_COOLDOWN):
            return meta["count"]
        _last_attempt = time.time()

    with _FETCH_LOCK:
        meta, fresh = _catalog_state(max_age_days, sources)
        if fresh and not force:
            return meta["count"]
        try:
            items, succeeded = fetch_catalog(sources)
        except Exception:
            logger.exception("Recipe catalogue fetch failed — using cached data")
            return db.get_recipe_meta()["count"]

        if items:
            db.save_recipes(items)
            for name, keep in succeeded:
                removed = db.prune_recipes(name, keep)
                if removed:
                    logger.info("Pruned %d stale recipes from %s", removed, name)
            logger.info("Recipe catalogue refreshed with %d recipes", len(items))
        else:
            logger.warning("Recipe catalogue fetch returned no recipes")
    return db.get_recipe_meta()["count"]

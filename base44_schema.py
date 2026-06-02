"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         LEADFLOW AI — base44_schema.py                                       ║
║         Base44 field mapping, validation, sanitization, upsert               ║
║         Version: Phase 2 — generated against MASTER_BRIEF v10.0             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  THREE JOBS THIS MODULE DOES:                                                ║
║  1. LEAD_FIELD_MAP — translate Python snake_case → confirmed Base44 names   ║
║  2. _sanitize_payload() — strip None, fix types, apply name mapping          ║
║  3. _b44_upsert() — GET by place_id first, then POST or PATCH                ║
╚══════════════════════════════════════════════════════════════════════════════╝

CONFIRMED FACTS (from MASTER_BRIEF §4):
  - App is PUBLIC mode — must stay public or all calls return 403
  - api-key header: bare key, NO "Bearer" prefix
  - Only required field: name
  - Duplicate records created silently — NO uniqueness enforcement
  - Enum validation NOT enforced server-side — validate on Python side
  - NEVER send: id, created_date, updated_date, created_by (server-generated)
  - Field names are case-sensitive — always exact lowercase snake_case
  - Rate limit: ~10 req/s safe ceiling, use 0.2s sleep = 5 req/s
"""

import json
import time
import datetime
import requests

# ── Base44 connection (imported from main config at runtime) ──────────────────
# These are set by leadflow_v9.py before calling any function here.
# We define them as module-level vars so the module is importable standalone.
BASE44_APP_ID  = ""
BASE44_API_KEY = ""
BASE44_MAX_RETRIES = 3

def _b44_headers() -> dict:
    return {"Content-Type": "application/json", "api-key": BASE44_API_KEY}

def _b44_base() -> str:
    return f"https://app.base44.com/api/apps/{BASE44_APP_ID}/entities"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — FIELD NAME MAPPING
# Python variable name → confirmed Base44 field name
# Source: MASTER_BRIEF §4.8 CRITICAL FIELD NAME MISMATCHES
# ══════════════════════════════════════════════════════════════════════════════

# Fields where the Python name differs from the Base44 confirmed name.
# Any field NOT in this dict is sent with its Python name unchanged.
LEAD_FIELD_MAP = {
    # CRITICAL MISMATCHES (§4.8)
    "has_website"          : "no_website",          # INVERTED boolean — handled in sanitize
    "fb_page_exists"       : "has_facebook",
    "ig_profile_exists"    : "has_instagram",
    "user_ratings_total"   : "reviews",
    "phone_number"         : "phone",
    "composite_score"      : "ai_score",
    "niche"                : "business_type",
    # dm_email on Lead entity stays as "email" in the payload
    "dm_email"             : "email",
}

# WholesaleLead entity uses its own field names (§4.9)
WHOLESALE_FIELD_MAP = {
    "niche"                : "niche",               # same on WholesaleLead
    "composite_score"      : "score",
    "ltv_12mo_estimate"    : "ltv_estimate",
    "lead_price"           : "base_price",
    "fb_page_exists"       : "has_facebook_pixel",  # WholesaleLead uses pixel flag
    "ig_profile_exists"    : "has_facebook_pixel",  # not mapped — skip via exclude list
}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — FIELD LISTS
# ══════════════════════════════════════════════════════════════════════════════

# Fields that are NEVER sent to Base44 — either server-generated or internal
NEVER_SEND = {
    "id", "created_date", "updated_date", "created_by",
    "_homepage_html",       # raw HTML cache — never push
    "_discovery_source",    # internal tag
    "_serp_source",         # internal tag
}

# All 80+ confirmed Lead entity fields to push (§4.8 full list)
# These are the BASE44 field names (after mapping), not Python names.
LEAD_PUSH_FIELDS = {
    # Identity
    "name", "phone", "address", "website", "city", "state", "zip_code",
    "place_id", "business_type", "source", "status",
    # Scores & priority
    "ai_score", "priority", "heuristic_score",
    # Flags
    "no_website", "has_facebook", "has_instagram", "has_yelp",
    "website_live", "website_has_ssl", "website_slow",
    # Reviews
    "rating", "reviews",
    # Contact
    "email", "dm_first_name", "dm_position", "dm_linkedin",
    "email_confidence", "decision_maker_title",
    # Outreach content
    "outreach_sms", "email_subject_a", "email_subject_b",
    "voicemail_script", "objection_rebuttal", "best_hook",
    "best_contact_time", "primary_motivation", "biggest_fear",
    # Psychology & packages
    "archetype", "owner_profile", "recommended_package",
    "package_price_setup", "package_price_monthly", "offer_match_score",
    # Scoring details
    "pain_points", "pain_summary", "pitch_script",
    "dim_scores_json",         # serialized JSON string
    "data_completeness_score",
    # AI
    "groq_validated", "ai_reason",
    # Enrichment
    "enrichment_failures", "service_recommendation",
    "monthly_value_estimate",
    # SERP
    "serp_rank", "serp_top_competitors", "in_local_3pack",
    "owns_search_result",
    # Social presence
    "social_presence_count",
    # Competitor
    "top_competitor",
    # Pain
    "pain_point_count",
    # Geography
    "zip_median_income", "zip_income_tier", "zip_wealth_tier",
    # Yelp
    "yelp_claimed", "yelp_rating", "yelp_review_count",
    "google_rating_delta",
    # Weather
    "weather_urgency_boost",
    # Tracking
    "run_date", "snippet", "hours",
    "assigned_to", "assigned_rep_id", "assigned_at",
    "source_confidence",
    # Outreach tracking
    "outreach_channel_sent", "outreach_sent_at", "crm_sync_status",
    "win_loss_outcome", "outcome_set_at", "outcome_set_by", "reply_source",
    "sms_char_count",
    # Timestamps (ISO strings)
    "discovered_at", "scored_at",
    # Metadata
    "fingerprint", "customer_id",
}

# Required fields — push will fail if these are empty/None
LEAD_REQUIRED_FIELDS = {"name"}
WHOLESALE_REQUIRED_FIELDS = {"name", "niche", "city"}

# Valid enum values
VALID_PRIORITY   = {"hot", "warm", "cold", "disqualified"}
VALID_STATUS     = {"new", "contacted", "replied", "closed", "disqualified"}
VALID_WS_TIER    = {"hot", "warm", "cold"}
VALID_WS_STATUS  = {"listed", "reserved", "sold", "archived"}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _validate_lead_payload(payload: dict) -> tuple[bool, list]:
    """
    Validate a pre-sanitized Lead payload before sending.
    Returns (is_valid: bool, errors: list[str]).
    """
    errors = []

    # Required fields
    for f in LEAD_REQUIRED_FIELDS:
        if not payload.get(f):
            errors.append(f"Required field '{f}' is empty or missing")

    # Score must be int 0-100
    score = payload.get("ai_score")
    if score is not None:
        if not isinstance(score, int):
            errors.append(f"ai_score must be int, got {type(score).__name__}: {score!r}")
        elif not (0 <= score <= 100):
            errors.append(f"ai_score out of range: {score} (must be 0–100)")

    # Priority must be valid enum
    priority = payload.get("priority")
    if priority and priority not in VALID_PRIORITY:
        errors.append(f"priority '{priority}' not in {VALID_PRIORITY}")

    # place_id should be present (not strictly required but always expected)
    if not payload.get("place_id"):
        errors.append("place_id is missing — upsert dedup will not work")

    return len(errors) == 0, errors


def _validate_wholesale_payload(payload: dict) -> tuple[bool, list]:
    """Validate a WholesaleLead payload before sending."""
    errors = []
    for f in WHOLESALE_REQUIRED_FIELDS:
        if not payload.get(f):
            errors.append(f"Required field '{f}' is empty or missing")
    tier = payload.get("tier")
    if tier and tier not in VALID_WS_TIER:
        errors.append(f"tier '{tier}' not in {VALID_WS_TIER}")
    status = payload.get("status")
    if status and status not in VALID_WS_STATUS:
        errors.append(f"status '{status}' not in {VALID_WS_STATUS}")
    score = payload.get("score")
    if score is not None and not isinstance(score, int):
        errors.append(f"score must be int, got {type(score).__name__}")
    return len(errors) == 0, errors


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — SANITIZATION
# Handles: None stripping, type coercion, field name remapping,
#          inverted booleans, dim_scores serialization, timestamps
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_lead_payload(lead: dict) -> dict:
    """
    Build a clean Base44 Lead payload from a Python lead dict.

    Rules applied (per MASTER_BRIEF §12.1):
      B44-1  — apply LEAD_FIELD_MAP name translations
      B44-1  — no_website is INVERTED (True when has_website=False)
      B44-1  — numeric fields get 0 not None
      B44-1  — strip all None → "" for strings, 0 for numbers
      B44-9  — dim_scores → dim_scores_json (JSON string)
      B44-10 — discovered_at / scored_at must be ISO format strings
      B44-6  — never send server-generated or internal fields
      B44-8  — roi_multiplier always int
    """
    p = {}

    # ── Identity ─────────────────────────────────────────────────────────────
    p["name"]          = str(lead.get("name") or "").strip()
    p["phone"]         = str(lead.get("phone_number") or "").strip()
    p["address"]       = str(lead.get("formatted_address") or "").strip()
    p["website"]       = str(lead.get("website") or "").strip()
    p["city"]          = str(lead.get("city") or "").strip()
    p["state"]         = str(lead.get("state") or "").strip()
    p["zip_code"]      = str(lead.get("zip_code") or "").strip()
    p["place_id"]      = str(lead.get("place_id") or "").strip()
    p["business_type"] = str(lead.get("niche") or "").strip()
    p["source"]        = str(lead.get("_discovery_source") or "").strip()
    p["run_date"]      = str(lead.get("run_id") or "").strip()
    p["snippet"]       = str(lead.get("editorial_summary") or "").strip()
    p["hours"]         = str(lead.get("opening_hours") or "").strip() if not isinstance(lead.get("opening_hours"), list) else json.dumps(lead.get("opening_hours", []))
    p["fingerprint"]   = str(lead.get("place_id") or "").strip()   # same as place_id for now

    # ── Contact ───────────────────────────────────────────────────────────────
    p["email"]              = str(lead.get("dm_email") or "").strip()
    p["dm_first_name"]      = str(lead.get("dm_first_name") or "").strip()
    p["dm_position"]        = str(lead.get("dm_position") or "").strip()
    p["dm_linkedin"]        = str(lead.get("dm_linkedin") or "").strip()
    p["email_confidence"]   = int(lead.get("email_confidence") or 0)
    p["decision_maker_title"] = str(lead.get("dm_position") or "").strip()

    # ── Google / ratings ─────────────────────────────────────────────────────
    _rat = lead.get("rating")
    try:
        p["rating"] = round(float(_rat), 1) if _rat not in (None, "", "None") else 0.0
    except (ValueError, TypeError):
        p["rating"] = 0.0

    p["reviews"] = int(lead.get("user_ratings_total") or 0)

    # ── Website flags ─────────────────────────────────────────────────────────
    has_web             = bool(lead.get("has_website", False))
    p["no_website"]     = not has_web          # B44-1: INVERTED
    p["website_live"]   = has_web
    p["website_has_ssl"]= bool(lead.get("has_ssl", False))
    p["website_slow"]   = bool(
        (lead.get("website_load_time_s") or 0) > 4
    )

    # ── Social flags ──────────────────────────────────────────────────────────
    p["has_facebook"]   = bool(lead.get("fb_page_exists", False))
    p["has_instagram"]  = bool(lead.get("ig_profile_exists", False))
    p["has_yelp"]       = bool(lead.get("yelp_url") or lead.get("yelp_rating"))
    _social_count = sum([
        bool(lead.get("fb_page_exists")),
        bool(lead.get("ig_profile_exists")),
        bool(lead.get("linkedin_exists")),
        bool(lead.get("yelp_url")),
    ])
    p["social_presence_count"] = _social_count

    # ── Scores & priority ────────────────────────────────────────────────────
    _score = lead.get("composite_score")
    try:
        p["ai_score"] = int(_score) if _score is not None else 0
    except (ValueError, TypeError):
        p["ai_score"] = 0

    p["heuristic_score"] = p["ai_score"]   # deterministic score before Groq
    p["priority"]        = str(lead.get("priority") or "cold").strip()
    if p["priority"] not in VALID_PRIORITY:
        p["priority"] = "cold"

    p["status"] = "new"

    # ── B44-9: dim_scores serialized to JSON string ───────────────────────────
    p["dim_scores_json"] = json.dumps(lead.get("dim_scores") or {})

    # ── Pain & pitch ─────────────────────────────────────────────────────────
    all_pains = (lead.get("pain_points") or []) + (lead.get("pain_points_locked") or [])
    p["pain_points"]    = "; ".join(str(x) for x in all_pains)
    p["pain_summary"]   = "; ".join(str(x) for x in all_pains[:3])
    p["pain_point_count"] = len(all_pains)
    p["pitch_script"]   = str(lead.get("pitch_script") or "").strip()

    # ── Outreach content ─────────────────────────────────────────────────────
    p["outreach_sms"]         = str(lead.get("outreach_sms") or "").strip()
    p["email_subject_a"]      = str(lead.get("email_subject_a") or "").strip()
    p["email_subject_b"]      = str(lead.get("email_subject_b") or "").strip()
    p["voicemail_script"]     = str(lead.get("voicemail_script") or "").strip()
    p["objection_rebuttal"]   = str(lead.get("objection_rebuttal_1") or "").strip()
    p["best_hook"]            = str(lead.get("best_hook") or "").strip()
    p["best_contact_time"]    = str(lead.get("best_contact_time") or "").strip()
    p["primary_motivation"]   = str(lead.get("primary_motivation") or "").strip()
    p["biggest_fear"]         = str(lead.get("biggest_fear") or "").strip()
    _sms = p["outreach_sms"]
    p["sms_char_count"]       = len(_sms) if _sms else 0

    # ── Psychology & packages ─────────────────────────────────────────────────
    p["archetype"]             = str(lead.get("owner_profile") or lead.get("archetype") or "").strip()
    p["owner_profile"]         = p["archetype"]
    p["recommended_package"]   = str(lead.get("recommended_package") or "").strip()
    _setup = lead.get("package_price_setup")
    _mo    = lead.get("package_price_monthly")
    p["package_price_setup"]   = int(_setup) if _setup is not None else 0
    p["package_price_monthly"] = int(_mo)    if _mo    is not None else 0
    p["offer_match_score"]     = int(lead.get("offer_match_score") or 0)
    p["service_recommendation"]= str(lead.get("service_recommendation") or "").strip()
    _ltv = lead.get("ltv_12mo_estimate")
    p["monthly_value_estimate"]= int(_ltv // 12) if _ltv else 0

    # ── AI validation ─────────────────────────────────────────────────────────
    p["groq_validated"] = bool(lead.get("groq_validated", False))
    p["ai_reason"]      = str(lead.get("groq_reasoning") or "").strip()
    p["data_completeness_score"] = int(lead.get("data_completeness_score") or 0)

    # ── Enrichment failures ───────────────────────────────────────────────────
    _failures = lead.get("enrichment_failures") or []
    p["enrichment_failures"] = ", ".join(_failures) if isinstance(_failures, list) else str(_failures)

    # ── SERP / competition ────────────────────────────────────────────────────
    p["in_local_3pack"]     = bool(lead.get("in_local_3pack", False))
    p["owns_search_result"] = bool(lead.get("in_local_3pack", False))
    _rank = lead.get("organic_rank")
    p["serp_rank"] = int(_rank) if _rank is not None else 0
    _comps = lead.get("serp_top_competitors") or []
    p["serp_top_competitors"] = json.dumps(_comps) if isinstance(_comps, list) else str(_comps)
    _top = _comps[0] if _comps else ""
    p["top_competitor"] = str(_top).strip()

    # ── Yelp ─────────────────────────────────────────────────────────────────
    p["yelp_claimed"]       = bool(lead.get("yelp_claimed", False))
    _yr = lead.get("yelp_rating")
    try:
        p["yelp_rating"] = round(float(_yr), 1) if _yr not in (None, "", "None") else 0.0
    except (ValueError, TypeError):
        p["yelp_rating"] = 0.0
    p["yelp_review_count"]  = int(lead.get("yelp_review_count") or 0)

    # ── Geography ─────────────────────────────────────────────────────────────
    _inc = lead.get("median_household_income")
    p["zip_median_income"]  = int(_inc) if _inc is not None else 0
    p["zip_income_tier"]    = str(lead.get("zip_income_tier") or "").strip()
    p["zip_wealth_tier"]    = str(lead.get("zip_wealth_tier") or "").strip()

    # ── Weather ───────────────────────────────────────────────────────────────
    _wu = lead.get("weather_urgency_boost")
    p["weather_urgency_boost"] = int(_wu) if _wu is not None else 0

    # ── Google rating delta (vs Yelp) ─────────────────────────────────────────
    _gr  = p["rating"]
    _yr2 = p["yelp_rating"]
    p["google_rating_delta"] = round(_gr - _yr2, 1) if (_gr and _yr2) else 0.0

    # ── B44-10: timestamps must be ISO strings ────────────────────────────────
    def _iso(val) -> str:
        if not val:
            return datetime.datetime.utcnow().isoformat()
        return str(val)

    p["discovered_at"] = _iso(lead.get("discovered_at"))
    p["scored_at"]     = _iso(lead.get("scored_at"))

    # ── Tracking fields (defaults) ────────────────────────────────────────────
    p["crm_sync_status"]     = "pending"
    p["outreach_channel_sent"] = ""
    p["outreach_sent_at"]    = ""
    p["win_loss_outcome"]    = ""
    p["outcome_set_at"]      = ""
    p["outcome_set_by"]      = ""
    p["reply_source"]        = ""
    p["assigned_to"]         = ""
    p["assigned_rep_id"]     = ""
    p["assigned_at"]         = ""
    p["source_confidence"]   = int(lead.get("email_confidence") or 0)
    p["customer_id"]         = ""

    # ── Final: strip any None values that slipped through ────────────────────
    # Replace None with "" for strings, 0 for numbers (per B44-1)
    cleaned = {}
    for k, v in p.items():
        if v is None:
            cleaned[k] = ""
        else:
            cleaned[k] = v

    return cleaned


def _sanitize_wholesale_payload(lead: dict) -> dict:
    """
    Build a clean Base44 WholesaleLead payload from a Python lead dict.
    Covers the confirmed WholesaleLead field list (§4.9).
    """
    all_pains = (lead.get("pain_points") or []) + (lead.get("pain_points_locked") or [])

    _score = lead.get("composite_score")
    try:
        score = int(_score) if _score is not None else 0
    except (ValueError, TypeError):
        score = 0

    _ltv = lead.get("ltv_12mo_estimate")
    try:
        ltv = int(_ltv) if _ltv is not None else 0
    except (ValueError, TypeError):
        ltv = 0

    tier = str(lead.get("priority") or "warm").strip()
    if tier not in VALID_WS_TIER:
        tier = "warm"

    p = {
        # Required
        "name"          : str(lead.get("name") or "").strip(),
        "niche"         : str(lead.get("niche") or "").strip(),
        "city"          : str(lead.get("city") or "").strip(),
        # Core
        "tier"          : tier,
        "score"         : score,
        "base_price"    : int(lead.get("lead_price") or 97),
        "list_price"    : int(lead.get("lead_price") or 97),
        "status"        : "listed",
        "ltv_estimate"  : ltv,
        "pain_points"   : "; ".join(str(x) for x in all_pains),
        "archetype"     : str(lead.get("owner_profile") or lead.get("archetype") or "").strip(),
        "phone"         : str(lead.get("phone_number") or "").strip(),
        "email"         : str(lead.get("dm_email") or "").strip(),
        "website"       : str(lead.get("website") or "").strip(),
        "age_days"      : 0,
        "pitch_script"  : str(lead.get("pitch_script") or "").strip(),
        "place_id"      : str(lead.get("place_id") or "").strip(),
        "zip_code"      : str(lead.get("zip_code") or "").strip(),
        "state"         : str(lead.get("state") or "").strip(),
        "dm_first_name" : str(lead.get("dm_first_name") or "").strip(),
        "dm_position"   : str(lead.get("dm_position") or "").strip(),
        "email_confidence": int(lead.get("email_confidence") or 0),
        # Flags
        "no_website"            : not bool(lead.get("has_website", False)),
        "has_facebook_pixel"    : bool(lead.get("has_facebook_pixel", False)),
        "booking_detected"      : bool(lead.get("has_booking_widget", False)),
        "has_reputation_tool"   : bool(lead.get("has_review_widget", False)),
        "in_local_3pack"        : bool(lead.get("in_local_3pack", False)),
        "competitor_running_ads": bool(lead.get("competitor_running_ads", False)),
        "website_is_stale"      : bool((lead.get("site_staleness_years") or 0) >= 3),
        # Package
        "recommended_package"   : str(lead.get("recommended_package") or "").strip(),
        "package_price_setup"   : int(lead.get("package_price_setup") or 0),
        "package_price_monthly" : int(lead.get("package_price_monthly") or 0),
        "offer_match_score"     : int(lead.get("offer_match_score") or 0),
        # AI
        "groq_validated"        : bool(lead.get("groq_validated", False)),
        "groq_reasoning"        : str(lead.get("groq_reasoning") or "").strip(),
        "data_completeness_score": int(lead.get("data_completeness_score") or 0),
        "disqualified"          : bool(lead.get("disqualified", False)),
        "disqualify_reason"     : str(lead.get("disqualify_reason") or "").strip(),
        # Scores JSON
        "dim_scores_json"       : json.dumps(lead.get("dim_scores") or {}),
        # Outreach tracking
        "sequence_step"         : int(lead.get("sequence_step") or 0),
        "sequence_status"       : str(lead.get("sequence_status") or "pending").strip(),
        "total_touches"         : int(lead.get("total_touches") or 0),
        "email_opens"           : int(lead.get("email_opens") or 0),
        "email_clicks"          : int(lead.get("email_clicks") or 0),
        "email_replies"         : int(lead.get("email_replies") or 0),
        "sms_replies"           : int(lead.get("sms_replies") or 0),
        "bounced"               : bool(lead.get("bounced", False)),
        # Dates
        "imported_at"           : datetime.date.today().isoformat(),
        "sold_at"               : str(lead.get("sold_at") or ""),
        "sold_price"            : int(lead.get("sold_price") or 0),
        "reserved_for"          : str(lead.get("reserved_for") or ""),
        # Enrichment
        "zip_income_tier"       : str(lead.get("zip_income_tier") or ""),
        "zip_wealth_tier"       : str(lead.get("zip_wealth_tier") or ""),
        "serp_top_competitors"  : json.dumps(lead.get("serp_top_competitors") or []),
        "discovered_at"         : str(lead.get("discovered_at") or datetime.datetime.utcnow().isoformat()),
        "scored_at"             : str(lead.get("scored_at") or datetime.datetime.utcnow().isoformat()),
        "buyer_type"            : "",
    }

    # Strip any None that slipped through
    return {k: ("" if v is None else v) for k, v in p.items()}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — HTTP PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════

def _b44_request(method: str, url: str, payload: dict = None,
                 params: dict = None) -> dict | None:
    """
    Core HTTP helper for all Base44 calls.
    Handles rate limiting (429) with exponential backoff.
    Never throws — returns None on permanent failure.
    """
    headers = _b44_headers()
    for attempt in range(BASE44_MAX_RETRIES):
        try:
            if method == "GET":
                r = requests.get(url, headers=headers, params=params, timeout=15)
            elif method == "POST":
                r = requests.post(url, headers=headers, json=payload, timeout=15)
            elif method == "PATCH":
                r = requests.patch(url, headers=headers, json=payload, timeout=15)
            elif method == "DELETE":
                r = requests.delete(url, headers=headers, timeout=15)
            else:
                return None

            if r.status_code in (200, 201):
                return r.json()
            elif r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"      ⏳ [B44] Rate limit — waiting {wait}s")
                time.sleep(wait)
                continue
            elif r.status_code == 400:
                print(f"      ⚠️  [B44] 400 Bad request: {r.text[:300]}")
                return None   # don't retry bad payloads
            elif r.status_code == 401:
                print(f"      ⚠️  [B44] 401 Invalid API key — check BASE44_API_KEY")
                return None
            elif r.status_code == 403:
                print(f"      ⚠️  [B44] 403 Forbidden — app must be PUBLIC mode")
                return None
            elif r.status_code == 404:
                print(f"      ⚠️  [B44] 404 Entity not found — check entity name case")
                return None
            else:
                print(f"      ⚠️  [B44] HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(2)

        except requests.exceptions.Timeout:
            print(f"      ⚠️  [B44] Timeout (attempt {attempt + 1}/{BASE44_MAX_RETRIES})")
            time.sleep(2)
        except Exception as e:
            print(f"      ⚠️  [B44] Error: {e}")
            time.sleep(2)

    return None


def _b44_get_by_place_id(entity: str, place_id: str) -> dict | None:
    """
    Query Base44 for an existing record by place_id.
    Returns the first matching record dict (with 'id'), or None.
    """
    if not place_id:
        return None
    url    = f"{_b44_base()}/{entity}"
    params = {"filters": json.dumps({"place_id": place_id}), "limit": 1}
    result = _b44_request("GET", url, params=params)
    if isinstance(result, list) and result and result[0].get("id"):
        return result[0]
    return None


def _b44_update(entity: str, record_id: str, payload: dict) -> dict | None:
    """
    PATCH an existing Base44 record by its id.
    B44-4: implements update logic missing from original v9.
    """
    url = f"{_b44_base()}/{entity}/{record_id}"
    return _b44_request("PATCH", url, payload=payload)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — UPSERT (the main public function)
# Implements B44-5: always GET by place_id first, then POST or PATCH
# ══════════════════════════════════════════════════════════════════════════════

def _b44_upsert(entity: str, payload: dict,
                id_field: str = "place_id") -> tuple[str | None, str]:
    """
    Upsert a record to Base44.

    Strategy (B44-5):
      1. GET by place_id — if exists, PATCH and return existing id
      2. If not found, POST new record and return new id

    Returns:
      (record_id: str | None, action: str)
      action is "created", "updated", or "failed"
    """
    if not BASE44_APP_ID or not BASE44_API_KEY:
        return None, "failed"

    entity_url = f"{_b44_base()}/{entity}"
    place_id   = payload.get(id_field, "")

    # Step 1: Check for existing record
    existing = _b44_get_by_place_id(entity, place_id) if place_id else None

    if existing:
        record_id = existing["id"]
        result    = _b44_update(entity, record_id, payload)
        if result:
            return record_id, "updated"
        else:
            # PATCH failed — log but don't retry as POST (would create duplicate)
            return None, "failed"

    # Step 2: POST new record
    result = _b44_request("POST", entity_url, payload=payload)
    if result and result.get("id"):
        return result["id"], "created"

    return None, "failed"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — FAILED PUSH LOG
# B44-6: clear entries after successful retry
# ══════════════════════════════════════════════════════════════════════════════

FAILED_PUSH_LOG = "failed_pushes.json"

def _log_failed_push(lead: dict, entity: str):
    """Append a failed push to the retry log."""
    try:
        try:
            with open(FAILED_PUSH_LOG) as f:
                log = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            log = []
        log.append({
            "place_id"  : lead.get("place_id"),
            "name"      : lead.get("name"),
            "entity"    : entity,
            "score"     : lead.get("composite_score"),
            "failed_at" : datetime.datetime.utcnow().isoformat(),
        })
        with open(FAILED_PUSH_LOG, "w") as f:
            json.dump(log, f, indent=2)
    except Exception:
        pass


def _clear_failed_push(place_id: str, entity: str):
    """
    B44-6: Remove a place_id+entity entry from failed_pushes.json
    after a successful retry.
    """
    try:
        with open(FAILED_PUSH_LOG) as f:
            log = json.load(f)
        updated = [
            e for e in log
            if not (e.get("place_id") == place_id and e.get("entity") == entity)
        ]
        if len(updated) < len(log):
            with open(FAILED_PUSH_LOG, "w") as f:
                json.dump(updated, f, indent=2)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def load_failed_pushes() -> list:
    """Load all entries from the failed push log."""
    try:
        with open(FAILED_PUSH_LOG) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

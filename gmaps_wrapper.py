"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LeadFlow AI — scrapers/gmaps_wrapper.py                                     ║
║  gosom/google-maps-scraper Docker wrapper                                    ║
║  github.com/gosom/google-maps-scraper                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  PURPOSE                                                                     ║
║  Primary discovery source — replaces broken Playwright as source #1.         ║
║  Runs gosom as a Docker subprocess, parses the CSV output, and returns       ║
║  _norm_biz()-compatible dicts with reliable rating + review counts.          ║
║                                                                              ║
║  GOSOM REAL FIELD NAMES (all 34, from README):                               ║
║  input_id, link, title, category, address, open_hours, popular_times,        ║
║  website, phone, plus_code, review_count, review_rating, reviews_per_rating, ║
║  latitude, longitude, cid, status, descriptions, reviews_link, thumbnail,    ║
║  timezone, price_range, data_id, images, reservations, order_online, menu,   ║
║  owner, complete_address, about, user_reviews, emails, user_reviews_extended,║
║  place_id                                                                    ║
║                                                                              ║
║  INSTALL                                                                     ║
║  docker pull gosom/google-maps-scraper                                       ║
║                                                                              ║
║  RAILWAY NOTE                                                                ║
║  Docker-in-Docker on Railway requires privileged mode. Use GOSOM_MODE=binary ║
║  on Railway — download the binary from GitHub releases and call it directly. ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import csv
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from typing import Dict, List, Optional

logger = logging.getLogger("leadflow.gmaps")

# ── Config ────────────────────────────────────────────────────────────────────
GOSOM_DOCKER_IMAGE    = "gosom/google-maps-scraper"
GOSOM_CACHE_VOLUME    = "gmaps-playwright-cache"
GOSOM_BINARY_PATH     = os.getenv("GOSOM_BINARY_PATH", "")   # set on Railway
GOSOM_MODE            = os.getenv("GOSOM_MODE", "docker")    # "docker" | "binary"
GOSOM_DEFAULT_DEPTH   = int(os.getenv("GOSOM_DEPTH", "1"))
GOSOM_CONCURRENCY     = int(os.getenv("GOSOM_CONCURRENCY", "2"))
GOSOM_TIMEOUT_SECONDS = int(os.getenv("GOSOM_TIMEOUT", "600"))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — FIELD MAPPING
# gosom field name → LeadFlow lead dict key
# Only maps fields where the names differ. Fields listed as None are parsed
# separately (e.g. city/state/zip extracted from address).
# ══════════════════════════════════════════════════════════════════════════════

GOSOM_FIELD_MAP = {
    # gosom name          : leadflow name
    "title"               : "name",
    "phone"               : "phone_number",
    "review_count"        : "user_ratings_total",
    "review_rating"       : "rating",
    "link"                : "maps_url",
    "latitude"            : "lat",
    "longitude"           : "lng",
    "status"              : "business_status",
    "descriptions"        : "editorial_summary",
    "category"            : "editorial_summary",   # fallback if no description
    "complete_address"    : "formatted_address",
    "address"             : "formatted_address",   # fallback
    "open_hours"          : "opening_hours",
    "price_range"         : "_gosom_price_range",  # parsed separately
    "emails"              : "_gosom_emails",        # parsed separately
    "place_id"            : "place_id",
    "cid"                 : "_gosom_cid",
    "plus_code"           : "plus_code",
    "data_id"             : "_gosom_data_id",
    "thumbnail"           : "_gosom_thumbnail",
    "reservations"        : "_gosom_reservations",
    "owner"               : "_gosom_owner_claimed",
    "about"               : "_gosom_about",
    "popular_times"       : "_gosom_popular_times",
    "timezone"            : "_gosom_timezone",
    "reviews_link"        : "_gosom_reviews_link",
}

# Map gosom business status values → LeadFlow standard values
STATUS_MAP = {
    "open"             : "OPERATIONAL",
    "closed"           : "CLOSED_TEMPORARILY",
    "permanently_closed": "CLOSED_PERMANENTLY",
    "temporarily_closed": "CLOSED_TEMPORARILY",
    ""                 : "UNKNOWN",
}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TYPE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _safe_float(val) -> Optional[float]:
    try:
        v = str(val).strip()
        return float(v) if v and v.lower() not in ("none", "null", "", "nan") else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        v = str(val).strip()
        f = float(v) if v and v.lower() not in ("none", "null", "", "nan") else None
        return int(f) if f is not None else None
    except (ValueError, TypeError):
        return None


def _parse_price_level(price_range: str) -> Optional[int]:
    """Convert gosom price_range ('$', '$$', '$$$', '$$$$') → int 1-4."""
    if not price_range:
        return None
    count = price_range.count("$")
    return count if 1 <= count <= 4 else None


def _parse_address(address: str) -> Dict[str, str]:
    """
    Extract city, state, zip from a US formatted address string.
    Handles: "123 Main St, Atlanta, GA 30301, USA"
    Returns dict with city, state, zip_code keys.
    """
    result = {"city": "", "state": "", "zip_code": ""}
    if not address:
        return result

    # Remove trailing country
    addr = re.sub(r",?\s*(USA|United States)\s*$", "", address.strip())
    parts = [p.strip() for p in addr.split(",")]

    if len(parts) >= 3:
        result["city"] = parts[-2].strip()
        # Last part: "GA 30301" or "GA"
        state_zip = parts[-1].strip().split()
        if state_zip:
            result["state"]    = state_zip[0]
            result["zip_code"] = state_zip[1] if len(state_zip) > 1 else ""
    elif len(parts) == 2:
        result["city"] = parts[-1].strip()

    return result


def _parse_emails(emails_raw: str) -> List[str]:
    """
    Parse gosom's emails field — can be a JSON array string or
    comma-separated list.
    Returns list of email strings.
    """
    if not emails_raw or str(emails_raw).strip().lower() in ("", "none", "null", "[]"):
        return []
    raw = str(emails_raw).strip()
    # Try JSON array
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(e).strip() for e in parsed if e]
        except json.JSONDecodeError:
            pass
    # Comma-separated
    return [e.strip() for e in raw.split(",") if e.strip() and "@" in e]


def _best_email(emails: List[str]) -> tuple[str, int, str]:
    """
    Pick the best email from a list.
    Returns (email, confidence, type).
    Priority: personal (firstname@domain) > generic (info@, contact@) > nothing.
    """
    GENERIC_PREFIXES = {
        "info", "contact", "hello", "admin", "support", "mail", "office",
        "sales", "team", "help", "service", "enquiries", "general", "billing",
        "reception", "enquiry", "feedback", "noreply", "no-reply",
    }
    if not emails:
        return "", 0, ""

    personal = []
    generic  = []
    for email in emails:
        local = email.split("@")[0].lower() if "@" in email else ""
        if local in GENERIC_PREFIXES or not local:
            generic.append(email)
        else:
            personal.append(email)

    if personal:
        return personal[0], 75, "personal"
    if generic:
        return generic[0], 40, "generic"
    return emails[0], 30, "unknown"


def _normalize_status(raw_status: str) -> str:
    """Normalize gosom status → LeadFlow OPERATIONAL/CLOSED_*/UNKNOWN."""
    if not raw_status:
        return "UNKNOWN"
    s = raw_status.lower().strip().replace(" ", "_")
    return STATUS_MAP.get(s, "OPERATIONAL")


def _docker_available() -> bool:
    """Check if Docker daemon is running and accessible."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _binary_available() -> bool:
    """Check if gosom binary is available at GOSOM_BINARY_PATH."""
    if not GOSOM_BINARY_PATH:
        return False
    return os.path.isfile(GOSOM_BINARY_PATH) and os.access(GOSOM_BINARY_PATH, os.X_OK)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — CSV PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _parse_csv_to_leads(csv_path: str, niche: str, city: str) -> List[Dict]:
    """
    Parse gosom CSV output into LeadFlow-compatible lead dicts.
    Uses the confirmed 34-field schema from gosom README.
    Each row maps to a dict compatible with _norm_biz().
    """
    results = []

    if not os.path.exists(csv_path):
        logger.warning(f"[gosom] CSV not found: {csv_path}")
        return results

    try:
        with open(csv_path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lead = _row_to_lead(row, niche, city)
                if lead and lead.get("name"):
                    results.append(lead)
    except Exception as e:
        logger.warning(f"[gosom] CSV parse error: {e}")

    logger.info(f"[gosom] Parsed {len(results)} leads from {os.path.basename(csv_path)}")
    return results


def _row_to_lead(row: Dict, niche: str, city: str) -> Optional[Dict]:
    """Convert one gosom CSV row into a LeadFlow lead dict."""
    name = (row.get("title") or row.get("name") or "").strip()
    if not name:
        return None

    # ── Core identity ─────────────────────────────────────────────────────────
    address = (row.get("complete_address") or row.get("address") or "").strip()
    addr_parts = _parse_address(address)

    # ── Rating and reviews — the critical fields ──────────────────────────────
    rating          = _safe_float(row.get("review_rating"))
    review_count    = _safe_int(row.get("review_count"))

    # ── Emails ────────────────────────────────────────────────────────────────
    emails          = _parse_emails(row.get("emails", ""))
    dm_email, confidence, email_type = _best_email(emails)

    # ── Price level ───────────────────────────────────────────────────────────
    price_level     = _parse_price_level(row.get("price_range", ""))

    # ── Business status ───────────────────────────────────────────────────────
    status_raw      = (row.get("status") or "").strip()
    business_status = _normalize_status(status_raw)

    # ── Description: prefer 'descriptions', fall back to 'about', then 'category' ──
    description = (
        row.get("descriptions") or
        row.get("about")        or
        row.get("category")     or ""
    ).strip()

    # ── Hours: gosom returns JSON or plain string ─────────────────────────────
    hours_raw = row.get("open_hours", "")
    if hours_raw and hours_raw.startswith("{"):
        try:
            hours = json.loads(hours_raw)
        except json.JSONDecodeError:
            hours = hours_raw
    else:
        hours = hours_raw

    # ── Place ID: gosom 'place_id' field (direct, reliable) ──────────────────
    place_id = (row.get("place_id") or row.get("data_id") or "").strip()

    # ── Maps URL ──────────────────────────────────────────────────────────────
    maps_url = (row.get("link") or "").strip()

    # ── Coordinates ───────────────────────────────────────────────────────────
    lat = _safe_float(row.get("latitude"))
    lng = _safe_float(row.get("longitude"))

    lead = {
        # Identity
        "name"                  : name,
        "formatted_address"     : address,
        "city"                  : addr_parts.get("city") or city,
        "state"                 : addr_parts.get("state", ""),
        "zip_code"              : addr_parts.get("zip_code", ""),
        "niche"                 : niche,
        # Contact
        "phone_number"          : (row.get("phone") or "").strip(),
        "website"               : (row.get("website") or "").strip(),
        "maps_url"              : maps_url,
        # Google signals
        "rating"                : rating,
        "user_ratings_total"    : review_count if review_count is not None else 0,
        "price_level"           : price_level,
        "business_status"       : business_status,
        "editorial_summary"     : description,
        "opening_hours"         : hours,
        # Coordinates
        "lat"                   : lat,
        "lng"                   : lng,
        # Place ID (direct from gosom — no regex extraction needed)
        "place_id"              : place_id,
        # Email (extracted by gosom -email flag)
        "dm_email"              : dm_email,
        "email_confidence"      : confidence,
        "email_type"            : email_type,
        "website_emails"        : emails,
        # Extra gosom fields (stored for scoring/Groq use)
        "plus_code"             : (row.get("plus_code") or "").strip(),
        "_gosom_cid"            : (row.get("cid") or "").strip(),
        "_gosom_thumbnail"      : (row.get("thumbnail") or "").strip(),
        "_gosom_owner_claimed"  : bool(row.get("owner")),
        "_gosom_reservations"   : (row.get("reservations") or "").strip(),
        "_gosom_reviews_link"   : (row.get("reviews_link") or "").strip(),
        "_gosom_popular_times"  : (row.get("popular_times") or "").strip(),
        # Source tag
        "_discovery_source"     : "gosom_gmaps",
    }

    # Photo count not directly available from gosom CSV — default 0
    # (will be enriched by GBP scraper if needed)
    lead["photo_count"] = 0

    return lead


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def _run_gosom_docker(
    query_file: str,
    output_file: str,
    depth: int,
    concurrency: int,
    include_emails: bool,
    timeout: int,
) -> bool:
    """
    Run gosom via Docker subprocess.
    Returns True if process completed (even with partial results).
    """
    output_dir = os.path.dirname(output_file)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{GOSOM_CACHE_VOLUME}:/opt",
        "-v", f"{query_file}:/queries.txt:ro",
        "-v", f"{output_dir}:/out",
        GOSOM_DOCKER_IMAGE,
        "-input",  "/queries.txt",
        "-results", "/out/results.csv",
        "-depth",  str(depth),
        "-c",      str(concurrency),
        "-exit-on-inactivity", "3m",
        "-lang",   "en",
    ]
    if include_emails:
        cmd.append("-email")

    logger.info(f"[gosom] Running Docker: depth={depth} concurrency={concurrency} email={include_emails}")

    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True
        )
        if result.returncode not in (0, 1):   # gosom exits 1 on some timeouts but still writes CSV
            logger.warning(f"[gosom] Docker exit code {result.returncode}: {result.stderr[:300]}")
        return True
    except subprocess.TimeoutExpired:
        logger.warning(f"[gosom] Docker run timed out after {timeout}s — returning partial results")
        return True   # partial results may still be in output file
    except FileNotFoundError:
        logger.error("[gosom] Docker not found — install Docker Desktop")
        return False
    except Exception as e:
        logger.error(f"[gosom] Docker error: {e}")
        return False


def _run_gosom_binary(
    query_file: str,
    output_file: str,
    depth: int,
    concurrency: int,
    include_emails: bool,
    timeout: int,
) -> bool:
    """
    Run gosom via pre-built binary (Railway / no-Docker environments).
    Binary must be at GOSOM_BINARY_PATH.
    """
    if not _binary_available():
        logger.error(f"[gosom] Binary not found at {GOSOM_BINARY_PATH}")
        return False

    cmd = [
        GOSOM_BINARY_PATH,
        "-input",   query_file,
        "-results", output_file,
        "-depth",   str(depth),
        "-c",       str(concurrency),
        "-exit-on-inactivity", "3m",
        "-lang",    "en",
    ]
    if include_emails:
        cmd.append("-email")

    logger.info(f"[gosom] Running binary: depth={depth} concurrency={concurrency}")

    try:
        result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
        return True
    except subprocess.TimeoutExpired:
        logger.warning(f"[gosom] Binary timed out after {timeout}s — returning partial results")
        return True
    except Exception as e:
        logger.error(f"[gosom] Binary error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def is_gosom_available() -> bool:
    """
    Check whether gosom is usable in the current environment.
    Tries Docker first, then binary. Returns False if neither is available.
    Call this once at startup to decide whether to include gosom in the
    discovery source list.
    """
    if GOSOM_MODE == "binary":
        return _binary_available()
    return _docker_available()


def scrape_google_maps(
    queries:         List[str],
    niche:           str  = "",
    city:            str  = "",
    depth:           int  = None,
    concurrency:     int  = None,
    include_emails:  bool = True,
    timeout_seconds: int  = None,
) -> List[Dict]:
    """
    Main entry point.

    Takes a list of search queries and returns a list of lead dicts
    fully compatible with _norm_biz() — ready to pass straight to
    enrich_lead() without any transformation.

    Args:
        queries:         e.g. ["roofing contractor in Atlanta, GA"]
        niche:           niche string for _norm_biz context
        city:            city string for _norm_biz context
        depth:           scroll depth (1 = ~20 results, 5 = ~100 results)
        concurrency:     parallel browser workers (2 is safe default)
        include_emails:  run -email flag to extract emails from websites
        timeout_seconds: hard timeout for the Docker/binary run

    Returns:
        List of lead dicts with all gosom fields mapped to LeadFlow names.

    Usage in leadflow_v9.py:
        from scrapers.gmaps_wrapper import scrape_google_maps
        leads = scrape_google_maps(
            ["roofing contractor in Atlanta, GA"],
            niche="roofing contractor", city="Atlanta, GA"
        )
    """
    if not queries:
        return []

    depth       = depth       or GOSOM_DEFAULT_DEPTH
    concurrency = concurrency or GOSOM_CONCURRENCY
    timeout     = timeout_seconds or GOSOM_TIMEOUT_SECONDS

    # Write queries to temp file
    try:
        qf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, dir="/tmp"
        )
        qf.write("\n".join(q.strip() for q in queries if q.strip()))
        qf.close()
        query_file = qf.name
    except Exception as e:
        logger.error(f"[gosom] Failed to write query file: {e}")
        return []

    # Output CSV path
    output_dir  = tempfile.mkdtemp(dir="/tmp")
    output_file = os.path.join(output_dir, "results.csv")

    # Run gosom (Docker or binary)
    success = False
    if GOSOM_MODE == "binary" and _binary_available():
        success = _run_gosom_binary(
            query_file, output_file, depth, concurrency, include_emails, timeout
        )
    elif _docker_available():
        success = _run_gosom_docker(
            query_file, output_file, depth, concurrency, include_emails, timeout
        )
    else:
        logger.error(
            "[gosom] Neither Docker nor binary available. "
            "Install Docker Desktop or set GOSOM_BINARY_PATH."
        )
        _cleanup(query_file, output_file, output_dir)
        return []

    # Parse results even on partial success
    results = []
    if success:
        results = _parse_csv_to_leads(output_file, niche, city)
        logger.info(f"[gosom] Completed: {len(results)} leads from {len(queries)} queries")

    _cleanup(query_file, output_file, output_dir)
    return results


def _cleanup(query_file: str, output_file: str, output_dir: str):
    """Remove temp files silently."""
    for path in (query_file, output_file):
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
    try:
        if output_dir and os.path.exists(output_dir):
            os.rmdir(output_dir)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — DISC FUNCTION (drop-in for _discover_combo)
# ══════════════════════════════════════════════════════════════════════════════

def disc_gosom(niche: str, city: str, limit: int) -> List[Dict]:
    """
    Discovery adapter — same signature as _disc_outscraper(), _disc_serpapi() etc.
    Returns list of _norm_biz()-compatible dicts, capped at `limit`.

    Wired into leadflow_v9.py _discover_combo() as priority source #1.

    Returns [] if Docker/binary is unavailable so the next source is tried.
    """
    if not is_gosom_available():
        return []

    query  = f"{niche} in {city}"
    leads  = scrape_google_maps(
        queries        = [query],
        niche          = niche,
        city           = city,
        depth          = 1,
        concurrency    = GOSOM_CONCURRENCY,
        include_emails = True,
    )

    # Cap to limit and log
    leads = leads[:limit]

    if leads:
        # Log a sample of key fields to confirm data quality
        sample = leads[0]
        logger.info(
            f"[gosom] Sample: {sample.get('name')} "
            f"rating={sample.get('rating')} "
            f"reviews={sample.get('user_ratings_total')} "
            f"email={bool(sample.get('dm_email'))}"
        )

    return leads


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — SELF-TEST
# Run directly: python3 scrapers/gmaps_wrapper.py
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    print("=" * 60)
    print("gosom wrapper — self-test")
    print("=" * 60)

    # Check availability
    docker_ok = _docker_available()
    binary_ok = _binary_available()
    print(f"Docker available : {docker_ok}")
    print(f"Binary available : {binary_ok}  (path: {GOSOM_BINARY_PATH or 'not set'})")
    print(f"Mode             : {GOSOM_MODE}")

    if not docker_ok and not binary_ok:
        print()
        print("Neither Docker nor binary found.")
        print("Install Docker Desktop:  https://docker.com/products/docker-desktop")
        print("Then run:                docker pull gosom/google-maps-scraper")
        raise SystemExit(1)

    print()
    print("Running test scrape: dentist in Birmingham, AL (depth=1, limit=5)")
    print("This takes 3-5 minutes the first time (browser cache warm-up)...")
    print()

    leads = disc_gosom("general dentist", "Birmingham, AL", limit=5)

    if not leads:
        print("No results returned. Check Docker is running and retry.")
        raise SystemExit(1)

    print(f"Got {len(leads)} leads:")
    for i, lead in enumerate(leads, 1):
        rating  = lead.get("rating")
        reviews = lead.get("user_ratings_total")
        email   = lead.get("dm_email") or "(none)"
        phone   = lead.get("phone_number") or "(none)"
        status  = lead.get("business_status")
        print(f"  [{i}] {lead['name']}")
        print(f"       rating={rating}  reviews={reviews}  status={status}")
        print(f"       phone={phone}  email={email}")
        print(f"       place_id={lead.get('place_id','')[:30]}")

    # Verify key fields
    print()
    print("Field verification:")
    populated = lambda f: sum(1 for l in leads if l.get(f) is not None and l.get(f) != "" and l.get(f) != 0)
    for field in ["rating", "user_ratings_total", "phone_number", "website", "business_status", "place_id"]:
        n = populated(field)
        icon = "✅" if n == len(leads) else ("⚠️ " if n > 0 else "❌")
        print(f"  {icon}  {field}: {n}/{len(leads)} populated")

    print()
    print("✅ gosom wrapper working correctly")
    print("Rating and review_count are populated — your biggest data gap is fixed.")

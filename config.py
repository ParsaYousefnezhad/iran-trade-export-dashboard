# ============================================================
# config.py
# ============================================================

BASE_URL = "https://ir-trades.com"

# Iran exports to the world
EXPORT_URL = (
    "https://ir-trades.com/stat/"
    "MQAzADkANQAwADEAEycxADQAMAA0ADEAMAATJ0UAeABwAG8AcgB0ABMnbgB1AGwAbAATJxMnEyduAHUAbABsABMnbgB1AGwAbAATJzgAEycxADQAMAA0ADEAMAATJ20AbwBuAHQAaAA="
)

# Later we can add the equivalent import URL here.
IMPORT_URL = None


# ------------------------------------------------------------
# Scraper settings
# ------------------------------------------------------------

PAGE_TIMEOUT = 120_000
BLazor_WAIT_SECONDS = 5

MAX_RETRIES = 3

# Countries with >= 1% of total export value
MAJOR_COUNTRY_THRESHOLD = 1.0


# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------

RAW_DATA_DIR = "data/raw"
PROCESSED_DATA_DIR = "data/processed"

GLOBAL_EXPORT_HTML = f"{RAW_DATA_DIR}/global_export.html"
GLOBAL_EXPORT_COUNTRIES_CSV = f"{PROCESSED_DATA_DIR}/export_countries.csv"
GLOBAL_EXPORT_PRODUCTS_CSV = f"{PROCESSED_DATA_DIR}/export_hs_sections.csv"


# Database
DB_PATH = "exports.db"
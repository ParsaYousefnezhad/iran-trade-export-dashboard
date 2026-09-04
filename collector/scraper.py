# ============================================================
# collector/scraper.py
# ============================================================

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional
import pandas as pd
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from config import EXPORT_URL, PAGE_TIMEOUT, BLazor_WAIT_SECONDS, MAX_RETRIES, MAJOR_COUNTRY_THRESHOLD, RAW_DATA_DIR, DB_PATH
from collector.parser import parse_export_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def ensure_directories() -> None:
    Path(RAW_DATA_DIR).mkdir(parents=True, exist_ok=True)

def safe_float(val):
    if val is None: return None
    try:
        if pd.isna(val): return None
        return float(val)
    except (ValueError, TypeError):
        return None

def safe_str(val) -> Optional[str]:
    """Safely cast to string, filtering out NaNs, Nones, and empty strings."""
    if val is None: return None
    try:
        if pd.isna(val): return None
    except Exception:
        pass
    s = str(val).strip()
    if s.lower() in ("", "nan", "none", "null"): return None
    return s

CREATE_TABLES = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key_name TEXT PRIMARY KEY,
    header_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS countries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name_fa TEXT UNIQUE NOT NULL,
    rank INTEGER,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    export_value_musd REAL,
    share_pct REAL
);

CREATE TABLE IF NOT EXISTS customs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name_fa TEXT UNIQUE NOT NULL,
    rank INTEGER,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    export_value_musd REAL,
    share_pct REAL
);

CREATE TABLE IF NOT EXISTS hs_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT,
    tariff_code TEXT,
    name_fa TEXT,
    UNIQUE(section, tariff_code)
);

CREATE TABLE IF NOT EXISTS global_hs (
    hs_section_id INTEGER PRIMARY KEY,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    total_value_musd REAL,
    ratio_pct REAL,
    FOREIGN KEY (hs_section_id) REFERENCES hs_sections(id)
);

CREATE TABLE IF NOT EXISTS monthly_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL CHECK(level IN ('global', 'country', 'product')),
    level_id INTEGER,
    period TEXT NOT NULL,
    value_musd REAL
);

CREATE TABLE IF NOT EXISTS monthly_weight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL CHECK(level IN ('global', 'country', 'product')),
    level_id INTEGER,
    period TEXT NOT NULL,
    value_ton REAL
);

CREATE TABLE IF NOT EXISTS country_hs (
    country_id INTEGER NOT NULL,
    hs_section_id INTEGER NOT NULL,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    total_value_musd REAL,
    ratio_pct REAL,
    PRIMARY KEY (country_id, hs_section_id),
    FOREIGN KEY (country_id) REFERENCES countries(id),
    FOREIGN KEY (hs_section_id) REFERENCES hs_sections(id)
);

CREATE TABLE IF NOT EXISTS country_customs (
    country_id INTEGER NOT NULL,
    customs_id INTEGER NOT NULL,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    export_value_musd REAL,
    share_pct REAL,
    PRIMARY KEY (country_id, customs_id),
    FOREIGN KEY (country_id) REFERENCES countries(id),
    FOREIGN KEY (customs_id) REFERENCES customs(id)
);

CREATE TABLE IF NOT EXISTS product_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    country_id INTEGER NOT NULL,
    hs_section_id INTEGER NOT NULL,
    UNIQUE(country_id, hs_section_id),
    FOREIGN KEY (country_id) REFERENCES countries(id),
    FOREIGN KEY (hs_section_id) REFERENCES hs_sections(id)
);

CREATE TABLE IF NOT EXISTS product_subgroups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_instance_id INTEGER NOT NULL,
    subgroup_section TEXT,
    tariff_code TEXT,
    name_fa TEXT,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    total_value_musd REAL,
    ratio_pct REAL,
    FOREIGN KEY (product_instance_id) REFERENCES product_instances(id)
);

CREATE TABLE IF NOT EXISTS product_country_share (
    product_instance_id INTEGER NOT NULL,
    country_id INTEGER NOT NULL,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    export_value_musd REAL,
    share_pct REAL,
    PRIMARY KEY (product_instance_id, country_id),
    FOREIGN KEY (product_instance_id) REFERENCES product_instances(id),
    FOREIGN KEY (country_id) REFERENCES countries(id)
);

CREATE TABLE IF NOT EXISTS product_customs_share (
    product_instance_id INTEGER NOT NULL,
    customs_id INTEGER NOT NULL,
    period1_value_musd REAL,
    period2_value_musd REAL,
    change_pct REAL,
    export_value_musd REAL,
    share_pct REAL,
    PRIMARY KEY (product_instance_id, customs_id),
    FOREIGN KEY (product_instance_id) REFERENCES product_instances(id),
    FOREIGN KEY (customs_id) REFERENCES customs(id)
);
"""

class IRTradeExportScraper:
    def __init__(self, url: str = EXPORT_URL, headless: bool = True, db_path: str = DB_PATH):
        self.url = url
        self.headless = headless
        self.db_path = db_path
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.current_country_fa: Optional[str] = None
        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: Optional[sqlite3.Cursor] = None

    def _init_db(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()
        self.cursor.executescript(CREATE_TABLES)
        self.conn.commit()

    def _get_country_id(self, name_fa: str) -> Optional[int]:
        name = safe_str(name_fa)
        if not name: return None
        self.cursor.execute("SELECT id FROM countries WHERE name_fa = ?", (name,))
        row = self.cursor.fetchone()
        if row: return row[0]
        self.cursor.execute("INSERT INTO countries (name_fa) VALUES (?)", (name,))
        self.conn.commit()
        return self.cursor.lastrowid

    def _get_customs_id(self, name_fa: str) -> Optional[int]:
        name = safe_str(name_fa)
        if not name: return None
        self.cursor.execute("SELECT id FROM customs WHERE name_fa = ?", (name,))
        row = self.cursor.fetchone()
        if row: return row[0]
        self.cursor.execute("INSERT INTO customs (name_fa) VALUES (?)", (name,))
        self.conn.commit()
        return self.cursor.lastrowid

    def _get_hs_section_id(self, section: str, tariff: Optional[str], name_fa: str) -> Optional[int]:
        sec = safe_str(section)
        if not sec: return None
        tar = safe_str(tariff)
        self.cursor.execute("SELECT id FROM hs_sections WHERE section = ? AND tariff_code IS ?", (sec, tar))
        row = self.cursor.fetchone()
        if row: return row[0]
        
        display_name = safe_str(name_fa) or f"Section {sec}"
        self.cursor.execute("INSERT INTO hs_sections (section, tariff_code, name_fa) VALUES (?, ?, ?)", (sec, tar, display_name))
        self.conn.commit()
        return self.cursor.lastrowid

    def _get_product_instance_id(self, country_id: int, hs_section_id: int) -> Optional[int]:
        if country_id is None or hs_section_id is None: return None
        self.cursor.execute("SELECT id FROM product_instances WHERE country_id = ? AND hs_section_id = ?", (country_id, hs_section_id))
        row = self.cursor.fetchone()
        if row: return row[0]
        self.cursor.execute("INSERT INTO product_instances (country_id, hs_section_id) VALUES (?, ?)", (country_id, hs_section_id))
        self.conn.commit()
        return self.cursor.lastrowid

    def _insert_global(self, data: dict) -> None:
        periods = data.get("metadata", {}).get("periods", {})
        if periods:
            self.cursor.execute("INSERT OR REPLACE INTO metadata (key_name, header_value) VALUES ('start_month', ?)", (safe_str(periods.get("start", "First Month")),))
            self.cursor.execute("INSERT OR REPLACE INTO metadata (key_name, header_value) VALUES ('end_month', ?)", (safe_str(periods.get("end", "Last Month")),))
        
        headers = data.get("products_headers")
        if headers:
            for key, value in headers.items():
                self.cursor.execute("INSERT OR REPLACE INTO metadata (key_name, header_value) VALUES (?, ?)", (safe_str(key), safe_str(value)))

        df = data.get("countries")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = safe_str(row.get("country_fa"))
                if not name: continue
                self.cursor.execute(
                    """INSERT INTO countries (name_fa, rank, period1_value_musd, period2_value_musd, change_pct, export_value_musd, share_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(name_fa) DO UPDATE SET
                           rank=excluded.rank,
                           period1_value_musd=excluded.period1_value_musd,
                           period2_value_musd=excluded.period2_value_musd,
                           change_pct=excluded.change_pct,
                           export_value_musd=excluded.export_value_musd,
                           share_pct=excluded.share_pct""",
                    (name, safe_float(row.get("rank")), safe_float(row.get("period1_value_musd")), safe_float(row.get("period2_value_musd")),
                     safe_float(row.get("change_pct")), safe_float(row.get("export_value_musd")), safe_float(row.get("share_pct")))
                )

        df = data.get("customs")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = safe_str(row.get("customs_fa"))
                if not name: continue
                self.cursor.execute(
                    """INSERT INTO customs (name_fa, rank, period1_value_musd, period2_value_musd, change_pct, export_value_musd, share_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(name_fa) DO UPDATE SET
                           rank=excluded.rank,
                           period1_value_musd=excluded.period1_value_musd,
                           period2_value_musd=excluded.period2_value_musd,
                           change_pct=excluded.change_pct,
                           export_value_musd=excluded.export_value_musd,
                           share_pct=excluded.share_pct""",
                    (name, safe_float(row.get("rank")), safe_float(row.get("period1_value_musd")), safe_float(row.get("period2_value_musd")),
                     safe_float(row.get("change_pct")), safe_float(row.get("export_value_musd")), safe_float(row.get("share_pct")))
                )

        df = data.get("products")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                section = safe_str(row.get("hs_section"))
                if not section: continue
                hs_id = self._get_hs_section_id(section, row.get("hs_code"), row.get("product_fa"))
                if hs_id is None: continue
                self.cursor.execute(
                    """INSERT OR REPLACE INTO global_hs 
                       (hs_section_id, period1_value_musd, period2_value_musd, change_pct, total_value_musd, ratio_pct) 
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (hs_id, safe_float(row.get("period_1_value_musd")), safe_float(row.get("period_2_value_musd")),
                     safe_float(row.get("change_pct")), safe_float(row.get("total_value_musd")), safe_float(row.get("ratio_pct")))
                )

        for df, table in [(data.get("monthly_price"), "monthly_price"), (data.get("monthly_weight"), "monthly_weight")]:
            if df is not None and not df.empty:
                val_col = "value_musd" if table == "monthly_price" else "value_ton"
                for _, row in df.iterrows():
                    period = safe_str(row.get("period"))
                    val = safe_float(row.get(val_col))
                    if period and val is not None:
                        self.cursor.execute(f"INSERT INTO {table} (level, level_id, period, {val_col}) VALUES ('global', NULL, ?, ?)", (period, val))

        self.conn.commit()

    def _insert_country(self, country_fa: str, data: dict) -> None:
        country_id = self._get_country_id(country_fa)
        if country_id is None: return

        df = data.get("products")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                section = safe_str(row.get("hs_section"))
                if not section: continue
                hs_id = self._get_hs_section_id(section, row.get("hs_code"), row.get("product_fa"))
                if hs_id is None: continue
                self.cursor.execute(
                    """INSERT OR REPLACE INTO country_hs 
                       (country_id, hs_section_id, period1_value_musd, period2_value_musd, change_pct, total_value_musd, ratio_pct) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (country_id, hs_id, safe_float(row.get("period_1_value_musd")), safe_float(row.get("period_2_value_musd")),
                     safe_float(row.get("change_pct")), safe_float(row.get("total_value_musd")), safe_float(row.get("ratio_pct")))
                )

        df = data.get("customs")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                customs_name = safe_str(row.get("customs_fa"))
                if not customs_name: continue
                customs_id = self._get_customs_id(customs_name)
                if customs_id is None: continue
                self.cursor.execute(
                    """INSERT OR REPLACE INTO country_customs 
                       (country_id, customs_id, period1_value_musd, period2_value_musd, change_pct, export_value_musd, share_pct) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (country_id, customs_id, safe_float(row.get("period1_value_musd")), safe_float(row.get("period2_value_musd")), 
                     safe_float(row.get("change_pct")), safe_float(row.get("export_value_musd")), safe_float(row.get("share_pct")))
                )

        for df, table in [(data.get("monthly_price"), "monthly_price"), (data.get("monthly_weight"), "monthly_weight")]:
            if df is not None and not df.empty:
                val_col = "value_musd" if table == "monthly_price" else "value_ton"
                for _, row in df.iterrows():
                    period = safe_str(row.get("period"))
                    val = safe_float(row.get(val_col))
                    if period and val is not None:
                        self.cursor.execute(f"INSERT INTO {table} (level, level_id, period, {val_col}) VALUES ('country', ?, ?, ?)", (country_id, period, val))

        self.conn.commit()

    def _insert_product(self, country_fa: str, product_id: str, data: dict) -> None:
        parts = product_id.split("_", 1)
        section = parts[0]
        tariff = parts[1] if len(parts) > 1 else None

        country_id = self._get_country_id(country_fa)
        hs_id = self._get_hs_section_id(section, tariff, "")
        pi_id = self._get_product_instance_id(country_id, hs_id)
        if pi_id is None: return

        df = data.get("products")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                subgroup_section = safe_str(row.get("hs_section"))
                if not subgroup_section: continue
                self.cursor.execute(
                    """INSERT INTO product_subgroups 
                       (product_instance_id, subgroup_section, tariff_code, name_fa, period1_value_musd, period2_value_musd, change_pct, total_value_musd, ratio_pct) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pi_id, subgroup_section, safe_str(row.get("hs_code")), safe_str(row.get("product_fa")), safe_float(row.get("period_1_value_musd")),
                     safe_float(row.get("period_2_value_musd")), safe_float(row.get("change_pct")), safe_float(row.get("total_value_musd")), safe_float(row.get("ratio_pct")))
                )

        df = data.get("countries")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                dest_name = safe_str(row.get("country_fa"))
                if not dest_name: continue
                dest_id = self._get_country_id(dest_name)
                self.cursor.execute(
                    """INSERT OR REPLACE INTO product_country_share 
                       (product_instance_id, country_id, period1_value_musd, period2_value_musd, change_pct, export_value_musd, share_pct) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (pi_id, dest_id, safe_float(row.get("period1_value_musd")), safe_float(row.get("period2_value_musd")), 
                     safe_float(row.get("change_pct")), safe_float(row.get("export_value_musd")), safe_float(row.get("share_pct")))
                )

        df = data.get("customs")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                customs_name = safe_str(row.get("customs_fa"))
                if not customs_name: continue
                customs_id = self._get_customs_id(customs_name)
                self.cursor.execute(
                    """INSERT OR REPLACE INTO product_customs_share 
                       (product_instance_id, customs_id, period1_value_musd, period2_value_musd, change_pct, export_value_musd, share_pct) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (pi_id, customs_id, safe_float(row.get("period1_value_musd")), safe_float(row.get("period2_value_musd")), 
                     safe_float(row.get("change_pct")), safe_float(row.get("export_value_musd")), safe_float(row.get("share_pct")))
                )

        for df, table in [(data.get("monthly_price"), "monthly_price"), (data.get("monthly_weight"), "monthly_weight")]:
            if df is not None and not df.empty:
                val_col = "value_musd" if table == "monthly_price" else "value_ton"
                for _, row in df.iterrows():
                    period = safe_str(row.get("period"))
                    val = safe_float(row.get(val_col))
                    if period and val is not None:
                        self.cursor.execute(f"INSERT INTO {table} (level, level_id, period, {val_col}) VALUES ('product', ?, ?, ?)", (pi_id, period, val))

        self.conn.commit()

    async def start(self) -> None:
        logger.info("Starting Playwright...")
        self.playwright = await async_playwright().start()
        
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.context = await self.browser.new_context(
            locale="fa-IR", 
            viewport={"width": 1600, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36" 
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(PAGE_TIMEOUT)
        self.page.set_default_navigation_timeout(PAGE_TIMEOUT)
        self._init_db()
        await self.open_export_page()
        

    async def stop(self) -> None:
        logger.info("Stopping browser...")
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()
        if self.conn: self.conn.close()

    async def wait_for_export_data(self, page: Page) -> None:
        logger.info("Waiting for Blazor to render export data...")
        await page.wait_for_selector("body", state="attached", timeout=PAGE_TIMEOUT)
        for attempt in range(1, 15):
            count = await page.locator("#PriceByCountry_Table_Container .country-link").count()
            if count > 0:
                logger.info("Country data rendered: %s items", count)
                return
            await page.wait_for_timeout(2500)
            
        product_count = await page.locator(".product-link").count()
        if product_count > 0:
            logger.info("Product data rendered: %s items", product_count)
            return
        raise RuntimeError("IR-Trade export data did not render within timeout.")

    async def open_export_page(self) -> None:
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info("Opening export page (attempt %s/%s)...", attempt, MAX_RETRIES)
                await self.page.goto(self.url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                logger.info("URL: %s", self.page.url)
                logger.info("Title: %s", await self.page.title())
                
                await self.wait_for_export_data(self.page)
                logger.info("Export dashboard loaded successfully.")
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Export page loading failed on attempt %s: %s", attempt, exc)
                if attempt < MAX_RETRIES: await self.page.wait_for_timeout(5000)
        raise RuntimeError("Unable to load IR-Trade export page.") from last_error

    async def get_html(self) -> str:
        return await self.page.content()

    async def parse_current_page(self) -> dict:
        return parse_export_page(await self.get_html())

    async def collect_global_export(self) -> dict:
        data = await self.parse_current_page()
        if data["countries"].empty: raise RuntimeError("No export country data was found.")
        self._insert_global(data)
        return data

    async def find_country(self, country_fa: str):
        links = self.page.locator(".country-link")
        for i in range(await links.count()):
            link = links.nth(i)
            name = (await link.get_attribute("name") or "").strip()
            text = (await link.inner_text() or "").strip()
            if name == country_fa or text == country_fa: return link
        return None

    async def open_country(self, country_fa: str) -> str:
        link = await self.find_country(country_fa)
        if link is None: raise ValueError(f"Country not found: {country_fa}")
        await link.scroll_into_view_if_needed()
        await link.click()
        await self.page.wait_for_timeout(3000)
        await self.wait_for_export_data(self.page)
        self.current_country_fa = country_fa
        return await self.get_html()

    async def return_to_country(self) -> None:
        await self.page.go_back()
        await self.page.wait_for_timeout(2000)
        await self.wait_for_export_data(self.page)

    async def return_to_global(self) -> None:
        await self.page.goto(self.url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await self.page.wait_for_timeout(BLazor_WAIT_SECONDS * 1000)
        await self.wait_for_export_data(self.page)

    async def collect_country(self, country_fa: str, collect_products: bool = False) -> dict:
        html = await self.open_country(country_fa)
        data = parse_export_page(html)
        self._insert_country(country_fa, data)
        if collect_products: await self.collect_country_products(country_fa)
        return data

    async def collect_country_products(self, country_fa: str) -> None:
        data = parse_export_page(await self.get_html())
        products_df = data.get("products")
        if products_df is None or products_df.empty: return
        products_df = products_df[products_df["total_value_musd"].notna() & (products_df["total_value_musd"] > 0)]
        for _, row in products_df.iterrows():
            section = safe_str(row.get("hs_section"))
            if not section: continue
            tariff = safe_str(row.get("hs_code"))
            product_id = f"{section}_{tariff}" if tariff else section
            link = await self.find_product_link(section, tariff)
            if link is None: continue
            await link.scroll_into_view_if_needed()
            await link.click()
            await self.page.wait_for_timeout(3000)
            await self.wait_for_export_data(self.page)
            self._insert_product(country_fa, product_id, parse_export_page(await self.get_html()))
            await self.return_to_country()

    async def find_product_link(self, section: Optional[str], tariff: Optional[str]):
        selector = ".product-link"
        if section: selector += f'[section="{section}"]'
        if tariff: selector += f'[tariff="{tariff}"]'
        link = self.page.locator(selector).first
        return link if await link.count() > 0 else None

    @staticmethod
    def get_major_countries(countries: pd.DataFrame, threshold: float = MAJOR_COUNTRY_THRESHOLD) -> pd.DataFrame:
        if countries.empty: return countries.copy()
        result = countries[countries["share_pct"] >= threshold].copy()
        return result.sort_values("share_pct", ascending=False).reset_index(drop=True)

    async def collect_major_countries(self, countries: pd.DataFrame, threshold: float = MAJOR_COUNTRY_THRESHOLD, collect_products: bool = False) -> dict:
        major = self.get_major_countries(countries, threshold)
        results = {}
        for index, row in major.iterrows():
            country = row["country_fa"]
            logger.info("Scraping country %s/%s: %s", index + 1, len(major), country)
            try: 
                results[country] = await self.collect_country(country, collect_products=collect_products)
            except Exception as e: 
                logger.exception("Failed scraping %s: %s", country, e)
            try: 
                await self.return_to_global()
            except Exception as e: 
                logger.exception("Failed returning to global: %s", e)
        return results

async def main():
    ensure_directories()
    scraper = IRTradeExportScraper(url=EXPORT_URL, headless=True)
    try:
        await scraper.start()
        data = await scraper.collect_global_export()
        await scraper.collect_major_countries(data["countries"], threshold=MAJOR_COUNTRY_THRESHOLD, collect_products=True)
        logger.info("Scraping completed successfully.")
    finally:
        await scraper.stop()

if __name__ == "__main__":
    asyncio.run(main())
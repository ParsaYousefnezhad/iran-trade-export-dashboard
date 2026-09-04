# ============================================================
# collector/parser.py
# ============================================================

from __future__ import annotations
import re
from typing import Optional
import pandas as pd
from bs4 import BeautifulSoup

# ============================================================
# Persian / Arabic number conversion
# ============================================================

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ENGLISH_DIGITS = "0123456789"

DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS,
    ENGLISH_DIGITS + ENGLISH_DIGITS,
)

# ============================================================
# Text utilities
# ============================================================

def normalize_text(value: str | None) -> str:
    """Normalize Persian/Arabic text and strip hidden unicode characters."""
    if value is None:
        return ""
    value = str(value)
    value = value.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    value = value.translate(DIGIT_TRANSLATION)
    
    # Remove hidden directional formatting characters like \u202b (RTE), \u202c (PDF), \u200e (LRM), \u200f (RLM)
    value = re.sub(r'[\u202a-\u202e\u200e\u200f]', '', value)
    
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def parse_number(value: str | None) -> Optional[float]:
    """Convert Persian/Arabic formatted numbers into float."""
    if value is None:
        return None
    value = normalize_text(value)
    if not value:
        return None
    value = value.replace("%", "").replace("٫", ".").replace("٬", "").replace("،", "").replace(",", "").replace(" ", "")
    value = re.sub(r"[^0-9.\-+]", "", value)
    if value in {"", "-", "+", ".", "-.", "+."}:
        return None
    try:
        return float(value)
    except ValueError:
        return None

def direct_texts(element) -> list[str]:
    """Return direct child text values."""
    result = []
    for child in element.find_all(recursive=False):
        text = normalize_text(child.get_text(" ", strip=True))
        if text:
            result.append(text)
    return result

# ============================================================
# Parsers
# ============================================================

def parse_export_country_table(html: str) -> pd.DataFrame:
    """Parse the destination-country export table extracting all 6 columns."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#PriceByCountry_Table_Container")
    if container is None:
        return pd.DataFrame(columns=["rank", "country_fa", "period1_value_musd", "period2_value_musd", "change_pct", "export_value_musd", "share_pct"])

    rows = []
    country_rows = container.select(".anlyze-tile-desc-country-item")
    
    for row in country_rows:
        country_element = row.select_one(".country-link")
        if country_element is None:
            continue
            
        country_name = normalize_text(country_element.get("name") or country_element.get_text(" ", strip=True))
        children = direct_texts(row)
        
        # Find all numerical values in row
        numeric_values = [parse_number(text) for text in children if parse_number(text) is not None]
        
        # Map values counting backwards from the end to ensure accuracy
        rank = numeric_values[0] if len(numeric_values) > 0 else None
        share = numeric_values[-1] if len(numeric_values) > 1 else None
        export_value = numeric_values[-2] if len(numeric_values) > 2 else None
        change_pct = numeric_values[-3] if len(numeric_values) > 3 else None
        period2 = numeric_values[-4] if len(numeric_values) > 4 else None
        period1 = numeric_values[-5] if len(numeric_values) > 5 else None

        rows.append({
            "rank": int(rank) if rank is not None else None,
            "country_fa": country_name,
            "period1_value_musd": period1,
            "period2_value_musd": period2,
            "change_pct": change_pct,
            "export_value_musd": export_value,
            "share_pct": share,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
        df = df.drop_duplicates(subset=["country_fa"], keep="first")
        df = df.sort_values(by="rank", ascending=True).reset_index(drop=True)
    return df

def parse_export_product_table(html: str) -> dict:
    """Parse HS section / product group data AND dynamic column headers."""
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Extract Headers
    # Provide fallbacks in case the selectors fail to find the elements
    headers = {
        "period1": "Period 1",
        "period2": "Period 2",
        "total": "Total",
        "ratio": "Share"
    }
    
    # Try finding the specific column header classes
    col2 = soup.select_one(".a-t-h-2")
    col3 = soup.select_one(".a-t-h-3")
    col5 = soup.select_one(".a-t-h-5")
    col6 = soup.select_one(".a-t-h-6")
    
    if col2: headers["period1"] = normalize_text(col2.get_text(strip=True))
    if col3: headers["period2"] = normalize_text(col3.get_text(strip=True))
    if col5: headers["total"] = normalize_text(col5.get_text(strip=True))
    if col6: headers["ratio"] = normalize_text(col6.get_text(strip=True))

    # 2. Extract Data
    rows = []
    product_links = soup.select(".product-link")
    for product in product_links:
        section = normalize_text(product.get("section"))
        tariff = normalize_text(product.get("tariff"))
        tariff_title = normalize_text(product.get("tarifftitle"))
        product_text = normalize_text(product.get_text(" ", strip=True))

        if not section and not tariff_title:
            continue

        parent = product.find_parent(class_="analyze-tile-desc")
        if parent is None:
            parent = product.parent

        value_1 = parent.select_one(".a-t-h-2")
        value_2 = parent.select_one(".a-t-h-3")
        value_3 = parent.select_one(".a-t-h-4")
        value_4 = parent.select_one(".a-t-h-5")
        value_5 = parent.select_one(".a-t-h-6")

        p1_val = parse_number(value_1.get_text(" ", strip=True)) if value_1 else None
        p2_val = parse_number(value_2.get_text(" ", strip=True)) if value_2 else None
        
        # Calculate change_pct manually if it's missing but p1 and p2 exist
        change_val = parse_number(value_3.get_text(" ", strip=True)) if value_3 else None
        if change_val is None and p1_val is not None and p2_val is not None:
            if p1_val != 0:
                change_val = round(((p2_val - p1_val) / p1_val) * 100, 2)
            else:
                change_val = 100.0 if p2_val > 0 else 0.0

        rows.append({
            "hs_section": section,
            "hs_code": tariff,
            "product_fa": tariff_title or product_text,
            "period_1_value_musd": p1_val,
            "period_2_value_musd": p2_val,
            "change_pct": change_val,
            "total_value_musd": parse_number(value_4.get_text(" ", strip=True)) if value_4 else None,
            "ratio_pct": parse_number(value_5.get_text(" ", strip=True)) if value_5 else None,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["hs_section", "hs_code", "product_fa"], keep="first")
        df["hs_section_sort"] = pd.to_numeric(df["hs_section"], errors="coerce")
        df = df.sort_values(by="hs_section_sort", na_position="last").drop(columns=["hs_section_sort"]).reset_index(drop=True)
        
    return {"headers": headers, "data": df}

def parse_customs_table(html: str) -> pd.DataFrame:
    """Parse customs contribution table extracting all 6 columns."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#PriceByCustoms_Table_Container")
    if not container:
        return pd.DataFrame(columns=["rank", "customs_fa", "period1_value_musd", "period2_value_musd", "change_pct", "export_value_musd", "share_pct"])
    
    rows = container.select(".anlyze-tile-desc-country-item")
    data = []
    for row in rows:
        if row.select_one(".c-tile-desc-Lastitem") or "مجموع" in row.get_text():
            continue
            
        customs_link = row.select_one(".customs-link")
        customs_name = normalize_text(customs_link.get("name") or customs_link.get_text(strip=True)) if customs_link else None
        
        children = direct_texts(row)
        numeric_values = [parse_number(text) for text in children if parse_number(text) is not None]
        
        rank = numeric_values[0] if len(numeric_values) > 0 else None
        share = numeric_values[-1] if len(numeric_values) > 1 else None
        export_value = numeric_values[-2] if len(numeric_values) > 2 else None
        change_pct = numeric_values[-3] if len(numeric_values) > 3 else None
        period2 = numeric_values[-4] if len(numeric_values) > 4 else None
        period1 = numeric_values[-5] if len(numeric_values) > 5 else None
        
        data.append({
            "rank": rank,
            "customs_fa": customs_name,
            "period1_value_musd": period1,
            "period2_value_musd": period2,
            "change_pct": change_pct,
            "export_value_musd": export_value,
            "share_pct": share,
        })
        
    df = pd.DataFrame(data)
    if not df.empty:
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
        df = df.drop_duplicates(subset=["customs_fa"], keep="first")
        df = df.sort_values("rank").reset_index(drop=True)
    return df

def parse_monthly_price_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#PriceByMonth_Table_Container")
    if not container: return pd.DataFrame(columns=["period", "value_musd"])
    rows = container.select(".c-chart-desc-item, .c-chart-desc-Lastitem")
    data = []
    for row in rows:
        cols = row.select(".col-7, .col-5")
        if len(cols) >= 2:
            period = normalize_text(cols[0].get_text(strip=True))
            value = parse_number(cols[1].get_text(strip=True))
            data.append({"period": period, "value_musd": value})
    return pd.DataFrame(data)

def parse_monthly_weight_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#WeightByMonth_Table_Container")
    if not container: return pd.DataFrame(columns=["period", "value_ton"])
    rows = container.select(".c-chart-desc-item, .c-chart-desc-Lastitem")
    data = []
    for row in rows:
        cols = row.select(".col-7, .col-5")
        if len(cols) >= 2:
            period = normalize_text(cols[0].get_text(strip=True))
            value = parse_number(cols[1].get_text(strip=True))
            data.append({"period": period, "value_ton": value})
    return pd.DataFrame(data)

def parse_page_metadata(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("#PageHeadTitle")
    title_text = normalize_text(title.get_text(" ", strip=True)) if title else ""
    page_title = soup.title
    browser_title = normalize_text(page_title.get_text(" ", strip=True)) if page_title else ""
    return {
        "page_title": title_text,
        "browser_title": browser_title,
        "trade_type": "export",
    }

def extract_periods_from_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("#PageHeadTitle")
    if not title: return {"start": "First Month", "end": "Last Month"}
    text = normalize_text(title.get_text(" ", strip=True))
    parts = text.split("-")
    if len(parts) > 0:
        date_part = parts[-1].strip()
        if " تا " in date_part:
            start, end = date_part.split(" تا ")
            return {"start": start.strip(), "end": end.strip()}
    return {"start": "First Month", "end": "Last Month"}

def parse_export_page(html: str) -> dict:
    metadata = parse_page_metadata(html)
    metadata["periods"] = extract_periods_from_page(html)
    products_dict = parse_export_product_table(html)

    return {
        "metadata": metadata,
        "products_headers": products_dict["headers"],
        "countries": parse_export_country_table(html),
        "products": products_dict["data"],
        "monthly_price": parse_monthly_price_table(html),
        "monthly_weight": parse_monthly_weight_table(html),
        "customs": parse_customs_table(html),
    }
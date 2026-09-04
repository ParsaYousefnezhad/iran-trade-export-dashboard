# ============================================================
# app.py – Iran Export Dashboard (Professional Redesign)
# ============================================================

import sqlite3
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------- Page Configuration ----------
st.set_page_config(
    page_title="Iran Export Analytics Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- Theme & Styling Helpers ----------
COLOR_PRIMARY = "#1f77b4"
COLOR_PALETTE = px.colors.qualitative.Prism
CHART_BG = "rgba(0,0,0,0)"

def apply_chart_theme(fig: go.Figure, height: int = 420) -> go.Figure:
    """Applies a consistent, polished layout across all Plotly figures."""
    fig.update_layout(
        height=height,
        paper_bgcolor=CHART_BG,
        plot_bgcolor=CHART_BG,
        margin=dict(l=15, r=20, t=40, b=20),
        font=dict(family="Tahoma, Arial, sans-serif", size=12),
        hoverlabel=dict(bgcolor="white", font_size=12),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    return fig

# ---------- Database Connection ----------
DB_PATH = Path("exports.db")

@st.cache_resource
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

conn = get_connection()

# ---------- Data Loading Queries ----------
@st.cache_data
def get_table_headers():
    """Fetches dynamic column headers saved during scraping."""
    try:
        df = pd.read_sql("SELECT key_name, header_value FROM metadata", conn)
        meta = dict(zip(df.key_name, df.header_value))
        return {
            "period1": meta.get("period1", "Period 1"),
            "period2": meta.get("period2", "Period 2"),
            "total": meta.get("total", "Total"),
            "ratio": meta.get("ratio", "Share"),
            "start_month": meta.get("start_month", "First Month"),
            "end_month": meta.get("end_month", "Last Month")
        }
    except Exception:
        # Fallback if table doesn't exist yet
        return {
            "period1": "Period 1",
            "period2": "Period 2",
            "total": "Total",
            "ratio": "Share",
            "start_month": "First Month",
            "end_month": "Last Month"
        }

@st.cache_data
def load_global_countries():
    return pd.read_sql("""
        SELECT rank, name_fa, export_value_musd, share_pct 
        FROM countries 
        WHERE export_value_musd > 0 
        ORDER BY export_value_musd DESC
    """, conn)

@st.cache_data
def load_global_hs():
    return pd.read_sql("""
        SELECT h.section, h.tariff_code, h.name_fa,
               g.period1_value_musd, g.period2_value_musd,
               g.change_pct, g.total_value_musd, g.ratio_pct
        FROM global_hs g
        JOIN hs_sections h ON g.hs_section_id = h.id
        WHERE g.total_value_musd > 0
        ORDER BY g.total_value_musd DESC
    """, conn)

@st.cache_data
def load_global_monthly_price():
    return pd.read_sql("SELECT period, value_musd FROM monthly_price WHERE level='global' ORDER BY id ASC", conn)

@st.cache_data
def load_global_monthly_weight():
    return pd.read_sql("SELECT period, value_ton FROM monthly_weight WHERE level='global' ORDER BY id ASC", conn)

@st.cache_data
def load_country_list():
    return pd.read_sql("""
        SELECT DISTINCT c.name_fa 
        FROM countries c
        JOIN country_hs ch ON c.id = ch.country_id
        ORDER BY c.export_value_musd DESC
    """, conn)["name_fa"].tolist()

@st.cache_data
def load_country_data(country_fa):
    cur = conn.cursor()
    cur.execute("SELECT id FROM countries WHERE name_fa = ?", (country_fa,))
    row = cur.fetchone()
    if not row:
        return None
    country_id = row[0]

    hs = pd.read_sql("""
        SELECT h.section, h.tariff_code, h.name_fa,
               c.period1_value_musd, c.period2_value_musd,
               c.change_pct, c.total_value_musd, c.ratio_pct
        FROM country_hs c
        JOIN hs_sections h ON c.hs_section_id = h.id
        WHERE c.country_id = ? AND c.total_value_musd > 0
        ORDER BY c.total_value_musd DESC
    """, conn, params=(country_id,))

    customs = pd.read_sql("""
        SELECT cu.name_fa, cc.export_value_musd, cc.share_pct
        FROM country_customs cc
        JOIN customs cu ON cc.customs_id = cu.id
        WHERE cc.country_id = ? AND cc.export_value_musd > 0
        ORDER BY cc.export_value_musd DESC
    """, conn, params=(country_id,))

    price = pd.read_sql("""
        SELECT period, value_musd
        FROM monthly_price
        WHERE level='country' AND level_id = ?
        ORDER BY id ASC
    """, conn, params=(country_id,))

    weight = pd.read_sql("""
        SELECT period, value_ton
        FROM monthly_weight
        WHERE level='country' AND level_id = ?
        ORDER BY id ASC
    """, conn, params=(country_id,))

    products = pd.read_sql("""
        SELECT pi.id, h.section, h.tariff_code, h.name_fa,
               COALESCE(ch.total_value_musd, 0) AS total_value_musd
        FROM product_instances pi
        JOIN hs_sections h ON pi.hs_section_id = h.id
        LEFT JOIN country_hs ch ON ch.country_id = pi.country_id AND ch.hs_section_id = pi.hs_section_id
        WHERE pi.country_id = ?
        ORDER BY total_value_musd DESC
    """, conn, params=(country_id,))

    return {"hs": hs, "customs": customs, "price": price, "weight": weight, "products": products}

@st.cache_data
def load_product_data(product_instance_id):
    subgroups = pd.read_sql("""
        SELECT subgroup_section, tariff_code, name_fa,
               period1_value_musd, period2_value_musd,
               change_pct, total_value_musd, ratio_pct
        FROM product_subgroups
        WHERE product_instance_id = ? AND total_value_musd > 0
        ORDER BY total_value_musd DESC
    """, conn, params=(product_instance_id,))

    country_share = pd.read_sql("""
        SELECT c.name_fa, pcs.export_value_musd, pcs.share_pct
        FROM product_country_share pcs
        JOIN countries c ON pcs.country_id = c.id
        WHERE pcs.product_instance_id = ? AND pcs.export_value_musd > 0
        ORDER BY pcs.export_value_musd DESC
    """, conn, params=(product_instance_id,))

    customs_share = pd.read_sql("""
        SELECT cu.name_fa, pcs.export_value_musd, pcs.share_pct
        FROM product_customs_share pcs
        JOIN customs cu ON pcs.customs_id = cu.id
        WHERE pcs.product_instance_id = ? AND pcs.export_value_musd > 0
        ORDER BY pcs.export_value_musd DESC
    """, conn, params=(product_instance_id,))

    price = pd.read_sql("""
        SELECT period, value_musd
        FROM monthly_price
        WHERE level='product' AND level_id = ?
        ORDER BY id ASC
    """, conn, params=(product_instance_id,))

    weight = pd.read_sql("""
        SELECT period, value_ton
        FROM monthly_weight
        WHERE level='product' AND level_id = ?
        ORDER BY id ASC
    """, conn, params=(product_instance_id,))

    return {
        "subgroups": subgroups,
        "country_share": country_share,
        "customs_share": customs_share,
        "price": price,
        "weight": weight,
    }

# ---------- Chart Builders ----------

def build_horizontal_bar(df: pd.DataFrame, y_col: str, x_col: str, title: str, top_n: int = 10, unit: str = "M USD", height: int = None) -> go.Figure:
    """Generates an inverted horizontal bar chart so top items sit comfortably on top with full labels."""
    subset = df.head(top_n).iloc[::-1]  # Invert order for Plotly y-axis
    fig = go.Figure(
        go.Bar(
            x=subset[x_col],
            y=subset[y_col],
            orientation="h",
            text=[f"{val:,.1f} {unit}" if pd.notna(val) else "" for val in subset[x_col]],
            textposition="auto",
            marker=dict(
                color=subset[x_col],
                colorscale="Blues",
                line=dict(color="rgba(0,0,0,0.2)", width=1),
            ),
            hovertemplate="<b>%{y}</b><br>Value: %{x:,.2f} " + unit + "<extra></extra>",
        )
    )
    fig.update_layout(
        title=f"<b>{title}</b>",
        xaxis_title=unit,
        yaxis_title="",
        margin=dict(l=180, r=20, t=40, b=20),
    )
    
    final_height = height if height else max(360, top_n * 32)
    return apply_chart_theme(fig, height=final_height)

def build_area_trend(df: pd.DataFrame, x_col: str, y_col: str, title: str, fill_color: str = "rgba(31, 119, 180, 0.2)", line_color: str = "#1f77b4", unit: str = "M USD") -> go.Figure:
    """Generates a smoothed trend area chart."""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df[x_col],
            y=df[y_col],
            mode="lines+markers",
            line=dict(color=line_color, width=2.5, shape="spline"),
            marker=dict(size=6, color=line_color),
            fill="tozeroy",
            fillcolor=fill_color,
            hovertemplate="<b>Period:</b> %{x}<br><b>Value:</b> %{y:,.2f} " + unit + "<extra></extra>",
        )
    )
    fig.update_layout(title=f"<b>{title}</b>", yaxis_title=unit)
    fig.update_xaxes(tickangle=45)
    return apply_chart_theme(fig, height=360)

def build_donut_chart(df: pd.DataFrame, labels_col: str, values_col: str, title: str, top_n: int = 7) -> go.Figure:
    """Groups smaller tails into 'Other' to avoid crowded pie slices."""
    if len(df) > top_n:
        top_slice = df.iloc[:top_n][[labels_col, values_col]].copy()
        other_val = df.iloc[top_n:][values_col].sum()
        other_row = pd.DataFrame([{labels_col: "سایر مقاصد / کالاها (Other)", values_col: other_val}])
        plot_df = pd.concat([top_slice, other_row], ignore_index=True)
    else:
        plot_df = df

    fig = go.Figure(
        data=[
            go.Pie(
                labels=plot_df[labels_col],
                values=plot_df[values_col],
                hole=0.45,
                textinfo="percent",
                hoverinfo="label+value+percent",
                marker=dict(colors=COLOR_PALETTE),
            )
        ]
    )
    
    fig.update_layout(
        title=f"<b>{title}</b>",
        legend=dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="left",
            x=1.05
        )
    )
    return apply_chart_theme(fig, height=380)

# ---------- Pages ----------

def home_page():
    st.markdown("## 🌐 Global Export Overview")

    headers = get_table_headers()
    countries = load_global_countries()
    hs = load_global_hs()
    price = load_global_monthly_price()
    weight = load_global_monthly_weight()

    if countries.empty:
        st.warning("No export data loaded in the database.")
        return

    # Top KPI Metrics Row
    total_export = countries["export_value_musd"].sum()
    top_destination = countries.iloc[0]["name_fa"] if not countries.empty else "N/A"
    top_dest_pct = countries.iloc[0]["share_pct"] if not countries.empty else 0.0

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Total Export Value", f"${total_export:,.1f} M")
    kpi2.metric("Target Markets", f"{len(countries)} Countries")
    kpi3.metric("HS Sections", f"{len(hs)} Categories")
    kpi4.metric("Top Market", f"{top_destination}", f"{top_dest_pct:.1f}% Share")

    st.markdown("---")

    # Tabbed Views for Clean Information Hierarchy
    tab_geo, tab_products, tab_trends, tab_data = st.tabs([
        "🌍 Geographic Distribution",
        "📦 Products & Sectors",
        "📈 Monthly Time-Series",
        "📋 Master Data Tables",
    ])

    with tab_geo:
        col_left, col_right = st.columns([3, 2])
        with col_left:
            st.plotly_chart(
                build_horizontal_bar(countries, y_col="name_fa", x_col="export_value_musd", title="Top 15 Destination Countries by Export Value", top_n=15),
                use_container_width=True,
            )
        with col_right:
            st.plotly_chart(
                build_donut_chart(countries, labels_col="name_fa", values_col="export_value_musd", title="Country Share Breakdown (Top 7 + Others)"),
                use_container_width=True,
            )

        st.markdown("#### Global Export Market Composition")
        fig_tree = px.treemap(
            countries,
            path=["name_fa"],
            values="export_value_musd",
            color="share_pct",
            color_continuous_scale="Blues",
        )
        fig_tree.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=400)
        st.plotly_chart(apply_chart_theme(fig_tree, height=400), use_container_width=True)

    with tab_products:
        st.plotly_chart(
            build_horizontal_bar(hs, y_col="name_fa", x_col="total_value_musd", title="Top 12 HS Product Sections by Total Value", top_n=12),
            use_container_width=True,
        )
        
        st.markdown("##### Global HS Sections Details")
        st.dataframe(
            hs[["name_fa", "period1_value_musd", "period2_value_musd", "total_value_musd", "change_pct", "ratio_pct"]],
            column_config={
                "name_fa": "Section Name",
                "period1_value_musd": st.column_config.NumberColumn(f"{headers['start_month']}", format="$%.2f M"),
                "period2_value_musd": st.column_config.NumberColumn(f"{headers['end_month']}", format="$%.2f M"),
                "total_value_musd": st.column_config.NumberColumn(f"{headers['total']}", format="$%.2f M"),
                "change_pct": st.column_config.NumberColumn("Change %", format="%.1f%%"),
                "ratio_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(hs["ratio_pct"].max() or 100), format="%.2f%%"),
            },
            hide_index=True,
            use_container_width=True,
        )

    with tab_trends:
        c1, c2 = st.columns(2)
        with c1:
            if not price.empty:
                st.plotly_chart(
                    build_area_trend(price, x_col="period", y_col="value_musd", title="Monthly Export Value Trend", fill_color="rgba(31, 119, 180, 0.15)", line_color="#1f77b4", unit="M USD"),
                    use_container_width=True,
                )
        with c2:
            if not weight.empty:
                st.plotly_chart(
                    build_area_trend(weight, x_col="period", y_col="value_ton", title="Monthly Export Weight Trend", fill_color="rgba(44, 160, 44, 0.15)", line_color="#2ca02c", unit="Tons"),
                    use_container_width=True,
                )

    with tab_data:
        st.subheader("Countries Summary")
        st.dataframe(
            countries[["rank", "name_fa", "export_value_musd", "share_pct"]],
            column_config={
                "rank": st.column_config.NumberColumn("Rank", format="%d"),
                "name_fa": "Destination Country",
                "export_value_musd": st.column_config.NumberColumn(f"{headers['total']} (M USD)", format="$%.2f M"),
                "share_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(countries["share_pct"].max() or 100), format="%.2f%%"),
            },
            hide_index=True,
            use_container_width=True,
        )


def country_page():
    st.markdown("## 🏷️ Country Deep-Dive Analysis")

    headers = get_table_headers()
    countries = load_country_list()
    
    if not countries:
        st.warning("⚠️ No country deep-dive data has been collected yet. Please run the scraper to populate country details.")
        return

    selected_country = st.selectbox("Select Target Country:", countries)

    if not selected_country:
        return

    data = load_country_data(selected_country)
    if not data:
        st.error("No detailed data for selected country.")
        return

    hs = data["hs"]
    customs = data["customs"]
    price = data["price"]
    weight = data["weight"]
    products = data["products"]

    # Country Header Stats
    total_val = hs["total_value_musd"].sum() if not hs.empty else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Total Exports to {selected_country}", f"${total_val:,.2f} M")
    c2.metric("HS Sections Traded", len(hs))
    c3.metric("Active Customs Outlets", len(customs))

    st.markdown("---")

    tab_hs, tab_customs, tab_monthly, tab_drilldown = st.tabs([
        "📦 HS Product Sections",
        "🏛️ Customs Outlets",
        "📊 Monthly Trend",
        "🔍 Product Drilldown",
    ])

    with tab_hs:
        if not hs.empty:
            st.markdown("##### HS Section Details")
            st.dataframe(
                hs[["name_fa", "period1_value_musd", "period2_value_musd", "total_value_musd", "change_pct", "ratio_pct"]],
                column_config={
                    "name_fa": "Section Name",
                    "period1_value_musd": st.column_config.NumberColumn(f"{headers['start_month']}", format="$%.2f M"),
                    "period2_value_musd": st.column_config.NumberColumn(f"{headers['end_month']}", format="$%.2f M"),
                    "total_value_musd": st.column_config.NumberColumn(f"{headers['total']}", format="$%.2f M"),
                    "change_pct": st.column_config.NumberColumn("Change %", format="%.1f%%"),
                    "ratio_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(hs["ratio_pct"].max() or 100), format="%.1f%%"),
                },
                hide_index=True,
                use_container_width=True,
            )

    with tab_customs:
        if not customs.empty:
            col_c_chart, col_c_tbl = st.columns([3, 2])
            with col_c_chart:
                st.plotly_chart(
                    build_horizontal_bar(customs, y_col="name_fa", x_col="export_value_musd", title=f"Top Customs Offices for {selected_country}", top_n=10, height=510),
                    use_container_width=True,
                )
            with col_c_tbl:
                st.markdown("##### Customs Share")
                st.dataframe(
                    customs[["name_fa", "export_value_musd", "share_pct"]],
                    column_config={
                        "name_fa": "Customs Port",
                        "export_value_musd": st.column_config.NumberColumn(f"{headers['total']} (M USD)", format="$%.2f M"),
                        "share_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(customs["share_pct"].max() or 100), format="%.2f%%"),
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=450, 
                )

    with tab_monthly:
        mc1, mc2 = st.columns(2)
        with mc1:
            if not price.empty:
                st.plotly_chart(
                    build_area_trend(price, x_col="period", y_col="value_musd", title=f"Export Value Trend ({selected_country})", fill_color="rgba(31, 119, 180, 0.15)", line_color="#1f77b4", unit="M USD"),
                    use_container_width=True,
                )
        with mc2:
            if not weight.empty:
                st.plotly_chart(
                    build_area_trend(weight, x_col="period", y_col="value_ton", title=f"Export Weight Trend ({selected_country})", fill_color="rgba(255, 127, 14, 0.15)", line_color="#ff7f0e", unit="Tons"),
                    use_container_width=True,
                )

    with tab_drilldown:
        if products.empty:
            st.info("No detailed subgroup products available for drill-down.")
        else:
            product_dict = {f"{row['section']} - {row['name_fa']}": row["id"] for _, row in products.iterrows()}
            selected_item = st.selectbox("Choose Product to Inspect:", list(product_dict.keys()))
            instance_id = product_dict[selected_item]

            if instance_id:
                p_data = load_product_data(instance_id)

                if not p_data["subgroups"].empty:
                    st.plotly_chart(
                        build_horizontal_bar(p_data["subgroups"], y_col="name_fa", x_col="total_value_musd", title=f"Detailed Subgroups for {selected_item}", top_n=10),
                        use_container_width=True,
                    )
                    
                    st.dataframe(
                        p_data["subgroups"][["tariff_code", "name_fa", "period1_value_musd", "period2_value_musd", "total_value_musd", "change_pct", "ratio_pct"]],
                        column_config={
                            "tariff_code": "HS Code",
                            "name_fa": "Subgroup Name",
                            "period1_value_musd": st.column_config.NumberColumn(f"{headers['start_month']}", format="$%.2f M"),
                            "period2_value_musd": st.column_config.NumberColumn(f"{headers['end_month']}", format="$%.2f M"),
                            "total_value_musd": st.column_config.NumberColumn(f"{headers['total']}", format="$%.2f M"),
                            "change_pct": st.column_config.NumberColumn("Change %", format="%.1f%%"),
                            "ratio_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(p_data["subgroups"]["ratio_pct"].max() or 100), format="%.1f%%"),
                        },
                        hide_index=True,
                        use_container_width=True,
                    )

                p1, p2 = st.columns(2)
                with p1:
                    if not p_data["country_share"].empty:
                        st.plotly_chart(
                            build_horizontal_bar(p_data["country_share"], y_col="name_fa", x_col="export_value_musd", title="Destination Market Share", top_n=6),
                            use_container_width=True,
                        )
                        st.dataframe(
                            p_data["country_share"][["name_fa", "export_value_musd", "share_pct"]],
                            column_config={
                                "name_fa": "Country",
                                "export_value_musd": st.column_config.NumberColumn(f"{headers['total']}", format="$%.2f M"),
                                "share_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(p_data["country_share"]["share_pct"].max() or 100), format="%.2f%%"),
                            },
                            hide_index=True,
                            use_container_width=True,
                        )
                with p2:
                    if not p_data["customs_share"].empty:
                        st.plotly_chart(
                            build_horizontal_bar(p_data["customs_share"], y_col="name_fa", x_col="export_value_musd", title="Dispatch Customs Share", top_n=6),
                            use_container_width=True,
                        )
                        st.dataframe(
                            p_data["customs_share"][["name_fa", "export_value_musd", "share_pct"]],
                            column_config={
                                "name_fa": "Customs",
                                "export_value_musd": st.column_config.NumberColumn(f"{headers['total']}", format="$%.2f M"),
                                "share_pct": st.column_config.ProgressColumn(f"{headers['ratio']} (%)", min_value=0, max_value=float(p_data["customs_share"]["share_pct"].max() or 100), format="%.2f%%"),
                            },
                            hide_index=True,
                            use_container_width=True,
                        )

# ---------- Navigation ----------
st.sidebar.title("Iran Trade Analytics")
page = st.sidebar.radio("Navigation Menu", ["Global Overview", "Country Deep-Dive"])

if page == "Global Overview":
    home_page()
else:
    country_page()
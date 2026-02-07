import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

from database import get_connection


@st.cache_data(ttl=300)
def get_mekko_chart_data(_conn_id, fund_id, reporting_date=None):
    """Lädt Portfolio-Daten für Mekko Chart - GECACHED."""
    with get_connection() as conn:
        if reporting_date:
            query = """
            SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
            FROM portfolio_companies_history
            WHERE fund_id = %s AND reporting_date = %s
            ORDER BY (realized_tvpi + unrealized_tvpi) DESC
            """
            return pd.read_sql_query(query, conn, params=(fund_id, reporting_date))
        else:
            query = """
            SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
            FROM portfolio_companies
            WHERE fund_id = %s
            ORDER BY (realized_tvpi + unrealized_tvpi) DESC
            """
            return pd.read_sql_query(query, conn, params=(fund_id,))


def tvpi_formatter(v, p):
    """Formatter für TVPI Y-Achse"""
    return f"{v:.2f}x"

def wrap_label(text, max_chars=12, max_lines=2, base_fontsize=11):
    """Bricht Text für Chart-Labels um"""
    words = text.split()
    lines = []
    current = ""
    for w in words:
        if len(current + " " + w) <= max_chars:
            current = (current + " " + w).strip()
        else:
            lines.append(current)
            current = w
            if len(lines) == max_lines - 1:
                break
    lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    full_len = sum(len(l) for l in lines)
    orig_len = len(text)
    if full_len < orig_len:
        lines[-1] += "…"
    shrink_factor = max(0.7, min(1.0, 15 / max(full_len, 1)))
    fontsize = base_fontsize * shrink_factor
    return "\n".join(lines), fontsize

def get_mekko_chart_cached(fund_id, fund_name, reporting_date=None):
    """
    Erstellt oder lädt Mekko Chart aus Session State.
    Charts werden nur 1x pro Session erstellt und dann wiederverwendet.
    """
    if 'mekko_charts' not in st.session_state:
        st.session_state.mekko_charts = {}

    cache_key = f"{fund_id}_{reporting_date}"

    if cache_key in st.session_state.mekko_charts:
        return st.session_state.mekko_charts[cache_key]

    fig = _create_mekko_chart_internal(fund_id, fund_name, reporting_date)
    st.session_state.mekko_charts[cache_key] = fig

    return fig


def _create_mekko_chart_internal(fund_id, fund_name, reporting_date=None):
    """Interne Funktion: Erstellt das Mekko Chart."""
    with get_connection() as conn:
        if reporting_date:
            query = """
            SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
            FROM portfolio_companies_history
            WHERE fund_id = %s AND reporting_date = %s
            ORDER BY (realized_tvpi + unrealized_tvpi) DESC
            """
            df = pd.read_sql_query(query, conn, params=(fund_id, reporting_date))

            metrics_query = """
            SELECT total_tvpi, net_tvpi, net_irr
            FROM fund_metrics_history
            WHERE fund_id = %s AND reporting_date = %s
            """
            metrics_df = pd.read_sql_query(metrics_query, conn, params=(fund_id, reporting_date))
        else:
            query = """
            SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
            FROM portfolio_companies
            WHERE fund_id = %s
            ORDER BY (realized_tvpi + unrealized_tvpi) DESC
            """
            df = pd.read_sql_query(query, conn, params=(fund_id,))

            metrics_query = """
            SELECT total_tvpi, net_tvpi, net_irr
            FROM fund_metrics
            WHERE fund_id = %s
            """
            metrics_df = pd.read_sql_query(metrics_query, conn, params=(fund_id,))

        if not metrics_df.empty:
            gross_tvpi = metrics_df['total_tvpi'].iloc[0]
            net_tvpi = metrics_df['net_tvpi'].iloc[0]
            net_irr = metrics_df['net_irr'].iloc[0]
        else:
            gross_tvpi = None
            net_tvpi = None
            net_irr = None

    if df.empty:
        return None

    title_suffix = f"\n(Stichtag: {reporting_date})" if reporting_date else ""

    categories = df["company_name"].tolist()
    widths = df["invested_amount"].tolist()
    values = df[["realized_tvpi", "unrealized_tvpi"]].values.tolist()

    fig, ax = plt.subplots(figsize=(14, 7))

    total_width = sum(widths)
    x_start = 0
    REAL_COLOR = "darkblue"
    UNREAL_COLOR = "lightskyblue"
    max_height = max(map(sum, values)) if values else 1

    for i, cat in enumerate(categories):
        cat_width = widths[i]
        bottom = 0
        category_total = sum(values[i])

        for j, val in enumerate(values[i]):
            color = REAL_COLOR if j == 0 else UNREAL_COLOR

            ax.bar(
                x_start, val,
                width=cat_width,
                bottom=bottom,
                color=color,
                edgecolor='black',
                align='edge'
            )

            if val > 0:
                pct = val / category_total * 100 if category_total > 0 else 0
                ax.text(
                    x_start + cat_width / 2,
                    bottom + val / 2,
                    f"{pct:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="white" if color == REAL_COLOR else "black"
                )

            bottom += val

        label_y_base = -max_height * 0.08
        label_y_offset = max_height * 0.04
        label_y = label_y_base if i % 2 == 0 else label_y_base - label_y_offset
        wrapped_text, dyn_fontsize = wrap_label(cat)

        ax.text(
            x_start + cat_width / 2,
            label_y,
            wrapped_text,
            ha="center",
            va="top",
            fontsize=dyn_fontsize,
            fontweight="bold"
        )

        x_start += cat_width

    total_value_ccy = 0
    total_realized_ccy = 0
    company_total_values_ccy = []

    for i in range(len(values)):
        invested = widths[i]
        realized_ccy = values[i][0] * invested
        unrealized_ccy = values[i][1] * invested
        total_ccy = realized_ccy + unrealized_ccy

        total_realized_ccy += realized_ccy
        total_value_ccy += total_ccy
        company_total_values_ccy.append(total_ccy)

    realized_pct = total_realized_ccy / total_value_ccy * 100 if total_value_ccy > 0 else 0

    sorted_idx_value = sorted(
        range(len(company_total_values_ccy)),
        key=lambda i: company_total_values_ccy[i],
        reverse=True
    )

    top5_value_ccy = sum(company_total_values_ccy[i] for i in sorted_idx_value[:5])
    top5_value_pct = top5_value_ccy / sum(company_total_values_ccy) * 100 if company_total_values_ccy else 0

    top5_width_ccy = sum(widths[i] for i in sorted_idx_value[:5])
    top5_width_pct = top5_width_ccy / sum(widths) * 100 if widths else 0

    total_invested_ccy = sum(widths)
    loss_invested_ccy = sum(widths[i] for i in range(len(values)) if sum(values[i]) < 1.0)
    loss_ratio = loss_invested_ccy / total_invested_ccy * 100 if total_invested_ccy > 0 else 0

    gross_tvpi_str = f"{gross_tvpi:.2f}x" if gross_tvpi is not None else "-"
    net_tvpi_str = f"{net_tvpi:.2f}x" if net_tvpi is not None else "-"
    net_irr_str = f"{net_irr:.1f}%" if net_irr is not None else "-"

    textstr = (
        f"Gross TVPI: {gross_tvpi_str}\n"
        f"Net TVPI: {net_tvpi_str}\n"
        f"Net IRR: {net_irr_str}\n"
        f"───────────────────────────\n"
        f"Top 5 Anteil am Gesamtfonds: {top5_value_pct:.1f}%\n"
        f"Top 5 Anteil des investierten Kapitals: {top5_width_pct:.1f}%\n"
        f"Realisierter Anteil gesamt: {realized_pct:.1f}%\n"
        f"Loss ratio (<1.0x): {loss_ratio:.1f}%"
    )

    props = dict(boxstyle='round', facecolor='whitesmoke', alpha=0.9)
    ax.text(1.02, 0.5, textstr, transform=ax.transAxes, fontsize=10, va='center', bbox=props)

    ax.set_xlim(0, total_width)
    ax.set_ylabel("TVPI")
    ax.yaxis.set_major_formatter(FuncFormatter(tvpi_formatter))
    ax.set_title(fund_name + title_suffix, fontsize=15, fontweight="bold")

    ax.text(
        0.5, 0.95,
        "Höhe = Gesamtwertschöpfung (Realisiert + Unrealisiert);\n"
        "Breite = Investiertes Kapital; Sortiert nach TVPI (absteigend)",
        ha="center", va="bottom", fontsize=9, color="gray",
        transform=ax.transAxes, linespacing=1.4
    )

    ax.set_xticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    patches = [
        Patch(facecolor=REAL_COLOR, edgecolor="black", label="Realisiert"),
        Patch(facecolor=UNREAL_COLOR, edgecolor="black", label="Unrealisiert")
    ]
    ax.legend(handles=patches, title="Status", loc="upper left", bbox_to_anchor=(1.02, 1))

    plt.tight_layout()
    return fig

def clear_mekko_cache():
    """Löscht den Mekko Chart Cache (z.B. nach Datenänderung)"""
    if 'mekko_charts' in st.session_state:
        for fig in st.session_state.mekko_charts.values():
            if fig is not None:
                plt.close(fig)
        st.session_state.mekko_charts = {}

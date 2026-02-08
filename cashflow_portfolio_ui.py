"""
Cashflow Planning Tool — Dashboard-KPI-Widgets

Rendert Portfolio-KPIs am Anfang des Cashflow-Tabs.
"""

import streamlit as st
import pandas as pd
from datetime import date

from cashflow_queries import (
    get_all_funds_for_cashflow_cached,
    get_cashflow_summary_cached,
    get_upcoming_capital_calls_cached,
)

CURRENCY_OPTIONS = ['Gemischt', 'EUR', 'USD', 'CHF', 'GBP']


def render_dashboard_widgets(conn_id, base_currency='EUR'):
    """Rendert Portfolio-KPIs am Anfang des Cashflow-Tabs."""

    all_funds_df = get_all_funds_for_cashflow_cached(conn_id)
    if all_funds_df.empty:
        return

    fund_ids = tuple(int(x) for x in all_funds_df['fund_id'].tolist())

    # Dashboard-Währung auswahl
    dash_ccy = st.selectbox(
        "Dashboard-Währung", options=CURRENCY_OPTIONS,
        index=0, key="dash_ccy_select"
    )
    use_mixed = (dash_ccy == 'Gemischt')

    # Metriken sammeln
    total_commitment = 0.0
    total_unfunded = 0.0
    total_called = 0.0
    total_distributed = 0.0
    active_funds = 0
    dpi_values = []

    if use_mixed:
        # Gemischt: keine FX-Konversion, einfach summieren
        for _, row in all_funds_df.iterrows():
            fid = int(row['fund_id'])
            commit = row.get('commitment_amount') or 0
            unfunded = row.get('unfunded_amount') or 0
            total_commitment += commit
            total_unfunded += unfunded

            summary = get_cashflow_summary_cached(conn_id, fid, 'base')
            called = summary.get('total_called', 0)
            distributed = summary.get('total_distributed', 0)
            total_called += called
            total_distributed += distributed

            if called > 0 or distributed > 0:
                active_funds += 1
                dpi_values.append(summary.get('dpi', 0))
    else:
        # Mit FX-Konversion über Portfolio-Summary
        from cashflow_queries import get_portfolio_summary_cached
        summary = get_portfolio_summary_cached(conn_id, fund_ids, dash_ccy, 'base')
        total_commitment = summary['total_commitment']
        total_unfunded = summary['total_unfunded']
        total_called = summary['total_called']
        total_distributed = summary['total_distributed']
        active_funds = summary['num_funds']
        dpi_values = [summary['portfolio_dpi']]

    avg_dpi = sum(dpi_values) / len(dpi_values) if dpi_values else 0.0
    ccy_label = 'Mix' if use_mixed else dash_ccy

    # Nächster Call
    upcoming = get_upcoming_capital_calls_cached(conn_id, days_ahead=90)
    next_call_str = "–"
    if not upcoming.empty:
        first = upcoming.iloc[0]
        next_call_str = f"{first['fund_name']}: {first['amount']:,.0f} {first['currency']} ({first['days_until']}d)"

    # Zeile 1
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    with r1c1:
        st.metric("Total Commitment", f"{total_commitment:,.0f} {ccy_label}")
    with r1c2:
        st.metric("Total Unfunded", f"{total_unfunded:,.0f} {ccy_label}")
    with r1c3:
        st.metric("Total Called", f"{total_called:,.0f} {ccy_label}")
    with r1c4:
        st.metric("Total Distributed", f"{total_distributed:,.0f} {ccy_label}")

    # Zeile 2
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    with r2c1:
        portfolio_dpi = total_distributed / total_called if total_called > 0 else 0.0
        st.metric("Portfolio DPI", f"{portfolio_dpi:.2f}x")
    with r2c2:
        st.metric("Nächster Call", next_call_str)
    with r2c3:
        st.metric("Aktive Fonds", str(active_funds))
    with r2c4:
        st.metric("Ø DPI", f"{avg_dpi:.2f}x")

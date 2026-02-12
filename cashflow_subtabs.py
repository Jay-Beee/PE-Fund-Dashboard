"""
Cashflow Planning Tool — Sub-Tab Orchestrator

Ersetzt render_cashflow_tab() mit 5 Sub-Tabs:
  1. Dashboard — KPI-Widgets + Alerts
  2. Portfolio — Aggregation, Vergleich, Export
  3. Einzelfonds — Pro Fonds: Cashflows, Charts, Forecast, Szenario, Import
  4. Pipeline — Kanban, DD-Tracking, Simulation, Promote/Decline
  5. Liquidität — Funding-Gap, Cash-Reserve
"""

import streamlit as st


def render_cashflow_subtabs(conn, conn_id, selected_fund_ids, selected_fund_names):
    """Ersetzt render_cashflow_tab(). Erstellt 5 Sub-Tabs."""

    # Lazy imports to avoid circular import issues
    from cashflow_dashboard import render_dashboard_widgets
    from cashflow_alerts import render_alerts_banner
    from cashflow_portfolio_ui import render_portfolio_section
    from cashflow_ui import render_fund_detail_section
    from cashflow_pipeline_ui import render_pipeline_section
    from cashflow_liquidity_ui import render_liquidity_section

    st.header("Cashflow Planning")

    tab_dash, tab_port, tab_fund, tab_pipe, tab_liq = st.tabs([
        "Dashboard", "Portfolio", "Einzelfonds",
        "Pipeline", "Liquidität"
    ])

    with tab_dash:
        render_dashboard_widgets(conn_id)
        render_alerts_banner(conn_id)

    with tab_port:
        render_portfolio_section(conn, conn_id)

    with tab_fund:
        render_fund_detail_section(conn, conn_id, selected_fund_ids, selected_fund_names)

    with tab_pipe:
        render_pipeline_section(conn, conn_id)

    with tab_liq:
        render_liquidity_section(conn, conn_id)

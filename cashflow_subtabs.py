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
import sys


def render_cashflow_subtabs(conn, conn_id, selected_fund_ids, selected_fund_names):
    """Ersetzt render_cashflow_tab(). Erstellt 5 Sub-Tabs."""

    from cashflow_dashboard import render_dashboard_widgets
    from cashflow_alerts import render_alerts_banner
    from cashflow_portfolio_ui import render_portfolio_section
    from cashflow_pipeline_ui import render_pipeline_section
    from cashflow_liquidity_ui import render_liquidity_section

    # Diagnose: cashflow_ui Import mit detaillierter Fehlerausgabe
    render_fund_detail_section = None
    try:
        # Modul aus Cache entfernen falls vorhanden (broken state)
        if 'cashflow_ui' in sys.modules:
            del sys.modules['cashflow_ui']
        from cashflow_ui import render_fund_detail_section
    except Exception as e:
        st.error(f"Fehler beim Import von cashflow_ui: {type(e).__name__}: {e}")
        import traceback
        st.code(traceback.format_exc())
        # Fallback
        try:
            import cashflow_ui
            available = [x for x in dir(cashflow_ui) if not x.startswith('_')]
            st.warning(f"Verfuegbare Namen in cashflow_ui: {available}")
            if hasattr(cashflow_ui, 'render_cashflow_tab'):
                render_fund_detail_section = cashflow_ui.render_cashflow_tab
                st.info("Fallback: verwende render_cashflow_tab")
        except Exception as e2:
            st.error(f"Auch cashflow_ui Modul-Import fehlgeschlagen: {e2}")

    st.header("Cashflow Planning")

    tab_dash, tab_port, tab_fund, tab_pipe, tab_liq = st.tabs([
        "Dashboard", "Portfolio", "Einzelfonds",
        "Pipeline", "Liquiditaet"
    ])

    with tab_dash:
        render_dashboard_widgets(conn_id)
        render_alerts_banner(conn_id)

    with tab_port:
        render_portfolio_section(conn, conn_id)

    with tab_fund:
        if render_fund_detail_section is not None:
            render_fund_detail_section(conn, conn_id, selected_fund_ids, selected_fund_names)
        else:
            st.error("Einzelfonds-Ansicht konnte nicht geladen werden.")

    with tab_pipe:
        render_pipeline_section(conn, conn_id)

    with tab_liq:
        render_liquidity_section(conn, conn_id)

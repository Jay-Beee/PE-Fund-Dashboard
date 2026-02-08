"""
Cashflow Planning Tool — Portfolio-Aggregation UI

Fonds-Multiselect, Basiswährung, Portfolio-KPIs, Charts und
aggregierte Ist vs. Forecast Analyse.
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from cashflow_queries import (
    get_all_funds_for_cashflow_cached,
    get_portfolio_cumulative_cashflows_cached,
    get_portfolio_periodic_cashflows_cached,
    get_portfolio_summary_cached,
    get_portfolio_fund_breakdown_cached,
    get_scenarios_cached,
    get_portfolio_actual_vs_forecast_cached,
)
from cashflow_portfolio_charts import (
    create_portfolio_j_curve_chart,
    create_portfolio_bar_chart,
    create_portfolio_fund_contribution_chart,
    create_actual_vs_forecast_chart,
    create_deviation_chart,
)
from cashflow_export import export_portfolio_excel, export_portfolio_report_pdf

CURRENCY_OPTIONS = ['EUR', 'USD', 'CHF', 'GBP']


def render_portfolio_section(conn, conn_id):
    """Rendert die komplette Portfolio-Aggregations-Sektion."""

    with st.expander("📊 Portfolio-Aggregation", expanded=False):
        all_funds_df = get_all_funds_for_cashflow_cached(conn_id)
        if all_funds_df.empty:
            st.info("Keine Fonds vorhanden.")
            return

        # --- Fonds-Multiselect ---
        fund_names = all_funds_df['fund_name'].tolist()
        selected_names = st.multiselect(
            "Fonds auswählen",
            options=fund_names,
            default=fund_names,
            key="pf_fund_select"
        )

        if not selected_names:
            st.info("Bitte mindestens einen Fonds auswählen.")
            return

        selected_df = all_funds_df[all_funds_df['fund_name'].isin(selected_names)]
        fund_ids = tuple(int(x) for x in selected_df['fund_id'].tolist())

        # --- Basiswährung + Szenario ---
        pc1, pc2 = st.columns(2)
        with pc1:
            base_currency = st.selectbox(
                "Basiswährung", options=CURRENCY_OPTIONS, key="pf_base_ccy"
            )
        with pc2:
            scenarios = get_scenarios_cached(conn_id)
            scenario_names = [s['scenario_name'] for s in scenarios]
            selected_scenario = st.selectbox(
                "Szenario", options=scenario_names, key="pf_scenario"
            )

        # --- Portfolio Summary ---
        summary = get_portfolio_summary_cached(conn_id, fund_ids, base_currency, selected_scenario)

        # FX-Warnungen
        if summary.get('fx_warnings'):
            st.warning(
                f"⚠️ Fehlende FX-Raten für: {', '.join(summary['fx_warnings'])}. "
                f"Beträge ohne Rate werden ignoriert."
            )

        # KPI-Zeile
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.metric("Total Commitment", f"{summary['total_commitment']:,.0f} {base_currency}")
        with k2:
            st.metric("Total Called", f"{summary['total_called']:,.0f} {base_currency}")
        with k3:
            st.metric("Total Distributed", f"{summary['total_distributed']:,.0f} {base_currency}")
        with k4:
            st.metric("Portfolio DPI", f"{summary['portfolio_dpi']:.2f}x")

        # --- Fonds-Aufschlüsselung ---
        breakdown_df = get_portfolio_fund_breakdown_cached(conn_id, fund_ids, base_currency, selected_scenario)
        if not breakdown_df.empty:
            st.markdown("**Fonds-Aufschlüsselung**")
            display_bd = breakdown_df.copy()
            for col in ['commitment_base', 'called_base', 'distributed_base', 'net_base']:
                if col in display_bd.columns:
                    display_bd[col] = display_bd[col].apply(
                        lambda x: f"{x:,.0f}" if pd.notna(x) else "n/a"
                    )
            if 'dpi' in display_bd.columns:
                display_bd['dpi'] = display_bd['dpi'].apply(lambda x: f"{x:.2f}x")
            display_bd.columns = ['Fonds', 'Währung', f'Commitment ({base_currency})',
                                  f'Called ({base_currency})', f'Distributed ({base_currency})',
                                  f'Netto ({base_currency})', 'DPI']
            st.dataframe(display_bd, hide_index=True, use_container_width=True)

        # --- Export Buttons ---
        cumulative_df = get_portfolio_cumulative_cashflows_cached(
            conn_id, fund_ids, base_currency, selected_scenario
        )
        periodic_df = get_portfolio_periodic_cashflows_cached(
            conn_id, fund_ids, base_currency, 'quarter', selected_scenario
        )

        exp_col1, exp_col2 = st.columns(2)
        with exp_col1:
            excel_data = export_portfolio_excel(breakdown_df, summary, periodic_df, base_currency)
            st.download_button(
                label="📥 Portfolio Export (Excel)",
                data=excel_data,
                file_name=f"portfolio_{base_currency}_{selected_scenario}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="pf_excel_export_btn"
            )
        with exp_col2:
            pdf_data = export_portfolio_report_pdf(
                summary, breakdown_df, cumulative_df, periodic_df, base_currency
            )
            if pdf_data:
                st.download_button(
                    label="📄 Portfolio Report PDF",
                    data=pdf_data,
                    file_name=f"portfolio_report_{base_currency}.pdf",
                    mime="application/pdf",
                    key="pf_pdf_export_btn"
                )

        # --- Charts ---
        # (cumulative_df and periodic_df already loaded above)
        cumulative_df = get_portfolio_cumulative_cashflows_cached(
            conn_id, fund_ids, base_currency, selected_scenario
        )
        periodic_df = get_portfolio_periodic_cashflows_cached(
            conn_id, fund_ids, base_currency, 'quarter', selected_scenario
        )

        chart_tab1, chart_tab2, chart_tab3 = st.tabs([
            "Portfolio J-Curve", "Portfolio Balken", "Fonds-Beitrag"
        ])

        with chart_tab1:
            fig = create_portfolio_j_curve_chart(cumulative_df, base_currency)
            if fig:
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.info("Keine Daten für Portfolio J-Curve.")

        with chart_tab2:
            period = st.radio(
                "Aggregation", options=['quarter', 'year'],
                format_func=lambda x: 'Quartal' if x == 'quarter' else 'Jahr',
                horizontal=True, key="pf_period_toggle"
            )
            p_df = get_portfolio_periodic_cashflows_cached(
                conn_id, fund_ids, base_currency, period, selected_scenario
            )
            fig = create_portfolio_bar_chart(p_df, base_currency)
            if fig:
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.info("Keine Daten für Portfolio-Balkendiagramm.")

        with chart_tab3:
            fig = create_portfolio_fund_contribution_chart(breakdown_df, base_currency)
            if fig:
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.info("Keine Daten für Fonds-Beitrags-Chart.")

        # --- Portfolio Ist vs. Forecast ---
        st.markdown("---")
        st.markdown("**Portfolio Ist vs. Forecast**")
        avf = get_portfolio_actual_vs_forecast_cached(
            conn_id, fund_ids, base_currency, selected_scenario
        )
        metrics = avf['metrics']

        if avf['actual_cumulative'].empty and avf['forecast_cumulative'].empty:
            st.info("Keine Ist- oder Forecast-Daten für die ausgewählten Fonds.")
        else:
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric("Calls realisiert", f"{metrics['pct_calls_realized']:.1f}%")
            with m2:
                st.metric("Dists realisiert", f"{metrics['pct_dists_realized']:.1f}%")
            with m3:
                st.metric("Tracking Error", f"{metrics['tracking_error']:,.0f} {base_currency}")
            with m4:
                st.metric("Ø Abweichung", f"{metrics['mean_deviation']:,.0f} {base_currency}")

            fig = create_actual_vs_forecast_chart(
                avf['actual_cumulative'], avf['forecast_cumulative'],
                'Portfolio: Ist vs. Forecast', base_currency
            )
            if fig:
                st.pyplot(fig)
                plt.close(fig)

            if not avf['periodic_deviation'].empty:
                fig = create_deviation_chart(
                    avf['periodic_deviation'],
                    'Portfolio: Periodische Abweichung', base_currency
                )
                if fig:
                    st.pyplot(fig)
                    plt.close(fig)

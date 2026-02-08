"""
Cashflow Planning Tool — Liquiditätsplanung UI

Rendert:
  - Fonds-Multiselect, Basiswährung, Szenario, Startguthaben
  - KPI-Zeile
  - Tabs: Funding-Gap, Cash-Reserve, Wasserfall
  - Export-Button
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from cashflow_queries import (
    get_all_funds_for_cashflow_cached,
    get_scenarios_cached,
    get_funding_gap_cached,
    get_cash_reserve_simulation_cached,
)
from cashflow_liquidity import (
    create_funding_gap_chart,
    create_cash_reserve_chart,
    create_funding_gap_waterfall_chart,
)
from cashflow_export import export_liquidity_excel

CURRENCY_OPTIONS = ['EUR', 'USD', 'CHF', 'GBP']


def render_liquidity_section(conn, conn_id):
    """Rendert die komplette Liquiditätsplanungs-Sektion."""

    with st.expander("💧 Liquiditätsplanung", expanded=False):

        all_funds_df = get_all_funds_for_cashflow_cached(conn_id)
        if all_funds_df.empty:
            st.info("Keine Fonds vorhanden.")
            return

        # --- Parameter ---
        fund_names = all_funds_df['fund_name'].tolist()
        selected_names = st.multiselect(
            "Fonds auswählen",
            options=fund_names,
            default=fund_names,
            key="liq_fund_select"
        )

        if not selected_names:
            st.info("Bitte mindestens einen Fonds auswählen.")
            return

        selected_df = all_funds_df[all_funds_df['fund_name'].isin(selected_names)]
        fund_ids = tuple(int(x) for x in selected_df['fund_id'].tolist())

        pc1, pc2, pc3, pc4 = st.columns(4)
        with pc1:
            base_currency = st.selectbox(
                "Basiswährung", options=CURRENCY_OPTIONS, key="liq_base_ccy"
            )
        with pc2:
            scenarios = get_scenarios_cached(conn_id)
            scenario_names = [s['scenario_name'] for s in scenarios]
            selected_scenario = st.selectbox(
                "Szenario", options=scenario_names, key="liq_scenario"
            )
        with pc3:
            start_balance = st.number_input(
                "Startguthaben", min_value=0.0, value=5000000.0,
                step=500000.0, format="%.0f", key="liq_start_balance"
            )
        with pc4:
            period = st.selectbox(
                "Periode", options=['quarter', 'year'],
                format_func=lambda x: 'Quartal' if x == 'quarter' else 'Jahr',
                key="liq_period"
            )

        # --- Daten laden ---
        funding_gap_df = get_funding_gap_cached(
            conn_id, fund_ids, base_currency, period, selected_scenario
        )
        cash_reserve_df = get_cash_reserve_simulation_cached(
            conn_id, fund_ids, base_currency, start_balance,
            selected_scenario, include_actuals=True
        )

        # --- KPIs ---
        if not funding_gap_df.empty:
            max_need = funding_gap_df['cumulative_funding_need'].min()
            max_need_period = ''
            if max_need < 0:
                idx = funding_gap_df['cumulative_funding_need'].idxmin()
                max_need_period = funding_gap_df.loc[idx, 'period_label']
            total_net = funding_gap_df['net_funding_need'].sum()

            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.metric("Max. Funding-Bedarf",
                          f"{abs(max_need):,.0f} {base_currency}" if max_need < 0 else "0")
            with k2:
                st.metric("Zeitpunkt Max. Bedarf", max_need_period if max_need < 0 else "–")
            with k3:
                st.metric("Total Netto-Bedarf", f"{total_net:,.0f} {base_currency}")
            with k4:
                if not cash_reserve_df.empty:
                    months_under = (cash_reserve_df['balance'] < 0).sum()
                    st.metric("Perioden mit Unterdeckung", str(months_under))
                else:
                    st.metric("Perioden mit Unterdeckung", "–")

        # --- Tabs ---
        tab1, tab2, tab3 = st.tabs(["Funding-Gap", "Cash-Reserve", "Wasserfall"])

        with tab1:
            if not funding_gap_df.empty:
                fig = create_funding_gap_chart(funding_gap_df, base_currency)
                if fig:
                    st.pyplot(fig)
                    plt.close(fig)

                st.markdown("**Daten**")
                display_fg = funding_gap_df.copy()
                for col in ['expected_calls', 'expected_distributions',
                           'net_funding_need', 'cumulative_funding_need']:
                    if col in display_fg.columns:
                        display_fg[col] = display_fg[col].apply(lambda x: f"{x:,.0f}")
                display_fg.columns = ['Periode', f'Erw. Abrufe ({base_currency})',
                                     f'Erw. Ausschüttungen ({base_currency})',
                                     f'Netto ({base_currency})',
                                     f'Kumulativ ({base_currency})']
                st.dataframe(display_fg, hide_index=True, use_container_width=True)
            else:
                st.info("Keine geplanten Cashflows (is_actual=False) für Funding-Gap vorhanden.")

        with tab2:
            if not cash_reserve_df.empty:
                fig = create_cash_reserve_chart(cash_reserve_df, base_currency)
                if fig:
                    st.pyplot(fig)
                    plt.close(fig)

                st.markdown("**Daten**")
                display_cr = cash_reserve_df.copy()
                display_cr['date'] = pd.to_datetime(display_cr['date']).dt.strftime('%Y-%m-%d')
                for col in ['inflow', 'outflow', 'net', 'balance']:
                    if col in display_cr.columns:
                        display_cr[col] = display_cr[col].apply(lambda x: f"{x:,.0f}")
                display_cr.columns = ['Datum', f'Zufluss ({base_currency})',
                                     f'Abfluss ({base_currency})',
                                     f'Netto ({base_currency})',
                                     f'Kontostand ({base_currency})']
                st.dataframe(display_cr, hide_index=True, use_container_width=True)
            else:
                st.info("Keine Cashflow-Daten für Cash-Reserve Simulation vorhanden.")

        with tab3:
            if not funding_gap_df.empty:
                fig = create_funding_gap_waterfall_chart(funding_gap_df, base_currency)
                if fig:
                    st.pyplot(fig)
                    plt.close(fig)
            else:
                st.info("Keine Daten für Wasserfall-Diagramm.")

        # --- Export ---
        if not funding_gap_df.empty or not cash_reserve_df.empty:
            params = {
                'start_balance': start_balance,
                'scenario': selected_scenario,
            }
            excel_data = export_liquidity_excel(
                funding_gap_df, cash_reserve_df, params, base_currency
            )
            st.download_button(
                label="📥 Liquidität Export (Excel)",
                data=excel_data,
                file_name=f"liquidity_{base_currency}_{selected_scenario}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="liq_export_btn"
            )

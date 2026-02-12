"""
Cashflow Planning Tool — Ist vs. Forecast Analyse

Pro-Fonds und aggregierte Ist vs. Forecast Analyse mit Overlay-Charts
und Abweichungs-Balkendiagrammen.
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from cashflow_queries import get_actual_vs_forecast_cached
from cashflow_portfolio_charts import (
    create_actual_vs_forecast_chart,
    create_deviation_chart,
)


def render_actual_vs_forecast_section(conn_id, fund_id, fund_name, currency, scenario_name):
    """Pro-Fonds Ist vs. Forecast Analyse."""

    with st.expander("📈 Ist vs. Forecast", expanded=False):
        avf = get_actual_vs_forecast_cached(conn_id, fund_id, scenario_name)
        metrics = avf['metrics']

        if avf['actual_cumulative'].empty and avf['forecast_cumulative'].empty:
            st.info("Keine Ist- oder Forecast-Daten für diesen Fonds/Szenario.")
            return

        # Metriken
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Calls realisiert", f"{metrics['pct_calls_realized']:.1f}%")
        with m2:
            st.metric("Dists realisiert", f"{metrics['pct_dists_realized']:.1f}%")
        with m3:
            st.metric("Tracking Error", f"{metrics['tracking_error']:,.0f} {currency}")
        with m4:
            st.metric("Ø Abweichung", f"{metrics['mean_deviation']:,.0f} {currency}")

        # Overlay-Chart
        fig = create_actual_vs_forecast_chart(
            avf['actual_cumulative'], avf['forecast_cumulative'],
            f'Ist vs. Forecast: {fund_name}', currency
        )
        if fig:
            st.pyplot(fig)
            plt.close(fig)

        # Abweichungs-Balkendiagramm
        if not avf['periodic_deviation'].empty:
            fig = create_deviation_chart(
                avf['periodic_deviation'],
                f'Abweichung pro Quartal: {fund_name}', currency
            )
            if fig:
                st.pyplot(fig)
                plt.close(fig)

            # Detail-Tabelle
            st.markdown("**Abweichungs-Detail**")
            detail_df = avf['periodic_deviation'].copy()
            for col in ['net_actual', 'net_forecast', 'deviation']:
                if col in detail_df.columns:
                    detail_df[col] = detail_df[col].apply(lambda x: f"{x:,.0f}")
            detail_df.columns = ['Periode', f'Ist ({currency})', f'Forecast ({currency})',
                                 f'Abweichung ({currency})']
            st.dataframe(detail_df, hide_index=True, width='stretch')

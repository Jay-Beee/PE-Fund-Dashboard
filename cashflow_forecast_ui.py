"""
Cashflow Planning Tool — Forecast UI

Streamlit-UI für Modell-Auswahl, Parameter, Vorschau und Speichern.
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from datetime import date

from database import clear_cache
from cashflow_db import (
    bulk_insert_cashflows, delete_forecast_cashflows, insert_scenario
)
from cashflow_queries import (
    get_scenarios_cached, get_cashflows_for_fund_cached,
    get_fund_commitment_info_cached, get_historical_pacing_cached,
    get_all_funds_for_cashflow_cached, OUTFLOW_TYPES
)
from cashflow_charts import create_forecast_preview_chart
from cashflow_forecast import (
    forecast_takahashi_alexander,
    forecast_driessen_lin_phalippou,
    forecast_ljungqvist_richardson,
    forecast_cambridge_quantile,
    forecast_linear,
    forecast_manual,
    forecast_historical_average,
    prepare_forecast_for_insertion,
)

# Modell-Katalog
MODEL_OPTIONS = {
    'Takahashi-Alexander (Yale)': {
        'key': 'ta',
        'desc': 'Parabolisches Modell mit Bow-Faktor. Standard für PE-Fonds.',
    },
    'Driessen-Lin-Phalippou': {
        'key': 'dlp',
        'desc': 'Exponentieller Zerfall bei Calls, Distributions ab Jahr 3.',
    },
    'Ljungqvist-Richardson': {
        'key': 'lr',
        'desc': 'Schedule-basiert: Investment-Phase + Harvest-Phase.',
    },
    'Cambridge Quantile': {
        'key': 'cambridge',
        'desc': 'Benchmark-Kurven nach Strategie und Percentile.',
    },
    'Linear': {
        'key': 'linear',
        'desc': 'Gleichmässiger Abruf und Ausschüttung.',
    },
    'Manual Pacing': {
        'key': 'manual',
        'desc': 'Eigene Pacing-Kurven (% pro Jahr).',
    },
    'Historical Average': {
        'key': 'historical',
        'desc': 'Pacing aus historischem Fonds ableiten.',
    },
}


def render_forecast_section(conn, conn_id, fund_id, fund_name, currency, commit_info):
    """Rendert die komplette Forecast-Sektion."""

    with st.expander("🔮 Cashflow Forecast", expanded=False):
        # --- Modell-Auswahl ---
        model_names = list(MODEL_OPTIONS.keys())
        selected_model = st.selectbox(
            "Prognose-Modell",
            options=model_names,
            key="fc_model_select",
            help="Wählen Sie ein Modell für die Cashflow-Prognose."
        )
        model_info = MODEL_OPTIONS[selected_model]
        st.caption(model_info['desc'])

        # --- Gemeinsame Parameter ---
        st.markdown("**Gemeinsame Parameter**")
        cp1, cp2, cp3 = st.columns(3)
        with cp1:
            commitment = st.number_input(
                "Commitment",
                value=float(commit_info.get('commitment_amount') or 1_000_000),
                min_value=0.0, step=100_000.0, key="fc_commitment"
            )
        with cp2:
            lifetime = st.number_input(
                "Laufzeit (Jahre)", value=10, min_value=1, max_value=30,
                key="fc_lifetime"
            )
        with cp3:
            vintage_year = st.number_input(
                "Vintage Year", value=date.today().year,
                min_value=2000, max_value=2050, key="fc_vintage"
            )

        # --- Modell-spezifische Parameter ---
        model_key = model_info['key']
        model_params = _render_model_params(
            model_key, conn_id, fund_id, commitment, lifetime, vintage_year
        )

        # --- Ziel-Szenario ---
        st.markdown("---")
        st.markdown("**Ziel-Szenario**")
        scenarios = get_scenarios_cached(conn_id)
        scenario_names = [s['scenario_name'] for s in scenarios]

        sc1, sc2 = st.columns([3, 1])
        with sc1:
            target_scenario = st.selectbox(
                "Szenario", options=scenario_names, key="fc_target_scenario"
            )
        with sc2:
            new_sc = st.text_input("Neues Szenario", key="fc_new_scenario")
            if st.button("Erstellen", key="fc_create_scenario"):
                if new_sc and new_sc.strip():
                    insert_scenario(conn, new_sc.strip())
                    clear_cache()
                    st.success(f"Szenario '{new_sc}' erstellt.")
                    st.rerun()

        # --- Berechnen-Button ---
        st.markdown("---")
        if st.button("🔮 Forecast berechnen", key="fc_compute", type="primary"):
            forecast = _compute_forecast(
                model_key, commitment, lifetime, vintage_year, model_params,
                conn_id, fund_id
            )
            if forecast:
                st.session_state['fc_preview'] = forecast
                st.session_state['fc_model_name'] = selected_model
            else:
                st.warning("Forecast konnte nicht berechnet werden. Prüfen Sie die Parameter.")

        # --- Vorschau ---
        if 'fc_preview' in st.session_state and st.session_state['fc_preview']:
            forecast = st.session_state['fc_preview']
            _render_forecast_preview(forecast, currency)

            # --- Speichern / Verwerfen ---
            sv1, sv2 = st.columns(2)
            with sv1:
                if st.button("💾 Forecast speichern", key="fc_save", type="primary"):
                    _save_forecast(
                        conn, conn_id, fund_id, forecast, target_scenario,
                        currency, st.session_state.get('fc_model_name', '')
                    )
            with sv2:
                if st.button("🗑️ Verwerfen", key="fc_discard"):
                    st.session_state.pop('fc_preview', None)
                    st.session_state.pop('fc_model_name', None)
                    st.rerun()


def _render_model_params(model_key, conn_id, fund_id, commitment, lifetime, vintage_year):
    """Rendert modell-spezifische Parameter und gibt dict zurück."""

    params = {}
    st.markdown("**Modell-Parameter**")

    if model_key == 'ta':
        p1, p2 = st.columns(2)
        with p1:
            params['rc'] = st.slider("Rate of Contribution (RC)", 0.05, 0.60, 0.25,
                                     0.05, key="fc_ta_rc")
            params['rd'] = st.slider("Rate of Distribution (RD)", 0.05, 0.60, 0.20,
                                     0.05, key="fc_ta_rd")
        with p2:
            params['bow_factor'] = st.slider("Bow Factor", 0.5, 5.0, 2.5,
                                             0.5, key="fc_ta_bow")
            params['growth_rate'] = st.slider("NAV Growth Rate", 0.0, 0.30, 0.08,
                                              0.01, key="fc_ta_growth",
                                              format="%.2f")

    elif model_key == 'dlp':
        p1, p2 = st.columns(2)
        with p1:
            params['drawdown_rate'] = st.slider("Drawdown Rate", 0.10, 0.60, 0.30,
                                                0.05, key="fc_dlp_dd")
            params['distribution_rate'] = st.slider("Distribution Rate", 0.05, 0.50, 0.25,
                                                    0.05, key="fc_dlp_dist")
        with p2:
            params['nav_growth_rate'] = st.slider("NAV Growth Rate", 0.0, 0.30, 0.10,
                                                  0.01, key="fc_dlp_growth",
                                                  format="%.2f")

    elif model_key == 'lr':
        p1, p2 = st.columns(2)
        with p1:
            params['investment_period'] = st.number_input(
                "Investment Period (Jahre)", value=5, min_value=1, max_value=15,
                key="fc_lr_inv_period"
            )
            params['harvest_start'] = st.number_input(
                "Harvest Start (Jahr)", value=4, min_value=1, max_value=20,
                key="fc_lr_harvest_start"
            )
        with p2:
            params['nav_growth_rate'] = st.slider(
                "NAV Growth Rate", 0.0, 0.30, 0.10, 0.01,
                key="fc_lr_growth", format="%.2f"
            )

        st.markdown("**Investment Pace** (% von Commitment pro Jahr)")
        inv_period = params['investment_period']
        inv_pace = []
        cols = st.columns(min(inv_period, 5))
        for i in range(inv_period):
            with cols[i % len(cols)]:
                default_val = [0.25, 0.25, 0.20, 0.15, 0.15]
                def_v = default_val[i] if i < len(default_val) else 0.10
                val = st.number_input(
                    f"Jahr {i}", value=def_v, min_value=0.0, max_value=1.0,
                    step=0.05, key=f"fc_lr_inv_{i}", format="%.2f"
                )
                inv_pace.append(val)
        params['investment_pace'] = inv_pace

        st.markdown("**Harvest Pace** (% von NAV pro Jahr)")
        harvest_years = lifetime - params['harvest_start']
        harvest_pace = []
        if harvest_years > 0:
            cols2 = st.columns(min(harvest_years, 5))
            for i in range(harvest_years):
                with cols2[i % len(cols2)]:
                    default_hp = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
                    def_h = default_hp[i] if i < len(default_hp) else 0.40
                    val = st.number_input(
                        f"Jahr {params['harvest_start'] + i}", value=def_h,
                        min_value=0.0, max_value=1.0, step=0.05,
                        key=f"fc_lr_harv_{i}", format="%.2f"
                    )
                    harvest_pace.append(val)
        params['harvest_pace'] = harvest_pace

    elif model_key == 'cambridge':
        p1, p2, p3 = st.columns(3)
        with p1:
            strategy_map = {
                'Buyout': 'buyout',
                'Venture Capital': 'venture',
                'Growth Equity': 'growth',
                'Infrastructure': 'infrastructure',
                'Real Estate': 'real_estate',
            }
            strategy_label = st.selectbox(
                "Strategie", options=list(strategy_map.keys()),
                key="fc_cam_strategy"
            )
            params['strategy'] = strategy_map[strategy_label]
        with p2:
            percentile_map = {'Q1 (konservativ)': 'q1', 'Median': 'median', 'Q3 (optimistisch)': 'q3'}
            pct_label = st.radio(
                "Percentile", options=list(percentile_map.keys()),
                index=1, key="fc_cam_pct", horizontal=True
            )
            params['percentile'] = percentile_map[pct_label]
        with p3:
            params['tvpi_multiple'] = st.number_input(
                "Ziel-TVPI", value=1.6, min_value=0.5, max_value=5.0,
                step=0.1, key="fc_cam_tvpi", format="%.1f"
            )

    elif model_key == 'linear':
        p1, p2, p3 = st.columns(3)
        with p1:
            params['investment_period'] = st.number_input(
                "Investment Period (Jahre)", value=5, min_value=1, max_value=15,
                key="fc_lin_inv_period"
            )
        with p2:
            params['harvest_start'] = st.number_input(
                "Harvest Start (Jahr)", value=4, min_value=1, max_value=20,
                key="fc_lin_harvest_start"
            )
        with p3:
            params['tvpi_multiple'] = st.number_input(
                "Ziel-TVPI", value=1.5, min_value=0.5, max_value=5.0,
                step=0.1, key="fc_lin_tvpi", format="%.1f"
            )

    elif model_key == 'manual':
        st.markdown("Definieren Sie Pacing-Kurven (% von Commitment pro Jahr):")
        pacing_data = []
        for yr in range(lifetime):
            pacing_data.append({
                'Jahr': yr,
                'Calls (%)': 0.0,
                'Distributions (%)': 0.0,
            })
        # Defaults setzen
        default_calls = {0: 15.0, 1: 25.0, 2: 20.0, 3: 15.0, 4: 10.0}
        default_dists = {3: 3.0, 4: 7.0, 5: 12.0, 6: 18.0, 7: 22.0, 8: 20.0, 9: 15.0}
        for d in pacing_data:
            yr = d['Jahr']
            d['Calls (%)'] = default_calls.get(yr, 0.0)
            d['Distributions (%)'] = default_dists.get(yr, 0.0)

        pacing_df = pd.DataFrame(pacing_data)
        edited_pacing = st.data_editor(
            pacing_df, hide_index=True, key="fc_manual_pacing",
            column_config={
                'Jahr': st.column_config.NumberColumn(disabled=True),
                'Calls (%)': st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0, format="%.1f"
                ),
                'Distributions (%)': st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0, format="%.1f"
                ),
            }
        )
        params['edited_pacing'] = edited_pacing

        params['tvpi_multiple'] = st.number_input(
            "Ziel-TVPI", value=1.5, min_value=0.5, max_value=5.0,
            step=0.1, key="fc_manual_tvpi", format="%.1f"
        )

    elif model_key == 'historical':
        all_funds = get_all_funds_for_cashflow_cached(conn_id)
        if all_funds.empty:
            st.warning("Keine Fonds verfügbar.")
        else:
            source_fund_name = st.selectbox(
                "Quell-Fonds (historische Daten)",
                options=all_funds['fund_name'].tolist(),
                key="fc_hist_source"
            )
            source_row = all_funds[all_funds['fund_name'] == source_fund_name]
            if not source_row.empty:
                params['source_fund_id'] = int(source_row.iloc[0]['fund_id'])

    return params


def _compute_forecast(model_key, commitment, lifetime, vintage_year,
                      model_params, conn_id, fund_id):
    """Berechnet den Forecast basierend auf Modell und Parametern."""

    if model_key == 'ta':
        return forecast_takahashi_alexander(
            commitment, lifetime, vintage_year,
            rc=model_params.get('rc', 0.25),
            rd=model_params.get('rd', 0.20),
            bow_factor=model_params.get('bow_factor', 2.5),
            growth_rate=model_params.get('growth_rate', 0.08),
        )

    elif model_key == 'dlp':
        return forecast_driessen_lin_phalippou(
            commitment, lifetime, vintage_year,
            drawdown_rate=model_params.get('drawdown_rate', 0.30),
            distribution_rate=model_params.get('distribution_rate', 0.25),
            nav_growth_rate=model_params.get('nav_growth_rate', 0.10),
        )

    elif model_key == 'lr':
        return forecast_ljungqvist_richardson(
            commitment, lifetime, vintage_year,
            investment_period=model_params.get('investment_period', 5),
            investment_pace=model_params.get('investment_pace'),
            harvest_start=model_params.get('harvest_start', 4),
            harvest_pace=model_params.get('harvest_pace'),
            nav_growth_rate=model_params.get('nav_growth_rate', 0.10),
        )

    elif model_key == 'cambridge':
        return forecast_cambridge_quantile(
            commitment, lifetime, vintage_year,
            strategy=model_params.get('strategy', 'buyout'),
            percentile=model_params.get('percentile', 'median'),
            tvpi_multiple=model_params.get('tvpi_multiple', 1.6),
        )

    elif model_key == 'linear':
        return forecast_linear(
            commitment, lifetime, vintage_year,
            investment_period=model_params.get('investment_period', 5),
            harvest_start=model_params.get('harvest_start', 4),
            tvpi_multiple=model_params.get('tvpi_multiple', 1.5),
        )

    elif model_key == 'manual':
        edited = model_params.get('edited_pacing')
        if edited is None:
            return []
        call_pacing = {}
        dist_pacing = {}
        for _, row in edited.iterrows():
            yr = int(row['Jahr'])
            cp = row['Calls (%)'] / 100.0
            dp = row['Distributions (%)'] / 100.0
            if cp > 0:
                call_pacing[yr] = cp
            if dp > 0:
                dist_pacing[yr] = dp
        return forecast_manual(
            commitment, vintage_year,
            call_pacing=call_pacing,
            dist_pacing=dist_pacing,
            tvpi_multiple=model_params.get('tvpi_multiple', 1.5),
        )

    elif model_key == 'historical':
        source_fund_id = model_params.get('source_fund_id')
        if not source_fund_id:
            return []

        # Historische Daten holen
        hist_df = get_cashflows_for_fund_cached(conn_id, source_fund_id, 'base')
        if hist_df.empty:
            return []

        hist_cashflows = hist_df.to_dict('records')
        # date-Spalte konvertieren
        for cf in hist_cashflows:
            if hasattr(cf['date'], 'date'):
                cf['date'] = cf['date'].date()

        source_info = get_fund_commitment_info_cached(conn_id, source_fund_id)
        hist_commitment = source_info.get('commitment_amount') or 0

        return forecast_historical_average(
            commitment, lifetime, vintage_year,
            historical_cashflows=hist_cashflows,
            historical_commitment=hist_commitment,
        )

    return []


def _render_forecast_preview(forecast, currency):
    """Zeigt Forecast-Vorschau mit Chart und Tabelle."""

    st.markdown("### Vorschau")

    # Summary
    total_calls = sum(e['amount'] for e in forecast if e['type'] in OUTFLOW_TYPES)
    total_dists = sum(e['amount'] for e in forecast
                      if e['type'] not in OUTFLOW_TYPES)
    net = total_dists - total_calls
    dpi = total_dists / total_calls if total_calls > 0 else 0.0

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Total Calls", f"{total_calls:,.0f} {currency}")
    with m2:
        st.metric("Total Distributions", f"{total_dists:,.0f} {currency}")
    with m3:
        st.metric("Netto", f"{net:,.0f} {currency}")
    with m4:
        st.metric("DPI", f"{dpi:.2f}x")

    # Chart
    fig = create_forecast_preview_chart(forecast, currency)
    if fig:
        st.pyplot(fig)
        plt.close(fig)

    # Tabelle (aggregiert nach Jahr)
    table_data = {}
    for entry in forecast:
        year = entry['date'].year if hasattr(entry['date'], 'year') else entry['date']
        if year not in table_data:
            table_data[year] = {'Jahr': year, 'Calls': 0.0, 'Distributions': 0.0}
        if entry['type'] in OUTFLOW_TYPES:
            table_data[year]['Calls'] += entry['amount']
        else:
            table_data[year]['Distributions'] += entry['amount']

    if table_data:
        tdf = pd.DataFrame(sorted(table_data.values(), key=lambda x: x['Jahr']))
        tdf['Netto'] = tdf['Distributions'] - tdf['Calls']
        tdf['Calls'] = tdf['Calls'].apply(lambda x: f"{x:,.0f}")
        tdf['Distributions'] = tdf['Distributions'].apply(lambda x: f"{x:,.0f}")
        tdf['Netto'] = tdf['Netto'].apply(lambda x: f"{x:,.0f}")
        st.dataframe(tdf, hide_index=True, width='stretch')

    st.caption(f"Forecast: {len(forecast)} Einträge generiert")


def _save_forecast(conn, conn_id, fund_id, forecast, scenario_name,
                   currency, model_name):
    """Speichert den Forecast in die Datenbank."""

    # Bestehende Forecasts löschen
    deleted = delete_forecast_cashflows(conn, fund_id, scenario_name)
    if deleted > 0:
        st.info(f"{deleted} bestehende Forecast-Einträge gelöscht.")

    # Neue Forecasts einfügen
    records = prepare_forecast_for_insertion(
        forecast, fund_id, scenario_name, currency,
        notes_prefix=f"Forecast ({model_name})"
    )

    if records:
        count = bulk_insert_cashflows(conn, records)
        clear_cache()
        st.success(f"✅ {count} Forecast-Cashflows gespeichert ({scenario_name}).")
        st.session_state.pop('fc_preview', None)
        st.session_state.pop('fc_model_name', None)
        st.rerun()
    else:
        st.warning("Keine Forecast-Daten zum Speichern.")

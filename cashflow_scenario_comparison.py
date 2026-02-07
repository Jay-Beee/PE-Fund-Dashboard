"""
Cashflow Planning Tool — Szenario-Vergleich

Overlay-Charts und Metrik-Tabelle für den Vergleich mehrerer Szenarien.
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

from cashflow_queries import (
    get_cumulative_cashflows_cached, get_cashflow_summary_cached,
    get_scenarios_cached, OUTFLOW_TYPES, INFLOW_TYPES
)

# 6 Farben für Szenarien
SCENARIO_COLORS = [
    '#1a237e',  # dunkelblau
    '#c62828',  # rot
    '#2e7d32',  # grün
    '#f57f17',  # amber
    '#6a1b9a',  # lila
    '#00695c',  # teal
]


def render_scenario_comparison(conn_id, fund_id, fund_name, currency, commit_info):
    """Rendert die Szenario-Vergleichs-Sektion."""

    with st.expander("⚖️ Szenario-Vergleich", expanded=False):
        scenarios = get_scenarios_cached(conn_id)
        scenario_names = [s['scenario_name'] for s in scenarios]

        if len(scenario_names) < 2:
            st.info("Mindestens 2 Szenarien benötigt für einen Vergleich.")
            return

        selected_scenarios = st.multiselect(
            "Szenarien auswählen (max. 6)",
            options=scenario_names,
            default=scenario_names[:2],
            max_selections=6,
            key="sc_compare_select"
        )

        if len(selected_scenarios) < 2:
            st.info("Bitte mindestens 2 Szenarien auswählen.")
            return

        # Daten laden
        scenario_data = {}
        for sc_name in selected_scenarios:
            cum_df = get_cumulative_cashflows_cached(conn_id, fund_id, sc_name)
            summary = get_cashflow_summary_cached(conn_id, fund_id, sc_name)
            scenario_data[sc_name] = {
                'cumulative': cum_df,
                'summary': summary,
            }

        # Metriken berechnen
        _render_metrics_table(scenario_data, currency)

        # Charts
        chart_tab1, chart_tab2 = st.tabs(["J-Curve Overlay", "Kumulativer Vergleich"])

        with chart_tab1:
            fig = _create_jcurve_overlay(scenario_data, fund_name, currency)
            if fig:
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.info("Keine Daten für J-Curve Overlay.")

        with chart_tab2:
            fig = _create_cumulative_comparison(scenario_data, fund_name, currency)
            if fig:
                st.pyplot(fig)
                plt.close(fig)
            else:
                st.info("Keine Daten für kumulativen Vergleich.")


def _compute_scenario_metrics(cum_df, summary):
    """Berechnet erweiterte Metriken für ein Szenario."""

    total_called = summary.get('total_called', 0)
    total_dist = summary.get('total_distributed', 0)
    net = summary.get('net_cashflow', 0)
    dpi = summary.get('dpi', 0)

    breakeven_quarter = None
    peak_negative = 0.0

    if not cum_df.empty and 'cumulative_net_cashflow' in cum_df.columns:
        cum_net = cum_df['cumulative_net_cashflow']

        # Peak Negative
        peak_negative = cum_net.min()

        # Breakeven: erster Nulldurchgang (von negativ zu positiv)
        for i in range(1, len(cum_net)):
            if cum_net.iloc[i - 1] < 0 and cum_net.iloc[i] >= 0:
                d = cum_df['date'].iloc[i]
                if hasattr(d, 'strftime'):
                    breakeven_quarter = d.strftime('%Y-Q') + str((d.month - 1) // 3 + 1)
                else:
                    breakeven_quarter = str(d)
                break

    return {
        'Total Calls': total_called,
        'Total Distributions': total_dist,
        'Netto': net,
        'DPI': dpi,
        'Breakeven': breakeven_quarter or '–',
        'Peak Negative': peak_negative,
    }


def _render_metrics_table(scenario_data, currency):
    """Rendert die Metrik-Vergleichstabelle."""

    rows = []
    for sc_name, data in scenario_data.items():
        metrics = _compute_scenario_metrics(data['cumulative'], data['summary'])
        metrics['Szenario'] = sc_name
        rows.append(metrics)

    if not rows:
        return

    df = pd.DataFrame(rows)
    # Spaltenreihenfolge
    cols = ['Szenario', 'Total Calls', 'Total Distributions', 'Netto',
            'DPI', 'Breakeven', 'Peak Negative']
    df = df[[c for c in cols if c in df.columns]]

    # Formatierung
    for col in ['Total Calls', 'Total Distributions', 'Netto', 'Peak Negative']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: f"{x:,.0f} {currency}")
    if 'DPI' in df.columns:
        df['DPI'] = df['DPI'].apply(lambda x: f"{x:.2f}x")

    st.markdown("**Metrik-Vergleich**")
    st.dataframe(df, hide_index=True, use_container_width=True)


def _create_jcurve_overlay(scenario_data, fund_name, currency):
    """Erstellt J-Curve Overlay mit einer Linie pro Szenario."""

    fig, ax = plt.subplots(figsize=(12, 6))
    has_data = False

    for idx, (sc_name, data) in enumerate(scenario_data.items()):
        cum_df = data['cumulative']
        if cum_df.empty:
            continue
        has_data = True
        color = SCENARIO_COLORS[idx % len(SCENARIO_COLORS)]
        ax.plot(
            cum_df['date'], cum_df['cumulative_net_cashflow'],
            color=color, linewidth=2, label=sc_name, zorder=3
        )

    if not has_data:
        plt.close(fig)
        return None

    ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.8)
    ax.set_title(f'J-Curve Overlay: {fund_name}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kumulierter Netto-Cashflow ({currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def _create_cumulative_comparison(scenario_data, fund_name, currency):
    """Erstellt kumulativen Vergleich: Calls (gestrichelt) + Dists (durchgezogen)."""

    fig, ax = plt.subplots(figsize=(12, 6))
    has_data = False

    for idx, (sc_name, data) in enumerate(scenario_data.items()):
        cum_df = data['cumulative']
        if cum_df.empty:
            continue
        has_data = True
        color = SCENARIO_COLORS[idx % len(SCENARIO_COLORS)]

        # Kumulative Werte
        cum_calls = cum_df['capital_calls'].cumsum().abs()
        cum_dists = cum_df['distributions'].cumsum()

        ax.plot(
            cum_df['date'], cum_calls,
            color=color, linewidth=1.5, linestyle='--',
            label=f'{sc_name} (Calls)'
        )
        ax.plot(
            cum_df['date'], cum_dists,
            color=color, linewidth=2, linestyle='-',
            label=f'{sc_name} (Dists)'
        )

    if not has_data:
        plt.close(fig)
        return None

    ax.set_title(f'Kumulativer Vergleich: {fund_name}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kumulierter Betrag ({currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best', fontsize=8)
    plt.tight_layout()

    return fig

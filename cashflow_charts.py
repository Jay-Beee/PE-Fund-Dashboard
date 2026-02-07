"""
Cashflow Planning Tool — Chart-Funktionen

Vier Charts:
  1. J-Curve (kumulierter Netto-Cashflow)
  2. Cashflow-Balkendiagramm (Kapitalabrufe vs. Ausschüttungen pro Periode)
  3. Net Cashflow Timeline (Flächendiagramm kumulativ)
  4. Forecast Preview (Mini-Balkendiagramm für Vorschau)
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def create_j_curve_chart(cumulative_df, fund_name, currency='EUR'):
    """Erstellt den J-Curve Chart (kumulierter Netto-Cashflow).

    Args:
        cumulative_df: DataFrame von get_cumulative_cashflows_cached
        fund_name: Name des Fonds
        currency: Währung für Achsenbeschriftung

    Returns:
        matplotlib Figure oder None
    """
    if cumulative_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))

    dates = cumulative_df['date']
    cum_net = cumulative_df['cumulative_net_cashflow']

    # Ist vs. Plan aufteilen
    if 'is_actual' in cumulative_df.columns:
        actual_mask = cumulative_df['is_actual'].astype(bool)
        if actual_mask.any():
            ax.plot(dates[actual_mask], cum_net[actual_mask],
                    color='#1a237e', linewidth=2, label='Ist', zorder=3)
        if (~actual_mask).any():
            ax.plot(dates[~actual_mask], cum_net[~actual_mask],
                    color='#1a237e', linewidth=2, linestyle='--',
                    label='Plan', zorder=3)
    else:
        ax.plot(dates, cum_net, color='#1a237e', linewidth=2, zorder=3)

    # Grün über Null, rot unter Null
    ax.fill_between(dates, cum_net, 0,
                    where=(cum_net >= 0), color='#4caf50', alpha=0.3,
                    interpolate=True, label='Positiv')
    ax.fill_between(dates, cum_net, 0,
                    where=(cum_net < 0), color='#f44336', alpha=0.3,
                    interpolate=True, label='Negativ')

    # Nulllinie
    ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.8)

    ax.set_title(f'J-Curve: {fund_name}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kumulierter Netto-Cashflow ({currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_cashflow_bar_chart(periodic_df, fund_name, currency='EUR'):
    """Erstellt das Cashflow-Balkendiagramm.

    Args:
        periodic_df: DataFrame von get_periodic_cashflows_cached
        fund_name: Name des Fonds
        currency: Währung für Achsenbeschriftung

    Returns:
        matplotlib Figure oder None
    """
    if periodic_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    x = np.arange(len(periodic_df))
    width = 0.35

    # capital_calls sind bereits negativ, für Anzeige abs() nehmen
    calls_abs = periodic_df['capital_calls'].abs()
    dists = periodic_df['distributions']

    ax.bar(x - width / 2, -calls_abs, width, label='Kapitalabrufe',
           color='#ef5350', alpha=0.85)
    ax.bar(x + width / 2, dists, width, label='Ausschüttungen',
           color='#66bb6a', alpha=0.85)

    # Netto-Linie
    ax.plot(x, periodic_df['net_cashflow'], color='black', linewidth=2,
            marker='o', markersize=5, label='Netto-Cashflow', zorder=3)

    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)

    ax.set_title(f'Cashflows: {fund_name}', fontsize=14, fontweight='bold')
    ax.set_xlabel('Periode')
    ax.set_ylabel(f'Betrag ({currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(periodic_df['period_label'], rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_net_cashflow_timeline(cumulative_df, fund_name,
                                 commitment_amount=None, currency='EUR'):
    """Erstellt das Net Cashflow Timeline Flächendiagramm.

    Args:
        cumulative_df: DataFrame von get_cumulative_cashflows_cached
        fund_name: Name des Fonds
        commitment_amount: optionaler Commitment-Betrag für horizontale Linie
        currency: Währung für Achsenbeschriftung

    Returns:
        matplotlib Figure oder None
    """
    if cumulative_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))

    dates = cumulative_df['date']

    # Kumulative Werte berechnen
    cum_calls = cumulative_df['capital_calls'].cumsum().abs()
    cum_dists = cumulative_df['distributions'].cumsum()

    ax.fill_between(dates, cum_calls, alpha=0.4, color='#ef5350',
                    label='Kumulative Kapitalabrufe')
    ax.fill_between(dates, cum_dists, alpha=0.4, color='#66bb6a',
                    label='Kumulative Ausschüttungen')

    ax.plot(dates, cum_calls, color='#c62828', linewidth=1.5)
    ax.plot(dates, cum_dists, color='#2e7d32', linewidth=1.5)

    if commitment_amount and commitment_amount > 0:
        ax.axhline(y=commitment_amount, color='#1565c0', linestyle='--',
                   linewidth=1.5, label=f'Commitment ({commitment_amount:,.0f})')

    ax.set_title(f'Cashflow Timeline: {fund_name}', fontsize=14,
                 fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kumulierter Betrag ({currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_forecast_preview_chart(forecast, currency='EUR'):
    """Mini-Balkendiagramm für Forecast-Vorschau (aggregiert nach Jahr).

    Args:
        forecast: list[dict] mit {date, type, amount}
        currency: Währung für Achsenbeschriftung

    Returns:
        matplotlib Figure oder None
    """
    if not forecast:
        return None

    # Aggregiere nach Jahr
    calls_by_year = {}
    dists_by_year = {}
    outflow_types = {'capital_call', 'management_fee', 'carried_interest'}

    for entry in forecast:
        year = entry['date'].year if hasattr(entry['date'], 'year') else entry['date']
        amt = entry['amount']
        if entry['type'] in outflow_types:
            calls_by_year[year] = calls_by_year.get(year, 0) + amt
        else:
            dists_by_year[year] = dists_by_year.get(year, 0) + amt

    all_years = sorted(set(list(calls_by_year.keys()) + list(dists_by_year.keys())))
    if not all_years:
        return None

    calls = [calls_by_year.get(y, 0) for y in all_years]
    dists = [dists_by_year.get(y, 0) for y in all_years]
    labels = [str(y) for y in all_years]

    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(all_years))
    width = 0.35

    ax.bar(x - width / 2, [-c for c in calls], width, label='Kapitalabrufe',
           color='#ef5350', alpha=0.85)
    ax.bar(x + width / 2, dists, width, label='Ausschüttungen',
           color='#66bb6a', alpha=0.85)

    # Netto-Linie
    net = [d - c for c, d in zip(calls, dists)]
    ax.plot(x, net, color='black', linewidth=1.5, marker='o', markersize=4,
            label='Netto', zorder=3)

    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax.set_title('Forecast-Vorschau', fontsize=12, fontweight='bold')
    ax.set_ylabel(f'Betrag ({currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='best', fontsize=9)
    plt.tight_layout()

    return fig

"""
Cashflow Planning Tool — Liquiditätsplanung Charts

Charts:
  1. Funding-Gap Balkendiagramm (Calls vs. Distributions pro Periode)
  2. Cash-Reserve Liniendiagramm (Kontostand über Zeit)
  3. Funding-Gap Wasserfall (kumulativer Funding-Bedarf)
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def create_funding_gap_chart(funding_gap_df, base_currency='EUR'):
    """Balkendiagramm: Calls (rot) vs. Distributions (grün) pro Periode.
    Linie: Netto-Funding-Bedarf.
    """
    if funding_gap_df is None or funding_gap_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    x = np.arange(len(funding_gap_df))
    width = 0.35

    calls = funding_gap_df['expected_calls'].abs()
    dists = funding_gap_df['expected_distributions']

    ax.bar(x - width / 2, -calls, width, label='Erwartete Abrufe',
           color='#ef5350', alpha=0.85)
    ax.bar(x + width / 2, dists, width, label='Erwartete Ausschüttungen',
           color='#66bb6a', alpha=0.85)

    # Netto-Linie
    ax.plot(x, funding_gap_df['net_funding_need'], color='#1565c0',
            linewidth=2, marker='o', markersize=5, label='Netto-Funding-Bedarf',
            zorder=3)

    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)

    ax.set_title('Funding-Gap Analyse', fontsize=14, fontweight='bold')
    ax.set_xlabel('Periode')
    ax.set_ylabel(f'Betrag ({base_currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(funding_gap_df['period_label'], rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_cash_reserve_chart(cash_reserve_df, base_currency='EUR'):
    """Liniendiagramm: Kontostand über Zeit.
    Rote Zone unter 0. Grüne Zone über 0.
    Horizontale Warnlinie bei 0.
    """
    if cash_reserve_df is None or cash_reserve_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    dates = pd.to_datetime(cash_reserve_df['date'])
    balance = cash_reserve_df['balance']

    ax.plot(dates, balance, color='#1565c0', linewidth=2, label='Kontostand',
            zorder=3)

    # Farbflächen
    ax.fill_between(dates, balance, 0,
                    where=(balance >= 0), color='#4caf50', alpha=0.2,
                    interpolate=True, label='Deckung')
    ax.fill_between(dates, balance, 0,
                    where=(balance < 0), color='#f44336', alpha=0.3,
                    interpolate=True, label='Unterdeckung')

    # Nulllinie
    ax.axhline(y=0, color='#c62828', linestyle='--', linewidth=1.5,
               label='Null-Linie', zorder=2)

    ax.set_title('Cash-Reserve Simulation', fontsize=14, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kontostand ({base_currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_funding_gap_waterfall_chart(funding_gap_df, base_currency='EUR'):
    """Wasserfall-Diagramm: kumulativer Funding-Bedarf."""
    if funding_gap_df is None or funding_gap_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    labels = funding_gap_df['period_label'].tolist()
    values = funding_gap_df['net_funding_need'].tolist()
    cumulative = funding_gap_df['cumulative_funding_need'].tolist()

    x = np.arange(len(labels))

    # Wasserfall-Balken
    bottoms = []
    for i in range(len(cumulative)):
        if i == 0:
            bottoms.append(0)
        else:
            bottoms.append(cumulative[i - 1])

    bar_colors = ['#ef5350' if v < 0 else '#66bb6a' for v in values]

    ax.bar(x, values, bottom=bottoms, color=bar_colors, alpha=0.85,
           edgecolor='white', linewidth=0.5)

    # Kumulative Linie
    ax.plot(x, cumulative, color='#1a237e', linewidth=2, marker='D',
            markersize=5, label='Kumulativer Bedarf', zorder=3)

    # Verbindungslinien zwischen Balken
    for i in range(len(x) - 1):
        ax.plot([x[i] + 0.4, x[i + 1] - 0.4],
                [cumulative[i], cumulative[i]],
                color='gray', linestyle=':', linewidth=0.8)

    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)

    ax.set_title('Kumulativer Funding-Bedarf (Wasserfall)', fontsize=14,
                 fontweight='bold')
    ax.set_xlabel('Periode')
    ax.set_ylabel(f'Betrag ({base_currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='best')
    plt.tight_layout()

    return fig

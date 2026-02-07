"""
Cashflow Planning Tool — Portfolio-Charts

Charts für Portfolio-Aggregation, Fonds-Beiträge und Ist vs. Forecast.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def create_portfolio_j_curve_chart(cumulative_df, base_currency):
    """Aggregierte J-Curve über alle ausgewählten Fonds."""
    if cumulative_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))

    dates = cumulative_df['date']
    cum_net = cumulative_df['cumulative_net_cashflow']

    ax.plot(dates, cum_net, color='#1a237e', linewidth=2, zorder=3)

    ax.fill_between(dates, cum_net, 0,
                    where=(cum_net >= 0), color='#4caf50', alpha=0.3,
                    interpolate=True, label='Positiv')
    ax.fill_between(dates, cum_net, 0,
                    where=(cum_net < 0), color='#f44336', alpha=0.3,
                    interpolate=True, label='Negativ')

    ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.8)
    ax.set_title('Portfolio J-Curve (aggregiert)', fontsize=14, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kumulierter Netto-Cashflow ({base_currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_portfolio_bar_chart(periodic_df, base_currency):
    """Aggregiertes Cashflow-Balkendiagramm."""
    if periodic_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    x = np.arange(len(periodic_df))
    width = 0.35

    calls_abs = periodic_df['capital_calls'].abs()
    dists = periodic_df['distributions']

    ax.bar(x - width / 2, -calls_abs, width, label='Kapitalabrufe',
           color='#ef5350', alpha=0.85)
    ax.bar(x + width / 2, dists, width, label='Ausschüttungen',
           color='#66bb6a', alpha=0.85)

    ax.plot(x, periodic_df['net_cashflow'], color='black', linewidth=2,
            marker='o', markersize=5, label='Netto-Cashflow', zorder=3)

    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax.set_title('Portfolio Cashflows (aggregiert)', fontsize=14, fontweight='bold')
    ax.set_xlabel('Periode')
    ax.set_ylabel(f'Betrag ({base_currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(periodic_df['period_label'], rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_portfolio_fund_contribution_chart(fund_breakdown_df, base_currency):
    """Gestapeltes Balkendiagramm: Beitrag jedes Fonds zu Calls + Distributions."""
    if fund_breakdown_df.empty:
        return None

    valid_df = fund_breakdown_df.dropna(subset=['called_base', 'distributed_base'])
    if valid_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    fund_names = valid_df['fund_name'].tolist()
    x = np.arange(len(fund_names))
    width = 0.35

    calls = valid_df['called_base'].values
    dists = valid_df['distributed_base'].values

    ax.bar(x - width / 2, -calls, width, label='Kapitalabrufe',
           color='#ef5350', alpha=0.85)
    ax.bar(x + width / 2, dists, width, label='Ausschüttungen',
           color='#66bb6a', alpha=0.85)

    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax.set_title(f'Fonds-Beiträge ({base_currency})', fontsize=14, fontweight='bold')
    ax.set_xlabel('Fonds')
    ax.set_ylabel(f'Betrag ({base_currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(fund_names, rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_actual_vs_forecast_chart(actual_df, forecast_df, title, currency):
    """Overlay: Ist (durchgezogen) vs. Forecast (gestrichelt), Abweichung schattiert."""
    if actual_df.empty and forecast_df.empty:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))
    has_data = False

    if not actual_df.empty and 'cumulative_net_cashflow' in actual_df.columns:
        ax.plot(actual_df['date'], actual_df['cumulative_net_cashflow'],
                color='#1a237e', linewidth=2, label='Ist', zorder=3)
        has_data = True

    if not forecast_df.empty and 'cumulative_net_cashflow' in forecast_df.columns:
        ax.plot(forecast_df['date'], forecast_df['cumulative_net_cashflow'],
                color='#c62828', linewidth=2, linestyle='--', label='Forecast', zorder=3)
        has_data = True

    # Abweichung schattieren wenn beide vorhanden
    if not actual_df.empty and not forecast_df.empty:
        # Auf gemeinsame Daten mergen
        merged = pd.merge(
            actual_df[['date', 'cumulative_net_cashflow']].rename(columns={'cumulative_net_cashflow': 'actual'}),
            forecast_df[['date', 'cumulative_net_cashflow']].rename(columns={'cumulative_net_cashflow': 'forecast'}),
            on='date', how='inner'
        )
        if not merged.empty:
            ax.fill_between(merged['date'], merged['actual'], merged['forecast'],
                            alpha=0.15, color='#ff9800', label='Abweichung')

    if not has_data:
        plt.close(fig)
        return None

    ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.8)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel(f'Kumulierter Netto-Cashflow ({currency})')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    plt.tight_layout()

    return fig


def create_deviation_chart(deviation_df, title, currency):
    """Balkendiagramm: periodische Abweichungen (grün=positiv, rot=negativ)."""
    if deviation_df.empty or 'deviation' not in deviation_df.columns:
        return None

    fig, ax = plt.subplots(figsize=(14, 6))

    x = np.arange(len(deviation_df))
    deviations = deviation_df['deviation'].values
    colors = ['#4caf50' if d >= 0 else '#f44336' for d in deviations]

    ax.bar(x, deviations, color=colors, alpha=0.85)
    ax.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)

    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.set_xlabel('Periode')
    ax.set_ylabel(f'Abweichung ({currency})')
    ax.set_xticks(x)
    ax.set_xticklabels(deviation_df['period'].values, rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()

    return fig

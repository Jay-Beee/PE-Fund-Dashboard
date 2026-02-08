"""
Cashflow Planning Tool — Gecachte Queries für die UI

Alle Funktionen nutzen @st.cache_data(ttl=300) mit _conn_id als
Cache-Buster (Unterstrich = wird nicht gehasht).
"""

import pandas as pd
import numpy as np
import streamlit as st
from datetime import date, timedelta
from database import get_connection
from cashflow_db import get_cashflows_for_fund, get_all_scenarios, get_exchange_rate_with_inverse

# Typ-Richtung für Vorzeichen in Berechnungen
OUTFLOW_TYPES = {'capital_call', 'management_fee', 'carried_interest'}
INFLOW_TYPES = {'distribution', 'clawback'}


@st.cache_data(ttl=300)
def get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name=None):
    """Holt Cashflows als DataFrame"""
    with get_connection() as conn:
        rows = get_cashflows_for_fund(conn, fund_id, scenario_name)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df


@st.cache_data(ttl=300)
def get_cumulative_cashflows_cached(_conn_id, fund_id, scenario_name='base'):
    """Berechnet kumulative Cashflows für J-Curve.

    Returns DataFrame mit: date, capital_calls, distributions, net_cashflow,
                           cumulative_net_cashflow, is_actual
    """
    df = get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name)
    if df.empty:
        return pd.DataFrame()

    # Signed amounts berechnen
    df = df.copy()
    df['signed_amount'] = df.apply(
        lambda r: -r['amount'] if r['type'] in OUTFLOW_TYPES else r['amount'],
        axis=1
    )

    # Pro Datum aggregieren
    grouped = df.groupby('date').agg(
        capital_calls=('signed_amount', lambda x: x[x < 0].sum()),
        distributions=('signed_amount', lambda x: x[x > 0].sum()),
        is_actual=('is_actual', 'all'),
    ).reset_index()

    grouped['net_cashflow'] = grouped['capital_calls'] + grouped['distributions']
    grouped = grouped.sort_values('date').reset_index(drop=True)
    grouped['cumulative_net_cashflow'] = grouped['net_cashflow'].cumsum()

    return grouped


@st.cache_data(ttl=300)
def get_periodic_cashflows_cached(_conn_id, fund_id, period='quarter',
                                  scenario_name='base'):
    """Aggregiert Cashflows pro Quartal oder Jahr für Balkendiagramm.

    Returns DataFrame mit: period_label, capital_calls (negativ), distributions (positiv),
                           net_cashflow
    """
    df = get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name)
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['signed_amount'] = df.apply(
        lambda r: -r['amount'] if r['type'] in OUTFLOW_TYPES else r['amount'],
        axis=1
    )

    if period == 'quarter':
        df['period_label'] = df['date'].dt.to_period('Q').astype(str)
    else:
        df['period_label'] = df['date'].dt.year.astype(str)

    grouped = df.groupby('period_label').agg(
        capital_calls=('signed_amount', lambda x: x[x < 0].sum()),
        distributions=('signed_amount', lambda x: x[x > 0].sum()),
    ).reset_index()

    grouped['net_cashflow'] = grouped['capital_calls'] + grouped['distributions']
    grouped = grouped.sort_values('period_label').reset_index(drop=True)

    return grouped


@st.cache_data(ttl=300)
def get_fund_commitment_info_cached(_conn_id, fund_id):
    """Holt Commitment-Infos für einen Fonds"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
            SELECT fund_name, currency, commitment_amount, unfunded_amount,
                   commitment_date, expected_end_date
            FROM funds WHERE fund_id = %s
            """, (fund_id,))
            row = cursor.fetchone()
            if not row:
                return {}
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))


@st.cache_data(ttl=300)
def get_cashflow_summary_cached(_conn_id, fund_id, scenario_name='base'):
    """Berechnet Summary-Metriken: total_called, total_distributed, net_cashflow, dpi"""
    df = get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name)
    if df.empty:
        return {
            'total_called': 0.0,
            'total_distributed': 0.0,
            'net_cashflow': 0.0,
            'dpi': 0.0,
        }

    total_called = df.loc[df['type'].isin(OUTFLOW_TYPES), 'amount'].sum()
    total_distributed = df.loc[df['type'].isin(INFLOW_TYPES), 'amount'].sum()
    net = total_distributed - total_called
    dpi = total_distributed / total_called if total_called > 0 else 0.0

    return {
        'total_called': total_called,
        'total_distributed': total_distributed,
        'net_cashflow': net,
        'dpi': dpi,
    }


@st.cache_data(ttl=300)
def get_historical_pacing_cached(_conn_id, fund_id, scenario_name='base'):
    """Berechnet normalisierte Pacing-Kurven aus Ist-Daten.

    Returns:
        dict mit 'call_pacing': {year_offset: pct}, 'dist_pacing': {year_offset: pct},
                 'commitment': float, 'first_year': int
    """
    df = get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name)
    if df.empty:
        return {'call_pacing': {}, 'dist_pacing': {}, 'commitment': 0, 'first_year': None}

    # Nur Ist-Daten
    actual_df = df[df['is_actual'] == True].copy()
    if actual_df.empty:
        return {'call_pacing': {}, 'dist_pacing': {}, 'commitment': 0, 'first_year': None}

    # Commitment aus Fund-Info holen
    commit_info = get_fund_commitment_info_cached(_conn_id, fund_id)
    commitment = commit_info.get('commitment_amount') or 0
    if commitment <= 0:
        return {'call_pacing': {}, 'dist_pacing': {}, 'commitment': 0, 'first_year': None}

    first_year = actual_df['date'].dt.year.min()
    actual_df['year_offset'] = actual_df['date'].dt.year - first_year

    call_pacing = {}
    dist_pacing = {}

    for year_offset, group in actual_df.groupby('year_offset'):
        calls = group.loc[group['type'].isin(OUTFLOW_TYPES), 'amount'].sum()
        dists = group.loc[group['type'].isin(INFLOW_TYPES), 'amount'].sum()
        if calls > 0:
            call_pacing[int(year_offset)] = round(calls / commitment, 4)
        if dists > 0:
            dist_pacing[int(year_offset)] = round(dists / commitment, 4)

    return {
        'call_pacing': call_pacing,
        'dist_pacing': dist_pacing,
        'commitment': commitment,
        'first_year': int(first_year),
    }


@st.cache_data(ttl=300)
def get_scenarios_cached(_conn_id):
    """Holt alle Szenarien als list[dict]"""
    with get_connection() as conn:
        return get_all_scenarios(conn)


@st.cache_data(ttl=300)
def get_all_funds_for_cashflow_cached(_conn_id):
    """Holt alle Fonds mit Cashflow-relevanten Feldern"""
    with get_connection() as conn:
        query = """
        SELECT fund_id, fund_name, currency, commitment_amount,
               unfunded_amount, expected_end_date
        FROM funds ORDER BY fund_name
        """
        return pd.read_sql_query(query, conn)


# ============================================================================
# FX-KONVERSION
# ============================================================================

@st.cache_data(ttl=300)
def get_cashflows_in_base_currency_cached(_conn_id, fund_id, base_currency, scenario_name=None):
    """Holt Cashflows für einen Fonds, konvertiert in Basiswährung.

    Adds columns: original_currency, original_amount, fx_rate, amount_base.
    Wenn gleiche Währung → fx_rate=1.0.
    Wenn kein Rate gefunden → fx_rate=None, amount_base=None (Warning-Flag).
    """
    df = get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name)
    if df.empty:
        return df

    # Fund-Währung holen
    commit_info = get_fund_commitment_info_cached(_conn_id, fund_id)
    fund_currency = commit_info.get('currency') or 'EUR'

    df = df.copy()
    df['original_currency'] = fund_currency
    df['original_amount'] = df['amount']

    if fund_currency == base_currency:
        df['fx_rate'] = 1.0
        df['amount_base'] = df['amount']
    else:
        with get_connection() as conn:
            fx_rates = []
            for _, row in df.iterrows():
                rate_date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
                rate = get_exchange_rate_with_inverse(conn, fund_currency, base_currency, rate_date)
                fx_rates.append(rate)
            df['fx_rate'] = fx_rates
            df['amount_base'] = df.apply(
                lambda r: r['amount'] * r['fx_rate'] if r['fx_rate'] is not None else None,
                axis=1
            )

    return df


@st.cache_data(ttl=300)
def get_cashflows_multi_fund_base_currency_cached(_conn_id, fund_ids, base_currency, scenario_name='base'):
    """Holt Cashflows für MEHRERE Fonds, alle in Basiswährung konvertiert."""
    if not fund_ids:
        return pd.DataFrame()

    all_dfs = []
    for fid in fund_ids:
        df = get_cashflows_in_base_currency_cached(_conn_id, fid, base_currency, scenario_name)
        if not df.empty:
            commit_info = get_fund_commitment_info_cached(_conn_id, fid)
            df = df.copy()
            df['fund_name'] = commit_info.get('fund_name', f'Fund {fid}')
            all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    return pd.concat(all_dfs, ignore_index=True)


# ============================================================================
# PORTFOLIO-AGGREGATION
# ============================================================================

@st.cache_data(ttl=300)
def get_portfolio_cumulative_cashflows_cached(_conn_id, fund_ids, base_currency, scenario_name='base'):
    """Aggregiert kumulative Cashflows über ausgewählte Fonds in Basiswährung."""
    multi_df = get_cashflows_multi_fund_base_currency_cached(
        _conn_id, fund_ids, base_currency, scenario_name
    )
    if multi_df.empty or 'amount_base' not in multi_df.columns:
        return pd.DataFrame()

    # Nur Zeilen mit gültiger Konversion
    valid_df = multi_df.dropna(subset=['amount_base']).copy()
    if valid_df.empty:
        return pd.DataFrame()

    valid_df['signed_amount'] = valid_df.apply(
        lambda r: -r['amount_base'] if r['type'] in OUTFLOW_TYPES else r['amount_base'],
        axis=1
    )

    grouped = valid_df.groupby('date').agg(
        capital_calls=('signed_amount', lambda x: x[x < 0].sum()),
        distributions=('signed_amount', lambda x: x[x > 0].sum()),
    ).reset_index()

    grouped['net_cashflow'] = grouped['capital_calls'] + grouped['distributions']
    grouped = grouped.sort_values('date').reset_index(drop=True)
    grouped['cumulative_net_cashflow'] = grouped['net_cashflow'].cumsum()

    return grouped


@st.cache_data(ttl=300)
def get_portfolio_periodic_cashflows_cached(_conn_id, fund_ids, base_currency, period='quarter', scenario_name='base'):
    """Aggregiert periodische Cashflows über ausgewählte Fonds in Basiswährung."""
    multi_df = get_cashflows_multi_fund_base_currency_cached(
        _conn_id, fund_ids, base_currency, scenario_name
    )
    if multi_df.empty or 'amount_base' not in multi_df.columns:
        return pd.DataFrame()

    valid_df = multi_df.dropna(subset=['amount_base']).copy()
    if valid_df.empty:
        return pd.DataFrame()

    valid_df['signed_amount'] = valid_df.apply(
        lambda r: -r['amount_base'] if r['type'] in OUTFLOW_TYPES else r['amount_base'],
        axis=1
    )

    if period == 'quarter':
        valid_df['period_label'] = valid_df['date'].dt.to_period('Q').astype(str)
    else:
        valid_df['period_label'] = valid_df['date'].dt.year.astype(str)

    grouped = valid_df.groupby('period_label').agg(
        capital_calls=('signed_amount', lambda x: x[x < 0].sum()),
        distributions=('signed_amount', lambda x: x[x > 0].sum()),
    ).reset_index()

    grouped['net_cashflow'] = grouped['capital_calls'] + grouped['distributions']
    grouped = grouped.sort_values('period_label').reset_index(drop=True)

    return grouped


@st.cache_data(ttl=300)
def get_portfolio_summary_cached(_conn_id, fund_ids, base_currency, scenario_name='base'):
    """Portfolio-Metriken: total_commitment, total_called, total_distributed, etc."""
    multi_df = get_cashflows_multi_fund_base_currency_cached(
        _conn_id, fund_ids, base_currency, scenario_name
    )

    # FX-Warnungen sammeln
    fx_warnings = []
    if not multi_df.empty and 'amount_base' in multi_df.columns:
        missing_fx = multi_df[multi_df['amount_base'].isna()]
        if not missing_fx.empty:
            for _, row in missing_fx.drop_duplicates(subset=['fund_name', 'original_currency']).iterrows():
                fx_warnings.append(f"{row['fund_name']} ({row['original_currency']}→{base_currency})")

    valid_df = multi_df.dropna(subset=['amount_base']) if not multi_df.empty and 'amount_base' in multi_df.columns else pd.DataFrame()

    total_called = 0.0
    total_distributed = 0.0
    if not valid_df.empty:
        total_called = valid_df.loc[valid_df['type'].isin(OUTFLOW_TYPES), 'amount_base'].sum()
        total_distributed = valid_df.loc[valid_df['type'].isin(INFLOW_TYPES), 'amount_base'].sum()

    # Commitment in Basiswährung berechnen
    total_commitment = 0.0
    total_unfunded = 0.0
    for fid in fund_ids:
        info = get_fund_commitment_info_cached(_conn_id, fid)
        commit = info.get('commitment_amount') or 0
        unfunded = info.get('unfunded_amount') or 0
        fund_ccy = info.get('currency') or 'EUR'
        if fund_ccy == base_currency:
            total_commitment += commit
            total_unfunded += unfunded
        else:
            with get_connection() as conn:
                rate = get_exchange_rate_with_inverse(conn, fund_ccy, base_currency, date.today())
            if rate is not None:
                total_commitment += commit * rate
                total_unfunded += unfunded * rate

    net = total_distributed - total_called
    dpi = total_distributed / total_called if total_called > 0 else 0.0

    return {
        'total_commitment': total_commitment,
        'total_called': total_called,
        'total_distributed': total_distributed,
        'total_unfunded': total_unfunded,
        'net_cashflow': net,
        'portfolio_dpi': dpi,
        'num_funds': len(fund_ids),
        'fx_warnings': fx_warnings,
    }


@st.cache_data(ttl=300)
def get_portfolio_fund_breakdown_cached(_conn_id, fund_ids, base_currency, scenario_name='base'):
    """Pro-Fonds Aufschlüsselung in Basiswährung."""
    rows = []
    for fid in fund_ids:
        info = get_fund_commitment_info_cached(_conn_id, fid)
        fund_name = info.get('fund_name', f'Fund {fid}')
        fund_ccy = info.get('currency') or 'EUR'
        commit = info.get('commitment_amount') or 0

        # Commitment konvertieren
        if fund_ccy == base_currency:
            fx = 1.0
        else:
            with get_connection() as conn:
                fx = get_exchange_rate_with_inverse(conn, fund_ccy, base_currency, date.today())
            if fx is None:
                fx = None

        commit_base = commit * fx if fx is not None else None

        # Cashflow-Summary
        summary = get_cashflow_summary_cached(_conn_id, fid, scenario_name)
        called = summary.get('total_called', 0)
        distributed = summary.get('total_distributed', 0)

        called_base = called * fx if fx is not None else None
        distributed_base = distributed * fx if fx is not None else None
        net_base = (distributed_base - called_base) if called_base is not None and distributed_base is not None else None
        dpi = summary.get('dpi', 0)

        rows.append({
            'fund_name': fund_name,
            'currency': fund_ccy,
            'commitment_base': commit_base,
            'called_base': called_base,
            'distributed_base': distributed_base,
            'net_base': net_base,
            'dpi': dpi,
        })

    return pd.DataFrame(rows)


# ============================================================================
# IST VS. FORECAST
# ============================================================================

@st.cache_data(ttl=300)
def get_actual_vs_forecast_cached(_conn_id, fund_id, scenario_name='base'):
    """Splittet Cashflows in Ist und Forecast, berechnet Abweichungen."""
    df = get_cashflows_for_fund_cached(_conn_id, fund_id, scenario_name)
    if df.empty:
        return {'actual_cumulative': pd.DataFrame(), 'forecast_cumulative': pd.DataFrame(),
                'periodic_deviation': pd.DataFrame(),
                'metrics': {'tracking_error': 0, 'mean_deviation': 0,
                            'pct_calls_realized': 0, 'pct_dists_realized': 0}}

    df = df.copy()
    df['signed_amount'] = df.apply(
        lambda r: -r['amount'] if r['type'] in OUTFLOW_TYPES else r['amount'],
        axis=1
    )

    actual_df = df[df['is_actual'] == True].copy()
    forecast_df = df[df['is_actual'] == False].copy()

    def _build_cumulative(sub_df):
        if sub_df.empty:
            return pd.DataFrame()
        grouped = sub_df.groupby('date').agg(
            capital_calls=('signed_amount', lambda x: x[x < 0].sum()),
            distributions=('signed_amount', lambda x: x[x > 0].sum()),
        ).reset_index()
        grouped['net_cashflow'] = grouped['capital_calls'] + grouped['distributions']
        grouped = grouped.sort_values('date').reset_index(drop=True)
        grouped['cumulative_net_cashflow'] = grouped['net_cashflow'].cumsum()
        return grouped

    actual_cum = _build_cumulative(actual_df)
    forecast_cum = _build_cumulative(forecast_df)

    # Periodische Abweichung (quartalsweise)
    def _periodic(sub_df):
        if sub_df.empty:
            return pd.DataFrame()
        sub_df = sub_df.copy()
        sub_df['period'] = sub_df['date'].dt.to_period('Q').astype(str)
        return sub_df.groupby('period').agg(
            calls=('signed_amount', lambda x: x[x < 0].sum()),
            dists=('signed_amount', lambda x: x[x > 0].sum()),
            net=('signed_amount', 'sum'),
        ).reset_index()

    actual_per = _periodic(actual_df)
    forecast_per = _periodic(forecast_df)

    deviation_df = pd.DataFrame()
    if not actual_per.empty and not forecast_per.empty:
        merged = actual_per.merge(forecast_per, on='period', how='outer', suffixes=('_actual', '_forecast'))
        merged = merged.fillna(0).sort_values('period').reset_index(drop=True)
        merged['deviation'] = merged['net_actual'] - merged['net_forecast']
        deviation_df = merged[['period', 'net_actual', 'net_forecast', 'deviation']]

    # Metriken
    actual_calls_total = actual_df.loc[actual_df['type'].isin(OUTFLOW_TYPES), 'amount'].sum() if not actual_df.empty else 0
    forecast_calls_total = forecast_df.loc[forecast_df['type'].isin(OUTFLOW_TYPES), 'amount'].sum() if not forecast_df.empty else 0
    actual_dists_total = actual_df.loc[actual_df['type'].isin(INFLOW_TYPES), 'amount'].sum() if not actual_df.empty else 0
    forecast_dists_total = forecast_df.loc[forecast_df['type'].isin(INFLOW_TYPES), 'amount'].sum() if not forecast_df.empty else 0

    pct_calls = (actual_calls_total / forecast_calls_total * 100) if forecast_calls_total > 0 else 0
    pct_dists = (actual_dists_total / forecast_dists_total * 100) if forecast_dists_total > 0 else 0

    tracking_error = 0.0
    mean_dev = 0.0
    if not deviation_df.empty:
        tracking_error = deviation_df['deviation'].std()
        mean_dev = deviation_df['deviation'].mean()

    return {
        'actual_cumulative': actual_cum,
        'forecast_cumulative': forecast_cum,
        'periodic_deviation': deviation_df,
        'metrics': {
            'tracking_error': tracking_error,
            'mean_deviation': mean_dev,
            'pct_calls_realized': pct_calls,
            'pct_dists_realized': pct_dists,
        }
    }


@st.cache_data(ttl=300)
def get_portfolio_actual_vs_forecast_cached(_conn_id, fund_ids, base_currency, scenario_name='base'):
    """Aggregierte Ist vs. Forecast Analyse über mehrere Fonds."""
    multi_df = get_cashflows_multi_fund_base_currency_cached(
        _conn_id, fund_ids, base_currency, scenario_name
    )
    if multi_df.empty or 'amount_base' not in multi_df.columns:
        return {'actual_cumulative': pd.DataFrame(), 'forecast_cumulative': pd.DataFrame(),
                'periodic_deviation': pd.DataFrame(),
                'metrics': {'tracking_error': 0, 'mean_deviation': 0,
                            'pct_calls_realized': 0, 'pct_dists_realized': 0}}

    valid_df = multi_df.dropna(subset=['amount_base']).copy()
    if valid_df.empty:
        return {'actual_cumulative': pd.DataFrame(), 'forecast_cumulative': pd.DataFrame(),
                'periodic_deviation': pd.DataFrame(),
                'metrics': {'tracking_error': 0, 'mean_deviation': 0,
                            'pct_calls_realized': 0, 'pct_dists_realized': 0}}

    valid_df['signed_amount'] = valid_df.apply(
        lambda r: -r['amount_base'] if r['type'] in OUTFLOW_TYPES else r['amount_base'],
        axis=1
    )

    actual_df = valid_df[valid_df['is_actual'] == True]
    forecast_df = valid_df[valid_df['is_actual'] == False]

    def _build_cumulative(sub_df):
        if sub_df.empty:
            return pd.DataFrame()
        grouped = sub_df.groupby('date').agg(
            capital_calls=('signed_amount', lambda x: x[x < 0].sum()),
            distributions=('signed_amount', lambda x: x[x > 0].sum()),
        ).reset_index()
        grouped['net_cashflow'] = grouped['capital_calls'] + grouped['distributions']
        grouped = grouped.sort_values('date').reset_index(drop=True)
        grouped['cumulative_net_cashflow'] = grouped['net_cashflow'].cumsum()
        return grouped

    actual_cum = _build_cumulative(actual_df)
    forecast_cum = _build_cumulative(forecast_df)

    def _periodic(sub_df):
        if sub_df.empty:
            return pd.DataFrame()
        sub_df = sub_df.copy()
        sub_df['period'] = sub_df['date'].dt.to_period('Q').astype(str)
        return sub_df.groupby('period').agg(
            calls=('signed_amount', lambda x: x[x < 0].sum()),
            dists=('signed_amount', lambda x: x[x > 0].sum()),
            net=('signed_amount', 'sum'),
        ).reset_index()

    actual_per = _periodic(actual_df)
    forecast_per = _periodic(forecast_df)

    deviation_df = pd.DataFrame()
    if not actual_per.empty and not forecast_per.empty:
        merged = actual_per.merge(forecast_per, on='period', how='outer', suffixes=('_actual', '_forecast'))
        merged = merged.fillna(0).sort_values('period').reset_index(drop=True)
        merged['deviation'] = merged['net_actual'] - merged['net_forecast']
        deviation_df = merged[['period', 'net_actual', 'net_forecast', 'deviation']]

    actual_calls_total = actual_df.loc[actual_df['type'].isin(OUTFLOW_TYPES), 'amount_base'].sum() if not actual_df.empty else 0
    forecast_calls_total = forecast_df.loc[forecast_df['type'].isin(OUTFLOW_TYPES), 'amount_base'].sum() if not forecast_df.empty else 0
    actual_dists_total = actual_df.loc[actual_df['type'].isin(INFLOW_TYPES), 'amount_base'].sum() if not actual_df.empty else 0
    forecast_dists_total = forecast_df.loc[forecast_df['type'].isin(INFLOW_TYPES), 'amount_base'].sum() if not forecast_df.empty else 0

    pct_calls = (actual_calls_total / forecast_calls_total * 100) if forecast_calls_total > 0 else 0
    pct_dists = (actual_dists_total / forecast_dists_total * 100) if forecast_dists_total > 0 else 0

    tracking_error = 0.0
    mean_dev = 0.0
    if not deviation_df.empty:
        tracking_error = deviation_df['deviation'].std()
        mean_dev = deviation_df['deviation'].mean()

    return {
        'actual_cumulative': actual_cum,
        'forecast_cumulative': forecast_cum,
        'periodic_deviation': deviation_df,
        'metrics': {
            'tracking_error': tracking_error,
            'mean_deviation': mean_dev,
            'pct_calls_realized': pct_calls,
            'pct_dists_realized': pct_dists,
        }
    }


# ============================================================================
# ALERTS / DEADLINES
# ============================================================================

@st.cache_data(ttl=300)
def get_funding_gap_cached(_conn_id, fund_ids, base_currency, period='quarter', scenario_name='base'):
    """Funding-Gap Analyse: pro Periode erwartete Calls vs. Distributions.

    Nutzt nur is_actual=False (geplante Cashflows).
    Returns DataFrame: period_label, expected_calls, expected_distributions,
                       net_funding_need, cumulative_funding_need
    """
    multi_df = get_cashflows_multi_fund_base_currency_cached(
        _conn_id, fund_ids, base_currency, scenario_name
    )
    if multi_df.empty or 'amount_base' not in multi_df.columns:
        return pd.DataFrame()

    # Nur geplante Cashflows
    planned = multi_df[multi_df['is_actual'] == False].copy()
    if planned.empty:
        return pd.DataFrame()

    valid = planned.dropna(subset=['amount_base']).copy()
    if valid.empty:
        return pd.DataFrame()

    if period == 'quarter':
        valid['period_label'] = valid['date'].dt.to_period('Q').astype(str)
    else:
        valid['period_label'] = valid['date'].dt.year.astype(str)

    grouped = valid.groupby('period_label').apply(
        lambda g: pd.Series({
            'expected_calls': g.loc[g['type'].isin(OUTFLOW_TYPES), 'amount_base'].sum(),
            'expected_distributions': g.loc[g['type'].isin(INFLOW_TYPES), 'amount_base'].sum(),
        })
    ).reset_index()

    grouped['net_funding_need'] = grouped['expected_distributions'] - grouped['expected_calls']
    grouped = grouped.sort_values('period_label').reset_index(drop=True)
    grouped['cumulative_funding_need'] = grouped['net_funding_need'].cumsum()

    return grouped


@st.cache_data(ttl=300)
def get_cash_reserve_simulation_cached(_conn_id, fund_ids, base_currency, start_balance,
                                        scenario_name='base', include_actuals=True):
    """Cash-Reserve Simulation: simuliert Kontoverlauf über Zeit.

    Startet mit start_balance, addiert Inflows, subtrahiert Outflows.
    Returns DataFrame: date, inflow, outflow, net, balance
    """
    multi_df = get_cashflows_multi_fund_base_currency_cached(
        _conn_id, fund_ids, base_currency, scenario_name
    )
    if multi_df.empty or 'amount_base' not in multi_df.columns:
        return pd.DataFrame()

    valid = multi_df.dropna(subset=['amount_base']).copy()
    if not include_actuals:
        valid = valid[valid['is_actual'] == False]
    if valid.empty:
        return pd.DataFrame()

    # Pro Datum aggregieren
    daily = valid.groupby('date').apply(
        lambda g: pd.Series({
            'inflow': g.loc[g['type'].isin(INFLOW_TYPES), 'amount_base'].sum(),
            'outflow': g.loc[g['type'].isin(OUTFLOW_TYPES), 'amount_base'].sum(),
        })
    ).reset_index()

    daily = daily.sort_values('date').reset_index(drop=True)
    daily['net'] = daily['inflow'] - daily['outflow']

    # Kumulativer Kontostand
    daily['balance'] = start_balance + daily['net'].cumsum()

    return daily


@st.cache_data(ttl=300)
def get_upcoming_capital_calls_cached(_conn_id, days_ahead=90):
    """Anstehende Capital Calls (is_actual=False, type=capital_call, in Zukunft)."""
    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    with get_connection() as conn:
        query = """
        SELECT c.fund_id, f.fund_name, c.date, c.amount, c.currency, c.scenario_name
        FROM cashflows c
        JOIN funds f ON c.fund_id = f.fund_id
        WHERE c.is_actual = FALSE
          AND c.type = 'capital_call'
          AND c.date >= %s AND c.date <= %s
        ORDER BY c.date ASC
        """
        df = pd.read_sql_query(query, conn, params=(today, end_date))
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df['days_until'] = (df['date'] - pd.Timestamp(today)).dt.days
    return df


@st.cache_data(ttl=300)
def get_commitment_deadline_warnings_cached(_conn_id, days_ahead=90):
    """Fonds deren expected_end_date innerhalb von days_ahead liegt."""
    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    with get_connection() as conn:
        query = """
        SELECT fund_id, fund_name, expected_end_date, commitment_amount, unfunded_amount
        FROM funds
        WHERE expected_end_date IS NOT NULL
          AND expected_end_date >= %s AND expected_end_date <= %s
        ORDER BY expected_end_date ASC
        """
        df = pd.read_sql_query(query, conn, params=(today, end_date))
    if not df.empty:
        df['expected_end_date'] = pd.to_datetime(df['expected_end_date'])
        df['days_until'] = (df['expected_end_date'] - pd.Timestamp(today)).dt.days
    return df

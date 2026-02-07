"""
Cashflow Planning Tool — Gecachte Queries für die UI

Alle Funktionen nutzen @st.cache_data(ttl=300) mit _conn_id als
Cache-Buster (Unterstrich = wird nicht gehasht).
"""

import pandas as pd
import streamlit as st
from database import get_connection
from cashflow_db import get_cashflows_for_fund, get_all_scenarios

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
def get_scenarios_cached(_conn_id):
    """Holt alle Szenarien als list[dict]"""
    with get_connection() as conn:
        return get_all_scenarios(conn)


@st.cache_data(ttl=300)
def get_all_funds_for_cashflow_cached(_conn_id):
    """Holt alle Fonds mit Cashflow-relevanten Feldern"""
    with get_connection() as conn:
        query = """
        SELECT fund_id, fund_name, currency, commitment_amount
        FROM funds ORDER BY fund_name
        """
        return pd.read_sql_query(query, conn)

"""
Cashflow Planning Tool — Gecachte Pipeline-Queries

Alle Funktionen nutzen @st.cache_data(ttl=300) mit _conn_id als Cache-Buster.
"""

import pandas as pd
import streamlit as st
from database import get_connection
from cashflow_pipeline_db import (
    get_funds_by_status_group, get_pipeline_meta, get_fund_status_history,
    PIPELINE_STATUSES, STATUS_LABELS,
)


@st.cache_data(ttl=300)
def get_pipeline_summary_cached(_conn_id):
    """Pipeline-KPIs: Anzahl, gewichtetes Commitment, Avg DD-Score, nächste Deadline."""
    with get_connection() as conn:
        funds = get_funds_by_status_group(conn, 'pipeline')

    if not funds:
        return {
            'total_pipeline': 0,
            'by_status_count': {},
            'probability_weighted_commitment': 0.0,
            'avg_dd_score': 0.0,
            'upcoming_next_steps': [],
        }

    by_status = {}
    total_weighted = 0.0
    dd_scores = []
    upcoming = []

    for f in funds:
        status = f['status']
        by_status[status] = by_status.get(status, 0) + 1

        prob = (f.get('probability') or 0) / 100.0
        exp_commit = f.get('expected_commitment') or 0
        total_weighted += prob * exp_commit

        dd = f.get('dd_score')
        if dd is not None:
            dd_scores.append(dd)

        ns_date = f.get('next_step_date')
        if ns_date is not None:
            upcoming.append({
                'fund_name': f['fund_name'],
                'next_step': f.get('next_step') or '',
                'next_step_date': ns_date,
            })

    upcoming.sort(key=lambda x: x['next_step_date'])

    return {
        'total_pipeline': len(funds),
        'by_status_count': by_status,
        'probability_weighted_commitment': total_weighted,
        'avg_dd_score': sum(dd_scores) / len(dd_scores) if dd_scores else 0.0,
        'upcoming_next_steps': upcoming[:5],
    }


@st.cache_data(ttl=300)
def get_pipeline_funds_cached(_conn_id, status_filter=None):
    """Pipeline-Fonds als DataFrame."""
    with get_connection() as conn:
        funds = get_funds_by_status_group(conn, 'pipeline')

    if not funds:
        return pd.DataFrame()

    df = pd.DataFrame(funds)
    if status_filter:
        df = df[df['status'] == status_filter]

    return df


@st.cache_data(ttl=300)
def get_pipeline_history_cached(_conn_id, fund_id=None):
    """Status-Änderungshistorie. fund_id=None → alle Pipeline-Fonds."""
    with get_connection() as conn:
        if fund_id:
            return get_fund_status_history(conn, fund_id)
        # Alle Pipeline-Fonds
        funds = get_funds_by_status_group(conn, 'all')
        all_history = []
        for f in funds:
            history = get_fund_status_history(conn, f['fund_id'])
            for h in history:
                h['fund_name'] = f['fund_name']
            all_history.extend(history)
        all_history.sort(key=lambda x: x['changed_at'], reverse=True)
        return all_history


@st.cache_data(ttl=60)
def get_pipeline_kanban_data_cached(_conn_id):
    """Returns dict: {status: [fund_dicts]} für Kanban-Board."""
    with get_connection() as conn:
        funds = get_funds_by_status_group(conn, 'pipeline')

    result = {s: [] for s in PIPELINE_STATUSES}
    for f in funds:
        status = f['status']
        if status in result:
            result[status].append(f)

    return result

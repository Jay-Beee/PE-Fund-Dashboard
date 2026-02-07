"""
Cashflow Planning Tool — Alerts & Deadlines

Alert-Banner für anstehende Capital Calls und Commitment-Deadlines.
"""

import streamlit as st

from cashflow_queries import (
    get_upcoming_capital_calls_cached,
    get_commitment_deadline_warnings_cached,
)


def render_alerts_banner(conn_id):
    """Rendert Alert-Banner für anstehende Calls und Deadlines."""

    upcoming_calls = get_upcoming_capital_calls_cached(conn_id, days_ahead=90)
    deadline_warnings = get_commitment_deadline_warnings_cached(conn_id, days_ahead=90)

    alerts = []

    # Capital Calls
    if not upcoming_calls.empty:
        urgent = upcoming_calls[upcoming_calls['days_until'] <= 30]
        upcoming = upcoming_calls[upcoming_calls['days_until'] > 30]

        for _, row in urgent.iterrows():
            alerts.append({
                'type': 'warning',
                'msg': (f"⚠️ **Dringend**: Capital Call für {row['fund_name']} — "
                        f"{row['amount']:,.0f} {row['currency']} in {row['days_until']} Tagen "
                        f"({row['date'].strftime('%Y-%m-%d')})")
            })

        for _, row in upcoming.iterrows():
            alerts.append({
                'type': 'info',
                'msg': (f"📅 Capital Call für {row['fund_name']} — "
                        f"{row['amount']:,.0f} {row['currency']} in {row['days_until']} Tagen "
                        f"({row['date'].strftime('%Y-%m-%d')})")
            })

    # Commitment Deadlines
    if not deadline_warnings.empty:
        for _, row in deadline_warnings.iterrows():
            unfunded = row.get('unfunded_amount') or 0
            alerts.append({
                'type': 'warning',
                'msg': (f"⏰ **Commitment-Deadline**: {row['fund_name']} endet in "
                        f"{row['days_until']} Tagen ({row['expected_end_date'].strftime('%Y-%m-%d')})"
                        f" — Unfunded: {unfunded:,.0f}")
            })

    if not alerts:
        return

    # Bei > 3 Alerts: einklappbar
    if len(alerts) > 3:
        with st.expander(f"🔔 {len(alerts)} Alerts", expanded=True):
            for alert in alerts:
                if alert['type'] == 'warning':
                    st.warning(alert['msg'])
                else:
                    st.info(alert['msg'])
    else:
        for alert in alerts:
            if alert['type'] == 'warning':
                st.warning(alert['msg'])
            else:
                st.info(alert['msg'])

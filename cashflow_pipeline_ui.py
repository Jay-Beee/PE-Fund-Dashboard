"""
Cashflow Planning Tool — Pipeline Management UI

Kanban-Board, Tabelle, Neuer Fonds, Simulation.
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from datetime import date

from database import get_connection, clear_cache
from cashflow_pipeline_db import (
    VALID_TRANSITIONS, PIPELINE_STATUSES, STATUS_LABELS,
    create_pipeline_fund, change_fund_status, promote_fund, decline_fund,
    upsert_pipeline_meta, get_pipeline_meta, get_fund_status_history,
)
from cashflow_pipeline_queries import (
    get_pipeline_summary_cached,
    get_pipeline_funds_cached,
    get_pipeline_kanban_data_cached,
    get_pipeline_history_cached,
)


def render_pipeline_section(conn, conn_id):
    """Rendert das Pipeline-Management Sub-Tab."""

    st.subheader("Pipeline Management")

    # --- KPI-Zeile ---
    summary = get_pipeline_summary_cached(conn_id)

    if summary['total_pipeline'] == 0:
        st.info("Keine Pipeline-Fonds vorhanden. Erstelle einen neuen Fonds im Tab 'Neuer Fonds'.")
    else:
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.metric("Pipeline-Fonds", str(summary['total_pipeline']))
        with k2:
            st.metric("Gew. Commitment",
                       f"{summary['probability_weighted_commitment']:,.0f}")
        with k3:
            st.metric("Avg DD-Score",
                       f"{summary['avg_dd_score']:.1f}" if summary['avg_dd_score'] else "–")
        with k4:
            upcoming = summary['upcoming_next_steps']
            if upcoming:
                ns = upcoming[0]
                st.metric("Nächste Deadline",
                          f"{ns['fund_name']}: {ns['next_step_date']}")
            else:
                st.metric("Nächste Deadline", "–")

    # --- Sub-Tabs ---
    tab_kanban, tab_table, tab_new, tab_sim, tab_history = st.tabs([
        "Kanban", "Tabelle", "Neuer Fonds", "Simulation", "Historie"
    ])

    with tab_kanban:
        _render_kanban(conn, conn_id)

    with tab_table:
        _render_pipeline_table(conn, conn_id)

    with tab_new:
        _render_new_fund_form(conn, conn_id)

    with tab_sim:
        _render_simulation(conn, conn_id)

    with tab_history:
        _render_history(conn_id)


def _render_kanban(conn, conn_id):
    """Kanban-Board: 3 Spalten für Screening, DD, Negotiation."""
    kanban_data = get_pipeline_kanban_data_cached(conn_id)

    col_screen, col_dd, col_neg = st.columns(3)

    for col, status in [(col_screen, 'screening'),
                        (col_dd, 'due_diligence'),
                        (col_neg, 'negotiation')]:
        with col:
            st.markdown(f"**{STATUS_LABELS[status]}** ({len(kanban_data[status])})")
            st.markdown("---")

            for fund in kanban_data[status]:
                _render_fund_card(conn, conn_id, fund, status)


def _render_fund_card(conn, conn_id, fund, status):
    """Einzelne Fund-Card im Kanban-Board."""
    fund_id = fund['fund_id']
    with st.container(border=True):
        st.markdown(f"**{fund['fund_name']}**")
        if fund.get('gp_name'):
            st.caption(f"GP: {fund['gp_name']}")
        if fund.get('strategy'):
            st.caption(f"Strategy: {fund['strategy']}")

        prob = fund.get('probability') or 0
        exp_commit = fund.get('expected_commitment') or 0
        st.caption(f"Prob: {prob:.0f}% | Exp. Commit: {exp_commit:,.0f}")

        if fund.get('next_step'):
            st.caption(f"Next: {fund['next_step']}")

        # Action buttons
        valid_next = VALID_TRANSITIONS.get(status, [])
        promote_status = [s for s in valid_next if s != 'declined']

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if promote_status:
                next_s = promote_status[0]
                if next_s == 'committed':
                    # Special promote flow
                    if st.button("Promote", key=f"promote_{fund_id}",
                                 type="primary", width='stretch'):
                        st.session_state[f"show_promote_{fund_id}"] = True
                else:
                    if st.button(f"→ {STATUS_LABELS[next_s]}", key=f"advance_{fund_id}",
                                 type="primary", width='stretch'):
                        try:
                            change_fund_status(conn, fund_id, next_s)
                            clear_cache()
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))

        with btn_col2:
            if 'declined' in valid_next:
                if st.button("Decline", key=f"decline_{fund_id}",
                             width='stretch'):
                    st.session_state[f"show_decline_{fund_id}"] = True

        # Promote dialog
        if st.session_state.get(f"show_promote_{fund_id}"):
            with st.form(f"promote_form_{fund_id}"):
                st.markdown("**Commitment-Details für Promote:**")
                commit_amt = st.number_input(
                    "Commitment Amount", min_value=0.0,
                    value=float(exp_commit), step=100000.0,
                    key=f"promote_amt_{fund_id}"
                )
                commit_date = st.date_input(
                    "Commitment Datum", value=date.today(),
                    key=f"promote_date_{fund_id}"
                )
                if st.form_submit_button("Promote zu Committed"):
                    try:
                        promote_fund(conn, fund_id, commit_amt, commit_date)
                        clear_cache()
                        st.session_state.pop(f"show_promote_{fund_id}", None)
                        st.success(f"{fund['fund_name']} promoted!")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        # Decline dialog
        if st.session_state.get(f"show_decline_{fund_id}"):
            with st.form(f"decline_form_{fund_id}"):
                reason = st.text_area("Ablehnungsgrund", key=f"decline_reason_{fund_id}")
                if st.form_submit_button("Ablehnen"):
                    decline_fund(conn, fund_id, reason or 'Kein Grund angegeben')
                    clear_cache()
                    st.session_state.pop(f"show_decline_{fund_id}", None)
                    st.success(f"{fund['fund_name']} abgelehnt.")
                    st.rerun()


def _render_pipeline_table(conn, conn_id):
    """Sortierbare Tabelle aller Pipeline-Fonds."""
    df = get_pipeline_funds_cached(conn_id)

    if df.empty:
        st.info("Keine Pipeline-Fonds vorhanden.")
        return

    # Status-Filter
    status_filter = st.multiselect(
        "Status-Filter",
        options=list(PIPELINE_STATUSES),
        format_func=lambda x: STATUS_LABELS.get(x, x),
        default=list(PIPELINE_STATUSES),
        key="pipe_table_filter"
    )
    if status_filter:
        df = df[df['status'].isin(status_filter)]

    if df.empty:
        st.info("Keine Fonds für die Filterauswahl.")
        return

    # Display
    display_cols = ['fund_name', 'status', 'gp_name', 'strategy', 'geography',
                    'probability', 'expected_commitment', 'dd_score',
                    'next_step', 'next_step_date']
    available = [c for c in display_cols if c in df.columns]
    display_df = df[available].copy()

    col_map = {
        'fund_name': 'Fonds',
        'status': 'Status',
        'gp_name': 'GP',
        'strategy': 'Strategie',
        'geography': 'Geographie',
        'probability': 'Prob. %',
        'expected_commitment': 'Exp. Commitment',
        'dd_score': 'DD Score',
        'next_step': 'Nächster Schritt',
        'next_step_date': 'Deadline',
    }
    display_df = display_df.rename(columns=col_map)
    if 'Status' in display_df.columns:
        display_df['Status'] = display_df['Status'].map(STATUS_LABELS).fillna(display_df['Status'])

    st.dataframe(display_df, hide_index=True, width='stretch')

    # Inline edit for selected fund
    with st.expander("Pipeline-Fonds bearbeiten"):
        fund_names = df['fund_name'].tolist()
        if not fund_names:
            return
        selected = st.selectbox("Fonds wählen", fund_names, key="pipe_edit_select")
        fund_row = df[df['fund_name'] == selected].iloc[0]
        fund_id = int(fund_row['fund_id'])

        with st.form(f"pipe_edit_{fund_id}"):
            e1, e2 = st.columns(2)
            with e1:
                new_prob = st.number_input(
                    "Probability %", min_value=0.0, max_value=100.0,
                    value=float(fund_row.get('probability') or 50),
                    key=f"pipe_edit_prob_{fund_id}"
                )
                new_dd = st.number_input(
                    "DD Score", min_value=0.0, max_value=10.0,
                    value=float(fund_row.get('dd_score') or 0),
                    step=0.5, key=f"pipe_edit_dd_{fund_id}"
                )
            with e2:
                new_next_step = st.text_input(
                    "Nächster Schritt",
                    value=fund_row.get('next_step') or '',
                    key=f"pipe_edit_ns_{fund_id}"
                )
                new_ns_date = st.date_input(
                    "Deadline",
                    value=fund_row.get('next_step_date') or date.today(),
                    key=f"pipe_edit_nsd_{fund_id}"
                )

            if st.form_submit_button("Speichern"):
                upsert_pipeline_meta(
                    conn, fund_id,
                    probability=new_prob,
                    dd_score=new_dd if new_dd > 0 else None,
                    next_step=new_next_step or None,
                    next_step_date=new_ns_date,
                )
                clear_cache()
                st.success("Gespeichert.")
                st.rerun()


def _render_new_fund_form(conn, conn_id):
    """Formular zum Erstellen eines neuen Pipeline-Fonds."""
    st.markdown("**Neuen Pipeline-Fonds erfassen**")

    with st.form("new_pipeline_fund"):
        c1, c2 = st.columns(2)
        with c1:
            fund_name = st.text_input("Fonds-Name")
            gp_name = st.text_input("GP Name")
            strategy = st.text_input("Strategie")
            geography = st.text_input("Geographie")
            fund_size = st.number_input("Fund Size (Mio.)", min_value=0.0, step=10.0)
        with c2:
            currency = st.selectbox("Währung", ['EUR', 'USD', 'CHF', 'GBP'])
            vintage = st.number_input("Vintage Year", min_value=2000, max_value=2050,
                                       value=date.today().year)
            probability = st.number_input("Wahrscheinlichkeit %", min_value=0.0,
                                          max_value=100.0, value=50.0)
            expected_commitment = st.number_input("Expected Commitment",
                                                   min_value=0.0, step=100000.0)
            source = st.text_input("Quelle / Deal-Herkunft")
            contact = st.text_input("Kontaktperson")

        if st.form_submit_button("Erstellen", type="primary"):
            if not fund_name or not fund_name.strip():
                st.warning("Bitte einen Fonds-Namen eingeben.")
            else:
                from database import get_or_create_gp
                gp_id = get_or_create_gp(conn, gp_name) if gp_name else None
                fund_id = create_pipeline_fund(
                    conn, fund_name.strip(), gp_id, strategy or None,
                    geography or None, fund_size or None, currency,
                    int(vintage), probability, expected_commitment or None,
                    source or None, contact or None
                )
                clear_cache()
                st.success(f"Pipeline-Fonds '{fund_name}' erstellt (ID: {fund_id}).")
                st.rerun()


def _render_simulation(conn, conn_id):
    """Was-wäre-wenn Simulation für Pipeline-Fonds."""
    df = get_pipeline_funds_cached(conn_id)

    if df.empty:
        st.info("Keine Pipeline-Fonds für Simulation vorhanden.")
        return

    st.markdown("**Pipeline Impact-Simulation**")
    st.caption("Zeigt den hypothetischen Impact ausgewählter Pipeline-Fonds auf das Portfolio.")

    fund_names = df['fund_name'].tolist()
    selected = st.multiselect(
        "Pipeline-Fonds auswählen",
        options=fund_names,
        default=fund_names,
        key="pipe_sim_select"
    )

    if not selected:
        return

    selected_df = df[df['fund_name'].isin(selected)]

    # Summarize
    total_exp = 0.0
    total_weighted = 0.0
    for _, row in selected_df.iterrows():
        exp = row.get('expected_commitment') or 0
        prob = (row.get('probability') or 0) / 100.0
        total_exp += exp
        total_weighted += exp * prob

    s1, s2, s3 = st.columns(3)
    with s1:
        st.metric("Ausgewählte Fonds", str(len(selected)))
    with s2:
        st.metric("Total Exp. Commitment", f"{total_exp:,.0f}")
    with s3:
        st.metric("Prob.-Gewichtet", f"{total_weighted:,.0f}")

    # Simple bar chart of expected commitments
    if not selected_df.empty:
        fig, ax = plt.subplots(figsize=(10, 4))
        chart_df = selected_df[['fund_name', 'expected_commitment', 'probability']].copy()
        chart_df['expected_commitment'] = chart_df['expected_commitment'].fillna(0)
        chart_df['probability'] = chart_df['probability'].fillna(0)
        chart_df['weighted'] = chart_df['expected_commitment'] * chart_df['probability'] / 100

        x = range(len(chart_df))
        ax.bar(x, chart_df['expected_commitment'], alpha=0.3, label='Expected Commitment',
               color='steelblue')
        ax.bar(x, chart_df['weighted'], alpha=0.8, label='Prob.-gewichtet',
               color='steelblue')
        ax.set_xticks(x)
        ax.set_xticklabels(chart_df['fund_name'], rotation=45, ha='right')
        ax.set_ylabel("Betrag")
        ax.set_title("Pipeline: Expected vs. Gewichtetes Commitment")
        ax.legend()
        ax.grid(axis='y', alpha=0.3)
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)


def _render_history(conn_id):
    """Status-Änderungshistorie."""
    history = get_pipeline_history_cached(conn_id)

    if not history:
        st.info("Keine Status-Änderungen vorhanden.")
        return

    df = pd.DataFrame(history)
    display_cols = ['fund_name', 'old_status', 'new_status', 'changed_by',
                    'change_reason', 'changed_at']
    available = [c for c in display_cols if c in df.columns]
    display_df = df[available].copy()

    col_map = {
        'fund_name': 'Fonds',
        'old_status': 'Von',
        'new_status': 'Nach',
        'changed_by': 'Geändert von',
        'change_reason': 'Grund',
        'changed_at': 'Zeitpunkt',
    }
    display_df = display_df.rename(columns=col_map)

    for col in ['Von', 'Nach']:
        if col in display_df.columns:
            display_df[col] = display_df[col].map(STATUS_LABELS).fillna(display_df[col])

    st.dataframe(display_df, hide_index=True, width='stretch')

"""
Cashflow Planning Tool — Streamlit UI

Rendert den kompletten Cashflow-Planning-Tab mit:
  A) Fonds-Auswahl + Commitment-Info
  B) Szenario-Auswahl
  C) Manueller Cashflow-Eintrag
  D) Cashflow-Tabelle mit Lösch-Buttons
  E) Charts (J-Curve, Balken, Timeline)
  F) Cashflow Forecast
  G) Szenario-Vergleich
  H) Excel-Import
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from datetime import date, datetime

from database import get_connection, clear_cache
from cashflow_db import (
    insert_cashflow, delete_cashflow, bulk_insert_cashflows,
    insert_scenario, update_fund_commitment,
    delete_all_scenario_cashflows, delete_scenario
)
from cashflow_queries import (
    get_cashflows_for_fund_cached, get_cumulative_cashflows_cached,
    get_periodic_cashflows_cached, get_fund_commitment_info_cached,
    get_cashflow_summary_cached, get_scenarios_cached,
    get_all_funds_for_cashflow_cached, OUTFLOW_TYPES, INFLOW_TYPES
)
from cashflow_charts import (
    create_j_curve_chart, create_cashflow_bar_chart,
    create_net_cashflow_timeline
)
from cashflow_forecast_ui import render_forecast_section
from cashflow_scenario_comparison import render_scenario_comparison
from cashflow_dashboard import render_dashboard_widgets
from cashflow_alerts import render_alerts_banner
from cashflow_actual_vs_forecast import render_actual_vs_forecast_section
from cashflow_portfolio_ui import render_portfolio_section
from cashflow_liquidity_ui import render_liquidity_section
from cashflow_export import (
    export_cashflows_excel, export_fund_report_pdf
)

# Typ-Mapping: intern → deutsch
TYPE_LABELS = {
    'capital_call': 'Kapitalabruf',
    'distribution': 'Ausschüttung',
    'management_fee': 'Management Fee',
    'carried_interest': 'Carried Interest',
    'clawback': 'Clawback',
}
LABEL_TO_TYPE = {v: k for k, v in TYPE_LABELS.items()}


def render_cashflow_tab(conn, conn_id, selected_fund_ids, selected_fund_names):
    """Rendert den kompletten Cashflow-Planning-Tab."""

    st.header("💰 Cashflow Planning")

    # ================================================================
    # Dashboard-Widgets (Portfolio-KPIs)
    # ================================================================
    render_dashboard_widgets(conn_id)

    # ================================================================
    # Alerts-Banner
    # ================================================================
    render_alerts_banner(conn_id)

    st.markdown("---")

    # ================================================================
    # A) Fonds-Auswahl + Commitment-Info
    # ================================================================
    all_funds_df = get_all_funds_for_cashflow_cached(conn_id)
    if all_funds_df.empty:
        st.warning("Keine Fonds vorhanden. Bitte zuerst Fonds im Admin-Tab anlegen.")
        return

    # Fonds-Auswahl: gefilterte oder alle
    if selected_fund_ids:
        fund_options = all_funds_df[all_funds_df['fund_id'].isin(selected_fund_ids)]
    else:
        fund_options = all_funds_df

    if fund_options.empty:
        st.info("Keine Fonds für die aktuelle Filterauswahl verfügbar.")
        return

    fund_names_list = fund_options['fund_name'].tolist()
    selected_fund_name = st.selectbox(
        "Fonds auswählen", options=fund_names_list, key="cf_fund_select"
    )
    selected_fund_row = fund_options[fund_options['fund_name'] == selected_fund_name]
    if selected_fund_row.empty:
        return
    fund_id = int(selected_fund_row.iloc[0]['fund_id'])

    # Commitment-Infos anzeigen
    commit_info = get_fund_commitment_info_cached(conn_id, fund_id)
    currency = commit_info.get('currency') or 'EUR'

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Commitment",
                   f"{commit_info.get('commitment_amount', 0) or 0:,.0f} {currency}")
    with col2:
        st.metric("Unfunded",
                   f"{commit_info.get('unfunded_amount', 0) or 0:,.0f} {currency}")
    with col3:
        cd = commit_info.get('commitment_date')
        st.metric("Commitment Datum", str(cd) if cd else "–")
    with col4:
        ed = commit_info.get('expected_end_date')
        st.metric("Erwartetes Ende", str(ed) if ed else "–")

    # Commitment bearbeiten
    with st.expander("✏️ Commitment bearbeiten"):
        with st.form("commitment_form"):
            fc1, fc2 = st.columns(2)
            with fc1:
                new_commitment = st.number_input(
                    "Commitment Amount", value=float(commit_info.get('commitment_amount') or 0),
                    min_value=0.0, step=100000.0, key="cf_commit_amt"
                )
                new_commit_date = st.date_input(
                    "Commitment Datum",
                    value=commit_info.get('commitment_date') or date.today(),
                    key="cf_commit_date"
                )
            with fc2:
                new_unfunded = st.number_input(
                    "Unfunded Amount", value=float(commit_info.get('unfunded_amount') or 0),
                    min_value=0.0, step=100000.0, key="cf_unfunded_amt"
                )
                new_end_date = st.date_input(
                    "Erwartetes Ende",
                    value=commit_info.get('expected_end_date') or date.today(),
                    key="cf_end_date"
                )
            if st.form_submit_button("💾 Speichern"):
                update_fund_commitment(conn, fund_id, new_commitment,
                                       new_unfunded, new_commit_date, new_end_date)
                clear_cache()
                st.success("Commitment-Daten gespeichert.")
                st.rerun()

    st.markdown("---")

    # ================================================================
    # B) Szenario-Auswahl
    # ================================================================
    scenarios = get_scenarios_cached(conn_id)
    scenario_names = [s['scenario_name'] for s in scenarios]

    sc1, sc2 = st.columns([3, 1])
    with sc1:
        selected_scenario = st.selectbox(
            "Szenario", options=scenario_names, key="cf_scenario_select"
        )
    with sc2:
        with st.popover("➕ Neues Szenario"):
            new_sc_name = st.text_input("Name", key="cf_new_scenario_name")
            new_sc_desc = st.text_input("Beschreibung", key="cf_new_scenario_desc")
            if st.button("Erstellen", key="cf_create_scenario"):
                if new_sc_name and new_sc_name.strip():
                    insert_scenario(conn, new_sc_name.strip(), new_sc_desc.strip() or None)
                    clear_cache()
                    st.success(f"Szenario '{new_sc_name}' erstellt.")
                    st.rerun()
                else:
                    st.warning("Bitte einen Namen eingeben.")

    with st.expander("🗑️ Szenario verwalten"):
        del1, del2 = st.columns(2)
        with del1:
            st.markdown("**Cashflows dieses Szenarios löschen**")
            st.caption(f"Löscht alle Cashflows (Ist + Plan) für '{selected_scenario}' in diesem Fonds.")
            if st.button("🗑️ Alle Cashflows löschen", key="cf_delete_scenario_cfs"):
                deleted = delete_all_scenario_cashflows(conn, fund_id, selected_scenario)
                clear_cache()
                st.success(f"{deleted} Cashflows aus '{selected_scenario}' gelöscht.")
                st.rerun()
        with del2:
            st.markdown("**Ganzes Szenario löschen**")
            if selected_scenario == 'base':
                st.caption("Das Base-Szenario kann nicht gelöscht werden.")
            else:
                st.caption(f"Löscht '{selected_scenario}' inkl. aller Cashflows über alle Fonds.")
                if st.button(f"⚠️ Szenario '{selected_scenario}' endgültig löschen",
                             key="cf_delete_scenario"):
                    deleted_cf, deleted_sc = delete_scenario(conn, selected_scenario)
                    clear_cache()
                    st.success(f"Szenario '{selected_scenario}' gelöscht ({deleted_cf} Cashflows entfernt).")
                    st.rerun()

    st.markdown("---")

    # ================================================================
    # C) Manueller Cashflow-Eintrag
    # ================================================================
    with st.expander("➕ Cashflow erfassen"):
        with st.form("cashflow_entry_form"):
            ec1, ec2, ec3 = st.columns(3)
            with ec1:
                cf_date = st.date_input("Datum", value=date.today(), key="cf_entry_date")
                cf_type_label = st.selectbox(
                    "Typ", options=list(TYPE_LABELS.values()), key="cf_entry_type"
                )
            with ec2:
                cf_amount = st.number_input(
                    "Betrag (positiv)", min_value=0.0, step=10000.0, key="cf_entry_amount"
                )
                cf_is_actual = st.checkbox("Ist-Daten (sonst Plan)", value=True,
                                           key="cf_entry_actual")
            with ec3:
                cf_notes = st.text_area("Notizen", height=100, key="cf_entry_notes")

            if st.form_submit_button("💾 Cashflow speichern"):
                if cf_amount <= 0:
                    st.warning("Bitte einen Betrag > 0 eingeben.")
                else:
                    cf_type = LABEL_TO_TYPE[cf_type_label]
                    insert_cashflow(conn, fund_id, cf_date, cf_type, cf_amount,
                                    currency, cf_is_actual, selected_scenario,
                                    cf_notes or None)
                    clear_cache()
                    st.success(f"{cf_type_label} über {cf_amount:,.0f} {currency} gespeichert.")
                    st.rerun()

    st.markdown("---")

    # ================================================================
    # D) Cashflow-Tabelle + Summary
    # ================================================================
    st.subheader("📋 Cashflows")

    cf_df = get_cashflows_for_fund_cached(conn_id, fund_id, selected_scenario)

    if cf_df.empty:
        st.info("Noch keine Cashflows für diesen Fonds/Szenario erfasst.")
    else:
        # Summary-Metriken
        summary = get_cashflow_summary_cached(conn_id, fund_id, selected_scenario)
        sm1, sm2, sm3, sm4 = st.columns(4)
        with sm1:
            st.metric("Total Abrufe", f"{summary['total_called']:,.0f} {currency}")
        with sm2:
            st.metric("Total Ausschüttungen",
                       f"{summary['total_distributed']:,.0f} {currency}")
        with sm3:
            st.metric("Netto-Cashflow", f"{summary['net_cashflow']:,.0f} {currency}")
        with sm4:
            st.metric("DPI", f"{summary['dpi']:.2f}x")

        # Tabelle
        display_df = cf_df[['cashflow_id', 'date', 'type', 'amount', 'is_actual', 'notes']].copy()
        display_df['date'] = display_df['date'].dt.strftime('%Y-%m-%d')
        display_df['type_label'] = display_df['type'].map(TYPE_LABELS)
        display_df['Richtung'] = display_df['type'].apply(
            lambda t: '↗ Outflow' if t in OUTFLOW_TYPES else '↙ Inflow'
        )
        display_df['amount_fmt'] = display_df['amount'].apply(lambda x: f"{x:,.0f}")
        display_df['Status'] = display_df['is_actual'].apply(
            lambda x: 'Ist' if x else 'Plan'
        )

        # Anzeige-Tabelle
        show_df = display_df[['date', 'type_label', 'Richtung', 'amount_fmt', 'Status', 'notes']].copy()
        show_df.columns = ['Datum', 'Typ', 'Richtung', f'Betrag ({currency})', 'Status', 'Notizen']
        show_df['Notizen'] = show_df['Notizen'].fillna('')

        st.dataframe(show_df, width='stretch', hide_index=True)

        # Excel Export Button
        excel_data = export_cashflows_excel(cf_df, selected_fund_name, currency, selected_scenario)
        st.download_button(
            label="📥 Excel Export",
            data=excel_data,
            file_name=f"cashflows_{selected_fund_name}_{selected_scenario}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="cf_excel_export_btn"
        )

        # Lösch-Sektion
        with st.expander("🗑️ Cashflow löschen"):
            delete_options = {
                f"{row['date']} | {TYPE_LABELS.get(row['type'], row['type'])} | "
                f"{row['amount']:,.0f} {currency}": row['cashflow_id']
                for _, row in cf_df.iterrows()
            }
            selected_delete = st.selectbox(
                "Cashflow auswählen", options=list(delete_options.keys()),
                key="cf_delete_select"
            )
            if st.button("🗑️ Löschen", key="cf_delete_btn"):
                delete_cashflow(conn, delete_options[selected_delete])
                clear_cache()
                st.success("Cashflow gelöscht.")
                st.rerun()

    st.markdown("---")

    # ================================================================
    # E) Charts
    # ================================================================
    st.subheader("📊 Charts")

    cumulative_df = get_cumulative_cashflows_cached(conn_id, fund_id, selected_scenario)

    if cumulative_df.empty:
        st.info("Keine Cashflow-Daten für Charts vorhanden.")
    else:
        chart_tab1, chart_tab2, chart_tab3 = st.tabs([
            "J-Curve", "Cashflow Balken", "Netto-Zeitverlauf"
        ])

        with chart_tab1:
            fig = create_j_curve_chart(cumulative_df, selected_fund_name, currency)
            if fig:
                st.pyplot(fig)
                plt.close(fig)

        with chart_tab2:
            period = st.radio(
                "Aggregation", options=['quarter', 'year'],
                format_func=lambda x: 'Quartal' if x == 'quarter' else 'Jahr',
                horizontal=True, key="cf_period_toggle"
            )
            periodic_df = get_periodic_cashflows_cached(
                conn_id, fund_id, period, selected_scenario
            )
            if not periodic_df.empty:
                fig = create_cashflow_bar_chart(periodic_df, selected_fund_name, currency)
                if fig:
                    st.pyplot(fig)
                    plt.close(fig)
            else:
                st.info("Keine Daten für Balkendiagramm.")

        with chart_tab3:
            commit_amt = commit_info.get('commitment_amount')
            fig = create_net_cashflow_timeline(
                cumulative_df, selected_fund_name, commit_amt, currency
            )
            if fig:
                st.pyplot(fig)
                plt.close(fig)

        # PDF Export Button
        summary_for_pdf = get_cashflow_summary_cached(conn_id, fund_id, selected_scenario)
        periodic_for_pdf = get_periodic_cashflows_cached(conn_id, fund_id, 'quarter', selected_scenario)
        pdf_data = export_fund_report_pdf(
            selected_fund_name, currency, summary_for_pdf,
            cumulative_df, periodic_for_pdf, commit_info
        )
        if pdf_data:
            st.download_button(
                label="📄 Fund Report PDF",
                data=pdf_data,
                file_name=f"fund_report_{selected_fund_name}.pdf",
                mime="application/pdf",
                key="cf_pdf_export_btn"
            )

    st.markdown("---")

    # ================================================================
    # F) Cashflow Forecast
    # ================================================================
    render_forecast_section(conn, conn_id, fund_id, selected_fund_name, currency, commit_info)

    st.markdown("---")

    # ================================================================
    # G) Szenario-Vergleich
    # ================================================================
    render_scenario_comparison(conn_id, fund_id, selected_fund_name, currency, commit_info)

    st.markdown("---")

    # ================================================================
    # G.5) Ist vs. Forecast (pro Fonds)
    # ================================================================
    render_actual_vs_forecast_section(conn_id, fund_id, selected_fund_name, currency, selected_scenario)

    st.markdown("---")

    # ================================================================
    # H) Excel-Import
    # ================================================================
    with st.expander("📥 Excel-Import"):
        st.markdown("""
        **Erwartetes Format (Excel/CSV):**

        | Fund Name | Datum | Typ | Betrag | Ist/Plan | Szenario | Notizen |
        |-----------|-------|-----|--------|----------|----------|---------|

        - **Typ**: Kapitalabruf, Ausschüttung, Management Fee, Carried Interest, Clawback
        - **Ist/Plan**: `Ist` oder `Plan`
        - **Szenario**: z.B. `base` (optional, Default: base)
        """)

        uploaded_file = st.file_uploader(
            "Datei hochladen", type=['xlsx', 'xls', 'csv'],
            key="cf_file_upload"
        )

        if uploaded_file is not None:
            try:
                if uploaded_file.name.endswith('.csv'):
                    import_df = pd.read_csv(uploaded_file)
                else:
                    import_df = pd.read_excel(uploaded_file)

                st.write(f"**{len(import_df)} Zeilen** gefunden:")
                st.dataframe(import_df.head(10), hide_index=True)

                if st.button("📥 Importieren", key="cf_import_btn"):
                    # Fonds-Name → fund_id Mapping
                    fund_map = dict(zip(
                        all_funds_df['fund_name'], all_funds_df['fund_id']
                    ))

                    cashflows_list = []
                    errors = []

                    for idx, row in import_df.iterrows():
                        row_num = idx + 2  # Excel-Zeile (Header = 1)
                        try:
                            fund_name_raw = str(row.iloc[0]).strip()
                            fid = fund_map.get(fund_name_raw)
                            if not fid:
                                errors.append(f"Zeile {row_num}: Fonds '{fund_name_raw}' nicht gefunden")
                                continue

                            cf_date = pd.to_datetime(row.iloc[1]).date()

                            type_raw = str(row.iloc[2]).strip()
                            cf_type = LABEL_TO_TYPE.get(type_raw)
                            if not cf_type:
                                errors.append(f"Zeile {row_num}: Unbekannter Typ '{type_raw}'")
                                continue

                            amount = float(row.iloc[3])
                            if amount <= 0:
                                errors.append(f"Zeile {row_num}: Betrag muss positiv sein")
                                continue

                            is_actual_raw = str(row.iloc[4]).strip().lower() if len(row) > 4 else 'ist'
                            is_actual = is_actual_raw in ('ist', 'true', '1', 'ja', 'yes')

                            scenario = str(row.iloc[5]).strip() if len(row) > 5 and pd.notna(row.iloc[5]) else 'base'
                            notes = str(row.iloc[6]).strip() if len(row) > 6 and pd.notna(row.iloc[6]) else None

                            # Währung vom Fonds holen
                            fund_row = all_funds_df[all_funds_df['fund_id'] == fid]
                            cf_currency = fund_row.iloc[0]['currency'] if not fund_row.empty else 'EUR'

                            cashflows_list.append({
                                'fund_id': int(fid),
                                'date': cf_date,
                                'type': cf_type,
                                'amount': amount,
                                'currency': cf_currency or 'EUR',
                                'is_actual': is_actual,
                                'scenario_name': scenario,
                                'notes': notes,
                            })

                        except Exception as e:
                            errors.append(f"Zeile {row_num}: {str(e)}")

                    if errors:
                        st.warning(f"⚠️ {len(errors)} Fehler:")
                        for err in errors[:20]:
                            st.caption(err)

                    if cashflows_list:
                        count = bulk_insert_cashflows(conn, cashflows_list)
                        clear_cache()
                        st.success(f"✅ {count} Cashflows importiert.")
                        st.rerun()
                    elif not errors:
                        st.warning("Keine gültigen Cashflows gefunden.")

            except Exception as e:
                st.error(f"Fehler beim Lesen der Datei: {e}")

    st.markdown("---")

    # ================================================================
    # I) Portfolio-Aggregation
    # ================================================================
    render_portfolio_section(conn, conn_id)

    st.markdown("---")

    # ================================================================
    # J) Liquiditätsplanung
    # ================================================================
    render_liquidity_section(conn, conn_id)

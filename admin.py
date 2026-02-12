import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime, date
from database import get_connection, format_quarter, clear_cache
from cashflow_fx_ui import render_fx_management
from cashflow_pipeline_db import (
    ALL_STATUSES, STATUS_LABELS, VALID_TRANSITIONS,
    change_fund_status, force_change_fund_status, upsert_pipeline_meta
)


def render_admin_tab(conn):
    st.header("⚙️ Fund & GP Management")

    # Stichtag-Verwaltung
    with st.expander("📅 Stichtage verwalten", expanded=False):
        st.subheader("Verfügbare Stichtage")
        with conn.cursor() as cursor:
            cursor.execute("""
            SELECT f.fund_name, pch.reporting_date, COUNT(pch.company_name) as num_companies
            FROM portfolio_companies_history pch JOIN funds f ON pch.fund_id = f.fund_id
            GROUP BY f.fund_name, pch.reporting_date ORDER BY f.fund_name, pch.reporting_date DESC
            """)
            fund_dates = cursor.fetchall()

        if fund_dates:
            fund_dates_df = pd.DataFrame(fund_dates, columns=['Fonds', 'Stichtag', 'Anzahl Companies'])
            fund_dates_df['Quartal'] = fund_dates_df['Stichtag'].apply(format_quarter)
            st.dataframe(fund_dates_df[['Fonds', 'Stichtag', 'Quartal', 'Anzahl Companies']], width='stretch', hide_index=True)
        else:
            st.info("Keine historischen Stichtage vorhanden")

        st.markdown("---")
        st.subheader("Stichtag für einzelnen Fonds ändern")

        with conn.cursor() as cursor:
            cursor.execute("SELECT fund_id, fund_name FROM funds ORDER BY fund_name")
            funds_list = cursor.fetchall()

        if funds_list:
            fund_dict = {f[1]: f[0] for f in funds_list}
            selected_fund_for_date = st.selectbox("Fonds auswählen", options=list(fund_dict.keys()), key="fund_for_date_change")

            if selected_fund_for_date:
                selected_fund_id_for_date = fund_dict[selected_fund_for_date]
                with conn.cursor() as cursor:
                    cursor.execute("SELECT DISTINCT reporting_date FROM portfolio_companies_history WHERE fund_id = %s ORDER BY reporting_date DESC", (selected_fund_id_for_date,))
                    fund_specific_dates = [row[0].strftime('%Y-%m-%d') if isinstance(row[0], (date, datetime)) else row[0] for row in cursor.fetchall()]

                if fund_specific_dates:
                    col1, col2 = st.columns(2)
                    with col1:
                        old_date_single = st.selectbox("Alter Stichtag", options=fund_specific_dates, format_func=format_quarter, key="old_date_single")
                    with col2:
                        old_date_parsed = datetime.strptime(old_date_single, "%Y-%m-%d").date() if old_date_single else date.today()
                        new_date_single = st.date_input("Neuer Stichtag", value=old_date_parsed, key="new_date_single")

                    new_date_str = new_date_single.strftime("%Y-%m-%d")
                    date_already_exists = new_date_str in fund_specific_dates and new_date_str != old_date_single

                    if date_already_exists:
                        st.error(f"⚠️ Der Stichtag {format_quarter(new_date_single)} existiert bereits!")
                    elif new_date_str == old_date_single:
                        st.info("ℹ️ Der neue Stichtag ist identisch mit dem alten.")
                    else:
                        confirm_date_change = st.checkbox(f"✅ Ich bestätige die Änderung von {format_quarter(old_date_single)} zu {format_quarter(new_date_single)}", key="confirm_date_change")
                        if confirm_date_change:
                            if st.button("📅 Stichtag ändern", type="primary", key="change_date_btn"):
                                with conn.cursor() as cursor:
                                    cursor.execute("UPDATE portfolio_companies_history SET reporting_date = %s WHERE fund_id = %s AND reporting_date = %s", (new_date_single, selected_fund_id_for_date, old_date_single))
                                    cursor.execute("UPDATE fund_metrics_history SET reporting_date = %s WHERE fund_id = %s AND reporting_date = %s", (new_date_single, selected_fund_id_for_date, old_date_single))
                                    conn.commit()
                                clear_cache()
                                st.toast("Stichtag geändert!")
                                st.rerun()

    # Cleanup
    with st.expander("🧹 Datenbank bereinigen & Diagnose", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Bereinigung")
            if st.button("🧹 Jetzt bereinigen", key="cleanup_btn"):
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM fund_metrics WHERE fund_id NOT IN (SELECT fund_id FROM funds)")
                    cursor.execute("DELETE FROM portfolio_companies WHERE fund_id NOT IN (SELECT fund_id FROM funds)")
                    cursor.execute("DELETE FROM fund_metrics_history WHERE fund_id NOT IN (SELECT fund_id FROM funds)")
                    cursor.execute("DELETE FROM portfolio_companies_history WHERE fund_id NOT IN (SELECT fund_id FROM funds)")
                    conn.commit()
                clear_cache()
                st.success("✅ Bereinigung abgeschlossen!")
        with col2:
            st.subheader("Diagnose")
            if st.button("🔍 Datenbank analysieren", key="diagnose_btn"):
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM funds")
                    st.metric("Fonds", cursor.fetchone()[0])
                    cursor.execute("SELECT COUNT(*) FROM gps")
                    st.metric("GPs", cursor.fetchone()[0])
                    cursor.execute("SELECT COUNT(*) FROM portfolio_companies_history")
                    st.metric("Portfolio History", cursor.fetchone()[0])

    st.markdown("---")

    # Admin Tabs
    admin_tab1, admin_tab2, admin_tab3, admin_tab4, admin_tab5, admin_tab6, admin_tab7, admin_tab8, admin_tab9 = st.tabs(["➕ Import Excel", "🏢 Edit Portfolio Company", "✏️ Edit Fund", "👔 Edit GP", "🤝 Edit Placement Agent", "📊 Fund Status", "🗑️ Delete Fund", "🗑️ Delete GP", "🗑️ Delete Placement Agent"])

    # IMPORT EXCEL
    with admin_tab1:
        st.subheader("Excel-Datei importieren")

        # Hilfsfunktion für Lösch-Erkennung
        def is_delete_marker(val):
            """Prüft ob ein Wert ein Lösch-Marker ist"""
            if pd.isna(val):
                return False
            val_str = str(val).strip().upper()
            return val_str in ['-', 'DELETE', 'NULL', 'LÖSCHEN', '#CLEAR#']

        def get_value_or_delete(val):
            """Gibt (value, should_delete) zurück"""
            if pd.isna(val) or str(val).strip() == '':
                return None, False  # Leer = nicht ändern
            if is_delete_marker(val):
                return None, True   # Lösch-Marker = auf NULL setzen
            return str(val).strip(), False  # Normaler Wert

        # Format-Hilfe anzeigen
        with st.expander("📋 Excel-Format", expanded=False):
            st.markdown("""
            **Zeile 1 (GP Header):**
            ```
            GP Name | Strategy | Rating | Sektor | Headquarters | Website | Last Meeting | Next Raise Estimate | Kontakt1 Name | Kontakt1 Funktion | Kontakt1 E-Mail | Kontakt1 Telefon | Kontakt2 Name | Kontakt2 Funktion | Kontakt2 E-Mail | Kontakt2 Telefon | PA Name | PA Rating | PA Headquarters | PA Website | PA Last Meeting | PA Kontakt1 Name | PA Kontakt1 Funktion | PA Kontakt1 E-Mail | PA Kontakt1 Telefon
            ```

            **Zeile 2:** GP-Werte (inkl. Placement Agent Daten)

            **Zeile 3:** [Leer]

            **Zeile 4 (Fund/Portfolio Header):**
            ```
            Fund Name | Stichtag | Vintage Year | Fund Size | Currency | Geography | Net TVPI | Net IRR | Status | Probability | Expected Commitment | DD Score | DD Notes | Source | Contact Person | Next Step | Next Step Date | Portfolio Company | Investment Date | Exit Date | Ownership % | Investiert | Realisiert | Unrealisiert | Entry Multiple | Gross IRR
            ```

            **Zeile 5+:** Fund- und Portfolio-Daten (mehrere Fonds möglich)

            **Hinweise:**
            - Leere Zellen = Bestehende Daten bleiben erhalten
            - `-` oder `DELETE` oder `NULL` = Wert wird gelöscht
            - Datumsformat: YYYY-MM-DD oder YYYY-MM
            - Fund-Metadaten (Vintage, Size, etc.) nur bei erster Zeile pro Fund nötig
            - Placement Agent ist optional - wenn PA Name leer, wird kein PA zugeordnet
            - Status-Spalte optional: screening, due_diligence, negotiation, committed, active, harvesting, closed, declined (Default: active)
            - Pipeline-Spalten (Probability, DD Score, etc.) optional — werden in Pipeline-Meta gespeichert
            """)

        uploaded_file = st.file_uploader("Excel-Datei hochladen", type=['xlsx'], key="excel_upload")

        # Session State für Import-Workflow
        if 'import_preview' not in st.session_state:
            st.session_state.import_preview = None
        if 'import_data' not in st.session_state:
            st.session_state.import_data = None

        if uploaded_file and st.button("🔍 Vorschau & Änderungen prüfen", type="secondary", key="preview_btn"):
            try:
                # Excel komplett einlesen
                uploaded_file.seek(0)
                raw_df = pd.read_excel(uploaded_file, header=None)

                # GP-Daten aus Zeile 1-2
                gp_header = [str(h).strip() if pd.notna(h) else '' for h in raw_df.iloc[0]]
                gp_values = raw_df.iloc[1]

                # GP-Spalten-Mapping
                gp_col_map = {}
                for i, header in enumerate(gp_header):
                    header_lower = header.lower()
                    if 'gp name' in header_lower or header_lower == 'gp':
                        gp_col_map['gp_name'] = i
                    elif header_lower == 'strategy' or (header_lower.startswith('strategy') and 'pa' not in header_lower):
                        gp_col_map['strategy'] = i
                    elif header_lower == 'rating' or (header_lower.startswith('rating') and 'pa' not in header_lower):
                        gp_col_map['rating'] = i
                    elif ('sektor' in header_lower or 'sector' in header_lower) and 'pa' not in header_lower:
                        gp_col_map['sector'] = i
                    elif ('headquarters' in header_lower or 'hq' in header_lower) and 'pa' not in header_lower:
                        gp_col_map['headquarters'] = i
                    elif 'website' in header_lower and 'pa' not in header_lower:
                        gp_col_map['website'] = i
                    elif 'last meeting' in header_lower and 'pa' not in header_lower:
                        gp_col_map['last_meeting'] = i
                    elif 'next raise' in header_lower:
                        gp_col_map['next_raise_estimate'] = i
                    elif 'kontakt1 name' in header_lower or 'kontaktperson 1 name' in header_lower:
                        gp_col_map['contact1_name'] = i
                    elif 'kontakt1 funktion' in header_lower or 'kontaktperson 1 funktion' in header_lower:
                        gp_col_map['contact1_function'] = i
                    elif 'kontakt1 e-mail' in header_lower or 'kontaktperson 1 e-mail' in header_lower:
                        gp_col_map['contact1_email'] = i
                    elif 'kontakt1 telefon' in header_lower or 'kontaktperson 1 telefon' in header_lower:
                        gp_col_map['contact1_phone'] = i
                    elif 'kontakt2 name' in header_lower or 'kontaktperson 2 name' in header_lower:
                        gp_col_map['contact2_name'] = i
                    elif 'kontakt2 funktion' in header_lower or 'kontaktperson 2 funktion' in header_lower:
                        gp_col_map['contact2_function'] = i
                    elif 'kontakt2 e-mail' in header_lower or 'kontaktperson 2 e-mail' in header_lower:
                        gp_col_map['contact2_email'] = i
                    elif 'kontakt2 telefon' in header_lower or 'kontaktperson 2 telefon' in header_lower:
                        gp_col_map['contact2_phone'] = i
                    # Placement Agent Felder
                    elif 'pa name' in header_lower or 'placement agent name' in header_lower:
                        gp_col_map['pa_name'] = i
                    elif 'pa rating' in header_lower:
                        gp_col_map['pa_rating'] = i
                    elif 'pa headquarters' in header_lower or 'pa hq' in header_lower:
                        gp_col_map['pa_headquarters'] = i
                    elif 'pa website' in header_lower:
                        gp_col_map['pa_website'] = i
                    elif 'pa last meeting' in header_lower:
                        gp_col_map['pa_last_meeting'] = i
                    elif 'pa kontakt1 name' in header_lower or 'pa kontaktperson 1 name' in header_lower:
                        gp_col_map['pa_contact1_name'] = i
                    elif 'pa kontakt1 funktion' in header_lower or 'pa kontaktperson 1 funktion' in header_lower:
                        gp_col_map['pa_contact1_function'] = i
                    elif 'pa kontakt1 e-mail' in header_lower or 'pa kontaktperson 1 e-mail' in header_lower:
                        gp_col_map['pa_contact1_email'] = i
                    elif 'pa kontakt1 telefon' in header_lower or 'pa kontaktperson 1 telefon' in header_lower:
                        gp_col_map['pa_contact1_phone'] = i

                # GP-Werte extrahieren
                def get_gp_val(key):
                    """Gibt (value, should_delete) zurück"""
                    if key in gp_col_map:
                        val = gp_values.iloc[gp_col_map[key]]
                        if is_delete_marker(val):
                            return None, True  # Lösch-Marker
                        if pd.notna(val) and str(val).strip() != '':
                            return str(val).strip(), False
                    return None, False

                # GP-Daten mit Lösch-Markern extrahieren
                gp_data = {}
                gp_delete_fields = set()  # Felder die gelöscht werden sollen

                for field in ['gp_name', 'strategy', 'rating', 'sector', 'headquarters', 
                             'website', 'last_meeting', 'next_raise_estimate',
                             'contact1_name', 'contact1_function', 'contact1_email', 'contact1_phone',
                             'contact2_name', 'contact2_function', 'contact2_email', 'contact2_phone']:
                    val, should_delete = get_gp_val(field)
                    gp_data[field] = val
                    if should_delete:
                        gp_delete_fields.add(field)

                # Placement Agent Daten extrahieren
                pa_data = {}
                pa_delete_fields = set()

                pa_field_mapping = {
                    'pa_name': 'pa_name',
                    'rating': 'pa_rating',
                    'headquarters': 'pa_headquarters',
                    'website': 'pa_website',
                    'last_meeting': 'pa_last_meeting',
                    'contact1_name': 'pa_contact1_name',
                    'contact1_function': 'pa_contact1_function',
                    'contact1_email': 'pa_contact1_email',
                    'contact1_phone': 'pa_contact1_phone',
                }

                for pa_field, gp_col_key in pa_field_mapping.items():
                    val, should_delete = get_gp_val(gp_col_key)
                    pa_data[pa_field] = val
                    if should_delete:
                        pa_delete_fields.add(pa_field)

                if not gp_data['gp_name']:
                    st.error("❌ GP Name nicht gefunden in Zeile 2!")
                    st.stop()

                # Fund/Portfolio-Daten ab Zeile 4
                fund_header = [str(h).strip() if pd.notna(h) else '' for h in raw_df.iloc[3]]

                # Fund-Spalten-Mapping
                fund_col_map = {}
                for i, header in enumerate(fund_header):
                    header_lower = header.lower()
                    if 'fund name' in header_lower or header_lower == 'fund':
                        fund_col_map['fund_name'] = i
                    elif 'stichtag' in header_lower or 'reporting date' in header_lower:
                        fund_col_map['reporting_date'] = i
                    elif 'vintage' in header_lower:
                        fund_col_map['vintage_year'] = i
                    elif 'fund size' in header_lower or 'size' in header_lower:
                        fund_col_map['fund_size_m'] = i
                    elif 'currency' in header_lower or 'währung' in header_lower:
                        fund_col_map['currency'] = i
                    elif 'geography' in header_lower or 'geograph' in header_lower:
                        fund_col_map['geography'] = i
                    elif 'portfolio company' in header_lower or 'company' in header_lower:
                        fund_col_map['company_name'] = i
                    elif 'investment date' in header_lower or 'investitionsdatum' in header_lower:
                        fund_col_map['investment_date'] = i
                    elif 'exit date' in header_lower or 'exitdatum' in header_lower:
                        fund_col_map['exit_date'] = i
                    elif 'ownership' in header_lower:
                        fund_col_map['ownership'] = i
                    elif 'investiert' in header_lower or 'invested' in header_lower:
                        fund_col_map['invested_amount'] = i
                    elif 'unrealisiert' in header_lower or 'unrealized' in header_lower:
                        fund_col_map['unrealized_tvpi'] = i
                    elif 'realisiert' in header_lower or 'realized' in header_lower:
                        fund_col_map['realized_tvpi'] = i
                    elif 'net tvpi' in header_lower or 'net_tvpi' in header_lower:
                        fund_col_map['net_tvpi'] = i
                    elif 'net irr' in header_lower or 'net_irr' in header_lower:
                        fund_col_map['net_irr'] = i
                    elif 'entry' in header_lower and ('multiple' in header_lower or 'ebitda' in header_lower):
                        fund_col_map['entry_multiple'] = i
                    elif 'gross irr' in header_lower or 'irr' in header_lower:
                        fund_col_map['gross_irr'] = i
                    elif header_lower in ('status', 'fund status'):
                        fund_col_map['status'] = i
                    elif 'probability' in header_lower or 'wahrscheinlichkeit' in header_lower:
                        fund_col_map['probability'] = i
                    elif 'expected commitment' in header_lower:
                        fund_col_map['expected_commitment'] = i
                    elif 'dd score' in header_lower:
                        fund_col_map['dd_score'] = i
                    elif 'dd notes' in header_lower or 'dd notizen' in header_lower:
                        fund_col_map['dd_notes'] = i
                    elif header_lower in ('source', 'quelle'):
                        fund_col_map['source'] = i
                    elif 'contact person' in header_lower or 'kontaktperson' in header_lower:
                        fund_col_map['contact_person'] = i
                    elif 'next step date' in header_lower:
                        fund_col_map['next_step_date'] = i
                    elif 'next step' in header_lower or 'naechster schritt' in header_lower:
                        fund_col_map['next_step'] = i

                # Hilfsfunktion für Datumsparsen
                def parse_date(val):
                    if pd.isna(val) or val == '' or val is None:
                        return None
                    try:
                        if isinstance(val, (datetime, date)):
                            return val.strftime('%Y-%m-%d')
                        val_str = str(val).strip()
                        if len(val_str) == 7:  # YYYY-MM
                            return f"{val_str}-01"
                        return pd.to_datetime(val_str).strftime('%Y-%m-%d')
                    except (ValueError, TypeError):
                        return None

                # Fund/Portfolio-Daten extrahieren
                funds_data = {}

                for row_idx in range(4, len(raw_df)):
                    row = raw_df.iloc[row_idx]

                    fund_name = None
                    if 'fund_name' in fund_col_map:
                        val = row.iloc[fund_col_map['fund_name']]
                        if pd.notna(val) and str(val).strip():
                            fund_name = str(val).strip()

                    if not fund_name:
                        continue

                    if fund_name not in funds_data:
                        funds_data[fund_name] = {
                            'metadata': {},
                            'companies': []
                        }

                    # NEU: Lösch-Felder für Fund-Metriken
                    if 'metadata_delete_fields' not in funds_data[fund_name]:
                        funds_data[fund_name]['metadata_delete_fields'] = set()

                    for field in ['vintage_year', 'fund_size_m', 'currency', 'geography', 'reporting_date', 'net_tvpi', 'net_irr']:
                        if field in fund_col_map:
                            val = row.iloc[fund_col_map[field]]

                            # NEU: Lösch-Marker prüfen
                            if is_delete_marker(val):
                                funds_data[fund_name]['metadata_delete_fields'].add(field)
                            elif pd.notna(val) and str(val).strip():
                                if field == 'vintage_year':
                                    try:
                                        funds_data[fund_name]['metadata'][field] = int(float(val))
                                    except (ValueError, TypeError):
                                        pass
                                elif field == 'fund_size_m':
                                    try:
                                        funds_data[fund_name]['metadata'][field] = float(val)
                                    except (ValueError, TypeError):
                                        pass
                                elif field == 'net_tvpi':
                                    try:
                                        funds_data[fund_name]['metadata'][field] = float(val)
                                    except (ValueError, TypeError):
                                        pass
                                elif field == 'net_irr':
                                    try:
                                        # Excel speichert 17% als 0.17 → mit 100 multiplizieren
                                        val_float = float(val)
                                        if val_float < 1:  # Wahrscheinlich Dezimalformat
                                            funds_data[fund_name]['metadata'][field] = val_float * 100
                                        else:  # Bereits als Prozentzahl (z.B. 17)
                                            funds_data[fund_name]['metadata'][field] = val_float
                                    except (ValueError, TypeError):
                                        pass
                                elif field == 'reporting_date':
                                    funds_data[fund_name]['metadata'][field] = parse_date(val)
                                else:
                                    funds_data[fund_name]['metadata'][field] = str(val).strip()

                    # Status auslesen (Default: 'active')
                    if 'status' not in funds_data[fund_name]['metadata']:
                        status_val = 'active'
                        if 'status' in fund_col_map:
                            raw_status = row.iloc[fund_col_map['status']]
                            if pd.notna(raw_status) and str(raw_status).strip():
                                status_val = str(raw_status).strip().lower().replace(' ', '_')
                                if status_val not in ALL_STATUSES:
                                    status_val = 'active'
                        funds_data[fund_name]['metadata']['status'] = status_val

                    # Pipeline-Meta sammeln
                    if 'pipeline_meta' not in funds_data[fund_name]['metadata']:
                        pipeline_meta = {}
                        for field in ['probability', 'expected_commitment', 'dd_score', 'dd_notes',
                                      'source', 'contact_person', 'next_step', 'next_step_date']:
                            if field in fund_col_map:
                                val = row.iloc[fund_col_map[field]]
                                if pd.notna(val) and str(val).strip():
                                    if field in ('probability', 'expected_commitment', 'dd_score'):
                                        try:
                                            pipeline_meta[field] = float(val)
                                        except (ValueError, TypeError):
                                            pass
                                    elif field == 'next_step_date':
                                        pipeline_meta[field] = parse_date(val)
                                    else:
                                        pipeline_meta[field] = str(val).strip()
                        funds_data[fund_name]['metadata']['pipeline_meta'] = pipeline_meta

                    company_name = None
                    if 'company_name' in fund_col_map:
                        val = row.iloc[fund_col_map['company_name']]
                        if pd.notna(val) and str(val).strip():
                            company_name = str(val).strip()

                    if company_name:
                        company_data = {'company_name': company_name}
                        company_delete_fields = set()  # NEU: Lösch-Felder pro Company

                        for field in ['investment_date', 'exit_date', 'ownership', 'invested_amount', 
                                     'realized_tvpi', 'unrealized_tvpi', 'entry_multiple', 'gross_irr']:
                            if field in fund_col_map:
                                val = row.iloc[fund_col_map[field]]

                                # NEU: Lösch-Marker prüfen
                                if is_delete_marker(val):
                                    company_delete_fields.add(field)
                                elif pd.notna(val) and str(val).strip() != '':
                                    if field in ['investment_date', 'exit_date']:
                                        company_data[field] = parse_date(val)
                                    elif field in ['ownership', 'gross_irr']:
                                        try:
                                            company_data[field] = float(val) * 100
                                        except (ValueError, TypeError):
                                            pass
                                    elif field in ['invested_amount', 'realized_tvpi', 
                                                  'unrealized_tvpi', 'entry_multiple']:
                                        try:
                                            company_data[field] = float(val)
                                        except (ValueError, TypeError):
                                            pass

                        # NEU: Lösch-Felder speichern
                        company_data['_delete_fields'] = company_delete_fields
                        funds_data[fund_name]['companies'].append(company_data)

                # Änderungen ermitteln
                changes = {'gp': [], 'funds': {}, 'companies': {}}

                with conn.cursor() as cursor:
                    # GP-Änderungen prüfen
                    cursor.execute("SELECT * FROM gps WHERE gp_name = %s", (gp_data['gp_name'],))
                    existing_gp = cursor.fetchone()
                    gp_columns = [desc[0] for desc in cursor.description] if existing_gp else []

                    if existing_gp:
                        gp_dict = dict(zip(gp_columns, existing_gp))
                        for field in gp_data.keys():
                            if field == 'gp_name':
                                continue
                            old_val = gp_dict.get(field)

                            # Löschung prüfen
                            if field in gp_delete_fields and old_val is not None:
                                changes['gp'].append({
                                    'field': field,
                                    'old': old_val,
                                    'new': '🗑️ [LÖSCHEN]'
                                })
                            # Normale Änderung prüfen
                            elif gp_data[field] is not None and field in gp_dict:
                                if old_val != gp_data[field] and str(old_val) != str(gp_data[field]):
                                    changes['gp'].append({
                                        'field': field,
                                        'old': old_val,
                                        'new': gp_data[field]
                                    })

                    # Fund- und Company-Änderungen prüfen
                    for fund_name, fund_info in funds_data.items():
                        reporting_date = fund_info['metadata'].get('reporting_date')
                        if not reporting_date:
                            st.warning(f"⚠️ Kein Stichtag für Fund '{fund_name}' - wird übersprungen")
                            continue

                        cursor.execute("SELECT * FROM funds WHERE fund_name = %s", (fund_name,))
                        existing_fund = cursor.fetchone()
                        fund_columns = [desc[0] for desc in cursor.description] if existing_fund else []

                        changes['funds'][fund_name] = []
                        if existing_fund:
                            fund_dict = dict(zip(fund_columns, existing_fund))
                            if gp_data.get('strategy'):
                                old_strategy = fund_dict.get('strategy')
                                new_strategy = gp_data['strategy']
                                if old_strategy != new_strategy and str(old_strategy) != str(new_strategy):
                                    changes['funds'][fund_name].append({
                                        'field': 'strategy',
                                        'old': old_strategy,
                                        'new': new_strategy
                                    })

                            for field in ['vintage_year', 'fund_size_m', 'currency', 'geography']:
                                new_val = fund_info['metadata'].get(field)
                                if new_val is not None:
                                    old_val = fund_dict.get(field)
                                    if old_val != new_val and str(old_val) != str(new_val):
                                        changes['funds'][fund_name].append({
                                            'field': field,
                                            'old': old_val,
                                            'new': new_val
                                        })

                            cursor.execute("""
                                SELECT net_tvpi, net_irr FROM fund_metrics_history 
                                WHERE fund_id = (SELECT fund_id FROM funds WHERE fund_name = %s)
                                AND reporting_date = %s
                            """, (fund_name, reporting_date))
                            existing_metrics = cursor.fetchone()

                            new_net_tvpi = fund_info['metadata'].get('net_tvpi')
                            new_net_irr = fund_info['metadata'].get('net_irr')
                            metadata_delete_fields = fund_info.get('metadata_delete_fields', set())  # NEU

                            if existing_metrics:
                                old_net_tvpi, old_net_irr = existing_metrics

                                # NEU: Löschung prüfen für net_tvpi
                                if 'net_tvpi' in metadata_delete_fields and old_net_tvpi is not None:
                                    changes['funds'][fund_name].append({
                                        'field': 'net_tvpi',
                                        'old': f"{old_net_tvpi:.2f}x",
                                        'new': '🗑️ [LÖSCHEN]'
                                    })
                                elif new_net_tvpi is not None and old_net_tvpi != new_net_tvpi:
                                    changes['funds'][fund_name].append({
                                        'field': 'net_tvpi',
                                        'old': f"{old_net_tvpi:.2f}x" if old_net_tvpi else "-",
                                        'new': f"{new_net_tvpi:.2f}x"
                                    })

                                # NEU: Löschung prüfen für net_irr
                                if 'net_irr' in metadata_delete_fields and old_net_irr is not None:
                                    changes['funds'][fund_name].append({
                                        'field': 'net_irr',
                                        'old': f"{old_net_irr:.1f}%",
                                        'new': '🗑️ [LÖSCHEN]'
                                    })
                                elif new_net_irr is not None and old_net_irr != new_net_irr:
                                    changes['funds'][fund_name].append({
                                        'field': 'net_irr',
                                        'old': f"{old_net_irr:.1f}%" if old_net_irr else "-",
                                        'new': f"{new_net_irr:.1f}%"
                                    })

                            else:
                                # Neue Metriken (noch kein Eintrag vorhanden)
                                if new_net_tvpi is not None:
                                    changes['funds'][fund_name].append({
                                        'field': 'net_tvpi',
                                        'old': '-',
                                        'new': f"{new_net_tvpi:.2f}x"
                                    })
                                if new_net_irr is not None:
                                    changes['funds'][fund_name].append({
                                        'field': 'net_irr',
                                        'old': '-',
                                        'new': f"{new_net_irr:.1f}%"
                                    })

                        changes['companies'][fund_name] = {}
                        for company in fund_info['companies']:
                            company_name = company['company_name']

                            cursor.execute("""
                            SELECT * FROM portfolio_companies_history 
                            WHERE fund_id = (SELECT fund_id FROM funds WHERE fund_name = %s)
                            AND company_name = %s AND reporting_date = %s
                            """, (fund_name, company_name, reporting_date))
                            existing_company = cursor.fetchone()
                            company_columns = [desc[0] for desc in cursor.description] if existing_company else []

                            changes['companies'][fund_name][company_name] = []
                            if existing_company:
                                company_dict = dict(zip(company_columns, existing_company))
                                company_delete_fields = company.get('_delete_fields', set())  # NEU

                                for field in ['investment_date', 'exit_date', 'ownership', 'invested_amount',
                                             'realized_tvpi', 'unrealized_tvpi', 'entry_multiple', 'gross_irr']:
                                    old_val = company_dict.get(field)

                                    # NEU: Löschung prüfen
                                    if field in company_delete_fields and old_val is not None:
                                        changes['companies'][fund_name][company_name].append({
                                            'field': field,
                                            'old': old_val,
                                            'new': '🗑️ [LÖSCHEN]'
                                        })
                                    # Normale Änderung prüfen
                                    elif field in company and company.get(field) is not None:
                                        new_val = company.get(field)
                                        if old_val != new_val and str(old_val) != str(new_val):
                                            changes['companies'][fund_name][company_name].append({
                                                'field': field,
                                                'old': old_val,
                                                'new': new_val
                                            })

                st.session_state.import_data = {
                    'gp_data': gp_data,
                    'gp_delete_fields': gp_delete_fields,  # NEU
                    'pa_data': pa_data,
                    'funds_data': funds_data,
                    'changes': changes
                }

                st.session_state.import_preview = True
                st.rerun()

            except Exception as e:
                st.error(f"❌ Fehler beim Lesen: {e}")
                import traceback
                st.code(traceback.format_exc())

        # Vorschau anzeigen
        if st.session_state.import_preview and st.session_state.import_data:
            data = st.session_state.import_data
            changes = data['changes']

            st.markdown("---")
            st.subheader("📋 Import-Vorschau")

            st.markdown(f"**GP:** {data['gp_data']['gp_name']}")
            if data['pa_data'].get('pa_name'):
                st.markdown(f"**Placement Agent:** {data['pa_data']['pa_name']}")

            fund_names = list(data['funds_data'].keys())
            total_companies = sum(len(f['companies']) for f in data['funds_data'].values())
            st.markdown(f"**Fonds:** {len(fund_names)} | **Portfolio Companies:** {total_companies}")

            if 'selected_changes' not in st.session_state:
                st.session_state.selected_changes = {
                    'gp': {},
                    'funds': {},
                    'companies': {}
                }

            has_changes = False
            change_count = 0

            if changes['gp']:
                has_changes = True
                st.markdown("#### 🔄 GP-Änderungen")
                for idx, ch in enumerate(changes['gp']):
                    key = f"gp_{ch['field']}"
                    if key not in st.session_state.selected_changes['gp']:
                        st.session_state.selected_changes['gp'][key] = True

                    col1, col2 = st.columns([0.05, 0.95])
                    with col1:
                        st.session_state.selected_changes['gp'][key] = st.checkbox(
                            "Auswählen", value=st.session_state.selected_changes['gp'][key],
                            key=f"cb_gp_{idx}", label_visibility="collapsed"
                        )
                    with col2:
                        status = "✅" if st.session_state.selected_changes['gp'][key] else "⏸️"
                        st.markdown(f"{status} **{ch['field']}:** `{ch['old']}` → `{ch['new']}`")
                    change_count += 1

            for fund_name, fund_changes in changes['funds'].items():
                if fund_changes:
                    has_changes = True
                    st.markdown(f"#### 🔄 Fund '{fund_name}' - Änderungen")

                    if fund_name not in st.session_state.selected_changes['funds']:
                        st.session_state.selected_changes['funds'][fund_name] = {}

                    for idx, ch in enumerate(fund_changes):
                        key = f"{fund_name}_{ch['field']}"
                        if key not in st.session_state.selected_changes['funds'][fund_name]:
                            st.session_state.selected_changes['funds'][fund_name][key] = True

                        col1, col2 = st.columns([0.05, 0.95])
                        with col1:
                            st.session_state.selected_changes['funds'][fund_name][key] = st.checkbox(
                                "Auswählen", value=st.session_state.selected_changes['funds'][fund_name][key],
                                key=f"cb_fund_{fund_name}_{idx}", label_visibility="collapsed"
                            )
                        with col2:
                            status = "✅" if st.session_state.selected_changes['funds'][fund_name][key] else "⏸️"
                            st.markdown(f"{status} **{ch['field']}:** `{ch['old']}` → `{ch['new']}`")
                        change_count += 1

            for fund_name, companies in changes['companies'].items():
                for company_name, company_changes in companies.items():
                    if company_changes:
                        has_changes = True
                        st.markdown(f"#### 🔄 '{company_name}' ({fund_name})")

                        comp_key = f"{fund_name}_{company_name}"
                        if comp_key not in st.session_state.selected_changes['companies']:
                            st.session_state.selected_changes['companies'][comp_key] = {}

                        for idx, ch in enumerate(company_changes):
                            key = f"{comp_key}_{ch['field']}"
                            if key not in st.session_state.selected_changes['companies'][comp_key]:
                                st.session_state.selected_changes['companies'][comp_key][key] = True

                            col1, col2 = st.columns([0.05, 0.95])
                            with col1:
                                st.session_state.selected_changes['companies'][comp_key][key] = st.checkbox(
                                    "Auswählen", value=st.session_state.selected_changes['companies'][comp_key][key],
                                    key=f"cb_comp_{comp_key}_{idx}", label_visibility="collapsed"
                                )
                            with col2:
                                status = "✅" if st.session_state.selected_changes['companies'][comp_key][key] else "⏸️"
                                st.markdown(f"{status} **{ch['field']}:** `{ch['old']}` → `{ch['new']}`")
                            change_count += 1

            if has_changes:
                st.markdown("---")
                col1, col2, col3 = st.columns([1, 1, 2])
                with col1:
                    if st.button("✅ Alle auswählen", key="select_all_changes"):
                        for key in st.session_state.selected_changes['gp']:
                            st.session_state.selected_changes['gp'][key] = True
                        for fund in st.session_state.selected_changes['funds']:
                            for key in st.session_state.selected_changes['funds'][fund]:
                                st.session_state.selected_changes['funds'][fund][key] = True
                        for comp in st.session_state.selected_changes['companies']:
                            for key in st.session_state.selected_changes['companies'][comp]:
                                st.session_state.selected_changes['companies'][comp][key] = True
                        st.rerun()
                with col2:
                    if st.button("❌ Keine auswählen", key="select_none_changes"):
                        for key in st.session_state.selected_changes['gp']:
                            st.session_state.selected_changes['gp'][key] = False
                        for fund in st.session_state.selected_changes['funds']:
                            for key in st.session_state.selected_changes['funds'][fund]:
                                st.session_state.selected_changes['funds'][fund][key] = False
                        for comp in st.session_state.selected_changes['companies']:
                            for key in st.session_state.selected_changes['companies'][comp]:
                                st.session_state.selected_changes['companies'][comp][key] = False
                        st.rerun()

                selected_count = 0
                for key, val in st.session_state.selected_changes['gp'].items():
                    if val:
                        selected_count += 1
                for fund in st.session_state.selected_changes['funds'].values():
                    for val in fund.values():
                        if val:
                            selected_count += 1
                for comp in st.session_state.selected_changes['companies'].values():
                    for val in comp.values():
                        if val:
                            selected_count += 1

                st.info(f"📊 {selected_count} von {change_count} Änderungen ausgewählt")

            if not has_changes:
                st.info("ℹ️ Keine Änderungen an bestehenden Daten. Nur neue Einträge werden hinzugefügt.")

            # Neue Einträge zählen
            new_funds = []
            new_companies = {}

            with conn.cursor() as cursor:
                for fund_name, fund_info in data['funds_data'].items():
                    cursor.execute("SELECT fund_id FROM funds WHERE fund_name = %s", (fund_name,))
                    if not cursor.fetchone():
                        new_funds.append(fund_name)

                    reporting_date = fund_info['metadata'].get('reporting_date')
                    if reporting_date:
                        new_companies[fund_name] = []
                        for company in fund_info['companies']:
                            cursor.execute("""
                            SELECT history_id FROM portfolio_companies_history 
                            WHERE fund_id = (SELECT fund_id FROM funds WHERE fund_name = %s)
                            AND company_name = %s AND reporting_date = %s
                            """, (fund_name, company['company_name'], reporting_date))
                            if not cursor.fetchone():
                                new_companies[fund_name].append(company['company_name'])

            if new_funds:
                st.markdown("#### ➕ Neue Fonds")
                for f in new_funds:
                    st.markdown(f"- {f}")

            for fund_name, companies in new_companies.items():
                if companies:
                    st.markdown(f"#### ➕ Neue Companies in '{fund_name}'")
                    for c in companies:
                        st.markdown(f"- {c}")

            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("❌ Abbrechen", key="cancel_import"):
                    st.session_state.import_preview = None
                    st.session_state.import_data = None
                    st.session_state.selected_changes = {'gp': {}, 'funds': {}, 'companies': {}}
                    st.rerun()

            with col2:
                if st.button("✅ Bestätigen & Importieren", type="primary", key="confirm_import"):
                    try:
                        with conn.cursor() as cursor:
                            gp_data = data['gp_data']
                            gp_delete_fields = data.get('gp_delete_fields', set())  # NEU
                            pa_data = data['pa_data']
                            funds_data = data['funds_data']
                            selected = st.session_state.selected_changes

                            # GP anlegen/aktualisieren
                            cursor.execute("SELECT gp_id FROM gps WHERE gp_name = %s", (gp_data['gp_name'],))
                            existing_gp = cursor.fetchone()

                            if existing_gp:
                                gp_id = existing_gp[0]
                                update_fields = []
                                update_values = []
                                for field in ['rating', 'sector', 'headquarters', 'website',
                                             'last_meeting', 'next_raise_estimate', 'contact1_name',
                                             'contact1_function', 'contact1_email', 'contact1_phone',
                                             'contact2_name', 'contact2_function', 'contact2_email', 'contact2_phone']:
                                    # Prüfen ob Feld gelöscht werden soll
                                    if field in gp_delete_fields:
                                        change_key = f"gp_{field}"
                                        if change_key in selected['gp'] and not selected['gp'][change_key]:
                                            continue
                                        update_fields.append(f"{field} = %s")
                                        update_values.append(None)  # NULL setzen
                                    elif gp_data.get(field):
                                        change_key = f"gp_{field}"
                                        if change_key in selected['gp'] and not selected['gp'][change_key]:
                                            continue
                                        update_fields.append(f"{field} = %s")
                                        update_values.append(gp_data[field])

                                if update_fields:
                                    update_values.append(gp_id)
                                    cursor.execute(f"""
                                    UPDATE gps SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                                    WHERE gp_id = %s
                                    """, update_values)
                            else:
                                cursor.execute("""
                                INSERT INTO gps (gp_name, rating, sector, headquarters, website,
                                                last_meeting, next_raise_estimate, contact1_name, contact1_function,
                                                contact1_email, contact1_phone, contact2_name, contact2_function,
                                                contact2_email, contact2_phone)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING gp_id
                                """, (gp_data['gp_name'], gp_data.get('rating'),
                                     gp_data.get('sector'), gp_data.get('headquarters'), gp_data.get('website'),
                                     gp_data.get('last_meeting'), gp_data.get('next_raise_estimate'),
                                     gp_data.get('contact1_name'), gp_data.get('contact1_function'),
                                     gp_data.get('contact1_email'), gp_data.get('contact1_phone'),
                                     gp_data.get('contact2_name'), gp_data.get('contact2_function'),
                                     gp_data.get('contact2_email'), gp_data.get('contact2_phone')))
                                gp_id = cursor.fetchone()[0]

                            # Placement Agent anlegen/aktualisieren (falls vorhanden)
                            pa_id = None
                            if pa_data.get('pa_name'):
                                cursor.execute("SELECT pa_id FROM placement_agents WHERE pa_name = %s", (pa_data['pa_name'],))
                                existing_pa = cursor.fetchone()

                                if existing_pa:
                                    pa_id = existing_pa[0]
                                    update_fields = []
                                    update_values = []
                                    for field in ['rating', 'headquarters', 'website', 'last_meeting',
                                                 'contact1_name', 'contact1_function', 'contact1_email', 'contact1_phone']:
                                        if pa_data.get(field):
                                            update_fields.append(f"{field} = %s")
                                            update_values.append(pa_data[field])

                                    if update_fields:
                                        update_values.append(pa_id)
                                        cursor.execute(f"""
                                        UPDATE placement_agents SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                                        WHERE pa_id = %s
                                        """, update_values)
                                else:
                                    cursor.execute("""
                                    INSERT INTO placement_agents (pa_name, rating, headquarters, website, last_meeting,
                                                                  contact1_name, contact1_function, contact1_email, contact1_phone)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    RETURNING pa_id
                                    """, (pa_data['pa_name'], pa_data.get('rating'),
                                         pa_data.get('headquarters'), pa_data.get('website'), pa_data.get('last_meeting'),
                                         pa_data.get('contact1_name'), pa_data.get('contact1_function'),
                                         pa_data.get('contact1_email'), pa_data.get('contact1_phone')))
                                    pa_id = cursor.fetchone()[0]

                            imported_funds = 0
                            imported_companies = 0
                            updated_companies = 0
                            skipped_changes = 0

                            for fund_name, fund_info in funds_data.items():
                                reporting_date = fund_info['metadata'].get('reporting_date')
                                if not reporting_date:
                                    continue

                                cursor.execute("SELECT fund_id FROM funds WHERE fund_name = %s", (fund_name,))
                                existing_fund = cursor.fetchone()

                                if existing_fund:
                                    fund_id = existing_fund[0]
                                    update_fields = ['gp_id = %s']
                                    update_values = [gp_id]

                                    # Placement Agent verknüpfen
                                    if pa_id:
                                        update_fields.append('placement_agent_id = %s')
                                        update_values.append(pa_id)

                                    if gp_data.get('strategy'):
                                        change_key = f"{fund_name}_strategy"
                                        fund_selected = selected['funds'].get(fund_name, {})
                                        if change_key not in fund_selected or fund_selected[change_key]:
                                            update_fields.append("strategy = %s")
                                            update_values.append(gp_data['strategy'])
                                        else:
                                            skipped_changes += 1

                                    for field in ['vintage_year', 'fund_size_m', 'currency', 'geography']:
                                        if fund_info['metadata'].get(field):
                                            change_key = f"{fund_name}_{field}"
                                            fund_selected = selected['funds'].get(fund_name, {})
                                            if change_key not in fund_selected or fund_selected[change_key]:
                                                update_fields.append(f"{field} = %s")
                                                update_values.append(fund_info['metadata'][field])
                                            else:
                                                skipped_changes += 1

                                    update_values.append(fund_id)
                                    cursor.execute(f"""
                                    UPDATE funds SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                                    WHERE fund_id = %s
                                    """, update_values)
                                    # Status aktualisieren (wenn im Excel explizit gesetzt)
                                    fund_status = fund_info['metadata'].get('status')
                                    if fund_status and fund_status != 'active':
                                        cursor.execute("SELECT status FROM funds WHERE fund_id = %s", (fund_id,))
                                        old_status = cursor.fetchone()[0] or 'active'
                                        if old_status != fund_status:
                                            cursor.execute("UPDATE funds SET status = %s WHERE fund_id = %s", (fund_status, fund_id))
                                            cursor.execute("""
                                            INSERT INTO fund_status_history (fund_id, old_status, new_status, changed_by, change_reason)
                                            VALUES (%s, %s, %s, 'excel_import', 'Status geaendert via Excel-Import')
                                            """, (fund_id, old_status, fund_status))

                                    # Pipeline-Meta aktualisieren
                                    pipeline_meta = fund_info['metadata'].get('pipeline_meta', {})
                                    if pipeline_meta:
                                        upsert_pipeline_meta(conn, fund_id, **pipeline_meta)

                                else:
                                    fund_status = fund_info['metadata'].get('status', 'active')
                                    cursor.execute("""
                                    INSERT INTO funds (fund_name, gp_id, placement_agent_id, strategy, vintage_year, fund_size_m, currency, geography, status)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    RETURNING fund_id
                                    """, (fund_name, gp_id, pa_id, gp_data.get('strategy'),
                                         fund_info['metadata'].get('vintage_year'),
                                         fund_info['metadata'].get('fund_size_m'), fund_info['metadata'].get('currency'),
                                         fund_info['metadata'].get('geography'), fund_status))
                                    fund_id = cursor.fetchone()[0]
                                    imported_funds += 1

                                    # Status-History loggen
                                    cursor.execute("""
                                    INSERT INTO fund_status_history (fund_id, old_status, new_status, changed_by, change_reason)
                                    VALUES (%s, NULL, %s, 'excel_import', 'Importiert via Excel')
                                    """, (fund_id, fund_status))

                                    # Pipeline-Meta speichern
                                    pipeline_meta = fund_info['metadata'].get('pipeline_meta', {})
                                    if pipeline_meta:
                                        upsert_pipeline_meta(conn, fund_id, **pipeline_meta)

                                for company in fund_info['companies']:
                                    company_name = company['company_name']

                                    cursor.execute("""
                                    SELECT history_id FROM portfolio_companies_history
                                    WHERE fund_id = %s AND company_name = %s AND reporting_date = %s
                                    """, (fund_id, company_name, reporting_date))
                                    existing_company = cursor.fetchone()

                                    if existing_company:
                                        update_fields = []
                                        update_values = []
                                        comp_key = f"{fund_name}_{company_name}"
                                        comp_selected = selected['companies'].get(comp_key, {})
                                        company_delete_fields = company.get('_delete_fields', set())  # NEU

                                        for field in ['investment_date', 'exit_date', 'ownership', 'invested_amount',
                                                     'realized_tvpi', 'unrealized_tvpi', 'entry_multiple', 'gross_irr']:
                                            change_key = f"{comp_key}_{field}"

                                            # NEU: Löschung prüfen
                                            if field in company_delete_fields:
                                                if change_key not in comp_selected or comp_selected[change_key]:
                                                    update_fields.append(f"{field} = %s")
                                                    update_values.append(None)  # NULL setzen
                                                else:
                                                    skipped_changes += 1
                                            elif field in company and company[field] is not None:
                                                if change_key not in comp_selected or comp_selected[change_key]:
                                                    update_fields.append(f"{field} = %s")
                                                    update_values.append(company[field])
                                                else:
                                                    skipped_changes += 1

                                        if update_fields:
                                            update_values.extend([fund_id, company_name, reporting_date])
                                            cursor.execute(f"""
                                            UPDATE portfolio_companies_history
                                            SET {', '.join(update_fields)}
                                            WHERE fund_id = %s AND company_name = %s AND reporting_date = %s
                                            """, update_values)
                                            updated_companies += 1
                                    else:
                                        cursor.execute("""
                                        INSERT INTO portfolio_companies_history
                                            (fund_id, company_name, reporting_date, investment_date, exit_date,
                                             ownership, invested_amount, realized_tvpi, unrealized_tvpi,
                                             entry_multiple, gross_irr)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                        """, (fund_id, company_name, reporting_date,
                                             company.get('investment_date'), company.get('exit_date'),
                                             company.get('ownership'), company.get('invested_amount', 0),
                                             company.get('realized_tvpi', 0), company.get('unrealized_tvpi', 0),
                                             company.get('entry_multiple'), company.get('gross_irr')))
                                        imported_companies += 1

                                # Metriken berechnen
                                cursor.execute("""
                                SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
                                FROM portfolio_companies_history
                                WHERE fund_id = %s AND reporting_date = %s
                                """, (fund_id, reporting_date))
                                investments = cursor.fetchall()

                                if investments:
                                    total_invested = sum(inv[1] for inv in investments if inv[1])
                                    if total_invested > 0:
                                        company_values = []
                                        total_realized_ccy = 0
                                        total_value_ccy = 0
                                        loss_invested = 0

                                        for comp, invested, real_tvpi, unreal_tvpi in investments:
                                            invested = invested or 0
                                            real_tvpi = real_tvpi or 0
                                            unreal_tvpi = unreal_tvpi or 0

                                            realized_ccy = real_tvpi * invested
                                            unrealized_ccy = unreal_tvpi * invested
                                            total_ccy = realized_ccy + unrealized_ccy

                                            company_values.append((comp, invested, total_ccy))
                                            total_realized_ccy += realized_ccy
                                            total_value_ccy += total_ccy

                                            if (real_tvpi + unreal_tvpi) < 1.0:
                                                loss_invested += invested

                                        company_values.sort(key=lambda x: x[2], reverse=True)

                                        top5_value = sum(cv[2] for cv in company_values[:5])
                                        top5_capital = sum(cv[1] for cv in company_values[:5])

                                        top5_value_pct = (top5_value / total_value_ccy * 100) if total_value_ccy > 0 else 0
                                        top5_capital_pct = (top5_capital / total_invested * 100) if total_invested > 0 else 0

                                        calc_tvpi = total_value_ccy / total_invested if total_invested > 0 else 0
                                        dpi = total_realized_ccy / total_invested if total_invested > 0 else 0

                                        realized_pct = (total_realized_ccy / total_value_ccy * 100) if total_value_ccy > 0 else 0
                                        loss_ratio = (loss_invested / total_invested * 100) if total_invested > 0 else 0

                                        metadata_delete_fields = fund_info.get('metadata_delete_fields', set())

                                        # Löschung prüfen
                                        if 'net_tvpi' in metadata_delete_fields:
                                            import_net_tvpi = None  # NULL setzen
                                        else:
                                            import_net_tvpi = fund_info['metadata'].get('net_tvpi')

                                        if 'net_irr' in metadata_delete_fields:
                                            import_net_irr = None  # NULL setzen
                                        else:
                                            import_net_irr = fund_info['metadata'].get('net_irr')

                                        cursor.execute("""
                                        INSERT INTO fund_metrics_history
                                            (fund_id, reporting_date, total_tvpi, net_tvpi, net_irr, dpi, top5_value_concentration,
                                             top5_capital_concentration, loss_ratio, realized_percentage, num_investments)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                        ON CONFLICT (fund_id, reporting_date) DO UPDATE SET
                                            total_tvpi = EXCLUDED.total_tvpi,
                                            net_tvpi = EXCLUDED.net_tvpi,
                                            net_irr = EXCLUDED.net_irr,
                                            dpi = EXCLUDED.dpi,
                                            top5_value_concentration = EXCLUDED.top5_value_concentration,
                                            top5_capital_concentration = EXCLUDED.top5_capital_concentration,
                                            loss_ratio = EXCLUDED.loss_ratio,
                                            realized_percentage = EXCLUDED.realized_percentage,
                                            num_investments = EXCLUDED.num_investments
                                        """, (fund_id, reporting_date, calc_tvpi, import_net_tvpi, import_net_irr, dpi, top5_value_pct,
                                              top5_capital_pct, loss_ratio, realized_pct, len(investments)))

                                        cursor.execute("""
                                        SELECT MAX(reporting_date) FROM portfolio_companies_history WHERE fund_id = %s
                                        """, (fund_id,))
                                        latest_date = cursor.fetchone()[0]
                                        latest_date_str = latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') else str(latest_date)

                                        if reporting_date == latest_date_str:
                                            cursor.execute("DELETE FROM portfolio_companies WHERE fund_id = %s", (fund_id,))

                                            cursor.execute("""
                                            INSERT INTO portfolio_companies 
                                                (fund_id, company_name, invested_amount, realized_tvpi, unrealized_tvpi,
                                                 investment_date, exit_date, entry_multiple, gross_irr, ownership)
                                            SELECT fund_id, company_name, invested_amount, realized_tvpi, unrealized_tvpi,
                                                   investment_date, exit_date, entry_multiple, gross_irr, ownership
                                            FROM portfolio_companies_history
                                            WHERE fund_id = %s AND reporting_date = %s
                                            """, (fund_id, reporting_date))

                                            cursor.execute("""
                                            INSERT INTO fund_metrics 
                                                (fund_id, total_tvpi, net_tvpi, net_irr, dpi, top5_value_concentration,
                                                 top5_capital_concentration, loss_ratio, realized_percentage, 
                                                 num_investments, calculation_date)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                            ON CONFLICT (fund_id) DO UPDATE SET
                                                total_tvpi = EXCLUDED.total_tvpi,
                                                net_tvpi = EXCLUDED.net_tvpi,
                                                net_irr = EXCLUDED.net_irr,
                                                dpi = EXCLUDED.dpi,
                                                top5_value_concentration = EXCLUDED.top5_value_concentration,
                                                top5_capital_concentration = EXCLUDED.top5_capital_concentration,
                                                loss_ratio = EXCLUDED.loss_ratio,
                                                realized_percentage = EXCLUDED.realized_percentage,
                                                num_investments = EXCLUDED.num_investments,
                                                calculation_date = EXCLUDED.calculation_date
                                            """, (fund_id, calc_tvpi, import_net_tvpi, import_net_irr, dpi, top5_value_pct,
                                                  top5_capital_pct, loss_ratio, realized_pct, 
                                                  len(investments), reporting_date))

                            conn.commit()

                        clear_cache()

                        st.session_state.import_preview = None
                        st.session_state.import_data = None
                        st.session_state.selected_changes = {'gp': {}, 'funds': {}, 'companies': {}}

                        st.success(f"""✅ Import erfolgreich!
                        - GP: {data['gp_data']['gp_name']}
                        - Neue Fonds: {imported_funds}
                        - Neue Companies: {imported_companies}
                        - Aktualisierte Companies: {updated_companies}
                        - Übersprungene Änderungen: {skipped_changes}
                        """)
                        st.session_state.filter_version += 1
                        st.toast("Import erfolgreich!")
                        st.rerun()

                    except Exception as e:
                        conn.rollback()
                        st.error(f"❌ Fehler: {e}")
                        import traceback
                        st.code(traceback.format_exc())

    # EDIT PORTFOLIO COMPANY
    with admin_tab2:
        st.subheader("🏢 Portfolio Company bearbeiten")

        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT reporting_date FROM portfolio_companies_history ORDER BY reporting_date DESC")
            available_pc_dates = [row[0].strftime('%Y-%m-%d') if isinstance(row[0], (date, datetime)) else row[0] for row in cursor.fetchall()]

        if not available_pc_dates:
            st.warning("Keine Portfolio Companies vorhanden.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                edit_pc_date = st.selectbox("📅 Stichtag auswählen", options=available_pc_dates, format_func=format_quarter, key="edit_pc_date")

            with col2:
                with conn.cursor() as cursor:
                    cursor.execute("""
                    SELECT DISTINCT f.fund_id, f.fund_name
                    FROM funds f
                    JOIN portfolio_companies_history pch ON f.fund_id = pch.fund_id
                    WHERE pch.reporting_date = %s
                    ORDER BY f.fund_name
                    """, (edit_pc_date,))
                    funds_with_pc = cursor.fetchall()

                if funds_with_pc:
                    fund_pc_dict = {f[1]: f[0] for f in funds_with_pc}
                    selected_pc_fund = st.selectbox("📁 Fonds auswählen", options=list(fund_pc_dict.keys()), key="edit_pc_fund")
                    selected_pc_fund_id = fund_pc_dict[selected_pc_fund]
                else:
                    st.warning("Keine Fonds für diesen Stichtag.")
                    selected_pc_fund_id = None

            if selected_pc_fund_id:
                with conn.cursor() as cursor:
                    cursor.execute("""
                    SELECT company_name FROM portfolio_companies_history
                    WHERE fund_id = %s AND reporting_date = %s
                    ORDER BY company_name
                    """, (selected_pc_fund_id, edit_pc_date))
                    companies = [row[0] for row in cursor.fetchall()]

                if companies:
                    selected_company = st.selectbox("🏢 Portfolio Company auswählen", options=companies, key="edit_pc_company")

                    # Daten der Company laden
                    with conn.cursor() as cursor:
                        cursor.execute("""
                        SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi,
                               investment_date, exit_date, entry_multiple, gross_irr, ownership
                        FROM portfolio_companies_history
                        WHERE fund_id = %s AND reporting_date = %s AND company_name = %s
                        """, (selected_pc_fund_id, edit_pc_date, selected_company))
                        pc_data = cursor.fetchone()

                    if pc_data:
                        st.markdown("---")
                        st.markdown(f"**Bearbeite:** {selected_company} | **Stichtag:** {format_quarter(edit_pc_date)}")

                        with st.form(f"edit_pc_form_{selected_pc_fund_id}_{selected_company}"):
                            col1, col2 = st.columns(2)

                            with col1:
                                st.markdown("##### Finanzdaten")
                                new_invested = st.number_input(
                                    "Investiert (Mio.)",
                                    value=float(pc_data[1]) if pc_data[1] else 0.0,
                                    min_value=0.0,
                                    step=0.1,
                                    format="%.2f"
                                )
                                new_realized_tvpi = st.number_input(
                                    "Realized TVPI",
                                    value=float(pc_data[2]) if pc_data[2] else 0.0,
                                    min_value=0.0,
                                    step=0.01,
                                    format="%.2f"
                                )
                                new_unrealized_tvpi = st.number_input(
                                    "Unrealized TVPI",
                                    value=float(pc_data[3]) if pc_data[3] else 0.0,
                                    min_value=0.0,
                                    step=0.01,
                                    format="%.2f"
                                )
                                new_entry_multiple = st.number_input(
                                    "Entry Multiple",
                                    value=float(pc_data[6]) if pc_data[6] else 0.0,
                                    min_value=0.0,
                                    step=0.1,
                                    format="%.1f"
                                )
                                new_ownership = st.number_input(
                                    "Ownership (%)",
                                    value=float(pc_data[8]) if pc_data[8] else 0.0,
                                    min_value=0.0,
                                    max_value=100.0,
                                    step=0.01,
                                    format="%.2f"
                                )

                            with col2:
                                st.markdown("##### Datums- und Renditedaten")

                                # Investment Date - Monat/Jahr Auswahl
                                if pc_data[4]:
                                    try:
                                        inv_date_parsed = pd.to_datetime(pc_data[4])
                                        inv_month_default = inv_date_parsed.month
                                        inv_year_default = inv_date_parsed.year
                                    except (ValueError, TypeError):
                                        inv_month_default = 1
                                        inv_year_default = 2020
                                else:
                                    inv_month_default = 1
                                    inv_year_default = 2020

                                st.markdown("**Investitionsdatum**")
                                inv_col1, inv_col2, inv_col3 = st.columns([2, 2, 1])
                                with inv_col1:
                                    inv_month = st.selectbox(
                                        "Monat",
                                        options=[0] + list(range(1, 13)),
                                        index=inv_month_default if pc_data[4] else 0,
                                        format_func=lambda x: '-' if x == 0 else ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'][x-1],
                                        key=f"inv_month_{selected_company}"
                                    )
                                with inv_col2:
                                    inv_year = st.selectbox(
                                        "Jahr",
                                        options=[0] + list(range(2000, 2031)),
                                        index=(inv_year_default - 2000 + 1) if pc_data[4] and 2000 <= inv_year_default <= 2030 else 0,
                                        format_func=lambda x: '-' if x == 0 else str(x),
                                        key=f"inv_year_{selected_company}"
                                    )
                                with inv_col3:
                                    if inv_month > 0 and inv_year > 0:
                                        st.markdown("✓")
                                    else:
                                        st.markdown("")

                                if inv_month > 0 and inv_year > 0:
                                    new_investment_date = date(inv_year, inv_month, 1)
                                else:
                                    new_investment_date = None

                                # Exit Date - Monat/Jahr Auswahl
                                if pc_data[5]:
                                    try:
                                        exit_date_parsed = pd.to_datetime(pc_data[5])
                                        exit_month_default = exit_date_parsed.month
                                        exit_year_default = exit_date_parsed.year
                                    except (ValueError, TypeError):
                                        exit_month_default = 1
                                        exit_year_default = 2020
                                else:
                                    exit_month_default = 1
                                    exit_year_default = 2020

                                st.markdown("**Exitdatum**")
                                exit_col1, exit_col2, exit_col3 = st.columns([2, 2, 1])
                                with exit_col1:
                                    exit_month = st.selectbox(
                                        "Monat",
                                        options=[0] + list(range(1, 13)),
                                        index=exit_month_default if pc_data[5] else 0,
                                        format_func=lambda x: '-' if x == 0 else ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'][x-1],
                                        key=f"exit_month_{selected_company}"
                                    )
                                with exit_col2:
                                    exit_year = st.selectbox(
                                        "Jahr",
                                        options=[0] + list(range(2000, 2031)),
                                        index=(exit_year_default - 2000 + 1) if pc_data[5] and 2000 <= exit_year_default <= 2030 else 0,
                                        format_func=lambda x: '-' if x == 0 else str(x),
                                        key=f"exit_year_{selected_company}"
                                    )
                                with exit_col3:
                                    if exit_month > 0 and exit_year > 0:
                                        st.markdown("✓")
                                    else:
                                        st.markdown("")

                                if exit_month > 0 and exit_year > 0:
                                    new_exit_date = date(exit_year, exit_month, 1)
                                else:
                                    new_exit_date = None

                                new_gross_irr = st.number_input(
                                    "Gross IRR (%)",
                                    value=float(pc_data[7]) if pc_data[7] else 0.0,
                                    step=0.1,
                                    format="%.1f"
                                )

                            # Berechnung anzeigen
                            total_tvpi = new_realized_tvpi + new_unrealized_tvpi
                            total_value = total_tvpi * new_invested
                            st.markdown(f"**Berechnete Werte:** Total TVPI: {total_tvpi:.2f}x | Gesamtwert: {total_value:,.2f} Mio.")

                            submitted_pc = st.form_submit_button("💾 Portfolio Company speichern", type="primary")

                        if submitted_pc:
                            try:
                                with conn.cursor() as cursor:
                                    # History-Tabelle aktualisieren
                                    cursor.execute("""
                                    UPDATE portfolio_companies_history
                                    SET invested_amount = %s, realized_tvpi = %s, unrealized_tvpi = %s,
                                        investment_date = %s, exit_date = %s, entry_multiple = %s, gross_irr = %s, ownership = %s
                                    WHERE fund_id = %s AND reporting_date = %s AND company_name = %s
                                    """, (
                                        new_invested, new_realized_tvpi, new_unrealized_tvpi,
                                        new_investment_date, new_exit_date,
                                        new_entry_multiple if new_entry_multiple > 0 else None,
                                        new_gross_irr if new_gross_irr != 0 else None,
                                        new_ownership if new_ownership > 0 else None,
                                        selected_pc_fund_id, edit_pc_date, selected_company
                                    ))

                                    # Auch aktuelle Tabelle aktualisieren wenn es der neueste Stichtag ist
                                    cursor.execute("""
                                    SELECT MAX(reporting_date) FROM portfolio_companies_history WHERE fund_id = %s
                                    """, (selected_pc_fund_id,))
                                    latest_date_result = cursor.fetchone()
                                    latest_date = latest_date_result[0].strftime('%Y-%m-%d') if latest_date_result and latest_date_result[0] else None

                                    if edit_pc_date == latest_date:
                                        cursor.execute("""
                                        UPDATE portfolio_companies
                                        SET invested_amount = %s, realized_tvpi = %s, unrealized_tvpi = %s,
                                            investment_date = %s, exit_date = %s, entry_multiple = %s, gross_irr = %s, ownership = %s
                                        WHERE fund_id = %s AND company_name = %s
                                        """, (
                                            new_invested, new_realized_tvpi, new_unrealized_tvpi,
                                            new_investment_date, new_exit_date,
                                            new_entry_multiple if new_entry_multiple > 0 else None,
                                            new_gross_irr if new_gross_irr != 0 else None,
                                            new_ownership if new_ownership > 0 else None,
                                            selected_pc_fund_id, selected_company
                                        ))

                                    conn.commit()
                                clear_cache()

                                st.toast(f"'{selected_company}' aktualisiert!")
                                st.rerun()

                            except Exception as e:
                                conn.rollback()
                                st.error(f"❌ Fehler: {e}")

    # EDIT FUND
    with admin_tab3:
        st.subheader("Fund bearbeiten")
        with conn.cursor() as cursor:
            cursor.execute("SELECT fund_id, fund_name FROM funds ORDER BY fund_name")
            existing_funds = cursor.fetchall()

        if not existing_funds:
            st.warning("Keine Fonds vorhanden.")
        else:
            fund_dict_edit = {f[1]: f[0] for f in existing_funds}
            edit_fund_name = st.selectbox("Fund auswählen", options=list(fund_dict_edit.keys()), key="edit_fund_select")

            if edit_fund_name:
                edit_fund_id = fund_dict_edit[edit_fund_name]
                fund_data = pd.read_sql_query("SELECT fund_name, gp_id, placement_agent_id, vintage_year, strategy, geography, fund_size_m, currency, notes FROM funds WHERE fund_id = %s", conn, params=(edit_fund_id,))
                fund_metrics_data = pd.read_sql_query("SELECT net_tvpi, net_irr FROM fund_metrics WHERE fund_id = %s", conn, params=(edit_fund_id,))

                with conn.cursor() as cursor:
                    cursor.execute("SELECT gp_id, gp_name FROM gps ORDER BY gp_name")
                    gp_list = cursor.fetchall()
                    cursor.execute("SELECT pa_id, pa_name FROM placement_agents ORDER BY pa_name")
                    pa_list = cursor.fetchall()

                if not fund_data.empty:
                    gp_dict = {gp[1]: gp[0] for gp in gp_list}
                    gp_names = list(gp_dict.keys())
                    current_gp_id = fund_data['gp_id'].iloc[0]
                    current_gp_name = next((name for name, gid in gp_dict.items() if gid == current_gp_id), None)

                    # Placement Agent Dictionary mit "(Kein PA)" Option
                    pa_dict = {"(Kein PA)": None}
                    pa_dict.update({pa[1]: pa[0] for pa in pa_list})
                    pa_names = list(pa_dict.keys())
                    current_pa_id = fund_data['placement_agent_id'].iloc[0]
                    current_pa_name = next((name for name, pid in pa_dict.items() if pid == current_pa_id), "(Kein PA)")

                    with st.form(f"edit_fund_form_{edit_fund_id}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            new_fund_name = st.text_input("Fund Name", value=fund_data['fund_name'].iloc[0] or "")
                            if gp_names:
                                gp_index = gp_names.index(current_gp_name) if current_gp_name in gp_names else 0
                                selected_gp_name = st.selectbox("GP", options=gp_names, index=gp_index)
                                new_gp_id = gp_dict[selected_gp_name]
                            else:
                                new_gp_id = None
                            # Placement Agent Dropdown
                            pa_index = pa_names.index(current_pa_name) if current_pa_name in pa_names else 0
                            selected_pa_name = st.selectbox("Placement Agent", options=pa_names, index=pa_index)
                            new_pa_id = pa_dict[selected_pa_name]

                            new_vintage = st.number_input("Vintage Year", value=int(fund_data['vintage_year'].iloc[0]) if pd.notna(fund_data['vintage_year'].iloc[0]) else 2020, min_value=1990, max_value=2030)
                            new_strategy = st.text_input("Strategy", value=fund_data['strategy'].iloc[0] or "")
                        with col2:
                            new_geography = st.text_input("Geography", value=fund_data['geography'].iloc[0] or "")
                            new_fund_size = st.number_input("Fund Size (Mio.)", value=float(fund_data['fund_size_m'].iloc[0]) if pd.notna(fund_data['fund_size_m'].iloc[0]) else 0.0, min_value=0.0, step=1.0, format="%.2f")
                            currency_options = ['EUR', 'USD', 'GBP', 'CHF', 'JPY', 'CNY', 'Other']
                            current_currency = fund_data['currency'].iloc[0] if fund_data['currency'].iloc[0] in currency_options else 'EUR'
                            currency_idx = currency_options.index(current_currency) if current_currency in currency_options else 0
                            new_currency = st.selectbox("Währung", options=currency_options, index=currency_idx)

                        st.markdown("---")
                        st.markdown("**📊 Fund-Level Metriken**")
                        col_net1, col_net2 = st.columns(2)
                        with col_net1:
                            current_net_tvpi = fund_metrics_data['net_tvpi'].iloc[0] if not fund_metrics_data.empty and pd.notna(fund_metrics_data['net_tvpi'].iloc[0]) else None
                            new_net_tvpi = st.number_input("Net TVPI", value=float(current_net_tvpi) if current_net_tvpi else 0.0, min_value=0.0, step=0.01, format="%.2f", help="Net TVPI nach Gebühren und Carry")
                        with col_net2:
                            current_net_irr = fund_metrics_data['net_irr'].iloc[0] if not fund_metrics_data.empty and pd.notna(fund_metrics_data['net_irr'].iloc[0]) else None
                            new_net_irr = st.number_input("Net IRR (%)", value=float(current_net_irr) if current_net_irr else 0.0, step=0.1, format="%.1f", help="Net IRR nach Gebühren und Carry")

                        new_notes = st.text_area("Notes", value=fund_data['notes'].iloc[0] or "")

                        if st.form_submit_button("💾 Speichern", type="primary"):
                            with conn.cursor() as cursor:
                                # Fund-Daten aktualisieren
                                cursor.execute("""
                                UPDATE funds SET fund_name=%s, gp_id=%s, placement_agent_id=%s, vintage_year=%s, strategy=%s, geography=%s, 
                                fund_size_m=%s, currency=%s, notes=%s, updated_at=CURRENT_TIMESTAMP WHERE fund_id=%s
                                """, (new_fund_name, new_gp_id, new_pa_id, new_vintage, new_strategy, new_geography, new_fund_size, new_currency, new_notes, edit_fund_id))

                                # NEU: Net TVPI und Net IRR in fund_metrics speichern
                                cursor.execute("""
                                INSERT INTO fund_metrics (fund_id, net_tvpi, net_irr)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (fund_id) DO UPDATE SET
                                    net_tvpi = EXCLUDED.net_tvpi,
                                    net_irr = EXCLUDED.net_irr
                                """, (edit_fund_id, 
                                      new_net_tvpi if new_net_tvpi > 0 else None, 
                                      new_net_irr if new_net_irr != 0 else None))

                                conn.commit()
                            clear_cache()
                            st.toast(f"Fund '{new_fund_name}' aktualisiert!")
                            st.session_state.filter_version += 1
                            st.rerun()

    # EDIT GP
    with admin_tab4:
        st.subheader("👔 GP bearbeiten")
        with conn.cursor() as cursor:
            cursor.execute("SELECT gp_id, gp_name FROM gps ORDER BY gp_name")
            existing_gps = cursor.fetchall()

        if not existing_gps:
            st.warning("Keine GPs vorhanden.")
        else:
            gp_dict_edit = {gp[1]: gp[0] for gp in existing_gps}
            edit_gp_name = st.selectbox("GP auswählen", options=list(gp_dict_edit.keys()), key="edit_gp_select")

            if edit_gp_name:
                edit_gp_id = gp_dict_edit[edit_gp_name]
                gp_data = pd.read_sql_query("""
                SELECT gp_name, sector, headquarters, website, rating, last_meeting, next_raise_estimate, notes,
                       contact1_name, contact1_function, contact1_email, contact1_phone,
                       contact2_name, contact2_function, contact2_email, contact2_phone 
                FROM gps WHERE gp_id = %s
                """, conn, params=(edit_gp_id,))

                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM funds WHERE gp_id = %s", (edit_gp_id,))
                    fund_count = cursor.fetchone()[0]

                st.info(f"📊 Dieser GP hat {fund_count} zugeordnete Fonds")

                if not gp_data.empty:
                    with st.form(f"edit_gp_form_{edit_gp_id}"):
                        st.markdown("#### Basis-Informationen")
                        col1, col2 = st.columns(2)
                        with col1:
                            new_gp_name = st.text_input("GP Name *", value=gp_data['gp_name'].iloc[0] or "")
                            new_sector = st.text_input("Sektor", value=gp_data['sector'].iloc[0] or "")
                            new_headquarters = st.text_input("Headquarters", value=gp_data['headquarters'].iloc[0] or "")
                            new_website = st.text_input("Website", value=gp_data['website'].iloc[0] or "")
                        with col2:
                            rating_options = ['', 'A', 'B', 'C', 'D', 'E', 'P', 'U']
                            current_rating = gp_data['rating'].iloc[0]
                            current_rating_idx = rating_options.index(current_rating) if current_rating in rating_options else 0
                            new_rating = st.selectbox("Rating", options=rating_options, index=current_rating_idx)

                            last_meeting_val = gp_data['last_meeting'].iloc[0]
                            new_last_meeting = st.date_input("Last Meeting", value=pd.to_datetime(last_meeting_val).date() if pd.notna(last_meeting_val) else None)

                            next_raise_val = gp_data['next_raise_estimate'].iloc[0]
                            new_next_raise = st.date_input("Next Raise Estimate", value=pd.to_datetime(next_raise_val).date() if pd.notna(next_raise_val) else None)

                        st.markdown("#### Kontaktperson 1")
                        col1, col2 = st.columns(2)
                        with col1:
                            new_c1_name = st.text_input("Name", value=gp_data['contact1_name'].iloc[0] or "", key=f"c1n_{edit_gp_id}")
                            new_c1_func = st.text_input("Funktion", value=gp_data['contact1_function'].iloc[0] or "", key=f"c1f_{edit_gp_id}")
                        with col2:
                            new_c1_email = st.text_input("E-Mail", value=gp_data['contact1_email'].iloc[0] or "", key=f"c1e_{edit_gp_id}")
                            new_c1_phone = st.text_input("Telefon", value=gp_data['contact1_phone'].iloc[0] or "", key=f"c1p_{edit_gp_id}")

                        st.markdown("#### Kontaktperson 2")
                        col1, col2 = st.columns(2)
                        with col1:
                            new_c2_name = st.text_input("Name", value=gp_data['contact2_name'].iloc[0] or "", key=f"c2n_{edit_gp_id}")
                            new_c2_func = st.text_input("Funktion", value=gp_data['contact2_function'].iloc[0] or "", key=f"c2f_{edit_gp_id}")
                        with col2:
                            new_c2_email = st.text_input("E-Mail", value=gp_data['contact2_email'].iloc[0] or "", key=f"c2e_{edit_gp_id}")
                            new_c2_phone = st.text_input("Telefon", value=gp_data['contact2_phone'].iloc[0] or "", key=f"c2p_{edit_gp_id}")

                        new_gp_notes = st.text_area("Notizen", value=gp_data['notes'].iloc[0] or "")

                        if st.form_submit_button("💾 GP Speichern", type="primary"):
                            if new_gp_name.strip():
                                with conn.cursor() as cursor:
                                    cursor.execute("""
                                    UPDATE gps SET gp_name=%s, sector=%s, headquarters=%s, website=%s, rating=%s, 
                                    last_meeting=%s, next_raise_estimate=%s, notes=%s,
                                    contact1_name=%s, contact1_function=%s, contact1_email=%s, contact1_phone=%s,
                                    contact2_name=%s, contact2_function=%s, contact2_email=%s, contact2_phone=%s,
                                    updated_at=CURRENT_TIMESTAMP 
                                    WHERE gp_id=%s
                                    """, (new_gp_name.strip(), new_sector or None, new_headquarters or None, 
                                          new_website or None, new_rating or None, new_last_meeting, new_next_raise, 
                                          new_gp_notes or None,
                                          new_c1_name or None, new_c1_func or None, new_c1_email or None, new_c1_phone or None,
                                          new_c2_name or None, new_c2_func or None, new_c2_email or None, new_c2_phone or None,
                                          edit_gp_id))
                                    conn.commit()
                                clear_cache()
                                st.toast(f"GP '{new_gp_name}' aktualisiert!")
                                st.session_state.filter_version += 1
                                st.rerun()
                            else:
                                st.error("GP Name ist erforderlich!")

        st.markdown("---")
        st.subheader("➕ Neuen GP anlegen")
        with st.form("new_gp_form"):
            new_gp_name_input = st.text_input("GP Name *", key="new_gp_name")
            if st.form_submit_button("➕ GP anlegen", type="primary"):
                if new_gp_name_input.strip():
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute("INSERT INTO gps (gp_name) VALUES (%s)", (new_gp_name_input.strip(),))
                            conn.commit()
                        clear_cache()
                        st.toast(f"GP '{new_gp_name_input}' angelegt!")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        conn.rollback()
                        st.error("Ein GP mit diesem Namen existiert bereits!")
                else:
                    st.error("Bitte GP Namen eingeben!")

    # EDIT PLACEMENT AGENT
    with admin_tab5:
        st.subheader("🤝 Placement Agent bearbeiten")

        with conn.cursor() as cursor:
            cursor.execute("SELECT pa_id, pa_name FROM placement_agents ORDER BY pa_name")
            pa_list = cursor.fetchall()

        # Immer "(Neu erstellen)" als Option anbieten
        pa_dict = {pa[1]: pa[0] for pa in pa_list} if pa_list else {}
        edit_pa_name = st.selectbox("Placement Agent auswählen", options=["(Neu erstellen)"] + list(pa_dict.keys()), key="edit_pa_select")

        if edit_pa_name != "(Neu erstellen)":
            edit_pa_id = pa_dict[edit_pa_name]
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM placement_agents WHERE pa_id = %s", (edit_pa_id,))
                pa_data_row = cursor.fetchone()
                pa_columns = [desc[0] for desc in cursor.description]
                pa_info = dict(zip(pa_columns, pa_data_row))
        else:
            edit_pa_id = None
            pa_info = {}

        # Formular mit dynamischem Key basierend auf ausgewähltem PA
        form_key = f"edit_pa_form_{edit_pa_id if edit_pa_id else 'new'}"

        with st.form(form_key):
            col1, col2 = st.columns(2)
            with col1:
                new_pa_name = st.text_input("PA Name *", value=pa_info.get('pa_name', ''))
                new_pa_rating = st.text_input("Rating", value=pa_info.get('rating') or '')
                new_pa_hq = st.text_input("Headquarters", value=pa_info.get('headquarters') or '')
                new_pa_website = st.text_input("Website", value=pa_info.get('website') or '')
                pa_last_meeting = pa_info.get('last_meeting')
                new_pa_last_meeting = st.date_input("Last Meeting", value=pa_last_meeting if pa_last_meeting else None)
            with col2:
                st.markdown("**Kontaktperson**")
                new_pa_contact1_name = st.text_input("Kontakt Name", value=pa_info.get('contact1_name') or '')
                new_pa_contact1_function = st.text_input("Kontakt Funktion", value=pa_info.get('contact1_function') or '')
                new_pa_contact1_email = st.text_input("Kontakt E-Mail", value=pa_info.get('contact1_email') or '')
                new_pa_contact1_phone = st.text_input("Kontakt Telefon", value=pa_info.get('contact1_phone') or '')

            if st.form_submit_button("💾 Placement Agent speichern", type="primary"):
                if new_pa_name:
                    with conn.cursor() as cursor:
                        if edit_pa_id:
                            cursor.execute("""
                            UPDATE placement_agents SET pa_name = %s, rating = %s, headquarters = %s, website = %s, last_meeting = %s,
                                   contact1_name = %s, contact1_function = %s, contact1_email = %s, contact1_phone = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE pa_id = %s
                            """, (new_pa_name, new_pa_rating or None, new_pa_hq or None, new_pa_website or None, new_pa_last_meeting,
                                 new_pa_contact1_name or None, new_pa_contact1_function or None, new_pa_contact1_email or None, new_pa_contact1_phone or None, edit_pa_id))
                            conn.commit()
                            clear_cache()
                            st.toast(f"Placement Agent '{new_pa_name}' aktualisiert!")
                            st.rerun()
                        else:
                            cursor.execute("SELECT pa_id FROM placement_agents WHERE pa_name = %s", (new_pa_name,))
                            if cursor.fetchone():
                                st.error("Ein Placement Agent mit diesem Namen existiert bereits!")
                            else:
                                cursor.execute("""
                                INSERT INTO placement_agents (pa_name, rating, headquarters, website, last_meeting,
                                                              contact1_name, contact1_function, contact1_email, contact1_phone)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """, (new_pa_name, new_pa_rating or None, new_pa_hq or None, new_pa_website or None, new_pa_last_meeting,
                                     new_pa_contact1_name or None, new_pa_contact1_function or None, new_pa_contact1_email or None, new_pa_contact1_phone or None))
                                conn.commit()
                                clear_cache()
                                st.toast(f"Placement Agent '{new_pa_name}' erstellt!")
                                st.rerun()
                else:
                    st.error("Bitte PA Namen eingeben!")

    # FUND STATUS MANAGEMENT
    with admin_tab6:
        st.subheader("Fund Status verwalten")

        status_mode = st.radio("Modus", ["Einzelner Fund", "Bulk Status-Aenderung", "Direkt setzen (Ersteinrichtung)"], key="status_mode", horizontal=True)

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT f.fund_id, f.fund_name, COALESCE(f.status, 'active') as status, g.gp_name
                FROM funds f LEFT JOIN gps g ON f.gp_id = g.gp_id
                ORDER BY f.fund_name
            """)
            all_funds_status = cursor.fetchall()

        if not all_funds_status:
            st.info("Keine Fonds vorhanden.")
        elif status_mode == "Einzelner Fund":
            fund_options = {f"{name} [{STATUS_LABELS.get(status, status)}]": (fid, status)
                           for fid, name, status, gp in all_funds_status}
            selected_fund_label = st.selectbox("Fonds", options=list(fund_options.keys()), key="status_single_fund")
            fund_id, current_status = fund_options[selected_fund_label]

            st.info(f"Aktueller Status: **{STATUS_LABELS.get(current_status, current_status)}**")

            valid_next = VALID_TRANSITIONS.get(current_status, [])
            force = st.checkbox("Erzwingen (ungueltige Transition)", key="force_single")

            if force:
                st.warning("Alle Status-Optionen verfuegbar. Transition-Regeln werden ignoriert.")
                status_options = [s for s in ALL_STATUSES if s != current_status]
            else:
                status_options = list(valid_next)

            if not status_options:
                st.info("Keine erlaubten Uebergaenge fuer diesen Status." + (" Aktivieren Sie 'Erzwingen' fuer Admin-Override." if not force else ""))
            else:
                new_status = st.selectbox("Neuer Status", options=status_options,
                                          format_func=lambda s: STATUS_LABELS.get(s, s), key="status_single_new")
                reason = st.text_input("Grund", key="reason_single")

                if st.button("Status aendern", key="btn_status_single"):
                    try:
                        if force:
                            force_change_fund_status(conn, fund_id, new_status, 'admin', reason)
                        else:
                            change_fund_status(conn, fund_id, new_status, 'admin', reason)
                        clear_cache()
                        st.success(f"Status geaendert: {STATUS_LABELS.get(current_status, current_status)} → {STATUS_LABELS.get(new_status, new_status)}")
                        st.rerun()
                    except ValueError as e:
                        st.error(f"Fehler: {e}")

        elif status_mode == "Bulk Status-Aenderung":
            filter_status = st.selectbox("Aktueller Status filtern", list(ALL_STATUSES),
                                         format_func=lambda s: STATUS_LABELS.get(s, s), key="status_bulk_filter")

            matching_funds = [(fid, name) for fid, name, status, gp in all_funds_status if status == filter_status]

            if not matching_funds:
                st.info(f"Keine Fonds mit Status '{STATUS_LABELS.get(filter_status, filter_status)}'.")
            else:
                selected_funds = st.multiselect("Fonds auswaehlen",
                    options=matching_funds,
                    format_func=lambda x: x[1], key="status_bulk_select")

                force = st.checkbox("Erzwingen", key="force_bulk")
                valid_next = VALID_TRANSITIONS.get(filter_status, [])
                target_options = [s for s in ALL_STATUSES if s != filter_status] if force else list(valid_next)

                if not target_options:
                    st.info("Keine erlaubten Uebergaenge." + (" Aktivieren Sie 'Erzwingen'." if not force else ""))
                else:
                    target_status = st.selectbox("Ziel-Status", target_options,
                                                 format_func=lambda s: STATUS_LABELS.get(s, s), key="status_bulk_target")
                    reason = st.text_input("Grund", key="reason_bulk")

                    if selected_funds and st.button(f"{len(selected_funds)} Fonds aendern", key="btn_status_bulk"):
                        errors = []
                        for fund_id, fund_name in selected_funds:
                            try:
                                if force:
                                    force_change_fund_status(conn, fund_id, target_status, 'admin', reason)
                                else:
                                    change_fund_status(conn, fund_id, target_status, 'admin', reason)
                            except ValueError as e:
                                errors.append(f"{fund_name}: {e}")
                        clear_cache()
                        if errors:
                            st.warning(f"{len(selected_funds) - len(errors)} geaendert, {len(errors)} Fehler:")
                            for err in errors:
                                st.error(err)
                        else:
                            st.success(f"{len(selected_funds)} Fonds auf {STATUS_LABELS.get(target_status, target_status)} gesetzt.")
                        st.rerun()

        elif status_mode == "Direkt setzen (Ersteinrichtung)":
            st.info("Fuer einmalige Ersteinrichtung: Status direkt setzen ohne Transition-Regeln.")

            changes = {}
            for fund_id, fund_name, current_status, gp_name in all_funds_status:
                col1, col2, col3 = st.columns([3, 2, 2])
                with col1:
                    st.text(f"{fund_name} ({gp_name or '-'})")
                with col2:
                    st.text(f"Aktuell: {STATUS_LABELS.get(current_status, current_status)}")
                with col3:
                    current_idx = list(ALL_STATUSES).index(current_status) if current_status in ALL_STATUSES else 0
                    new = st.selectbox("Neuer Status", list(ALL_STATUSES),
                        index=current_idx,
                        format_func=lambda s: STATUS_LABELS.get(s, s),
                        key=f"direct_{fund_id}")
                    if new != current_status:
                        changes[fund_id] = (current_status, new)

            reason = st.text_input("Grund fuer alle Aenderungen", key="reason_direct",
                                   value="Ersteinrichtung Status")

            if changes and st.button(f"{len(changes)} Aenderungen speichern", key="btn_status_direct"):
                for fid, (old, new) in changes.items():
                    force_change_fund_status(conn, fid, new, 'admin', reason)
                clear_cache()
                st.success(f"{len(changes)} Status-Aenderungen gespeichert.")
                st.rerun()
            elif not changes:
                st.info("Keine Aenderungen vorgenommen.")

    # DELETE FUND
    with admin_tab7:
        st.subheader("Fund löschen")
        st.warning("⚠️ Diese Aktion kann nicht rückgängig gemacht werden!")

        with conn.cursor() as cursor:
            cursor.execute("SELECT fund_id, fund_name FROM funds ORDER BY fund_name")
            delete_fund_list = cursor.fetchall()

        if delete_fund_list:
            delete_fund_dict = {f[1]: f[0] for f in delete_fund_list}
            delete_fund_name = st.selectbox("Fund zum Löschen", options=list(delete_fund_dict.keys()), key="delete_fund_select")

            if delete_fund_name:
                delete_fund_id = delete_fund_dict[delete_fund_name]
                confirm_delete = st.checkbox(f"Ich bestätige, dass ich '{delete_fund_name}' löschen möchte", key="confirm_delete_fund")

                if confirm_delete and st.button("🗑️ Fund LÖSCHEN", type="primary", key="delete_fund_btn"):
                    with conn.cursor() as cursor:
                        cursor.execute("DELETE FROM fund_metrics WHERE fund_id = %s", (delete_fund_id,))
                        cursor.execute("DELETE FROM portfolio_companies WHERE fund_id = %s", (delete_fund_id,))
                        cursor.execute("DELETE FROM fund_metrics_history WHERE fund_id = %s", (delete_fund_id,))
                        cursor.execute("DELETE FROM portfolio_companies_history WHERE fund_id = %s", (delete_fund_id,))
                        cursor.execute("DELETE FROM funds WHERE fund_id = %s", (delete_fund_id,))
                        conn.commit()
                    clear_cache()
                    st.toast(f"Fund '{delete_fund_name}' gelöscht!")
                    st.session_state.filter_version += 1
                    st.rerun()

    # DELETE GP
    with admin_tab8:
        st.subheader("GP löschen")
        st.warning("⚠️ Diese Aktion kann nicht rückgängig gemacht werden!")

        with conn.cursor() as cursor:
            cursor.execute("SELECT gp_id, gp_name FROM gps ORDER BY gp_name")
            delete_gp_list = cursor.fetchall()

        if delete_gp_list:
            delete_gp_dict = {gp[1]: gp[0] for gp in delete_gp_list}
            delete_gp_name = st.selectbox("GP zum Löschen", options=list(delete_gp_dict.keys()), key="delete_gp_select")

            if delete_gp_name:
                delete_gp_id = delete_gp_dict[delete_gp_name]

                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM funds WHERE gp_id = %s", (delete_gp_id,))
                    fund_count = cursor.fetchone()[0]

                if fund_count > 0:
                    st.error(f"❌ Dieser GP hat noch {fund_count} zugeordnete Fonds! Bitte erst die Fonds löschen oder einem anderen GP zuordnen.")
                else:
                    confirm_delete_gp = st.checkbox(f"Ich bestätige, dass ich '{delete_gp_name}' löschen möchte", key="confirm_delete_gp")

                    if confirm_delete_gp and st.button("🗑️ GP LÖSCHEN", type="primary", key="delete_gp_btn"):
                        with conn.cursor() as cursor:
                            cursor.execute("DELETE FROM gps WHERE gp_id = %s", (delete_gp_id,))
                            conn.commit()
                        clear_cache()
                        st.toast(f"GP '{delete_gp_name}' gelöscht!")
                        st.rerun()

    # DELETE PLACEMENT AGENT
    with admin_tab9:
        st.subheader("Placement Agent löschen")
        st.warning("⚠️ Diese Aktion kann nicht rückgängig gemacht werden!")

        with conn.cursor() as cursor:
            cursor.execute("SELECT pa_id, pa_name FROM placement_agents ORDER BY pa_name")
            delete_pa_list = cursor.fetchall()

        if delete_pa_list:
            delete_pa_dict = {pa[1]: pa[0] for pa in delete_pa_list}
            delete_pa_name = st.selectbox("Placement Agent zum Löschen", options=list(delete_pa_dict.keys()), key="delete_pa_select")

            if delete_pa_name:
                delete_pa_id = delete_pa_dict[delete_pa_name]

                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM funds WHERE placement_agent_id = %s", (delete_pa_id,))
                    pa_fund_count = cursor.fetchone()[0]

                if pa_fund_count > 0:
                    st.error(f"❌ Dieser Placement Agent hat noch {pa_fund_count} zugeordnete Fonds! Bitte erst die Fonds einem anderen PA zuordnen oder die Zuordnung entfernen.")
                else:
                    confirm_delete_pa = st.checkbox(f"Ich bestätige, dass ich '{delete_pa_name}' löschen möchte", key="confirm_delete_pa")

                    if confirm_delete_pa and st.button("🗑️ Placement Agent LÖSCHEN", type="primary", key="delete_pa_btn"):
                        with conn.cursor() as cursor:
                            cursor.execute("DELETE FROM placement_agents WHERE pa_id = %s", (delete_pa_id,))
                            conn.commit()
                        clear_cache()
                        st.toast(f"Placement Agent '{delete_pa_name}' gelöscht!")
                        st.rerun()
        else:
            st.info("Keine Placement Agents vorhanden.")

    # ================================================================
    # FX-Verwaltung
    # ================================================================
    st.markdown("---")
    conn_id = id(conn)
    render_fx_management(conn, conn_id)

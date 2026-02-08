"""
Cashflow Planning Tool — FX-Raten Verwaltung (Admin-Tab)

Bietet:
  - Bestehende Raten anzeigen/löschen
  - Neue Rate erfassen (Form)
  - CSV/Excel Bulk-Import
"""

import streamlit as st
import pandas as pd
from datetime import date

from database import clear_cache
from cashflow_db import (
    get_all_exchange_rates, delete_exchange_rate, insert_exchange_rate
)

COMMON_PAIRS = [
    ('EUR', 'USD'), ('EUR', 'CHF'), ('EUR', 'GBP'),
    ('USD', 'CHF'), ('USD', 'GBP'), ('GBP', 'CHF'),
]
CURRENCIES = ['EUR', 'USD', 'CHF', 'GBP']


def render_fx_management(conn, conn_id):
    """Rendert FX-Verwaltung im Admin-Tab."""

    with st.expander("💱 Wechselkurse verwalten", expanded=False):

        # --- Bestehende Raten ---
        rates = get_all_exchange_rates(conn)

        if rates:
            st.markdown("**Bestehende Wechselkurse**")
            rates_df = pd.DataFrame(rates)
            display_df = rates_df[['from_currency', 'to_currency', 'rate_date', 'rate']].copy()
            display_df.columns = ['Von', 'Nach', 'Datum', 'Rate']
            display_df['Datum'] = pd.to_datetime(display_df['Datum']).dt.strftime('%Y-%m-%d')
            display_df['Rate'] = display_df['Rate'].apply(lambda x: f"{x:.6f}")
            st.dataframe(display_df, hide_index=True, use_container_width=True)

            # Löschen
            with st.popover("🗑️ Rate löschen"):
                delete_options = {
                    f"{r['from_currency']}/{r['to_currency']} {r['rate_date']} ({r['rate']:.4f})": r['rate_id']
                    for r in rates
                }
                selected_delete = st.selectbox(
                    "Rate auswählen", options=list(delete_options.keys()),
                    key="fx_delete_select"
                )
                if st.button("🗑️ Löschen", key="fx_delete_btn"):
                    delete_exchange_rate(conn, delete_options[selected_delete])
                    clear_cache()
                    st.success("Wechselkurs gelöscht.")
                    st.rerun()
        else:
            st.info("Noch keine Wechselkurse erfasst.")

        st.markdown("---")

        # --- Neue Rate erfassen ---
        st.markdown("**Neue Rate erfassen**")
        with st.form("fx_new_rate_form"):
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                from_ccy = st.selectbox("Von", options=CURRENCIES, key="fx_from_ccy")
            with fc2:
                to_ccy = st.selectbox("Nach", options=CURRENCIES, index=1, key="fx_to_ccy")
            with fc3:
                fx_date = st.date_input("Datum", value=date.today(), key="fx_rate_date")
            with fc4:
                fx_rate = st.number_input("Rate", min_value=0.0001, value=1.0,
                                          step=0.0001, format="%.6f", key="fx_rate_value")

            submitted = st.form_submit_button("💾 Speichern")
            if submitted:
                if from_ccy == to_ccy:
                    st.warning("Von und Nach dürfen nicht gleich sein.")
                else:
                    insert_exchange_rate(conn, from_ccy, to_ccy, fx_date, fx_rate)
                    clear_cache()
                    st.success(f"Rate {from_ccy}/{to_ccy} = {fx_rate:.6f} am {fx_date} gespeichert.")
                    st.rerun()

        st.markdown("---")

        # --- Bulk Import ---
        st.markdown("**CSV/Excel Import**")
        st.caption("Format: Von | Nach | Datum | Rate (z.B. EUR | USD | 2025-01-01 | 1.0850)")

        uploaded = st.file_uploader(
            "Datei hochladen", type=['csv', 'xlsx', 'xls'],
            key="fx_bulk_upload"
        )

        if uploaded is not None:
            try:
                if uploaded.name.endswith('.csv'):
                    import_df = pd.read_csv(uploaded)
                else:
                    import_df = pd.read_excel(uploaded)

                # Spalten normalisieren
                import_df.columns = [c.strip().lower() for c in import_df.columns]
                col_map = {}
                for c in import_df.columns:
                    if c in ('von', 'from', 'from_currency'):
                        col_map['from_currency'] = c
                    elif c in ('nach', 'to', 'to_currency'):
                        col_map['to_currency'] = c
                    elif c in ('datum', 'date', 'rate_date'):
                        col_map['rate_date'] = c
                    elif c in ('rate', 'kurs', 'exchange_rate'):
                        col_map['rate'] = c

                if len(col_map) < 4:
                    st.error("Konnte nicht alle Spalten zuordnen. Benötigt: Von, Nach, Datum, Rate")
                else:
                    import_df = import_df.rename(columns={v: k for k, v in col_map.items()})
                    st.write(f"**{len(import_df)} Zeilen** erkannt:")
                    st.dataframe(import_df.head(10), hide_index=True)

                    if st.button("📥 Importieren", key="fx_import_btn"):
                        count = 0
                        errors = []
                        for idx, row in import_df.iterrows():
                            try:
                                from_c = str(row['from_currency']).strip().upper()
                                to_c = str(row['to_currency']).strip().upper()
                                r_date = pd.to_datetime(row['rate_date']).date()
                                r_rate = float(row['rate'])
                                insert_exchange_rate(conn, from_c, to_c, r_date, r_rate)
                                count += 1
                            except Exception as e:
                                errors.append(f"Zeile {idx + 2}: {e}")

                        if errors:
                            st.warning(f"⚠️ {len(errors)} Fehler:")
                            for err in errors[:10]:
                                st.caption(err)

                        if count > 0:
                            clear_cache()
                            st.success(f"✅ {count} Wechselkurse importiert.")
                            st.rerun()

            except Exception as e:
                st.error(f"Fehler beim Lesen: {e}")

        # --- Häufige Paare ---
        st.markdown("---")
        st.markdown("**Häufige Währungspaare**")
        pairs_str = ", ".join([f"{a}/{b}" for a, b in COMMON_PAIRS])
        st.caption(pairs_str)

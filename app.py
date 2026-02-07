import streamlit as st
import pandas as pd
import psycopg2
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
from datetime import datetime, date
import time
import warnings

# Warnungen unterdrücken
warnings.filterwarnings('ignore')

# Seitenkonfiguration
st.set_page_config(page_title="PE Fund Analyzer", layout="wide", page_icon="📊")

# === MODULE IMPORTS ===
from auth import init_auth_state, login, logout, is_admin, show_login_page
from database import (
    get_connection, initialize_database, format_quarter, clear_cache
)
from queries import (
    get_available_reporting_dates_cached, get_available_years_cached,
    get_latest_date_for_year_per_fund_cached, load_all_funds_cached,
    load_funds_with_history_metrics_cached, get_fund_info_batch,
    get_fund_metrics_batch, get_fund_history_batch,
    get_portfolio_data_for_funds_batch
)
from charts import get_mekko_chart_cached, clear_mekko_cache
from admin import render_admin_tab
from cashflow_ui import render_cashflow_tab

# === SESSION STATE INITIALISIERUNG ===
if 'filter_version' not in st.session_state:
    st.session_state.filter_version = 0
if 'filters_applied' not in st.session_state:
    st.session_state.filters_applied = False


# === HAUPTAPP ===

def show_main_app():
    """Zeigt die Hauptanwendung nach erfolgreichem Login"""

    start_time = time.time()

    # Header mit User-Info und Logout
    header_col1, header_col2 = st.columns([6, 1])
    with header_col1:
        st.title("📊 Private Equity Fund Analyzer")
    with header_col2:
        role_badge = "🔑 Admin" if is_admin() else "👤 User"
        st.markdown(f"{role_badge}")
        st.caption(st.session_state.user_email)
        if st.button("Abmelden", width='stretch'):
            logout()
            st.rerun()

    st.markdown("---")

    try:
      with get_connection() as conn:
        # Datenbank initialisieren
        initialize_database(conn)

        conn_id = id(conn)
        available_years = get_available_years_cached(conn_id)
        available_dates = get_available_reporting_dates_cached(conn_id)

        st.sidebar.header("🔍 Filter & Auswahl")
        st.sidebar.subheader("📅 Stichtag")

        date_mode = st.sidebar.radio("Zeitraum wählen", options=["Aktuell", "Jahr", "Quartal"], key="date_mode", horizontal=True)

        selected_year = None
        selected_reporting_date = None

        if date_mode == "Jahr" and available_years:
            selected_year = st.sidebar.selectbox("Jahr auswählen", options=available_years, key="year_select")
            st.sidebar.caption("📌 Zeigt letzte verfügbare Daten pro Fonds im gewählten Jahr")
        elif date_mode == "Quartal" and available_dates:
            quarter_options = {format_quarter(d): d for d in available_dates}
            selected_quarter_label = st.sidebar.selectbox("Quartal auswählen", options=list(quarter_options.keys()), key="quarter_select")
            selected_reporting_date = quarter_options[selected_quarter_label]

        st.sidebar.markdown("---")

        if st.sidebar.button("🔄 Filter zurücksetzen"):
            st.session_state.filter_version += 1
            st.session_state.filters_applied = False
            st.rerun()

        if date_mode == "Aktuell":
            all_funds_df = load_all_funds_cached(conn_id)
            current_date_info = "Aktuelle Daten"
        elif date_mode == "Jahr" and selected_year:
            all_funds_df = load_funds_with_history_metrics_cached(conn_id, year=selected_year)
            current_date_info = f"Jahr {selected_year} (letzte verfügbare Daten)"
        elif date_mode == "Quartal" and selected_reporting_date:
            all_funds_df = load_funds_with_history_metrics_cached(conn_id, quarter_date=selected_reporting_date)
            current_date_info = f"Stichtag: {selected_reporting_date}"
        else:
            all_funds_df = load_all_funds_cached(conn_id)
            current_date_info = "Aktuelle Daten"

        # Duplikate entfernen - nur ein Eintrag pro Fund
        if not all_funds_df.empty:
            all_funds_df = all_funds_df.drop_duplicates(subset=['fund_id'], keep='first')

        st.sidebar.info(f"📅 {current_date_info}")

        if all_funds_df.empty:
            st.warning("⚠️ Keine Fonds in der Datenbank gefunden.")
            st.info("💡 Verwende den Admin-Tab um Daten zu importieren.")
        else:
            fv = st.session_state.filter_version

            ratings = sorted(all_funds_df['rating'].dropna().unique())
            selected_ratings = st.sidebar.multiselect("Rating", options=ratings, default=[], key=f"rating_{fv}") if ratings else []

            strategies = sorted(all_funds_df['strategy'].dropna().unique())
            selected_strategies = st.sidebar.multiselect("Strategy", options=strategies, default=[], key=f"strategy_{fv}") if strategies else []

            # Sektoren aufsplitten (Komma-getrennte Werte)
            all_sectors = set()
            for sector_str in all_funds_df['sector'].dropna().unique():
                for sector in str(sector_str).split(','):
                    sector_clean = sector.strip()
                    if sector_clean:
                        all_sectors.add(sector_clean)
            sectors = sorted(all_sectors)
            selected_sectors = st.sidebar.multiselect("Sektor", options=sectors, default=[], key=f"sector_{fv}") if sectors else []

            geographies = sorted(all_funds_df['geography'].dropna().unique())
            selected_geographies = st.sidebar.multiselect("Geography", options=geographies, default=[], key=f"geography_{fv}") if geographies else []

            vintage_years = sorted(all_funds_df['vintage_year'].dropna().unique())
            selected_vintages = st.sidebar.multiselect("Vintage Year", options=vintage_years, default=[], key=f"vintage_{fv}") if vintage_years else []

            gps = sorted(all_funds_df['gp_name'].dropna().unique())
            selected_gps = st.sidebar.multiselect("GP Name", options=gps, default=[], key=f"gp_{fv}") if gps else []

            placement_agents = sorted([pa for pa in all_funds_df['pa_name'].dropna().unique() if pa])
            if placement_agents:
                pa_options = ["(Alle)"] + placement_agents + ["(Ohne PA)"]
                selected_pas = st.sidebar.multiselect("Placement Agent", options=pa_options, default=[], key=f"pa_{fv}")
            else:
                selected_pas = []

            # Prüfen ob mindestens ein Filter gesetzt wurde
            any_filter_set = (
                len(selected_ratings) > 0 or
                len(selected_strategies) > 0 or
                len(selected_sectors) > 0 or
                len(selected_geographies) > 0 or
                len(selected_vintages) > 0 or
                len(selected_gps) > 0 or
                (len(selected_pas) > 0 and "(Alle)" not in selected_pas)
            )

            if any_filter_set:
                st.session_state.filters_applied = True

                # Filter anwenden
                filtered_df = all_funds_df.copy()
                if selected_ratings:
                    filtered_df = filtered_df[filtered_df['rating'].isin(selected_ratings)]
                if selected_strategies:
                    filtered_df = filtered_df[filtered_df['strategy'].isin(selected_strategies)]
                if selected_sectors:
                    def sector_matches(sector_str):
                        if pd.isna(sector_str):
                            return False
                        fund_sectors = [s.strip() for s in str(sector_str).split(',')]
                        return any(s in fund_sectors for s in selected_sectors)
                    filtered_df = filtered_df[filtered_df['sector'].apply(sector_matches)]
                if selected_geographies:
                    filtered_df = filtered_df[filtered_df['geography'].isin(selected_geographies)]
                if selected_vintages:
                    filtered_df = filtered_df[filtered_df['vintage_year'].isin(selected_vintages)]
                if selected_gps:
                    filtered_df = filtered_df[filtered_df['gp_name'].isin(selected_gps)]
                if selected_pas and "(Alle)" not in selected_pas:
                    if "(Ohne PA)" in selected_pas:
                        pa_filter = [pa for pa in selected_pas if pa != "(Ohne PA)"]
                        filtered_df = filtered_df[(filtered_df['pa_name'].isin(pa_filter)) | (filtered_df['pa_name'].isna())]
                    else:
                        filtered_df = filtered_df[filtered_df['pa_name'].isin(selected_pas)]

                selected_fund_ids = filtered_df['fund_id'].tolist()
                selected_fund_names = filtered_df['fund_name'].tolist()

                st.sidebar.success(f"✅ {len(selected_fund_ids)} Fonds gefunden")
            else:
                st.session_state.filters_applied = False
                filtered_df = pd.DataFrame()
                selected_fund_ids = []
                selected_fund_names = []

                st.sidebar.info("👆 Wähle mindestens einen Filter um Fonds anzuzeigen")

            fund_reporting_dates = {}
            if date_mode == "Jahr" and selected_year:
                fund_reporting_dates = get_latest_date_for_year_per_fund_cached(conn_id, selected_year, selected_fund_ids)
            elif date_mode == "Quartal" and selected_reporting_date:
                fund_reporting_dates = {fid: selected_reporting_date for fid in selected_fund_ids}

            # Tabs basierend auf Rolle erstellen
            if is_admin():
                tab1, tab2, tab3, tab4, tab5, tab6, tab_cf, tab7 = st.tabs([
                    "📊 Charts", "📈 Vergleichstabelle Fonds", "🏢 Portfoliounternehmen",
                    "📋 Fonds", "👔 GPs", "🤝 Placement Agents",
                    "💰 Cashflow Planning", "⚙️ Admin"
                ])
            else:
                tab1, tab2, tab3, tab4, tab5, tab6, tab_cf = st.tabs([
                    "📊 Charts", "📈 Vergleichstabelle Fonds", "🏢 Portfoliounternehmen",
                    "📋 Fonds", "👔 GPs", "🤝 Placement Agents",
                    "💰 Cashflow Planning"
                ])
                tab7 = None

            # TAB 1: CHARTS
            with tab1:
                st.header("Mekko Charts")
                if date_mode != "Aktuell":
                    st.caption(f"📅 {current_date_info}")

                if not selected_fund_ids:
                    if not st.session_state.filters_applied:
                        st.info("👈 Wähle mindestens einen Filter in der Sidebar um Fonds anzuzeigen")
                    else:
                        st.warning("Keine Fonds entsprechen den gewählten Filterkriterien")
                else:
                    for i in range(0, len(selected_fund_ids), 2):
                        cols = st.columns(2)
                        with cols[0]:
                            fund_id = selected_fund_ids[i]
                            fund_name = selected_fund_names[i]
                            report_date = fund_reporting_dates.get(fund_id)
                            fig = get_mekko_chart_cached(fund_id, fund_name, report_date)
                            if fig:
                                st.pyplot(fig)
                                plt.close()
                        if i + 1 < len(selected_fund_ids):
                            with cols[1]:
                                fund_id = selected_fund_ids[i + 1]
                                fund_name = selected_fund_names[i + 1]
                                report_date = fund_reporting_dates.get(fund_id)
                                fig = get_mekko_chart_cached(fund_id, fund_name, report_date)
                                if fig:
                                    st.pyplot(fig, bbox_inches='tight', pad_inches=0.1)
                                    plt.close()
                        if i + 2 < len(selected_fund_ids):
                            st.markdown("---")

            # TAB 2: VERGLEICHSTABELLE
            with tab2:
                st.header("Vergleichstabelle")
                if date_mode != "Aktuell":
                    st.caption(f"📅 {current_date_info}")

                if not selected_fund_ids:
                    if not st.session_state.filters_applied:
                        st.info("👈 Wähle mindestens einen Filter in der Sidebar um Fonds anzuzeigen")
                    else:
                        st.warning("Keine Fonds entsprechen den gewählten Filterkriterien")
                else:
                    comparison_df = filtered_df[filtered_df['fund_id'].isin(selected_fund_ids)].copy()
                    comparison_df = comparison_df.drop_duplicates(subset=['fund_id'], keep='first')

                    if 'reporting_date' in comparison_df.columns and date_mode != "Aktuell":
                        comparison_df = comparison_df[['fund_name', 'gp_name', 'pa_name', 'vintage_year', 'strategy', 'currency', 'rating', 'total_tvpi', 'net_tvpi', 'net_irr', 'dpi', 'top5_value_concentration', 'loss_ratio', 'reporting_date']]
                        comparison_df.columns = ['Fund', 'GP', 'Placement Agent', 'Vintage', 'Strategy', 'Währung', 'Rating', 'Gross TVPI', 'Net TVPI', 'Net IRR', 'DPI', 'Top 5 Conc.', 'Loss Ratio', 'Stichtag']
                        comparison_df['Stichtag'] = comparison_df['Stichtag'].apply(lambda x: format_quarter(x) if pd.notna(x) else "-")
                    else:
                        comparison_df = comparison_df[['fund_name', 'gp_name', 'pa_name', 'vintage_year', 'strategy', 'currency', 'rating', 'total_tvpi', 'net_tvpi', 'net_irr', 'dpi', 'top5_value_concentration', 'loss_ratio']]
                        comparison_df.columns = ['Fund', 'GP', 'Placement Agent', 'Vintage', 'Strategy', 'Währung', 'Rating', 'Gross TVPI', 'Net TVPI', 'Net IRR', 'DPI', 'Top 5 Conc.', 'Loss Ratio']

                    comparison_df['Placement Agent'] = comparison_df['Placement Agent'].apply(lambda x: x if pd.notna(x) else "-")
                    comparison_df['Währung'] = comparison_df['Währung'].apply(lambda x: x if pd.notna(x) else "-")
                    comparison_df['Gross TVPI'] = comparison_df['Gross TVPI'].apply(lambda x: f"{x:.2f}x" if pd.notna(x) else "-")
                    comparison_df['Net TVPI'] = comparison_df['Net TVPI'].apply(lambda x: f"{x:.2f}x" if pd.notna(x) else "-")
                    comparison_df['Net IRR'] = comparison_df['Net IRR'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "-")
                    comparison_df['DPI'] = comparison_df['DPI'].apply(lambda x: f"{x:.2f}x" if pd.notna(x) else "-")
                    comparison_df['Top 5 Conc.'] = comparison_df['Top 5 Conc.'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "-")
                    comparison_df['Loss Ratio'] = comparison_df['Loss Ratio'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "-")

                    st.dataframe(comparison_df, width='stretch', hide_index=True)

                    csv = comparison_df.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download als CSV", data=csv, file_name=f"fund_comparison_{pd.Timestamp.now().strftime('%Y%m%d')}.csv", mime="text/csv")

            # TAB 3: PORTFOLIOUNTERNEHMEN
            with tab3:
                st.header("🏢 Portfoliounternehmen")
                if date_mode != "Aktuell":
                    st.caption(f"📅 {current_date_info}")

                if not selected_fund_ids:
                    if not st.session_state.filters_applied:
                        st.info("👈 Wähle mindestens einen Filter in der Sidebar um Fonds anzuzeigen")
                    else:
                        st.warning("Keine Fonds entsprechen den gewählten Filterkriterien")
                else:
                    fund_ids_tuple = tuple(selected_fund_ids)
                    reporting_dates_dict = fund_reporting_dates if date_mode != "Aktuell" else None

                    portfolio_batch = get_portfolio_data_for_funds_batch(conn_id, fund_ids_tuple, reporting_dates_dict)

                    if portfolio_batch.empty:
                        all_portfolio = pd.DataFrame()
                    else:
                        all_portfolio = portfolio_batch.copy()
                        all_portfolio['Total TVPI'] = all_portfolio['realized_tvpi'] + all_portfolio['unrealized_tvpi']
                        all_portfolio['Gesamtwert'] = all_portfolio['Total TVPI'] * all_portfolio['invested_amount']
                        all_portfolio = all_portfolio.rename(columns={
                            'company_name': 'Unternehmen',
                            'fund_name': 'Fonds',
                            'gp_name': 'GP',
                            'invested_amount': 'Investiert',
                            'realized_tvpi': 'Realized TVPI',
                            'unrealized_tvpi': 'Unrealized TVPI',
                            'investment_date': 'Investitionsdatum',
                            'exit_date': 'Exitdatum',
                            'ownership': 'Ownership',
                            'entry_multiple': 'Entry Multiple',
                            'gross_irr': 'Gross IRR'
                        })

                    if all_portfolio.empty:
                        st.info("Keine Portfoliounternehmen für die ausgewählten Fonds vorhanden.")
                    else:
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            search_term = st.text_input("🔎 Unternehmen suchen", key="company_search")
                        with col2:
                            tvpi_range = st.slider("Total TVPI Bereich", min_value=0.0, max_value=float(all_portfolio['Total TVPI'].max()) + 0.5, value=(0.0, float(all_portfolio['Total TVPI'].max()) + 0.5), step=0.1, key="tvpi_filter")
                        with col3:
                            perf_filter = st.selectbox("Performance-Kategorie", options=["Alle", "Winner (>1.5x)", "Performer (1.0-1.5x)", "Under Water (<1.0x)"], key="perf_filter")

                        filtered_portfolio = all_portfolio.copy()
                        if search_term:
                            filtered_portfolio = filtered_portfolio[filtered_portfolio['Unternehmen'].str.contains(search_term, case=False, na=False)]
                        filtered_portfolio = filtered_portfolio[(filtered_portfolio['Total TVPI'] >= tvpi_range[0]) & (filtered_portfolio['Total TVPI'] <= tvpi_range[1])]
                        if perf_filter == "Winner (>1.5x)":
                            filtered_portfolio = filtered_portfolio[filtered_portfolio['Total TVPI'] > 1.5]
                        elif perf_filter == "Performer (1.0-1.5x)":
                            filtered_portfolio = filtered_portfolio[(filtered_portfolio['Total TVPI'] >= 1.0) & (filtered_portfolio['Total TVPI'] <= 1.5)]
                        elif perf_filter == "Under Water (<1.0x)":
                            filtered_portfolio = filtered_portfolio[filtered_portfolio['Total TVPI'] < 1.0]

                        st.markdown("---")
                        stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
                        with stat_col1:
                            st.metric("Anzahl Unternehmen", len(filtered_portfolio))
                        with stat_col2:
                            st.metric("Ø TVPI", f"{filtered_portfolio['Total TVPI'].mean():.2f}x" if not filtered_portfolio.empty else "0.00x")
                        with stat_col3:
                            st.metric("Gesamt investiert", f"{filtered_portfolio['Investiert'].sum():,.0f}" if not filtered_portfolio.empty else "0")
                        with stat_col4:
                            st.metric("Gesamtwert", f"{filtered_portfolio['Gesamtwert'].sum():,.0f}" if not filtered_portfolio.empty else "0")

                        st.markdown("---")
                        display_portfolio = filtered_portfolio.copy()
                        display_portfolio['Realized TVPI'] = display_portfolio['Realized TVPI'].apply(lambda x: f"{x:.2f}x")
                        display_portfolio['Unrealized TVPI'] = display_portfolio['Unrealized TVPI'].apply(lambda x: f"{x:.2f}x")
                        display_portfolio['Total TVPI'] = display_portfolio['Total TVPI'].apply(lambda x: f"{x:.2f}x")
                        display_portfolio['Investiert'] = display_portfolio['Investiert'].apply(lambda x: f"{x:,.0f}")
                        display_portfolio['Gesamtwert'] = display_portfolio['Gesamtwert'].apply(lambda x: f"{x:,.0f}")
                        if 'Entry Multiple' in display_portfolio.columns:
                            display_portfolio['Entry Multiple'] = display_portfolio['Entry Multiple'].apply(lambda x: f"{x:.1f}x" if pd.notna(x) else "-")
                        if 'Gross IRR' in display_portfolio.columns:
                            display_portfolio['Gross IRR'] = display_portfolio['Gross IRR'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "-")
                        if 'Stichtag' in display_portfolio.columns:
                            display_portfolio['Stichtag'] = display_portfolio['Stichtag'].apply(format_quarter)
                        st.dataframe(display_portfolio, width='stretch', hide_index=True)

                        csv_portfolio = filtered_portfolio.to_csv(index=False).encode('utf-8')
                        st.download_button("📥 Download als CSV", data=csv_portfolio, file_name=f"portfolio_companies_{pd.Timestamp.now().strftime('%Y%m%d')}.csv", mime="text/csv", key="download_portfolio")

            # TAB 4: FONDS DETAILS
            with tab4:
                st.header("📋 Fonds")
                if date_mode != "Aktuell":
                    st.caption(f"📅 {current_date_info}")

                if not selected_fund_ids:
                    if not st.session_state.filters_applied:
                        st.info("👈 Wähle mindestens einen Filter in der Sidebar um Fonds anzuzeigen")
                    else:
                        st.warning("Keine Fonds entsprechen den gewählten Filterkriterien")
                else:
                    fund_ids_tuple = tuple(selected_fund_ids)
                    reporting_dates_dict = fund_reporting_dates if date_mode != "Aktuell" else None

                    all_fund_info = get_fund_info_batch(conn_id, fund_ids_tuple)
                    all_fund_metrics = get_fund_metrics_batch(conn_id, fund_ids_tuple, reporting_dates_dict)
                    all_fund_history = get_fund_history_batch(conn_id, fund_ids_tuple)
                    all_portfolio_data = get_portfolio_data_for_funds_batch(conn_id, fund_ids_tuple, reporting_dates_dict)

                    for fund_id, fund_name in zip(selected_fund_ids, selected_fund_names):
                        report_date = fund_reporting_dates.get(fund_id)

                        with st.expander(f"📂 {fund_name}" + (f" ({report_date})" if report_date else ""), expanded=True):
                            fund_info_dict = all_fund_info.get(fund_id, {})
                            metrics_dict = all_fund_metrics.get(fund_id, {})

                            if fund_info_dict:
                                col1, col2, col3, col4, col5, col6 = st.columns(6)
                                with col1:
                                    st.metric("GP", fund_info_dict.get('gp_name') or "N/A")
                                    vintage = fund_info_dict.get('vintage_year')
                                    st.metric("Vintage", int(vintage) if vintage and pd.notna(vintage) else "N/A")
                                with col2:
                                    gross_tvpi = metrics_dict.get('total_tvpi')
                                    st.metric("Gross TVPI", f"{gross_tvpi:.2f}x" if gross_tvpi and pd.notna(gross_tvpi) else "N/A")
                                    net_tvpi = metrics_dict.get('net_tvpi')
                                    st.metric("Net TVPI", f"{net_tvpi:.2f}x" if net_tvpi and pd.notna(net_tvpi) else "N/A")
                                with col3:
                                    dpi = metrics_dict.get('dpi')
                                    st.metric("DPI", f"{dpi:.2f}x" if dpi and pd.notna(dpi) else "N/A")
                                    net_irr = metrics_dict.get('net_irr')
                                    st.metric("Net IRR", f"{net_irr:.1f}%" if net_irr and pd.notna(net_irr) else "N/A")
                                with col4:
                                    st.metric("Strategy", fund_info_dict.get('strategy') or "N/A")
                                    num_inv = metrics_dict.get('num_investments')
                                    st.metric("# Investments", int(num_inv) if num_inv and pd.notna(num_inv) else "N/A")
                                with col5:
                                    st.metric("Währung", fund_info_dict.get('currency') or "N/A")
                                    fund_size = fund_info_dict.get('fund_size_m')
                                    currency = fund_info_dict.get('currency') or ""
                                    st.metric("Fund Size", f"{fund_size:,.0f} Mio. {currency}" if fund_size and pd.notna(fund_size) else "N/A")
                                with col6:
                                    st.metric("Placement Agent", fund_info_dict.get('pa_name') or "N/A")

                                st.subheader("Portfolio Companies")
                                if not all_portfolio_data.empty:
                                    portfolio = all_portfolio_data[all_portfolio_data['fund_id'] == fund_id].copy()
                                else:
                                    portfolio = pd.DataFrame()

                                if not portfolio.empty:
                                    portfolio['Total TVPI'] = portfolio['realized_tvpi'] + portfolio['unrealized_tvpi']
                                    portfolio = portfolio[['company_name', 'invested_amount', 'realized_tvpi', 'unrealized_tvpi', 'Total TVPI']]
                                    portfolio.columns = ['Company', 'Invested', 'Realized', 'Unrealized', 'Total TVPI']
                                    portfolio['Total TVPI'] = portfolio['Total TVPI'].apply(lambda x: f"{x:.2f}x")
                                    portfolio['Realized'] = portfolio['Realized'].apply(lambda x: f"{x:.2f}x")
                                    portfolio['Unrealized'] = portfolio['Unrealized'].apply(lambda x: f"{x:.2f}x")
                                    st.dataframe(portfolio, width='stretch', hide_index=True)
                                else:
                                    st.info("Keine Portfolio Companies vorhanden")

                            # Historische Entwicklung aus Batch
                            st.subheader("📈 Historische Entwicklung")

                            col_chart, col_empty = st.columns([1, 1])

                            with col_chart:
                                history = all_fund_history.get(fund_id, [])

                                if history:
                                    df_history = pd.DataFrame(history)
                                    df_history['reporting_date'] = pd.to_datetime(df_history['reporting_date'])
                                    df_history = df_history.rename(columns={
                                        'reporting_date': 'Stichtag',
                                        'total_tvpi': 'Gross TVPI',
                                        'net_tvpi': 'Net TVPI',
                                        'net_irr': 'Net IRR',
                                        'dpi': 'DPI',
                                        'loss_ratio': 'Loss Ratio',
                                        'realized_percentage': 'Realisiert %'
                                    })

                                    selected_chart_metrics = st.multiselect(
                                        "📊 Metriken auswählen",
                                        options=['Gross TVPI', 'Net TVPI', 'Net IRR', 'DPI', 'Loss Ratio', 'Realisiert %'],
                                        default=['Gross TVPI', 'Net TVPI'],
                                        key=f"chart_metrics_{fund_id}"
                                    )

                                    if selected_chart_metrics:
                                        fig, ax1 = plt.subplots(figsize=(12, 5))
                                        colors = {
                                            'Gross TVPI': 'darkblue',
                                            'Net TVPI': 'royalblue',
                                            'Net IRR': 'purple',
                                            'DPI': 'green',
                                            'Loss Ratio': 'red',
                                            'Realisiert %': 'orange'
                                        }
                                        markers = {
                                            'Gross TVPI': 'o',
                                            'Net TVPI': 'o',
                                            'Net IRR': 'D',
                                            'DPI': 's',
                                            'Loss Ratio': '^',
                                            'Realisiert %': 'd'
                                        }

                                        multiple_metrics = [m for m in selected_chart_metrics if m in ['Gross TVPI', 'Net TVPI', 'DPI']]
                                        percent_metrics = [m for m in selected_chart_metrics if m in ['Net IRR', 'Loss Ratio', 'Realisiert %']]
                                        lines, labels = [], []

                                        if multiple_metrics:
                                            for metric in multiple_metrics:
                                                if metric in df_history.columns:
                                                    line, = ax1.plot(
                                                        df_history['Stichtag'],
                                                        df_history[metric],
                                                        marker=markers[metric],
                                                        linewidth=2,
                                                        markersize=8,
                                                        color=colors[metric],
                                                        label=metric
                                                    )
                                                    lines.append(line)
                                                    labels.append(metric)
                                            ax1.set_ylabel("Multiple (x)", color='darkblue')
                                            ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:.2f}x"))

                                        if percent_metrics:
                                            ax2 = ax1.twinx() if multiple_metrics else ax1
                                            for metric in percent_metrics:
                                                if metric in df_history.columns:
                                                    line, = ax2.plot(
                                                        df_history['Stichtag'],
                                                        df_history[metric],
                                                        marker=markers[metric],
                                                        linewidth=2,
                                                        markersize=8,
                                                        color=colors[metric],
                                                        linestyle='--',
                                                        label=metric
                                                    )
                                                    lines.append(line)
                                                    labels.append(metric)
                                            ax2.set_ylabel("Prozent (%)", color='gray')
                                            ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:.1f}%"))

                                        ax1.set_xlabel("Stichtag")
                                        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
                                        plt.xticks(rotation=45)
                                        ax1.set_title(f"Historische Entwicklung: {fund_name}", fontsize=13, fontweight='bold')
                                        ax1.grid(True, alpha=0.3)
                                        ax1.legend(lines, labels, loc='upper left')
                                        plt.tight_layout()
                                        st.pyplot(fig, width='content')
                                        plt.close()
                                else:
                                    st.info("Keine historischen Daten vorhanden.")

                            with col_empty:
                                st.markdown("📝 Notizen")
                                notes = fund_info_dict.get('notes')
                                st.markdown(notes if notes and pd.notna(notes) else "Keine Notizen vorhanden")

                            st.markdown("---")

            # TAB 5: GPs
            with tab5:
                st.header("👔 General Partners (GPs)")

                gp_query = """
                SELECT g.gp_id, g.gp_name, g.sector, g.headquarters, g.website, g.rating,
                       g.last_meeting, g.next_raise_estimate,
                       g.contact1_name, g.contact1_function, g.contact1_email, g.contact1_phone,
                       g.contact2_name, g.contact2_function, g.contact2_email, g.contact2_phone,
                       COUNT(f.fund_id) as fund_count,
                       STRING_AGG(DISTINCT pa.pa_name, ', ' ORDER BY pa.pa_name) as placement_agents
                FROM gps g
                LEFT JOIN funds f ON g.gp_id = f.gp_id
                LEFT JOIN placement_agents pa ON f.placement_agent_id = pa.pa_id
                GROUP BY g.gp_id, g.gp_name, g.sector, g.headquarters, g.website, g.rating,
                         g.last_meeting, g.next_raise_estimate,
                         g.contact1_name, g.contact1_function, g.contact1_email, g.contact1_phone,
                         g.contact2_name, g.contact2_function, g.contact2_email, g.contact2_phone
                ORDER BY g.gp_name
                """
                all_gps_df = pd.read_sql_query(gp_query, conn)

                if all_gps_df.empty:
                    st.info("ℹ️ Keine GPs vorhanden. GPs können im Admin-Tab erstellt oder über Excel importiert werden.")
                else:
                    display_gps = all_gps_df.copy()
                    display_gps['last_meeting'] = display_gps['last_meeting'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) and x else "-")
                    display_gps['next_raise_estimate'] = display_gps['next_raise_estimate'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) and x else "-")
                    display_gps['placement_agents'] = display_gps['placement_agents'].apply(lambda x: x if x else "-")

                    display_columns = {
                        'gp_name': 'GP Name',
                        'sector': 'Sektor',
                        'headquarters': 'Headquarters',
                        'rating': 'Rating',
                        'last_meeting': 'Last Meeting',
                        'next_raise_estimate': 'Next Raise',
                        'contact1_name': 'Kontakt 1',
                        'contact1_email': 'E-Mail 1',
                        'fund_count': 'Anzahl Fonds',
                        'placement_agents': 'Placement Agent'
                    }

                    display_df = display_gps[list(display_columns.keys())].rename(columns=display_columns)
                    display_df = display_df.fillna("-")

                    st.dataframe(display_df, width='stretch', hide_index=True)

                    csv_gps = display_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "📥 Download GPs als CSV",
                        data=csv_gps,
                        file_name=f"gps_overview_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )

            # TAB 6: PLACEMENT AGENTS
            with tab6:
                st.header("🤝 Placement Agents")

                with conn.cursor() as cursor:
                    cursor.execute("""
                    SELECT pa.pa_id, pa.pa_name, pa.headquarters, pa.website, pa.rating, pa.last_meeting,
                           pa.contact1_name, pa.contact1_function, pa.contact1_email, pa.contact1_phone,
                           COUNT(f.fund_id) as fund_count,
                           STRING_AGG(f.fund_name, ', ' ORDER BY f.fund_name) as funds
                    FROM placement_agents pa
                    LEFT JOIN funds f ON pa.pa_id = f.placement_agent_id
                    GROUP BY pa.pa_id, pa.pa_name, pa.headquarters, pa.website, pa.rating, pa.last_meeting,
                             pa.contact1_name, pa.contact1_function, pa.contact1_email, pa.contact1_phone
                    ORDER BY pa.pa_name
                    """)
                    all_pas = cursor.fetchall()

                if not all_pas:
                    st.info("ℹ️ Keine Placement Agents vorhanden. Placement Agents können im Admin-Tab erstellt oder über Excel importiert werden.")
                else:
                    pa_df = pd.DataFrame(all_pas, columns=['ID', 'Name', 'Headquarters', 'Website', 'Rating', 'Last Meeting',
                                                           'Kontakt Name', 'Kontakt Funktion', 'Kontakt E-Mail', 'Kontakt Telefon',
                                                           'Anzahl Fonds', 'Zugeordnete Fonds'])
                    pa_df['Last Meeting'] = pa_df['Last Meeting'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) and x else "-")
                    pa_df['Zugeordnete Fonds'] = pa_df['Zugeordnete Fonds'].apply(lambda x: x if x else "-")

                    st.dataframe(
                        pa_df[['Name', 'Headquarters', 'Rating', 'Last Meeting', 'Kontakt Name', 'Anzahl Fonds', 'Zugeordnete Fonds']],
                        width='stretch',
                        hide_index=True
                    )

                    st.markdown("---")

                    pa_names = [pa[1] for pa in all_pas]
                    selected_pa_name = st.selectbox("📋 Placement Agent Details anzeigen", options=["(Auswählen)"] + pa_names, key="pa_detail_select")

                    if selected_pa_name != "(Auswählen)":
                        selected_pa = next((pa for pa in all_pas if pa[1] == selected_pa_name), None)
                        if selected_pa:
                            st.subheader(f"📋 {selected_pa_name}")

                            col1, col2, col3 = st.columns(3)
                            with col1:
                                st.metric("Headquarters", selected_pa[2] or "N/A")
                                st.metric("Rating", selected_pa[4] or "N/A")
                            with col2:
                                st.metric("Website", selected_pa[3] or "N/A")
                                st.metric("Last Meeting", selected_pa[5].strftime('%Y-%m-%d') if selected_pa[5] else "N/A")
                            with col3:
                                st.metric("Anzahl Fonds", selected_pa[10])

                            if selected_pa[6]:
                                st.markdown("**Kontaktperson:**")
                                contact_info = f"**{selected_pa[6]}**"
                                if selected_pa[7]:
                                    contact_info += f" - {selected_pa[7]}"
                                st.markdown(contact_info)
                                if selected_pa[8]:
                                    st.markdown(f"📧 {selected_pa[8]}")
                                if selected_pa[9]:
                                    st.markdown(f"📞 {selected_pa[9]}")

                            if selected_pa[11]:
                                st.markdown("**Zugeordnete Fonds:**")
                                for fund in selected_pa[11].split(', '):
                                    st.markdown(f"- {fund}")

            # TAB CASHFLOW PLANNING
            with tab_cf:
                render_cashflow_tab(conn, conn_id, selected_fund_ids, selected_fund_names)

            # TAB 7: ADMIN (nur für Admins sichtbar)
            if is_admin() and tab7 is not None:
                with tab7:
                    render_admin_tab(conn)

    except psycopg2.Error as e:
        st.error(f"❌ Datenbankfehler: {e}")
        st.info("💡 Bitte prüfen Sie die PostgreSQL-Verbindungseinstellungen.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**PE Fund Analyzer v4.2**")
    st.sidebar.markdown("🔐 Mit Supabase Auth & Rollen")

    end_time = time.time()
    st.sidebar.info(f"⏱️ Ladezeit: {end_time - start_time:.2f}s")

# === APP ENTRY POINT ===

init_auth_state()

if st.session_state.authenticated:
    show_main_app()
else:
    show_login_page()

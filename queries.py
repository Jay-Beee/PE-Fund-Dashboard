import streamlit as st
import pandas as pd
from datetime import datetime, date

from database import get_connection


# ============================================================================
# GECACHTE ABFRAGEN (TTL=300 Sekunden)
# ============================================================================

@st.cache_data(ttl=300)
def get_available_reporting_dates_cached(_conn_id):
    """Lädt alle verfügbaren Reporting-Daten - gecached"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT reporting_date FROM portfolio_companies_history ORDER BY reporting_date DESC")
            return [row[0].strftime('%Y-%m-%d') if isinstance(row[0], (date, datetime)) else row[0] for row in cursor.fetchall()]


@st.cache_data(ttl=300)
def get_available_years_cached(_conn_id):
    """Lädt alle verfügbaren Jahre - gecached"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT EXTRACT(YEAR FROM reporting_date)::INTEGER as year FROM portfolio_companies_history ORDER BY year DESC")
            return [int(row[0]) for row in cursor.fetchall() if row[0]]


@st.cache_data(ttl=300)
def get_latest_date_for_year_per_fund_cached(_conn_id, year, fund_ids_tuple=None):
    """Lädt das letzte Datum pro Fund für ein Jahr - gecached"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if fund_ids_tuple:
                cursor.execute("""
                SELECT fund_id, MAX(reporting_date) as latest_date
                FROM portfolio_companies_history
                WHERE EXTRACT(YEAR FROM reporting_date) = %s AND fund_id = ANY(%s)
                GROUP BY fund_id
                """, (year, list(fund_ids_tuple)))
            else:
                cursor.execute("""
                SELECT fund_id, MAX(reporting_date) as latest_date
                FROM portfolio_companies_history WHERE EXTRACT(YEAR FROM reporting_date) = %s
                GROUP BY fund_id
                """, (year,))

            return {row[0]: row[1].strftime('%Y-%m-%d') if isinstance(row[1], (date, datetime)) else row[1] for row in cursor.fetchall()}


@st.cache_data(ttl=300)
def get_portfolio_data_for_date_cached(_conn_id, fund_id, reporting_date):
    """Lädt Portfolio-Daten für ein Datum - gecached"""
    with get_connection() as conn:
        if reporting_date:
            query = """
            SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
            FROM portfolio_companies_history
            WHERE fund_id = %s AND reporting_date = %s
            ORDER BY (realized_tvpi + unrealized_tvpi) DESC
            """
            return pd.read_sql_query(query, conn, params=(fund_id, reporting_date))
        else:
            query = """
            SELECT company_name, invested_amount, realized_tvpi, unrealized_tvpi
            FROM portfolio_companies
            WHERE fund_id = %s
            ORDER BY (realized_tvpi + unrealized_tvpi) DESC
            """
            return pd.read_sql_query(query, conn, params=(fund_id,))


@st.cache_data(ttl=300)
def get_fund_metrics_for_date_cached(_conn_id, fund_id, reporting_date):
    """Lädt Fund-Metriken für ein Datum - gecached"""
    with get_connection() as conn:
        query = """
        SELECT total_tvpi, net_tvpi, net_irr, dpi, top5_value_concentration, top5_capital_concentration,
               loss_ratio, realized_percentage, num_investments
        FROM fund_metrics_history WHERE fund_id = %s AND reporting_date = %s
        """
        return pd.read_sql_query(query, conn, params=(fund_id, reporting_date))


@st.cache_data(ttl=300)
def load_all_funds_cached(_conn_id):
    """Lädt alle Fonds mit aktuellen Metriken - gecached"""
    with get_connection() as conn:
        query = """
        SELECT DISTINCT ON (f.fund_id) f.fund_id, f.fund_name, g.gp_name, g.sector, f.vintage_year, f.strategy, f.geography, g.rating,
               f.currency, pa.pa_name, m.total_tvpi, m.net_tvpi, m.net_irr, m.dpi, m.top5_value_concentration, m.loss_ratio
        FROM funds f
        LEFT JOIN gps g ON f.gp_id = g.gp_id
        LEFT JOIN placement_agents pa ON f.placement_agent_id = pa.pa_id
        LEFT JOIN fund_metrics m ON f.fund_id = m.fund_id
        WHERE f.fund_id IS NOT NULL
        ORDER BY f.fund_id, f.fund_name
        """
        return pd.read_sql_query(query, conn)


@st.cache_data(ttl=300)
def load_funds_with_history_metrics_cached(_conn_id, year=None, quarter_date=None):
    """Lädt Fonds mit historischen Metriken - gecached"""
    with get_connection() as conn:
        if quarter_date:
            query = """
            SELECT DISTINCT ON (f.fund_id) f.fund_id, f.fund_name, g.gp_name, g.sector, f.vintage_year, f.strategy, f.geography, g.rating,
                   f.currency, pa.pa_name, m.total_tvpi, m.net_tvpi, m.net_irr, m.dpi, m.top5_value_concentration, m.loss_ratio, m.reporting_date
            FROM funds f
            LEFT JOIN gps g ON f.gp_id = g.gp_id
            LEFT JOIN placement_agents pa ON f.placement_agent_id = pa.pa_id
            LEFT JOIN fund_metrics_history m ON f.fund_id = m.fund_id AND m.reporting_date = %s
            WHERE f.fund_id IS NOT NULL
            ORDER BY f.fund_id, f.fund_name
            """
            return pd.read_sql_query(query, conn, params=(quarter_date,))
        elif year:
            query = """
            SELECT DISTINCT ON (f.fund_id) f.fund_id, f.fund_name, g.gp_name, g.sector, f.vintage_year, f.strategy, f.geography, g.rating,
                   f.currency, pa.pa_name, m.total_tvpi, m.net_tvpi, m.net_irr, m.dpi, m.top5_value_concentration, m.loss_ratio, m.reporting_date
            FROM funds f
            LEFT JOIN gps g ON f.gp_id = g.gp_id
            LEFT JOIN placement_agents pa ON f.placement_agent_id = pa.pa_id
            LEFT JOIN (
                SELECT fund_id, MAX(reporting_date) as max_date
                FROM fund_metrics_history
                WHERE EXTRACT(YEAR FROM reporting_date) = %s
                GROUP BY fund_id
            ) latest ON f.fund_id = latest.fund_id
            LEFT JOIN fund_metrics_history m ON f.fund_id = m.fund_id AND m.reporting_date = latest.max_date
            WHERE f.fund_id IS NOT NULL
            ORDER BY f.fund_id, f.fund_name
            """
            return pd.read_sql_query(query, conn, params=(year,))
        else:
            return load_all_funds_cached(_conn_id)


# ============================================================================
# BATCH-FUNKTIONEN FÜR PERFORMANCE
# ============================================================================

@st.cache_data(ttl=300)
def get_fund_info_batch(_conn_id, fund_ids_tuple):
    """Lädt Fund-Infos für mehrere Fonds in einer Abfrage - gecached"""
    if not fund_ids_tuple:
        return {}

    with get_connection() as conn:
        query = """
        SELECT f.fund_id, f.fund_name, g.gp_name, g.sector, f.vintage_year, f.fund_size_m,
               f.currency, f.strategy, f.geography, g.rating,
               g.last_meeting, g.next_raise_estimate, pa.pa_name, f.notes
        FROM funds f
        LEFT JOIN gps g ON f.gp_id = g.gp_id
        LEFT JOIN placement_agents pa ON f.placement_agent_id = pa.pa_id
        WHERE f.fund_id = ANY(%s)
        """
        df = pd.read_sql_query(query, conn, params=(list(fund_ids_tuple),))
        return {row['fund_id']: row.to_dict() for _, row in df.iterrows()}


@st.cache_data(ttl=300)
def get_fund_metrics_batch(_conn_id, fund_ids_tuple, reporting_dates_dict_keys=None, reporting_dates_dict_values=None):
    """Lädt Metriken für mehrere Fonds in einer Abfrage - gecached"""
    if not fund_ids_tuple:
        return {}

    reporting_dates_dict = None
    if reporting_dates_dict_keys and reporting_dates_dict_values:
        reporting_dates_dict = dict(zip(reporting_dates_dict_keys, reporting_dates_dict_values))

    with get_connection() as conn:
        if reporting_dates_dict:
            results = {}
            date_to_funds = {}
            for fund_id in fund_ids_tuple:
                report_date = reporting_dates_dict.get(fund_id)
                if report_date:
                    if report_date not in date_to_funds:
                        date_to_funds[report_date] = []
                    date_to_funds[report_date].append(fund_id)

            for report_date, funds in date_to_funds.items():
                query = """
                SELECT fund_id, total_tvpi, net_tvpi, net_irr, dpi,
                       top5_value_concentration, top5_capital_concentration,
                       loss_ratio, realized_percentage, num_investments
                FROM fund_metrics_history
                WHERE fund_id = ANY(%s) AND reporting_date = %s
                """
                df = pd.read_sql_query(query, conn, params=(list(funds), report_date))
                for _, row in df.iterrows():
                    results[row['fund_id']] = row.to_dict()

            return results
        else:
            query = """
            SELECT fund_id, total_tvpi, net_tvpi, net_irr, dpi,
                   top5_value_concentration, top5_capital_concentration,
                   loss_ratio, realized_percentage, num_investments
            FROM fund_metrics
            WHERE fund_id = ANY(%s)
            """
            df = pd.read_sql_query(query, conn, params=(list(fund_ids_tuple),))
            return {row['fund_id']: row.to_dict() for _, row in df.iterrows()}


@st.cache_data(ttl=300)
def get_fund_history_batch(_conn_id, fund_ids_tuple):
    """Lädt Historien für mehrere Fonds in einer Abfrage - gecached"""
    if not fund_ids_tuple:
        return {}

    with get_connection() as conn:
        query = """
        SELECT fund_id, reporting_date, total_tvpi, net_tvpi, net_irr,
               dpi, loss_ratio, realized_percentage
        FROM fund_metrics_history
        WHERE fund_id = ANY(%s)
        ORDER BY fund_id, reporting_date
        """
        df = pd.read_sql_query(query, conn, params=(list(fund_ids_tuple),))

        result = {}
        for fund_id in fund_ids_tuple:
            fund_df = df[df['fund_id'] == fund_id]
            if not fund_df.empty:
                result[fund_id] = fund_df.to_dict('records')
            else:
                result[fund_id] = []

        return result


@st.cache_data(ttl=300)
def get_portfolio_data_for_funds_batch(_conn_id, fund_ids_tuple, reporting_dates_dict_keys=None, reporting_dates_dict_values=None):
    """Lädt Portfolio-Daten für mehrere Fonds in einer Abfrage - gecached"""
    if not fund_ids_tuple:
        return pd.DataFrame()

    reporting_dates_dict = None
    if reporting_dates_dict_keys and reporting_dates_dict_values:
        reporting_dates_dict = dict(zip(reporting_dates_dict_keys, reporting_dates_dict_values))

    with get_connection() as conn:
        if reporting_dates_dict:
            dfs = []
            date_to_funds = {}
            for fund_id in fund_ids_tuple:
                report_date = reporting_dates_dict.get(fund_id)
                if report_date:
                    if report_date not in date_to_funds:
                        date_to_funds[report_date] = []
                    date_to_funds[report_date].append(fund_id)

            for report_date, funds in date_to_funds.items():
                query = """
                SELECT pch.fund_id, pch.company_name, pch.invested_amount,
                       pch.realized_tvpi, pch.unrealized_tvpi,
                       pch.investment_date, pch.exit_date, pch.entry_multiple,
                       pch.gross_irr, pch.ownership,
                       f.fund_name, g.gp_name
                FROM portfolio_companies_history pch
                JOIN funds f ON pch.fund_id = f.fund_id
                LEFT JOIN gps g ON f.gp_id = g.gp_id
                WHERE pch.fund_id = ANY(%s) AND pch.reporting_date = %s
                ORDER BY pch.fund_id, (pch.realized_tvpi + pch.unrealized_tvpi) DESC
                """
                df = pd.read_sql_query(query, conn, params=(list(funds), report_date))
                if not df.empty:
                    df['reporting_date'] = report_date
                    dfs.append(df)

            return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        else:
            query = """
            SELECT pc.fund_id, pc.company_name, pc.invested_amount,
                   pc.realized_tvpi, pc.unrealized_tvpi,
                   pc.investment_date, pc.exit_date, pc.entry_multiple,
                   pc.gross_irr, pc.ownership,
                   f.fund_name, g.gp_name
            FROM portfolio_companies pc
            JOIN funds f ON pc.fund_id = f.fund_id
            LEFT JOIN gps g ON f.gp_id = g.gp_id
            WHERE pc.fund_id = ANY(%s)
            ORDER BY pc.fund_id, (pc.realized_tvpi + pc.unrealized_tvpi) DESC
            """
            return pd.read_sql_query(query, conn, params=(list(fund_ids_tuple),))

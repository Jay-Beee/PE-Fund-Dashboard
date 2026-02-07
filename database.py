import streamlit as st
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
from datetime import datetime, date
from cashflow_db import (
    ensure_cashflows_table, ensure_scenarios_table,
    ensure_exchange_rates_table, ensure_cashflow_fund_columns
)

# === DATABASE CONFIGURATION ===
DATABASE_CONFIG = {
    'host': st.secrets["postgres"]["host"],
    'port': st.secrets["postgres"]["port"],
    'database': st.secrets["postgres"]["database"],
    'user': st.secrets["postgres"]["user"],
    'password': st.secrets["postgres"]["password"],
}

CONNECTION_POOL = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    **DATABASE_CONFIG
)

@contextmanager
def get_connection():
    """Holt eine Verbindung aus dem Connection Pool als Context Manager"""
    conn = CONNECTION_POOL.getconn()
    try:
        yield conn
    finally:
        CONNECTION_POOL.putconn(conn)


# ============================================================================
# DATENBANK-INITIALISIERUNG UND SCHEMA-MANAGEMENT
# ============================================================================

def check_column_exists(conn, table_name, column_name):
    """Prüft ob eine Spalte in einer Tabelle existiert"""
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
        """, (table_name, column_name))
        return cursor.fetchone()[0]


def ensure_gps_table(conn):
    """Erstellt die GPs-Tabelle falls nicht vorhanden"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gps (
            gp_id SERIAL PRIMARY KEY,
            gp_name TEXT UNIQUE NOT NULL,
            sector TEXT,
            headquarters TEXT,
            website TEXT,
            rating TEXT,
            last_meeting DATE,
            next_raise_estimate DATE,
            notes TEXT,
            contact1_name TEXT,
            contact1_function TEXT,
            contact1_email TEXT,
            contact1_phone TEXT,
            contact2_name TEXT,
            contact2_function TEXT,
            contact2_email TEXT,
            contact2_phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()


def ensure_placement_agents_table(conn):
    """Erstellt die Placement Agents-Tabelle falls nicht vorhanden"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS placement_agents (
            pa_id SERIAL PRIMARY KEY,
            pa_name TEXT UNIQUE NOT NULL,
            headquarters TEXT,
            website TEXT,
            rating TEXT,
            last_meeting DATE,
            contact1_name TEXT,
            contact1_function TEXT,
            contact1_email TEXT,
            contact1_phone TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()


def ensure_placement_agent_contact_fields(conn):
    """Fügt Kontaktfelder zur Placement Agents Tabelle hinzu falls nicht vorhanden"""
    contact_fields = [
        ('contact1_name', 'TEXT'),
        ('contact1_function', 'TEXT'),
        ('contact1_email', 'TEXT'),
        ('contact1_phone', 'TEXT')
    ]

    with conn.cursor() as cursor:
        for field_name, field_type in contact_fields:
            if not check_column_exists(conn, 'placement_agents', field_name):
                cursor.execute(f"ALTER TABLE placement_agents ADD COLUMN {field_name} {field_type}")
                conn.commit()

        # Entferne sector falls vorhanden (nicht mehr benötigt)
        if check_column_exists(conn, 'placement_agents', 'sector'):
            cursor.execute("ALTER TABLE placement_agents DROP COLUMN sector")
            conn.commit()


def ensure_funds_table(conn):
    """Erstellt die Funds-Tabelle falls nicht vorhanden"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS funds (
            fund_id SERIAL PRIMARY KEY,
            fund_name TEXT NOT NULL,
            gp_id INTEGER REFERENCES gps(gp_id),
            placement_agent_id INTEGER REFERENCES placement_agents(pa_id),
            vintage_year INTEGER,
            strategy TEXT,
            geography TEXT,
            fund_size_m REAL,
            currency TEXT DEFAULT 'EUR',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()


def ensure_portfolio_companies_table(conn):
    """Erstellt die Portfolio Companies Tabelle falls nicht vorhanden"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_companies (
            company_id SERIAL PRIMARY KEY,
            fund_id INTEGER NOT NULL REFERENCES funds(fund_id),
            company_name TEXT NOT NULL,
            invested_amount REAL,
            realized_tvpi REAL DEFAULT 0,
            unrealized_tvpi REAL DEFAULT 0,
            investment_date DATE,
            exit_date DATE,
            entry_multiple REAL,
            gross_irr REAL,
            ownership REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()


def ensure_fund_metrics_table(conn):
    """Erstellt die Fund Metrics Tabelle falls nicht vorhanden"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fund_metrics (
            metric_id SERIAL PRIMARY KEY,
            fund_id INTEGER NOT NULL REFERENCES funds(fund_id) UNIQUE,
            total_tvpi REAL,
            net_tvpi REAL,
            net_irr REAL,
            dpi REAL,
            top5_value_concentration REAL,
            top5_capital_concentration REAL,
            loss_ratio REAL,
            realized_percentage REAL,
            num_investments INTEGER,
            calculation_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()


def ensure_history_tables(conn):
    """Erstellt die History-Tabellen für Portfolio Companies und Fund Metrics"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_companies_history (
            history_id SERIAL PRIMARY KEY,
            fund_id INTEGER NOT NULL REFERENCES funds(fund_id),
            company_name TEXT NOT NULL,
            invested_amount REAL,
            realized_tvpi REAL DEFAULT 0,
            unrealized_tvpi REAL DEFAULT 0,
            reporting_date DATE NOT NULL,
            investment_date DATE,
            exit_date DATE,
            entry_multiple REAL,
            gross_irr REAL,
            ownership REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fund_id, company_name, reporting_date)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fund_metrics_history (
            history_id SERIAL PRIMARY KEY,
            fund_id INTEGER NOT NULL REFERENCES funds(fund_id),
            reporting_date DATE NOT NULL,
            total_tvpi REAL,
            net_tvpi REAL,
            net_irr REAL,
            dpi REAL,
            top5_value_concentration REAL,
            top5_capital_concentration REAL,
            loss_ratio REAL,
            realized_percentage REAL,
            num_investments INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fund_id, reporting_date)
        )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pch_fund_date ON portfolio_companies_history(fund_id, reporting_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fmh_fund_date ON fund_metrics_history(fund_id, reporting_date)")

        conn.commit()


def ensure_currency_column(conn):
    """Fügt die Währungsspalte hinzu falls nicht vorhanden"""
    with conn.cursor() as cursor:
        if not check_column_exists(conn, 'funds', 'currency'):
            cursor.execute("ALTER TABLE funds ADD COLUMN currency TEXT DEFAULT 'EUR'")
            conn.commit()

        if not check_column_exists(conn, 'funds', 'fund_size_m'):
            cursor.execute("ALTER TABLE funds ADD COLUMN fund_size_m REAL")
            conn.commit()


def ensure_placement_agent_column(conn):
    """Fügt die Placement Agent Spalte zu Funds hinzu falls nicht vorhanden"""
    with conn.cursor() as cursor:
        if not check_column_exists(conn, 'funds', 'placement_agent_id'):
            cursor.execute("ALTER TABLE funds ADD COLUMN placement_agent_id INTEGER REFERENCES placement_agents(pa_id)")
            conn.commit()


def ensure_portfolio_company_fields(conn):
    """Fügt neue Felder für Portfolio Companies hinzu"""
    new_pc_fields = [
        ('investment_date', 'DATE'),
        ('exit_date', 'DATE'),
        ('entry_multiple', 'REAL'),
        ('gross_irr', 'REAL'),
        ('ownership', 'REAL')
    ]

    with conn.cursor() as cursor:
        for field_name, field_type in new_pc_fields:
            if not check_column_exists(conn, 'portfolio_companies', field_name):
                cursor.execute(f"ALTER TABLE portfolio_companies ADD COLUMN {field_name} {field_type}")
                conn.commit()

            if not check_column_exists(conn, 'portfolio_companies_history', field_name):
                cursor.execute(f"ALTER TABLE portfolio_companies_history ADD COLUMN {field_name} {field_type}")
                conn.commit()


def ensure_net_metrics_fields(conn):
    """Fügt Net TVPI und Net IRR Felder zu fund_metrics Tabellen hinzu"""
    net_fields = [
        ('net_tvpi', 'REAL'),
        ('net_irr', 'REAL')
    ]

    with conn.cursor() as cursor:
        for field_name, field_type in net_fields:
            if not check_column_exists(conn, 'fund_metrics', field_name):
                cursor.execute(f"ALTER TABLE fund_metrics ADD COLUMN {field_name} {field_type}")
                conn.commit()

            if not check_column_exists(conn, 'fund_metrics_history', field_name):
                cursor.execute(f"ALTER TABLE fund_metrics_history ADD COLUMN {field_name} {field_type}")
                conn.commit()


# ============================================================================
# MIGRATIONEN
# ============================================================================

def migrate_to_gp_table(conn):
    """Migriert bestehende Daten zur neuen GP-Struktur"""
    with conn.cursor() as cursor:
        if check_column_exists(conn, 'funds', 'gp_id'):
            return False

        if not check_column_exists(conn, 'funds', 'gp_name'):
            return False

        cursor.execute("""
        SELECT DISTINCT gp_name, rating, last_meeting, next_raise_estimate
        FROM funds WHERE gp_name IS NOT NULL AND gp_name != ''
        """)
        existing_gps = cursor.fetchall()

        for gp_name, rating, last_meeting, next_raise in existing_gps:
            cursor.execute("""
            INSERT INTO gps (gp_name, rating, last_meeting, next_raise_estimate)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (gp_name) DO NOTHING
            """, (gp_name, rating, last_meeting, next_raise))

        conn.commit()

        cursor.execute("ALTER TABLE funds ADD COLUMN IF NOT EXISTS gp_id INTEGER REFERENCES gps(gp_id)")
        conn.commit()

        cursor.execute("""
        UPDATE funds SET gp_id = (SELECT gp_id FROM gps WHERE gps.gp_name = funds.gp_name)
        WHERE gp_name IS NOT NULL AND gp_name != ''
        """)
        conn.commit()
        return True


def migrate_existing_data_if_needed(conn):
    """Migriert bestehende Portfolio-Daten in die History-Tabellen"""
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM portfolio_companies_history")
        history_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM portfolio_companies")
        current_count = cursor.fetchone()[0]

        if history_count == 0 and current_count > 0:
            today = date.today()
            quarter = (today.month - 1) // 3
            if quarter == 0:
                default_date = date(today.year - 1, 12, 31)
            else:
                last_month = quarter * 3
                default_date = date(today.year, last_month, 30 if last_month in [6, 9] else 31)

            cursor.execute("""
            INSERT INTO portfolio_companies_history
                (fund_id, company_name, invested_amount, realized_tvpi, unrealized_tvpi, reporting_date)
            SELECT fund_id, company_name, invested_amount, realized_tvpi, unrealized_tvpi, %s
            FROM portfolio_companies
            ON CONFLICT (fund_id, company_name, reporting_date) DO NOTHING
            """, (default_date,))

            cursor.execute("""
            INSERT INTO fund_metrics_history
                (fund_id, reporting_date, total_tvpi, dpi, top5_value_concentration,
                 top5_capital_concentration, loss_ratio, realized_percentage, num_investments)
            SELECT fund_id, %s, total_tvpi, dpi, top5_value_concentration,
                   top5_capital_concentration, loss_ratio, realized_percentage, num_investments
            FROM fund_metrics
            ON CONFLICT (fund_id, reporting_date) DO NOTHING
            """, (default_date,))

            conn.commit()
            return default_date

        return None


def initialize_database(conn):
    """Initialisiert alle benötigten Tabellen und führt Migrationen durch"""
    ensure_gps_table(conn)
    ensure_placement_agents_table(conn)
    ensure_funds_table(conn)
    ensure_portfolio_companies_table(conn)
    ensure_fund_metrics_table(conn)
    ensure_history_tables(conn)

    ensure_currency_column(conn)
    ensure_placement_agent_column(conn)
    ensure_placement_agent_contact_fields(conn)
    ensure_portfolio_company_fields(conn)
    ensure_net_metrics_fields(conn)

    ensure_cashflow_fund_columns(conn)
    ensure_scenarios_table(conn)
    ensure_cashflows_table(conn)
    ensure_exchange_rates_table(conn)

    migrate_to_gp_table(conn)
    migrate_existing_data_if_needed(conn)


# ============================================================================
# HELPER-FUNKTIONEN
# ============================================================================

def get_or_create_gp(conn, gp_name):
    """Holt oder erstellt einen GP anhand des Namens"""
    if not gp_name or gp_name.strip() == '':
        return None

    with conn.cursor() as cursor:
        cursor.execute("SELECT gp_id FROM gps WHERE gp_name = %s", (gp_name,))
        result = cursor.fetchone()

        if result:
            return result[0]

        cursor.execute("INSERT INTO gps (gp_name) VALUES (%s) RETURNING gp_id", (gp_name,))
        conn.commit()
        return cursor.fetchone()[0]


def get_or_create_placement_agent(conn, pa_name):
    """Holt oder erstellt einen Placement Agent anhand des Namens"""
    if not pa_name or pa_name.strip() == '':
        return None

    with conn.cursor() as cursor:
        cursor.execute("SELECT pa_id FROM placement_agents WHERE pa_name = %s", (pa_name,))
        result = cursor.fetchone()

        if result:
            return result[0]

        cursor.execute("INSERT INTO placement_agents (pa_name) VALUES (%s) RETURNING pa_id", (pa_name,))
        conn.commit()
        return cursor.fetchone()[0]


def format_quarter(date_str):
    """Formatiert ein Datum als Quartal (z.B. 'Q3 2024')"""
    if not date_str:
        return "N/A"
    try:
        if isinstance(date_str, date):
            d = date_str
        elif isinstance(date_str, datetime):
            d = date_str.date()
        else:
            d = datetime.strptime(str(date_str), "%Y-%m-%d").date()
        quarter = (d.month - 1) // 3 + 1
        return f"Q{quarter} {d.year}"
    except (ValueError, TypeError):
        return str(date_str)


def get_quarter_end_date(input_date):
    """Gibt das Quartalsende für ein gegebenes Datum zurück"""
    year = input_date.year
    month = input_date.month
    if month <= 3:
        return date(year, 3, 31)
    elif month <= 6:
        return date(year, 6, 30)
    elif month <= 9:
        return date(year, 9, 30)
    else:
        return date(year, 12, 31)


def clear_cache():
    """Löscht den gesamten Streamlit-Cache"""
    st.cache_data.clear()

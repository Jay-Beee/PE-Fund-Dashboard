"""
Cashflow Planning Tool — Datenbank-Schema und CRUD-Operationen

Vorzeichen-Konvention:
    Alle Beträge werden POSITIV gespeichert. Der type bestimmt die Richtung:
    - Outflows (negativ in Charts): capital_call, management_fee, carried_interest
    - Inflows (positiv in Charts): distribution, clawback
"""

# ============================================================================
# SCHEMA — Tabellen und Spalten erstellen
# ============================================================================

def _column_exists(conn, table_name, column_name):
    """Prüft ob eine Spalte in einer Tabelle existiert (lokal, kein Import aus database)"""
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
        """, (table_name, column_name))
        return cursor.fetchone()[0]


def ensure_cashflow_fund_columns(conn):
    """Fügt Cashflow-relevante Spalten zur funds-Tabelle hinzu"""
    columns = [
        ('commitment_amount', 'REAL'),
        ('unfunded_amount', 'REAL'),
        ('commitment_date', 'DATE'),
        ('expected_end_date', 'DATE'),
    ]
    with conn.cursor() as cursor:
        for col_name, col_type in columns:
            if not _column_exists(conn, 'funds', col_name):
                cursor.execute(f"ALTER TABLE funds ADD COLUMN {col_name} {col_type}")
                conn.commit()


def ensure_scenarios_table(conn):
    """Erstellt die scenarios-Tabelle und das Default-Szenario"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id SERIAL PRIMARY KEY,
            scenario_name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        INSERT INTO scenarios (scenario_name, description)
        VALUES ('base', 'Basisszenario')
        ON CONFLICT DO NOTHING
        """)
        conn.commit()


def ensure_cashflows_table(conn):
    """Erstellt die cashflows-Tabelle mit Indizes"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cashflows (
            cashflow_id SERIAL PRIMARY KEY,
            fund_id INTEGER NOT NULL REFERENCES funds(fund_id) ON DELETE CASCADE,
            date DATE NOT NULL,
            type TEXT NOT NULL CHECK (type IN (
                'capital_call', 'distribution', 'management_fee',
                'carried_interest', 'clawback'
            )),
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'EUR',
            is_actual BOOLEAN DEFAULT TRUE,
            scenario_name TEXT DEFAULT 'base',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fund_id, date, type, scenario_name)
        )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cf_fund_date ON cashflows(fund_id, date)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cf_scenario ON cashflows(scenario_name)"
        )
        conn.commit()


def ensure_exchange_rates_table(conn):
    """Erstellt die exchange_rates-Tabelle"""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            rate_id SERIAL PRIMARY KEY,
            from_currency TEXT NOT NULL,
            to_currency TEXT NOT NULL,
            rate_date DATE NOT NULL,
            rate REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_currency, to_currency, rate_date)
        )
        """)
        conn.commit()


# ============================================================================
# CRUD — Cashflows
# ============================================================================

def insert_cashflow(conn, fund_id, date, cf_type, amount, currency='EUR',
                    is_actual=True, scenario_name='base', notes=None):
    """Fügt einen Cashflow ein (UPSERT bei Duplikat)"""
    with conn.cursor() as cursor:
        cursor.execute("""
        INSERT INTO cashflows (fund_id, date, type, amount, currency, is_actual, scenario_name, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (fund_id, date, type, scenario_name)
        DO UPDATE SET amount = EXCLUDED.amount,
                      currency = EXCLUDED.currency,
                      is_actual = EXCLUDED.is_actual,
                      notes = EXCLUDED.notes
        RETURNING cashflow_id
        """, (fund_id, date, cf_type, amount, currency, is_actual, scenario_name, notes))
        conn.commit()
        return cursor.fetchone()[0]


def update_cashflow(conn, cashflow_id, **kwargs):
    """Aktualisiert einen bestehenden Cashflow"""
    allowed = {'date', 'type', 'amount', 'currency', 'is_actual', 'scenario_name', 'notes'}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    set_clause = ', '.join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [cashflow_id]
    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE cashflows SET {set_clause} WHERE cashflow_id = %s",
            values
        )
        conn.commit()


def delete_cashflow(conn, cashflow_id):
    """Löscht einen Cashflow"""
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM cashflows WHERE cashflow_id = %s", (cashflow_id,))
        conn.commit()


def get_cashflows_for_fund(conn, fund_id, scenario_name=None):
    """Holt alle Cashflows für einen Fonds, sortiert nach Datum"""
    with conn.cursor() as cursor:
        if scenario_name:
            cursor.execute("""
            SELECT cashflow_id, fund_id, date, type, amount, currency,
                   is_actual, scenario_name, notes, created_at
            FROM cashflows
            WHERE fund_id = %s AND scenario_name = %s
            ORDER BY date
            """, (fund_id, scenario_name))
        else:
            cursor.execute("""
            SELECT cashflow_id, fund_id, date, type, amount, currency,
                   is_actual, scenario_name, notes, created_at
            FROM cashflows
            WHERE fund_id = %s
            ORDER BY date
            """, (fund_id,))
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def bulk_insert_cashflows(conn, cashflows_list):
    """Bulk-Insert von Cashflows (für Excel-Import).

    cashflows_list: list of dicts mit Keys:
        fund_id, date, type, amount, currency, is_actual, scenario_name, notes
    """
    if not cashflows_list:
        return 0
    with conn.cursor() as cursor:
        count = 0
        for cf in cashflows_list:
            cursor.execute("""
            INSERT INTO cashflows (fund_id, date, type, amount, currency, is_actual, scenario_name, notes)
            VALUES (%(fund_id)s, %(date)s, %(type)s, %(amount)s, %(currency)s,
                    %(is_actual)s, %(scenario_name)s, %(notes)s)
            ON CONFLICT (fund_id, date, type, scenario_name)
            DO UPDATE SET amount = EXCLUDED.amount,
                          currency = EXCLUDED.currency,
                          is_actual = EXCLUDED.is_actual,
                          notes = EXCLUDED.notes
            """, cf)
            count += 1
        conn.commit()
        return count


# ============================================================================
# CRUD — Scenarios
# ============================================================================

def get_all_scenarios(conn):
    """Holt alle Szenarien"""
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT scenario_id, scenario_name, description, created_at
        FROM scenarios ORDER BY scenario_id
        """)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def insert_scenario(conn, name, description=None):
    """Erstellt ein neues Szenario"""
    with conn.cursor() as cursor:
        cursor.execute("""
        INSERT INTO scenarios (scenario_name, description)
        VALUES (%s, %s)
        ON CONFLICT (scenario_name) DO NOTHING
        RETURNING scenario_id
        """, (name, description))
        conn.commit()
        result = cursor.fetchone()
        return result[0] if result else None


# ============================================================================
# CRUD — Fund Commitment
# ============================================================================

def update_fund_commitment(conn, fund_id, commitment_amount=None,
                           unfunded_amount=None, commitment_date=None,
                           expected_end_date=None):
    """Aktualisiert die Commitment-Daten eines Fonds"""
    with conn.cursor() as cursor:
        cursor.execute("""
        UPDATE funds
        SET commitment_amount = COALESCE(%s, commitment_amount),
            unfunded_amount = COALESCE(%s, unfunded_amount),
            commitment_date = COALESCE(%s, commitment_date),
            expected_end_date = COALESCE(%s, expected_end_date)
        WHERE fund_id = %s
        """, (commitment_amount, unfunded_amount, commitment_date,
              expected_end_date, fund_id))
        conn.commit()


# ============================================================================
# CRUD — Exchange Rates
# ============================================================================

def insert_exchange_rate(conn, from_currency, to_currency, rate_date, rate):
    """Fügt einen Wechselkurs ein (UPSERT)"""
    with conn.cursor() as cursor:
        cursor.execute("""
        INSERT INTO exchange_rates (from_currency, to_currency, rate_date, rate)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (from_currency, to_currency, rate_date)
        DO UPDATE SET rate = EXCLUDED.rate
        RETURNING rate_id
        """, (from_currency, to_currency, rate_date, rate))
        conn.commit()
        return cursor.fetchone()[0]


def get_exchange_rate(conn, from_currency, to_currency, rate_date):
    """Holt den nächsten verfügbaren Wechselkurs vor oder am angegebenen Datum"""
    if from_currency == to_currency:
        return 1.0
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT rate FROM exchange_rates
        WHERE from_currency = %s AND to_currency = %s AND rate_date <= %s
        ORDER BY rate_date DESC
        LIMIT 1
        """, (from_currency, to_currency, rate_date))
        result = cursor.fetchone()
        return result[0] if result else None

"""
Cashflow Planning Tool — Pipeline DB Schema + CRUD

Fund-Status Workflow:
    Screening → Due Diligence → Negotiation → Committed → Active → Harvesting → Closed
                                    ↓              ↓
                                 Declined       Declined

Status-Gruppen:
    Pipeline: screening, due_diligence, negotiation
    Bestehend: committed, active, harvesting, closed
    Abgelehnt: declined
"""

# ============================================================================
# CONSTANTS
# ============================================================================

VALID_TRANSITIONS = {
    'screening': ['due_diligence', 'declined'],
    'due_diligence': ['negotiation', 'declined'],
    'negotiation': ['committed', 'declined'],
    'committed': ['active'],
    'active': ['harvesting'],
    'harvesting': ['closed'],
    'declined': [],
    'closed': [],
}

PIPELINE_STATUSES = ('screening', 'due_diligence', 'negotiation')
ACTIVE_STATUSES = ('committed', 'active', 'harvesting', 'closed')
ALL_STATUSES = PIPELINE_STATUSES + ACTIVE_STATUSES + ('declined',)

STATUS_LABELS = {
    'screening': 'Screening',
    'due_diligence': 'Due Diligence',
    'negotiation': 'Negotiation',
    'committed': 'Committed',
    'active': 'Active',
    'harvesting': 'Harvesting',
    'closed': 'Closed',
    'declined': 'Declined',
}


# ============================================================================
# SCHEMA — Migration + Tabellen
# ============================================================================

def ensure_fund_status_column(conn):
    """ALTER TABLE funds ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'.
    Bestehende Fonds bekommen 'active' (haben Commitment)."""
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'funds' AND column_name = 'status'
        )
        """)
        exists = cursor.fetchone()[0]
        if not exists:
            cursor.execute("ALTER TABLE funds ADD COLUMN status TEXT DEFAULT 'active'")
            cursor.execute("UPDATE funds SET status = 'active' WHERE status IS NULL")
            conn.commit()


def ensure_pipeline_tables(conn):
    """Erstellt fund_pipeline_meta und fund_status_history Tabellen."""
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fund_pipeline_meta (
            meta_id SERIAL PRIMARY KEY,
            fund_id INTEGER REFERENCES funds(fund_id) ON DELETE CASCADE,
            probability REAL DEFAULT 50,
            dd_score REAL,
            dd_notes TEXT,
            decline_reason TEXT,
            expected_commitment REAL,
            expected_commitment_date DATE,
            source TEXT,
            contact_person TEXT,
            next_step TEXT,
            next_step_date DATE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(fund_id)
        )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_meta_fund ON fund_pipeline_meta(fund_id)"
        )

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fund_status_history (
            history_id SERIAL PRIMARY KEY,
            fund_id INTEGER REFERENCES funds(fund_id) ON DELETE CASCADE,
            old_status TEXT,
            new_status TEXT NOT NULL,
            changed_by TEXT,
            change_reason TEXT,
            changed_at TIMESTAMP DEFAULT NOW()
        )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_status_history_fund ON fund_status_history(fund_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_status_history_date ON fund_status_history(changed_at)"
        )
        conn.commit()


# ============================================================================
# CRUD — Pipeline Meta
# ============================================================================

def get_pipeline_meta(conn, fund_id):
    """Holt Pipeline-Metadaten für einen Fonds. Returns dict oder None."""
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT meta_id, fund_id, probability, dd_score, dd_notes, decline_reason,
               expected_commitment, expected_commitment_date, source, contact_person,
               next_step, next_step_date, created_at, updated_at
        FROM fund_pipeline_meta
        WHERE fund_id = %s
        """, (fund_id,))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))


def upsert_pipeline_meta(conn, fund_id, **kwargs):
    """Erstellt oder aktualisiert Pipeline-Metadaten."""
    allowed = {
        'probability', 'dd_score', 'dd_notes', 'decline_reason',
        'expected_commitment', 'expected_commitment_date', 'source',
        'contact_person', 'next_step', 'next_step_date'
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return

    with conn.cursor() as cursor:
        # Check if exists
        cursor.execute("SELECT meta_id FROM fund_pipeline_meta WHERE fund_id = %s", (fund_id,))
        exists = cursor.fetchone()

        if exists:
            set_parts = [f"{k} = %s" for k in updates]
            set_parts.append("updated_at = NOW()")
            values = list(updates.values()) + [fund_id]
            cursor.execute(
                f"UPDATE fund_pipeline_meta SET {', '.join(set_parts)} WHERE fund_id = %s",
                values
            )
        else:
            cols = ['fund_id'] + list(updates.keys())
            placeholders = ['%s'] * len(cols)
            values = [fund_id] + list(updates.values())
            cursor.execute(
                f"INSERT INTO fund_pipeline_meta ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
                values
            )
        conn.commit()


# ============================================================================
# CRUD — Status Management
# ============================================================================

def change_fund_status(conn, fund_id, new_status, changed_by='system', reason=''):
    """Validiert Transition, loggt in fund_status_history, updated funds.status."""
    with conn.cursor() as cursor:
        # Aktuellen Status holen
        cursor.execute("SELECT status FROM funds WHERE fund_id = %s", (fund_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Fund {fund_id} nicht gefunden")

        old_status = row[0] or 'active'

        # Transition validieren
        valid = VALID_TRANSITIONS.get(old_status, [])
        if new_status not in valid:
            raise ValueError(
                f"Ungültiger Übergang: {old_status} → {new_status}. "
                f"Erlaubt: {valid}"
            )

        # Status aktualisieren
        cursor.execute(
            "UPDATE funds SET status = %s WHERE fund_id = %s",
            (new_status, fund_id)
        )

        # History loggen
        cursor.execute("""
        INSERT INTO fund_status_history (fund_id, old_status, new_status, changed_by, change_reason)
        VALUES (%s, %s, %s, %s, %s)
        """, (fund_id, old_status, new_status, changed_by, reason))

        conn.commit()


def force_change_fund_status(conn, fund_id, new_status, changed_by='admin', reason=''):
    """Admin-Override: Setzt Status ohne Transition-Validierung.
    Loggt trotzdem in fund_status_history."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT status FROM funds WHERE fund_id = %s", (fund_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Fund {fund_id} nicht gefunden")
        old_status = row[0] or 'active'
        if old_status == new_status:
            return
        cursor.execute("UPDATE funds SET status = %s WHERE fund_id = %s", (new_status, fund_id))
        cursor.execute("""
        INSERT INTO fund_status_history (fund_id, old_status, new_status, changed_by, change_reason)
        VALUES (%s, %s, %s, %s, %s)
        """, (fund_id, old_status, new_status, changed_by, reason))
        conn.commit()


def get_fund_status_history(conn, fund_id):
    """Holt Status-Änderungshistorie für einen Fonds."""
    with conn.cursor() as cursor:
        cursor.execute("""
        SELECT history_id, fund_id, old_status, new_status, changed_by,
               change_reason, changed_at
        FROM fund_status_history
        WHERE fund_id = %s
        ORDER BY changed_at DESC
        """, (fund_id,))
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_funds_by_status_group(conn, group='pipeline'):
    """Holt Fonds nach Status-Gruppe.
    group: 'pipeline', 'active', 'declined', 'all'
    """
    with conn.cursor() as cursor:
        if group == 'pipeline':
            statuses = PIPELINE_STATUSES
        elif group == 'active':
            statuses = ACTIVE_STATUSES
        elif group == 'declined':
            statuses = ('declined',)
        else:
            statuses = ALL_STATUSES

        placeholders = ','.join(['%s'] * len(statuses))
        cursor.execute(f"""
        SELECT f.fund_id, f.fund_name, f.status, f.currency, f.vintage_year,
               f.strategy, f.geography, f.fund_size_m,
               f.commitment_amount, f.unfunded_amount,
               g.gp_name,
               pm.probability, pm.expected_commitment, pm.dd_score,
               pm.next_step, pm.next_step_date, pm.source, pm.contact_person
        FROM funds f
        LEFT JOIN gps g ON f.gp_id = g.gp_id
        LEFT JOIN fund_pipeline_meta pm ON f.fund_id = pm.fund_id
        WHERE f.status IN ({placeholders})
        ORDER BY f.fund_name
        """, statuses)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ============================================================================
# CRUD — Pipeline Fund Lifecycle
# ============================================================================

def create_pipeline_fund(conn, fund_name, gp_id, strategy, geography,
                         fund_size_m, currency, vintage_year, probability,
                         expected_commitment, source, contact_person):
    """Erstellt einen neuen Pipeline-Fonds (status='screening') + Pipeline-Meta.
    Returns: fund_id
    """
    with conn.cursor() as cursor:
        cursor.execute("""
        INSERT INTO funds (fund_name, gp_id, strategy, geography, fund_size_m,
                          currency, vintage_year, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'screening')
        RETURNING fund_id
        """, (fund_name, gp_id, strategy, geography, fund_size_m,
              currency, vintage_year))
        fund_id = cursor.fetchone()[0]

        cursor.execute("""
        INSERT INTO fund_pipeline_meta
            (fund_id, probability, expected_commitment, source, contact_person)
        VALUES (%s, %s, %s, %s, %s)
        """, (fund_id, probability, expected_commitment, source, contact_person))

        # Initial history entry
        cursor.execute("""
        INSERT INTO fund_status_history (fund_id, old_status, new_status, changed_by, change_reason)
        VALUES (%s, NULL, 'screening', 'system', 'Neuer Pipeline-Fonds erstellt')
        """, (fund_id,))

        conn.commit()
        return fund_id


def promote_fund(conn, fund_id, commitment_amount, commitment_date, changed_by='system'):
    """Promoted einen Pipeline-Fonds zu 'committed'.
    Setzt commitment_amount, commitment_date, unfunded_amount auf funds.
    """
    # Status auf committed setzen (validiert Transition)
    change_fund_status(conn, fund_id, 'committed', changed_by,
                       f'Promoted mit Commitment {commitment_amount:,.0f}')

    with conn.cursor() as cursor:
        cursor.execute("""
        UPDATE funds
        SET commitment_amount = %s,
            commitment_date = %s,
            unfunded_amount = %s
        WHERE fund_id = %s
        """, (commitment_amount, commitment_date, commitment_amount, fund_id))
        conn.commit()


def decline_fund(conn, fund_id, reason, changed_by='system'):
    """Lehnt einen Pipeline-Fonds ab (status → 'declined')."""
    change_fund_status(conn, fund_id, 'declined', changed_by, reason)

    with conn.cursor() as cursor:
        cursor.execute("""
        UPDATE fund_pipeline_meta
        SET decline_reason = %s, updated_at = NOW()
        WHERE fund_id = %s
        """, (reason, fund_id))
        conn.commit()

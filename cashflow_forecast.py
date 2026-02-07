"""
Cashflow Planning Tool — 7 Prognose-Modelle

Pure Python, keine Streamlit-Abhängigkeiten.
Jedes Modell nimmt Commitment + Parameter und gibt list[dict] mit
{date, type, amount} zurück. Beträge sind positiv (bestehende Konvention).
"""

from datetime import date
import math


# ============================================================================
# HELPERS
# ============================================================================

def _quarter_end_dates(start_year, num_quarters):
    """Gibt eine Liste von Quartalsenddaten zurück.

    Args:
        start_year: Startjahr (Q1 dieses Jahres beginnt)
        num_quarters: Anzahl Quartale

    Returns:
        list[date] — z.B. [2024-03-31, 2024-06-30, 2024-09-30, 2024-12-31, ...]
    """
    quarter_ends = []
    for i in range(num_quarters):
        year = start_year + (i // 4)
        q = (i % 4) + 1
        if q == 1:
            quarter_ends.append(date(year, 3, 31))
        elif q == 2:
            quarter_ends.append(date(year, 6, 30))
        elif q == 3:
            quarter_ends.append(date(year, 9, 30))
        else:
            quarter_ends.append(date(year, 12, 31))
    return quarter_ends


def _annual_to_quarterly(annual_amounts):
    """Verteilt jährliche Beträge gleichmässig auf Quartale.

    Args:
        annual_amounts: list[float] — ein Wert pro Jahr

    Returns:
        list[float] — vier Werte pro Jahr (jeweils annual/4)
    """
    quarterly = []
    for amt in annual_amounts:
        q_amt = amt / 4.0
        quarterly.extend([q_amt] * 4)
    return quarterly


def prepare_forecast_for_insertion(forecast, fund_id, scenario_name,
                                   currency='EUR', notes_prefix='Forecast'):
    """Konvertiert Forecast-Output für bulk_insert_cashflows.

    Args:
        forecast: list[dict] mit {date, type, amount}
        fund_id: Fonds-ID
        scenario_name: Ziel-Szenario
        currency: Währung
        notes_prefix: Präfix für Notes-Feld

    Returns:
        list[dict] bereit für bulk_insert_cashflows
    """
    result = []
    for entry in forecast:
        if entry['amount'] <= 0:
            continue
        result.append({
            'fund_id': fund_id,
            'date': entry['date'],
            'type': entry['type'],
            'amount': round(entry['amount'], 2),
            'currency': currency,
            'is_actual': False,
            'scenario_name': scenario_name,
            'notes': f"{notes_prefix}: {entry['type']}",
        })
    return result


# ============================================================================
# MODELL 1: Takahashi-Alexander (Yale)
# ============================================================================

def forecast_takahashi_alexander(commitment, lifetime=10, vintage_year=2024,
                                  rc=0.25, rd=0.20, bow_factor=2.5,
                                  growth_rate=0.08):
    """Takahashi-Alexander (Yale) Modell.

    Parabolischer Bow-Faktor steuert Timing von Calls und Distributions.
    NAV wächst mit growth_rate und wird durch Distributions reduziert.

    Args:
        commitment: Commitment-Betrag
        lifetime: Fondslaufzeit in Jahren
        vintage_year: Vintage-Jahr
        rc: Rate of Contribution (max Abruf-Rate pro Periode)
        rd: Rate of Distribution (max Ausschüttungs-Rate pro Periode)
        bow_factor: Steuert die Konzentration um die Mitte (>1 = stärker)
        growth_rate: Jährliche NAV-Wachstumsrate

    Returns:
        list[dict] mit {date, type, amount}
    """
    L = lifetime
    num_quarters = L * 4
    dates = _quarter_end_dates(vintage_year, num_quarters)

    # Quarterly rates
    rc_q = rc / 4.0
    rd_q = rd / 4.0
    g_q = (1 + growth_rate) ** 0.25 - 1

    results = []
    nav = 0.0
    total_called = 0.0

    for i in range(num_quarters):
        t = (i + 1) / 4.0  # Jahr-Offset

        # Bow-Faktor: parabolisch, Peak in der Mitte
        mid = L / 2.0
        bow_raw = (t * (L - t)) / (mid ** 2)
        bow = bow_raw ** bow_factor if bow_raw > 0 else 0.0

        unfunded = commitment - total_called
        call = rc_q * bow * unfunded
        call = max(0.0, min(call, unfunded))

        total_called += call
        nav = nav + call

        # NAV wächst
        nav = nav * (1 + g_q)

        # Distribution
        dist = rd_q * bow * nav
        dist = max(0.0, min(dist, nav))
        nav -= dist

        if call > 0.01:
            results.append({
                'date': dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results


# ============================================================================
# MODELL 2: Driessen-Lin-Phalippou
# ============================================================================

def forecast_driessen_lin_phalippou(commitment, lifetime=10, vintage_year=2024,
                                     drawdown_rate=0.30, distribution_rate=0.25,
                                     nav_growth_rate=0.10):
    """Driessen-Lin-Phalippou Modell.

    Exponentieller Zerfall bei Calls, Distributions ab Jahr 3 als % von NAV.

    Args:
        commitment: Commitment-Betrag
        lifetime: Fondslaufzeit in Jahren
        vintage_year: Vintage-Jahr
        drawdown_rate: Jährliche Abruf-Rate auf Unfunded
        distribution_rate: Jährliche Ausschüttungs-Rate auf NAV
        nav_growth_rate: Jährliche NAV-Wachstumsrate

    Returns:
        list[dict] mit {date, type, amount}
    """
    num_quarters = lifetime * 4
    dates = _quarter_end_dates(vintage_year, num_quarters)

    dr_q = 1 - (1 - drawdown_rate) ** 0.25
    dist_r_q = 1 - (1 - distribution_rate) ** 0.25
    g_q = (1 + nav_growth_rate) ** 0.25 - 1

    results = []
    nav = 0.0
    total_called = 0.0

    for i in range(num_quarters):
        year_offset = (i + 1) / 4.0
        unfunded = commitment - total_called

        # Capital call: exponentieller Zerfall auf Unfunded
        call = dr_q * unfunded
        call = max(0.0, min(call, unfunded))
        total_called += call
        nav += call

        # NAV wächst
        nav *= (1 + g_q)

        # Distribution: erst ab Jahr 3
        dist = 0.0
        if year_offset >= 3.0:
            dist = dist_r_q * nav
            dist = max(0.0, min(dist, nav))
            nav -= dist

        if call > 0.01:
            results.append({
                'date': dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results


# ============================================================================
# MODELL 3: Ljungqvist-Richardson
# ============================================================================

def forecast_ljungqvist_richardson(commitment, lifetime=10, vintage_year=2024,
                                    investment_period=5,
                                    investment_pace=None,
                                    harvest_start=4,
                                    harvest_pace=None,
                                    nav_growth_rate=0.10):
    """Ljungqvist-Richardson Modell.

    Investment-Phase: Calls gemäss Pace-Schedule (% von Commitment pro Jahr).
    Harvest-Phase: Distributions als % von NAV (ansteigend).

    Args:
        commitment: Commitment-Betrag
        lifetime: Fondslaufzeit in Jahren
        vintage_year: Vintage-Jahr
        investment_period: Dauer der Investment-Phase (Jahre)
        investment_pace: list[float] — Abruf-% pro Jahr (Summe ~1.0)
        harvest_start: Ab welchem Jahr Distributions beginnen
        harvest_pace: list[float] — Distributions-% von NAV pro Jahr
        nav_growth_rate: Jährliche NAV-Wachstumsrate

    Returns:
        list[dict] mit {date, type, amount}
    """
    if investment_pace is None:
        investment_pace = [0.25, 0.25, 0.20, 0.15, 0.15]
    if harvest_pace is None:
        harvest_pace = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]

    # Pad investment pace to investment_period
    while len(investment_pace) < investment_period:
        investment_pace.append(0.0)

    num_quarters = lifetime * 4
    dates = _quarter_end_dates(vintage_year, num_quarters)
    g_q = (1 + nav_growth_rate) ** 0.25 - 1

    results = []
    nav = 0.0
    total_called = 0.0

    for i in range(num_quarters):
        year_idx = i // 4  # 0-indexed year
        q_in_year = i % 4

        # Capital calls: verteile jährlichen Pace auf 4 Quartale
        call = 0.0
        if year_idx < len(investment_pace):
            annual_call = investment_pace[year_idx] * commitment
            call = annual_call / 4.0
            unfunded = commitment - total_called
            call = max(0.0, min(call, unfunded))

        total_called += call
        nav += call
        nav *= (1 + g_q)

        # Distributions
        dist = 0.0
        harvest_year_idx = year_idx - harvest_start
        if harvest_year_idx >= 0 and harvest_year_idx < len(harvest_pace):
            annual_dist_rate = harvest_pace[harvest_year_idx]
            dist = (annual_dist_rate / 4.0) * nav
            dist = max(0.0, min(dist, nav))
            nav -= dist
        elif harvest_year_idx >= len(harvest_pace) and nav > 0.01:
            # Nach dem Schedule: verbleibenden NAV ausschütten
            remaining_quarters = num_quarters - i
            if remaining_quarters > 0:
                dist = nav / remaining_quarters
                dist = max(0.0, min(dist, nav))
                nav -= dist

        if call > 0.01:
            results.append({
                'date': dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results


# ============================================================================
# MODELL 4: Cambridge Quantile
# ============================================================================

# Eingebettete Benchmark-Kurven: % von Commitment pro Jahr
# Format: {strategy: {percentile: {'calls': [...], 'dists': [...]}}}
CAMBRIDGE_BENCHMARKS = {
    'buyout': {
        'q1': {
            'calls': [0.20, 0.20, 0.15, 0.10, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01],
            'dists': [0.00, 0.02, 0.05, 0.10, 0.15, 0.18, 0.20, 0.18, 0.12, 0.08],
        },
        'median': {
            'calls': [0.25, 0.25, 0.18, 0.12, 0.08, 0.05, 0.03, 0.02, 0.01, 0.01],
            'dists': [0.00, 0.03, 0.08, 0.14, 0.18, 0.22, 0.24, 0.22, 0.15, 0.10],
        },
        'q3': {
            'calls': [0.30, 0.28, 0.20, 0.12, 0.05, 0.03, 0.01, 0.01, 0.00, 0.00],
            'dists': [0.00, 0.05, 0.12, 0.20, 0.25, 0.28, 0.30, 0.28, 0.20, 0.15],
        },
    },
    'venture': {
        'q1': {
            'calls': [0.15, 0.20, 0.20, 0.15, 0.10, 0.08, 0.05, 0.03, 0.02, 0.02],
            'dists': [0.00, 0.00, 0.02, 0.05, 0.08, 0.12, 0.15, 0.18, 0.15, 0.10],
        },
        'median': {
            'calls': [0.20, 0.22, 0.20, 0.15, 0.10, 0.06, 0.04, 0.02, 0.01, 0.00],
            'dists': [0.00, 0.01, 0.04, 0.08, 0.12, 0.16, 0.20, 0.22, 0.18, 0.12],
        },
        'q3': {
            'calls': [0.25, 0.25, 0.22, 0.13, 0.08, 0.04, 0.02, 0.01, 0.00, 0.00],
            'dists': [0.00, 0.02, 0.08, 0.15, 0.20, 0.25, 0.30, 0.32, 0.25, 0.18],
        },
    },
    'growth': {
        'q1': {
            'calls': [0.22, 0.22, 0.18, 0.12, 0.08, 0.06, 0.04, 0.03, 0.02, 0.01],
            'dists': [0.00, 0.02, 0.06, 0.10, 0.14, 0.18, 0.20, 0.18, 0.14, 0.10],
        },
        'median': {
            'calls': [0.25, 0.25, 0.20, 0.12, 0.08, 0.05, 0.03, 0.01, 0.01, 0.00],
            'dists': [0.00, 0.03, 0.08, 0.14, 0.18, 0.22, 0.24, 0.22, 0.16, 0.12],
        },
        'q3': {
            'calls': [0.30, 0.28, 0.22, 0.10, 0.05, 0.03, 0.01, 0.01, 0.00, 0.00],
            'dists': [0.00, 0.05, 0.12, 0.20, 0.24, 0.28, 0.30, 0.28, 0.22, 0.16],
        },
    },
    'infrastructure': {
        'q1': {
            'calls': [0.15, 0.18, 0.18, 0.15, 0.12, 0.08, 0.05, 0.04, 0.03, 0.02],
            'dists': [0.00, 0.02, 0.05, 0.08, 0.10, 0.12, 0.14, 0.14, 0.12, 0.10],
        },
        'median': {
            'calls': [0.20, 0.22, 0.20, 0.15, 0.10, 0.06, 0.04, 0.02, 0.01, 0.00],
            'dists': [0.00, 0.03, 0.07, 0.10, 0.13, 0.16, 0.18, 0.18, 0.15, 0.12],
        },
        'q3': {
            'calls': [0.25, 0.25, 0.22, 0.13, 0.08, 0.04, 0.02, 0.01, 0.00, 0.00],
            'dists': [0.00, 0.05, 0.10, 0.15, 0.18, 0.22, 0.24, 0.24, 0.20, 0.16],
        },
    },
    'real_estate': {
        'q1': {
            'calls': [0.18, 0.20, 0.18, 0.15, 0.10, 0.07, 0.05, 0.03, 0.02, 0.02],
            'dists': [0.00, 0.03, 0.06, 0.10, 0.12, 0.15, 0.16, 0.16, 0.14, 0.10],
        },
        'median': {
            'calls': [0.22, 0.24, 0.20, 0.14, 0.08, 0.05, 0.04, 0.02, 0.01, 0.00],
            'dists': [0.00, 0.04, 0.08, 0.12, 0.16, 0.18, 0.20, 0.20, 0.16, 0.12],
        },
        'q3': {
            'calls': [0.28, 0.26, 0.22, 0.12, 0.06, 0.03, 0.02, 0.01, 0.00, 0.00],
            'dists': [0.00, 0.06, 0.12, 0.18, 0.22, 0.26, 0.28, 0.26, 0.22, 0.16],
        },
    },
}


def forecast_cambridge_quantile(commitment, lifetime=10, vintage_year=2024,
                                 strategy='buyout', percentile='median',
                                 tvpi_multiple=1.6):
    """Cambridge Associates Quantile-basiertes Modell.

    Verwendet eingebettete Benchmark-Kurven pro Strategie und Percentile.
    Distributions werden auf Ziel-TVPI skaliert.

    Args:
        commitment: Commitment-Betrag
        lifetime: Fondslaufzeit in Jahren
        vintage_year: Vintage-Jahr
        strategy: 'buyout', 'venture', 'growth', 'infrastructure', 'real_estate'
        percentile: 'q1', 'median', 'q3'
        tvpi_multiple: Ziel Total Value to Paid-In Multiple

    Returns:
        list[dict] mit {date, type, amount}
    """
    benchmarks = CAMBRIDGE_BENCHMARKS.get(strategy, CAMBRIDGE_BENCHMARKS['buyout'])
    curves = benchmarks.get(percentile, benchmarks['median'])

    call_pcts = list(curves['calls'])
    dist_pcts = list(curves['dists'])

    # Pad/trim to lifetime
    while len(call_pcts) < lifetime:
        call_pcts.append(0.0)
    while len(dist_pcts) < lifetime:
        dist_pcts.append(0.0)
    call_pcts = call_pcts[:lifetime]
    dist_pcts = dist_pcts[:lifetime]

    # Skaliere Distributions auf TVPI
    total_calls_pct = sum(call_pcts)
    total_dists_pct = sum(dist_pcts)
    if total_dists_pct > 0 and total_calls_pct > 0:
        target_dist_pct = total_calls_pct * tvpi_multiple
        dist_scale = target_dist_pct / total_dists_pct
        dist_pcts = [d * dist_scale for d in dist_pcts]

    # Quartale generieren
    annual_calls = [pct * commitment for pct in call_pcts]
    annual_dists = [pct * commitment for pct in dist_pcts]

    quarterly_calls = _annual_to_quarterly(annual_calls)
    quarterly_dists = _annual_to_quarterly(annual_dists)

    num_quarters = lifetime * 4
    dates = _quarter_end_dates(vintage_year, num_quarters)

    results = []
    for i in range(num_quarters):
        call = quarterly_calls[i] if i < len(quarterly_calls) else 0.0
        dist = quarterly_dists[i] if i < len(quarterly_dists) else 0.0

        if call > 0.01:
            results.append({
                'date': dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results


# ============================================================================
# MODELL 5: Linear
# ============================================================================

def forecast_linear(commitment, lifetime=10, vintage_year=2024,
                    investment_period=5, harvest_start=4,
                    tvpi_multiple=1.5):
    """Lineares Modell.

    Gleichmässiger Abruf über Investment-Periode,
    gleichmässige Ausschüttung über Harvest-Periode.

    Args:
        commitment: Commitment-Betrag
        lifetime: Fondslaufzeit in Jahren
        vintage_year: Vintage-Jahr
        investment_period: Dauer der Investment-Phase (Jahre)
        harvest_start: Ab welchem Jahr Distributions beginnen
        tvpi_multiple: Ziel TVPI Multiple

    Returns:
        list[dict] mit {date, type, amount}
    """
    num_quarters = lifetime * 4
    dates = _quarter_end_dates(vintage_year, num_quarters)

    # Calls: gleichmässig über investment_period
    call_quarters = investment_period * 4
    quarterly_call = commitment / call_quarters if call_quarters > 0 else 0.0

    # Distributions: gleichmässig über harvest-period
    harvest_quarters = (lifetime - harvest_start) * 4
    total_dist = commitment * tvpi_multiple
    quarterly_dist = total_dist / harvest_quarters if harvest_quarters > 0 else 0.0

    results = []
    for i in range(num_quarters):
        year_offset = i / 4.0

        call = quarterly_call if i < call_quarters else 0.0
        dist = quarterly_dist if year_offset >= harvest_start else 0.0

        if call > 0.01:
            results.append({
                'date': dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results


# ============================================================================
# MODELL 6: Manual Pacing
# ============================================================================

def forecast_manual(commitment, vintage_year=2024,
                    call_pacing=None, dist_pacing=None,
                    tvpi_multiple=1.5):
    """Manuelles Pacing-Modell.

    User definiert % von Commitment pro Jahr für Calls und Distributions.

    Args:
        commitment: Commitment-Betrag
        vintage_year: Vintage-Jahr
        call_pacing: dict {year_offset: pct} z.B. {0: 0.15, 1: 0.25, ...}
        dist_pacing: dict {year_offset: pct} z.B. {3: 0.03, 4: 0.07, ...}
        tvpi_multiple: Ziel TVPI (zum Skalieren der Distributions)

    Returns:
        list[dict] mit {date, type, amount}
    """
    if call_pacing is None:
        call_pacing = {}
    if dist_pacing is None:
        dist_pacing = {}

    if not call_pacing and not dist_pacing:
        return []

    # Lifetime aus Pacing ableiten
    max_year = 0
    if call_pacing:
        max_year = max(max_year, max(call_pacing.keys()) + 1)
    if dist_pacing:
        max_year = max(max_year, max(dist_pacing.keys()) + 1)
    lifetime = max(max_year, 1)

    # Skaliere Distributions auf TVPI
    total_call_pct = sum(call_pacing.values())
    total_dist_pct = sum(dist_pacing.values())
    dist_scale = 1.0
    if total_dist_pct > 0 and total_call_pct > 0:
        target_dist_pct = total_call_pct * tvpi_multiple
        dist_scale = target_dist_pct / total_dist_pct

    num_quarters = lifetime * 4
    dates = _quarter_end_dates(vintage_year, num_quarters)

    results = []
    for i in range(num_quarters):
        year_idx = i // 4

        call_pct = call_pacing.get(year_idx, 0.0)
        call = (call_pct * commitment) / 4.0

        dist_pct = dist_pacing.get(year_idx, 0.0)
        dist = (dist_pct * commitment * dist_scale) / 4.0

        if call > 0.01:
            results.append({
                'date': dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results


# ============================================================================
# MODELL 7: Historical Average
# ============================================================================

def forecast_historical_average(commitment, lifetime=10, vintage_year=2024,
                                 historical_cashflows=None,
                                 historical_commitment=None):
    """Historisches Durchschnittsmodell.

    Normalisiert historische Cashflows auf Commitment und leitet Pacing-Kurven ab.

    Args:
        commitment: Ziel-Commitment-Betrag
        lifetime: Fondslaufzeit in Jahren
        vintage_year: Vintage-Jahr
        historical_cashflows: list[dict] mit {date, type, amount}
                              (aus get_cashflows_for_fund)
        historical_commitment: Commitment des historischen Fonds

    Returns:
        list[dict] mit {date, type, amount}
    """
    if not historical_cashflows or not historical_commitment:
        return []
    if historical_commitment <= 0:
        return []

    # Bestimme erstes Datum als Referenz
    outflow_types = {'capital_call', 'management_fee', 'carried_interest'}
    inflow_types = {'distribution', 'clawback'}

    dates_list = sorted(set(cf['date'] for cf in historical_cashflows
                            if isinstance(cf.get('date'), date)))
    if not dates_list:
        return []

    first_date = dates_list[0]
    first_year = first_date.year

    # Aggregiere pro Jahr
    call_by_year = {}
    dist_by_year = {}
    for cf in historical_cashflows:
        cf_date = cf.get('date')
        if not isinstance(cf_date, date):
            continue
        year_offset = cf_date.year - first_year
        if year_offset < 0:
            continue

        amt = cf.get('amount', 0)
        if cf['type'] in outflow_types:
            call_by_year[year_offset] = call_by_year.get(year_offset, 0) + amt
        elif cf['type'] in inflow_types:
            dist_by_year[year_offset] = dist_by_year.get(year_offset, 0) + amt

    # Normalisiere auf Commitment
    call_pacing = {}
    for yr, amt in call_by_year.items():
        call_pacing[yr] = amt / historical_commitment

    dist_pacing = {}
    for yr, amt in dist_by_year.items():
        dist_pacing[yr] = amt / historical_commitment

    # Verwende Manual-Pacing-Modell mit abgeleiteten Kurven (ohne TVPI-Skalierung)
    if not call_pacing and not dist_pacing:
        return []

    max_year = 0
    if call_pacing:
        max_year = max(max_year, max(call_pacing.keys()) + 1)
    if dist_pacing:
        max_year = max(max_year, max(dist_pacing.keys()) + 1)
    actual_lifetime = max(max_year, lifetime)

    num_quarters = actual_lifetime * 4
    quarter_dates = _quarter_end_dates(vintage_year, num_quarters)

    results = []
    for i in range(num_quarters):
        year_idx = i // 4

        call_pct = call_pacing.get(year_idx, 0.0)
        call = (call_pct * commitment) / 4.0

        dist_pct = dist_pacing.get(year_idx, 0.0)
        dist = (dist_pct * commitment) / 4.0

        if call > 0.01:
            results.append({
                'date': quarter_dates[i],
                'type': 'capital_call',
                'amount': round(call, 2),
            })
        if dist > 0.01:
            results.append({
                'date': quarter_dates[i],
                'type': 'distribution',
                'amount': round(dist, 2),
            })

    return results

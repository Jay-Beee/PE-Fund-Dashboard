"""
Cashflow Planning Tool — Excel + PDF Export

Excel: openpyxl via pd.ExcelWriter
PDF:   reportlab (platypus layout, matplotlib charts als PNG eingebettet)
"""

import io
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import date


# ============================================================================
# EXCEL EXPORTS
# ============================================================================

def export_cashflows_excel(df, fund_name, currency, scenario_name):
    """Exportiert Cashflow-Tabelle als Excel (BytesIO).

    Sheet 1: Cashflows (Datum, Typ, Betrag, Status, Notizen)
    Sheet 2: Summary (Total Calls, Dists, Netto, DPI)
    """
    output = io.BytesIO()

    OUTFLOW_TYPES = {'capital_call', 'management_fee', 'carried_interest'}
    INFLOW_TYPES = {'distribution', 'clawback'}
    TYPE_LABELS = {
        'capital_call': 'Kapitalabruf', 'distribution': 'Ausschüttung',
        'management_fee': 'Management Fee', 'carried_interest': 'Carried Interest',
        'clawback': 'Clawback',
    }

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Cashflows
        if df is not None and not df.empty:
            export_df = df[['date', 'type', 'amount', 'is_actual', 'notes']].copy()
            export_df['date'] = pd.to_datetime(export_df['date']).dt.strftime('%Y-%m-%d')
            export_df['type'] = export_df['type'].map(TYPE_LABELS).fillna(export_df['type'])
            export_df['is_actual'] = export_df['is_actual'].apply(lambda x: 'Ist' if x else 'Plan')
            export_df.columns = ['Datum', 'Typ', f'Betrag ({currency})', 'Status', 'Notizen']
            export_df.to_excel(writer, sheet_name='Cashflows', index=False)
        else:
            pd.DataFrame({'Info': ['Keine Cashflows vorhanden']}).to_excel(
                writer, sheet_name='Cashflows', index=False
            )

        # Sheet 2: Summary
        if df is not None and not df.empty:
            total_called = df.loc[df['type'].isin(OUTFLOW_TYPES), 'amount'].sum()
            total_distributed = df.loc[df['type'].isin(INFLOW_TYPES), 'amount'].sum()
            net = total_distributed - total_called
            dpi = total_distributed / total_called if total_called > 0 else 0.0

            summary_data = {
                'Metrik': ['Fonds', 'Szenario', 'Währung', 'Total Abrufe',
                          'Total Ausschüttungen', 'Netto-Cashflow', 'DPI', 'Export-Datum'],
                'Wert': [fund_name, scenario_name, currency,
                        f'{total_called:,.0f}', f'{total_distributed:,.0f}',
                        f'{net:,.0f}', f'{dpi:.2f}x', str(date.today())],
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)

    output.seek(0)
    return output


def export_portfolio_excel(fund_breakdown_df, summary, periodic_df, base_currency):
    """Exportiert Portfolio-Daten als Excel (BytesIO).

    Sheet 1: Fonds-Aufschlüsselung
    Sheet 2: Portfolio-Summary (KPIs)
    Sheet 3: Periodische Cashflows
    """
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Fonds-Aufschlüsselung
        if fund_breakdown_df is not None and not fund_breakdown_df.empty:
            export_bd = fund_breakdown_df.copy()
            for col in ['commitment_base', 'called_base', 'distributed_base', 'net_base']:
                if col in export_bd.columns:
                    export_bd[col] = export_bd[col].apply(
                        lambda x: round(x, 0) if pd.notna(x) else None
                    )
            export_bd.columns = ['Fonds', 'Währung', f'Commitment ({base_currency})',
                                f'Called ({base_currency})', f'Distributed ({base_currency})',
                                f'Netto ({base_currency})', 'DPI']
            export_bd.to_excel(writer, sheet_name='Fonds-Aufschlüsselung', index=False)

        # Sheet 2: Summary
        summary_data = {
            'Metrik': ['Total Commitment', 'Total Called', 'Total Distributed',
                      'Total Unfunded', 'Netto-Cashflow', 'Portfolio DPI',
                      'Anzahl Fonds', 'Basiswährung', 'Export-Datum'],
            'Wert': [
                f"{summary.get('total_commitment', 0):,.0f}",
                f"{summary.get('total_called', 0):,.0f}",
                f"{summary.get('total_distributed', 0):,.0f}",
                f"{summary.get('total_unfunded', 0):,.0f}",
                f"{summary.get('net_cashflow', 0):,.0f}",
                f"{summary.get('portfolio_dpi', 0):.2f}x",
                str(summary.get('num_funds', 0)),
                base_currency,
                str(date.today()),
            ],
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Portfolio-Summary', index=False)

        # Sheet 3: Periodische Cashflows
        if periodic_df is not None and not periodic_df.empty:
            export_per = periodic_df.copy()
            export_per.columns = ['Periode', f'Kapitalabrufe ({base_currency})',
                                 f'Ausschüttungen ({base_currency})',
                                 f'Netto ({base_currency})']
            export_per.to_excel(writer, sheet_name='Periodische Cashflows', index=False)

    output.seek(0)
    return output


def export_liquidity_excel(funding_gap_df, cash_reserve_df, params, base_currency):
    """Exportiert Liquiditätsplanung als Excel (BytesIO).

    Sheet 1: Funding-Gap
    Sheet 2: Cash-Reserve Simulation
    """
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Funding-Gap
        if funding_gap_df is not None and not funding_gap_df.empty:
            export_fg = funding_gap_df.copy()
            export_fg.to_excel(writer, sheet_name='Funding-Gap', index=False)
        else:
            pd.DataFrame({'Info': ['Keine Funding-Gap Daten']}).to_excel(
                writer, sheet_name='Funding-Gap', index=False
            )

        # Sheet 2: Cash-Reserve
        if cash_reserve_df is not None and not cash_reserve_df.empty:
            export_cr = cash_reserve_df.copy()
            export_cr.to_excel(writer, sheet_name='Cash-Reserve', index=False)
        else:
            pd.DataFrame({'Info': ['Keine Cash-Reserve Daten']}).to_excel(
                writer, sheet_name='Cash-Reserve', index=False
            )

        # Sheet 3: Parameter
        param_data = {
            'Parameter': ['Basiswährung', 'Startguthaben', 'Szenario', 'Export-Datum'],
            'Wert': [
                base_currency,
                f"{params.get('start_balance', 0):,.0f}",
                params.get('scenario', 'base'),
                str(date.today()),
            ],
        }
        pd.DataFrame(param_data).to_excel(writer, sheet_name='Parameter', index=False)

    output.seek(0)
    return output


# ============================================================================
# PDF EXPORTS
# ============================================================================

def _fig_to_image_bytes(fig):
    """Konvertiert matplotlib Figure zu PNG bytes."""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def export_fund_report_pdf(fund_name, currency, summary, cumulative_df, periodic_df, commit_info):
    """Erstellt Fund-Report als PDF (BytesIO).

    Seite 1: Header + KPIs + J-Curve Chart
    Seite 2: Cashflow-Balkendiagramm + Tabelle
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
        )
        from reportlab.lib.utils import ImageReader
    except ImportError:
        return None

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'],
                                 fontSize=18, spaceAfter=20)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'],
                                    fontSize=14, spaceAfter=10)

    elements = []

    # Header
    elements.append(Paragraph(f"Fund Report: {fund_name}", title_style))
    elements.append(Paragraph(f"Datum: {date.today().strftime('%d.%m.%Y')} | Währung: {currency}", styles['Normal']))
    elements.append(Spacer(1, 0.5*cm))

    # KPIs
    commitment = commit_info.get('commitment_amount') or 0
    kpi_data = [
        ['Commitment', f"{commitment:,.0f} {currency}",
         'Total Abrufe', f"{summary.get('total_called', 0):,.0f} {currency}"],
        ['Total Ausschüttungen', f"{summary.get('total_distributed', 0):,.0f} {currency}",
         'Netto-Cashflow', f"{summary.get('net_cashflow', 0):,.0f} {currency}"],
        ['DPI', f"{summary.get('dpi', 0):.2f}x",
         'Unfunded', f"{commit_info.get('unfunded_amount', 0) or 0:,.0f} {currency}"],
    ]
    kpi_table = Table(kpi_data, colWidths=[4*cm, 4.5*cm, 4*cm, 4.5*cm])
    kpi_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (0, -1), colors.Color(0.93, 0.93, 0.97)),
        ('BACKGROUND', (2, 0), (2, -1), colors.Color(0.93, 0.93, 0.97)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 0.8*cm))

    # J-Curve Chart
    if cumulative_df is not None and not cumulative_df.empty:
        from cashflow_charts import create_j_curve_chart
        fig = create_j_curve_chart(cumulative_df, fund_name, currency)
        if fig:
            img_buf = _fig_to_image_bytes(fig)
            img = Image(img_buf, width=16*cm, height=8*cm)
            elements.append(Paragraph("J-Curve", heading_style))
            elements.append(img)
            elements.append(Spacer(1, 0.5*cm))

    # Balkendiagramm
    if periodic_df is not None and not periodic_df.empty:
        from cashflow_charts import create_cashflow_bar_chart
        fig = create_cashflow_bar_chart(periodic_df, fund_name, currency)
        if fig:
            img_buf = _fig_to_image_bytes(fig)
            img = Image(img_buf, width=16*cm, height=8*cm)
            elements.append(Paragraph("Cashflow-Balkendiagramm", heading_style))
            elements.append(img)
            elements.append(Spacer(1, 0.5*cm))

    # Cashflow-Tabelle (letzte 20)
    if periodic_df is not None and not periodic_df.empty:
        elements.append(Paragraph("Periodische Cashflows", heading_style))
        table_data = [['Periode', f'Abrufe ({currency})',
                       f'Ausschüttungen ({currency})', f'Netto ({currency})']]
        for _, row in periodic_df.tail(20).iterrows():
            table_data.append([
                str(row['period_label']),
                f"{row['capital_calls']:,.0f}",
                f"{row['distributions']:,.0f}",
                f"{row['net_cashflow']:,.0f}",
            ])
        cf_table = Table(table_data, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
        cf_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.2, 0.4)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(cf_table)

    doc.build(elements)
    output.seek(0)
    return output


def export_portfolio_report_pdf(summary, fund_breakdown_df, cumulative_df,
                                 periodic_df, base_currency):
    """Erstellt Portfolio-Report als PDF (BytesIO).

    Seite 1: Portfolio-KPIs + Fonds-Aufschlüsselungstabelle
    Seite 2: Portfolio J-Curve + Balkendiagramm
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
        )
    except ImportError:
        return None

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'],
                                 fontSize=18, spaceAfter=20)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'],
                                    fontSize=14, spaceAfter=10)

    elements = []

    # Header
    elements.append(Paragraph(f"Portfolio Report", title_style))
    elements.append(Paragraph(
        f"Datum: {date.today().strftime('%d.%m.%Y')} | Basiswährung: {base_currency} | "
        f"Fonds: {summary.get('num_funds', 0)}",
        styles['Normal']
    ))
    elements.append(Spacer(1, 0.5*cm))

    # KPIs
    kpi_data = [
        ['Total Commitment', f"{summary.get('total_commitment', 0):,.0f} {base_currency}",
         'Total Called', f"{summary.get('total_called', 0):,.0f} {base_currency}"],
        ['Total Distributed', f"{summary.get('total_distributed', 0):,.0f} {base_currency}",
         'Netto-Cashflow', f"{summary.get('net_cashflow', 0):,.0f} {base_currency}"],
        ['Portfolio DPI', f"{summary.get('portfolio_dpi', 0):.2f}x",
         'Total Unfunded', f"{summary.get('total_unfunded', 0):,.0f} {base_currency}"],
    ]
    kpi_table = Table(kpi_data, colWidths=[4*cm, 4.5*cm, 4*cm, 4.5*cm])
    kpi_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (0, -1), colors.Color(0.93, 0.93, 0.97)),
        ('BACKGROUND', (2, 0), (2, -1), colors.Color(0.93, 0.93, 0.97)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 0.5*cm))

    # Fonds-Aufschlüsselung
    if fund_breakdown_df is not None and not fund_breakdown_df.empty:
        elements.append(Paragraph("Fonds-Aufschlüsselung", heading_style))
        header = ['Fonds', 'Währung', f'Commit ({base_currency})',
                  f'Called ({base_currency})', f'Dist ({base_currency})', 'DPI']
        table_data = [header]
        for _, row in fund_breakdown_df.iterrows():
            table_data.append([
                str(row.get('fund_name', '')),
                str(row.get('currency', '')),
                f"{row['commitment_base']:,.0f}" if pd.notna(row.get('commitment_base')) else 'n/a',
                f"{row['called_base']:,.0f}" if pd.notna(row.get('called_base')) else 'n/a',
                f"{row['distributed_base']:,.0f}" if pd.notna(row.get('distributed_base')) else 'n/a',
                f"{row.get('dpi', 0):.2f}x",
            ])
        bd_table = Table(table_data, colWidths=[3.5*cm, 1.5*cm, 3*cm, 3*cm, 3*cm, 2*cm])
        bd_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.2, 0.4)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(bd_table)
        elements.append(Spacer(1, 0.5*cm))

    # Portfolio Charts
    if cumulative_df is not None and not cumulative_df.empty:
        from cashflow_portfolio_charts import create_portfolio_j_curve_chart
        fig = create_portfolio_j_curve_chart(cumulative_df, base_currency)
        if fig:
            img_buf = _fig_to_image_bytes(fig)
            img = Image(img_buf, width=16*cm, height=8*cm)
            elements.append(Paragraph("Portfolio J-Curve", heading_style))
            elements.append(img)
            elements.append(Spacer(1, 0.5*cm))

    if periodic_df is not None and not periodic_df.empty:
        from cashflow_portfolio_charts import create_portfolio_bar_chart
        fig = create_portfolio_bar_chart(periodic_df, base_currency)
        if fig:
            img_buf = _fig_to_image_bytes(fig)
            img = Image(img_buf, width=16*cm, height=8*cm)
            elements.append(Paragraph("Portfolio Cashflows", heading_style))
            elements.append(img)

    doc.build(elements)
    output.seek(0)
    return output

"""Render the research report, preserving source hyperlinks."""
from __future__ import annotations

import html
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, KeepTogether, LongTable,
    PageTemplate, Paragraph, Spacer, TableStyle,
)

from screen import ROOT


def inline(value: str) -> str:
    value = html.escape(value)
    value = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'<link href="\2" color="#14568C">\1</link>', value)
    value = re.sub(r'`([^`]+)`', r'<font name="Courier">\1</font>', value)
    return value


def main() -> None:
    source = (ROOT / 'report-source.md').read_text(encoding='utf-8')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ReportBody', fontName='Helvetica', fontSize=9.5,
                              leading=13.6, spaceAfter=7, allowWidows=0, allowOrphans=0,
                              textColor=colors.HexColor('#253341')))
    styles.add(ParagraphStyle(name='ReportTitle', fontName='Helvetica-Bold', fontSize=25,
                              leading=29, spaceAfter=15, textColor=colors.HexColor('#15334D')))
    styles.add(ParagraphStyle(name='ReportH2', fontName='Helvetica-Bold', fontSize=14,
                              leading=18, spaceBefore=13, spaceAfter=8, keepWithNext=True))
    styles.add(ParagraphStyle(name='ReportH3', fontName='Helvetica-Bold', fontSize=11,
                              leading=15, spaceBefore=10, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle(name='Cell', fontName='Helvetica', fontSize=8,
                              leading=11, spaceAfter=0, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='HeaderCell', fontName='Helvetica-Bold', fontSize=8,
                              leading=11, textColor=colors.white))
    page_width, page_height = A4
    margin = 19 * mm
    width = page_width - 2 * margin
    output = ROOT / 'BTC-delay-research-and-implementation-plan.pdf'
    doc = BaseDocTemplate(str(output), pagesize=A4, leftMargin=margin, rightMargin=margin,
                          topMargin=19 * mm, bottomMargin=18 * mm,
                          title='BTC delay monitor for Delta Exchange', author='Research for Delta Strategy Desk')

    def page_chrome(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#617282'))
        canvas.drawString(margin, page_height - 11 * mm, 'DELTA STRATEGY DESK  /  RESEARCH')
        canvas.drawRightString(page_width - margin, page_height - 11 * mm, '5 September 2026')
        canvas.drawString(margin, 10 * mm, 'Exploratory evidence. Monitoring shortlist; no coin approved for live execution.')
        canvas.drawRightString(page_width - margin, 10 * mm, str(document.page))
        canvas.restoreState()

    frame = Frame(margin, 18 * mm, width, page_height - 37 * mm, leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id='report', frames=[frame], onPage=page_chrome))
    lines = source.splitlines()
    flow = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\- ]+', cell) for cell in cells):
                    rows.append(cells)
                i += 1
            columns = len(rows[0])
            if any(len(row) != columns for row in rows):
                raise ValueError('Inconsistent table columns')
            weights = {3: [1, 2, 2], 6: [1.2, .65, 1, 1, 1, 1.2]}.get(columns, [1] * columns)
            if rows[0][0] == 'Role':
                weights = [.75, 1.35, .8, .95, .9, 1.5]
            if columns == 7:
                weights = [.8, 1.6, .7, .9, .9, 1.6, 1]
            cell_rows = [[Paragraph(inline(c), styles['HeaderCell' if r == 0 else 'Cell']) for c in row] for r, row in enumerate(rows)]
            table = LongTable(cell_rows, colWidths=[width * w / sum(weights) for w in weights], repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#15334D')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#F2F6FA'), colors.white]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LINEBELOW', (0, 0), (-1, 0), .4, colors.HexColor('#15334D')),
            ]))
            flow.extend([KeepTogether([table]), Spacer(1, 9)])
            continue
        if line.startswith('# '):
            flow.extend([Paragraph(inline(line[2:]), styles['ReportTitle']),
                         HRFlowable(width='100%', thickness=2, color=colors.HexColor('#D49335')), Spacer(1, 12)])
        elif line.startswith('## '):
            flow.append(Paragraph(inline(line[3:]), styles['ReportH2']))
        elif line.startswith('### '):
            flow.append(Paragraph(inline(line[4:]), styles['ReportH3']))
        else:
            paragraph = Paragraph(inline(line), styles['ReportBody'])
            following = next((s.strip() for s in lines[i+1:] if s.strip()), '')
            if following.startswith('|'):
                paragraph.keepWithNext = True
            flow.append(paragraph)
        i += 1
    doc.build(flow)
    print(str(output))


if __name__ == '__main__':
    main()

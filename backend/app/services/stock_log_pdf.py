"""One-page PDF export for stock-log items that need attention."""

from datetime import datetime
from io import BytesIO

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import Table, TableStyle
from reportlab.pdfgen import canvas

from app.core.clock import business_tz


GREEN = HexColor("#0B3A2E")
PAPER = HexColor("#FAF9F5")
BORDER = HexColor("#D8D5CB")
INK = HexColor("#17211D")
MUTED = HexColor("#66716B")
RED = HexColor("#B52B1E")
RED_LIGHT = HexColor("#FDECEC")
ORANGE = HexColor("#A85C08")
ORANGE_LIGHT = HexColor("#FFF2DE")


def _fmt_qty(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _stock_label(row: dict) -> str:
    qty = _fmt_qty(row.get("cover_qty"))
    if row.get("cover_source") == "gas log":
        return f"{qty} kg"
    unit = str(row.get("unit") or "").strip()
    return f"{qty} {unit}".strip()


def _daily_label(row: dict) -> str:
    daily = _fmt_qty(row.get("daily_consumption"))
    if row.get("cover_source") == "gas log":
        return f"{daily} kg/day"
    unit = str(row.get("unit") or "").strip()
    return f"{daily} {unit}/day".strip()


def build_low_cover_stock_pdf(rows: list[dict]) -> bytes:
    """Return a single-page landscape PDF with red/orange cover stock items."""
    items = [
        dict(row)
        for row in rows
        if row.get("cover_colour") in {"red", "orange"}
    ]
    items.sort(key=lambda row: (
        0 if row.get("cover_colour") == "red" else 1,
        row.get("cover_days") if row.get("cover_days") is not None else 999,
        str(row.get("category") or "").lower(),
        str(row.get("name") or "").lower(),
    ))

    width, height = landscape(A4)
    margin = 24
    header_h = 82
    footer_h = 46
    table_top = height - header_h - 10
    table_bottom = footer_h
    available_width = width - 2 * margin
    available_height = table_top - table_bottom

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(width, height), pageCompression=1)
    pdf.setTitle("URS Majestic stock log low-cover export")
    pdf.setAuthor("URS Majestic")
    pdf.setSubject("Red and orange cover stock items")

    # Header band.
    pdf.setFillColor(GREEN)
    pdf.rect(0, height - header_h, width, header_h, fill=1, stroke=0)
    pdf.setFillColor(white)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(margin, height - 36, "Stock Log Low-Cover Export")
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColor(HexColor("#CFE3DB"))
    pdf.drawString(margin, height - 54, "Items in red and orange cover only")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawRightString(width - margin, height - 31, f"{len(items)} items")
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(
        width - margin,
        height - 47,
        f"Generated {datetime.now(business_tz()).strftime('%d %b %Y, %I:%M %p IST')}",
    )

    headers = ["Item", "Category", "Current stock", "Daily use", "Cover"]
    data: list[list[str]] = [headers]

    if items:
        for row in items:
            cover_days = row.get("cover_days")
            data.append([
                str(row.get("name") or "—"),
                str(row.get("category") or "—"),
                _stock_label(row),
                _daily_label(row),
                f"{cover_days:.1f} days" if cover_days is not None else "—",
            ])
    else:
        data.append(["No red or orange items right now.", "", "", "", ""])

    col_widths = [260, 120, 125, 123, 165]
    table = Table(data, colWidths=col_widths, repeatRows=1)

    body_font = 7.9
    header_font = 8.2
    row_pad = 4.5
    if len(items) > 14:
        body_font = 7.3
        header_font = 7.8
        row_pad = 3.8
    if len(items) > 20:
        body_font = 6.9
        header_font = 7.4
        row_pad = 3.2

    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), header_font),
        ("LEADING", (0, 0), (-1, 0), header_font + 1),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), row_pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), row_pad),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ALIGN", (2, 1), (3, -1), "RIGHT"),
        ("ALIGN", (4, 1), (4, -1), "CENTER"),
    ])

    if items:
        for idx, row in enumerate(items, start=1):
            row_bg = RED_LIGHT if row.get("cover_colour") == "red" else ORANGE_LIGHT
            row_accent = RED if row.get("cover_colour") == "red" else ORANGE
            style.add("BACKGROUND", (0, idx), (-1, idx), row_bg)
            style.add("FONTNAME", (0, idx), (-1, idx), "Helvetica")
            style.add("FONTSIZE", (0, idx), (-1, idx), body_font)
            style.add("LEADING", (0, idx), (-1, idx), body_font + 1)
            style.add("TEXTCOLOR", (0, idx), (-1, idx), INK)
            style.add("BACKGROUND", (4, idx), (4, idx), row_accent)
            style.add("FONTNAME", (4, idx), (4, idx), "Helvetica-Bold")
            style.add("TEXTCOLOR", (4, idx), (4, idx), white)
            style.add("ALIGN", (0, idx), (1, idx), "LEFT")
            style.add("ALIGN", (2, idx), (3, idx), "RIGHT")
            style.add("ALIGN", (4, idx), (4, idx), "CENTER")
    else:
        style.add("SPAN", (0, 1), (-1, 1))
        style.add("ALIGN", (0, 1), (-1, 1), "CENTER")
        style.add("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold")
        style.add("FONTSIZE", (0, 1), (-1, 1), 9.5)
        style.add("TEXTCOLOR", (0, 1), (-1, 1), MUTED)
        style.add("BACKGROUND", (0, 1), (-1, 1), PAPER)
        style.add("LEFTPADDING", (0, 1), (-1, 1), 12)
        style.add("RIGHTPADDING", (0, 1), (-1, 1), 12)
        style.add("TOPPADDING", (0, 1), (-1, 1), 20)
        style.add("BOTTOMPADDING", (0, 1), (-1, 1), 20)

    table.setStyle(style)

    table_width, table_height = table.wrap(available_width, available_height)
    scale = 1.0
    if table_height > available_height:
        scale = available_height / table_height
    draw_width = table_width * scale
    draw_height = table_height * scale
    table_x = margin + max(0, (available_width - draw_width) / 2)
    table_y = table_bottom + available_height - draw_height

    pdf.saveState()
    pdf.translate(table_x, table_y)
    if scale != 1.0:
        pdf.scale(scale, scale)
    table.drawOn(pdf, 0, 0)
    pdf.restoreState()

    # Footer.
    pdf.setStrokeColor(HexColor("#BDAF92"))
    pdf.setLineWidth(1)
    pdf.line(margin, 40, width - margin, 40)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(margin, 26, "Red cover = under 3 days. Orange cover = under 7 days.")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawRightString(width - margin, 26, "Use this for immediate reorder decisions.")

    pdf.showPage()
    pdf.save()
    return output.getvalue()

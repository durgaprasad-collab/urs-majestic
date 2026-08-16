"""One-page supplier PDF for an approved weekly inventory order."""

from collections import defaultdict
from datetime import datetime
from io import BytesIO

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app.core.clock import business_tz


GREEN = HexColor("#0B3A2E")
GREEN_LIGHT = HexColor("#E8F1ED")
GOLD = HexColor("#C9A227")
INK = HexColor("#17211D")
MUTED = HexColor("#66716B")
BORDER = HexColor("#D8D5CB")
PAPER = HexColor("#FAF9F5")


def _shorten(value: str, font: str, size: float, max_width: float) -> str:
    if stringWidth(value, font, size) <= max_width:
        return value
    suffix = "..."
    while value and stringWidth(value + suffix, font, size) > max_width:
        value = value[:-1]
    return value + suffix


def build_supplier_delivery_pdf(order: dict, deliveries: list[dict]) -> bytes:
    """Return an A4-landscape PDF containing every scheduled delivery."""
    category = str(order.get("category") or "").strip()
    category_label = category if category else "Inventory"
    groups = defaultdict(list)
    for delivery in deliveries:
        if delivery.get("status") == "cancelled" or float(delivery.get("planned_qty") or 0) <= 0:
            continue
        groups[delivery["delivery_date"]].append(delivery)
    dates = sorted(groups)
    if not dates:
        raise ValueError("This order has no supplier deliveries to export.")
    if len(dates) > 3:
        raise ValueError("Supplier PDF supports the configured three-delivery weekly schedule.")

    output = BytesIO()
    width, height = landscape(A4)
    pdf = canvas.Canvas(output, pagesize=(width, height), pageCompression=1)
    pdf.setTitle(f"URS Majestic weekly delivery order {order['id']} - {category_label}")
    pdf.setAuthor("URS Majestic")

    margin = 24
    pdf.setFillColor(GREEN)
    pdf.rect(0, height - 86, width, 86, fill=1, stroke=0)
    pdf.setFillColor(GOLD)
    pdf.roundRect(margin, height - 43, 28, 28, 6, fill=1, stroke=0)
    pdf.setFillColor(GREEN)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawCentredString(margin + 14, height - 33, "UM")
    pdf.setFillColor(white)
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawString(margin + 40, height - 35, "Weekly Delivery Plan")
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColor(HexColor("#CFE3DB"))
    pdf.drawString(margin + 40, height - 52, f"URS Majestic supplier copy · {category_label}")

    pdf.setFillColor(white)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawRightString(width - margin, height - 31, f"ORDER #{order['id']}")
    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(HexColor("#CFE3DB"))
    week = f"{order['horizon_start']:%d %b} - {order['horizon_end']:%d %b %Y}"
    pdf.drawRightString(width - margin, height - 46, week)
    pdf.drawRightString(width - margin, height - 60, str(order["status"]).replace("_", " ").upper())

    panel_top = height - 105
    panel_bottom = 64
    gap = 10
    panel_width = (width - 2 * margin - 2 * gap) / 3
    max_rows = max(len(groups[d]) for d in dates)
    row_height = min(17.0, max(10.5, (panel_top - panel_bottom - 48) / max(max_rows, 1)))
    font_size = 7.5 if row_height >= 13 else 6.7

    for column in range(3):
        x = margin + column * (panel_width + gap)
        pdf.setFillColor(PAPER)
        pdf.setStrokeColor(BORDER)
        pdf.roundRect(x, panel_bottom, panel_width, panel_top - panel_bottom, 9, fill=1, stroke=1)
        if column >= len(dates):
            pdf.setFillColor(MUTED)
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawCentredString(x + panel_width / 2, panel_top - 28, "No delivery")
            continue

        delivery_date = dates[column]
        rows = sorted(groups[delivery_date], key=lambda row: row["name"].lower())
        pdf.setFillColor(GREEN_LIGHT)
        pdf.roundRect(x + 1, panel_top - 43, panel_width - 2, 42, 8, fill=1, stroke=0)
        pdf.setFillColor(GREEN)
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(x + 12, panel_top - 20, delivery_date.strftime("%A"))
        pdf.setFont("Helvetica-Bold", 8)
        pdf.setFillColor(MUTED)
        pdf.drawRightString(x + panel_width - 12, panel_top - 19, delivery_date.strftime("%d %b %Y").upper())
        pdf.setFont("Helvetica", 7)
        pdf.drawString(x + 12, panel_top - 34, f"{len(rows)} items scheduled")

        y = panel_top - 55
        for index, row in enumerate(rows):
            if index % 2 == 1:
                pdf.setFillColor(white)
                pdf.rect(x + 6, y - row_height + 3, panel_width - 12, row_height, fill=1, stroke=0)
            pdf.setStrokeColor(BORDER)
            pdf.setLineWidth(.5)
            pdf.rect(x + 12, y - 7, 7, 7, fill=0, stroke=1)
            pdf.setFillColor(INK)
            pdf.setFont("Helvetica-Bold", font_size)
            name = _shorten(str(row["name"]), "Helvetica-Bold", font_size, panel_width - 100)
            pdf.drawString(x + 25, y - 6, name)
            pdf.setFillColor(GREEN)
            pdf.setFont("Helvetica-Bold", font_size)
            quantity = f"{float(row['planned_qty']):g} {row['unit']}"
            pdf.drawRightString(x + panel_width - 12, y - 6, quantity)
            y -= row_height

    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(1.2)
    pdf.line(margin, 47, width - margin, 47)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(margin, 33, "Supplier: please confirm availability and delivery timing for all three drops.")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(margin, 20, "Quantities shown are the approved delivery quantities. Contact URS Majestic before making substitutions.")
    generated = datetime.now(business_tz()).strftime("Generated %d %b %Y, %I:%M %p IST")
    pdf.drawRightString(width - margin, 20, generated)

    pdf.showPage()
    pdf.save()
    return output.getvalue()

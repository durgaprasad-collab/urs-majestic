"""Catering invoice as a PDF (reportlab). Amounts are the order's own totals: line amounts at
catering rates, less any order-level discount; no GST is added here."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GREEN, MUTED, LINE = HexColor("#0B3A2E"), HexColor("#66716B"), HexColor("#D8D5CB")
H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=18, textColor=GREEN, leading=22)
S = ParagraphStyle("s", fontName="Helvetica", fontSize=9, textColor=MUTED, leading=12)
N = ParagraphStyle("n", fontName="Helvetica", fontSize=10, leading=13)
B = ParagraphStyle("b", parent=N, fontName="Helvetica-Bold")
R = ParagraphStyle("r", parent=N, alignment=2)
RB = ParagraphStyle("rb", parent=B, alignment=2)


def _rs(v) -> str:
    return f"Rs. {float(v or 0):,.2f}"


def build(order, biz: dict) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Invoice C-{order.id}")
    p = lambda t, st=N: Paragraph(escape(str(t)).replace("\n", "<br/>"), st)
    story = [p(biz.get("legal_name") or "URS Majestic", H), p(biz.get("address", ""), S)]
    contact = " · ".join(x for x in (f"Phone {biz['phone']}" if biz.get("phone") else "", f"GSTIN {biz['gstin']}" if biz.get("gstin") else "") if x)
    if contact:
        story.append(p(contact, S))
    story.append(Spacer(1, 8 * mm))
    when = f"{order.delivery_date:%d %b %Y}" + (f", {order.delivery_time:%I:%M %p}".replace(" 0", " ") if order.delivery_time else "")
    meta = Table([[p("INVOICE", B), p(f"Invoice no: C-{order.id}", N)],
                  [p("Billed to", S), p(f"Event date: {when}", N)],
                  [p(f"{order.customer_name}\n{order.customer_phone or ''}" + (f"\n{order.delivery_address}" if order.delivery_address else "")), p(f"Plates: {order.plates}" if order.plates else "", N)]],
                 colWidths=[95 * mm, 79 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [meta, Spacer(1, 6 * mm)]
    rows = [[p("#", B), p("Item", B), p("Qty", RB), p("Rate", RB), p("Amount", RB)]]
    for i, it in enumerate(order.items, 1):
        rate = it.rate if it.rate is not None else (it.amount / it.quantity if it.quantity else 0)
        rows.append([p(i), p(it.item_name), p(f"{it.quantity} {it.unit or ''}".strip(), R), p(_rs(rate), R), p(_rs(it.amount), R)])
    t = Table(rows, colWidths=[9 * mm, 80 * mm, 26 * mm, 29 * mm, 30 * mm], repeatRows=1)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1, GREEN), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                           ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [t, Spacer(1, 4 * mm)]
    lines_total = sum((it.amount for it in order.items), 0)
    extra = max(lines_total - order.total_amount, 0)
    tot = [["Items total", _rs(lines_total)]]
    if extra > 0:
        tot.append(["Discount", "- " + _rs(extra)])
    tot += [["Total", _rs(order.total_amount)], ["Advance received", _rs(order.advance_paid)], ["Balance due", _rs(order.balance_due)]]
    bt = Table([[p(a, B if a in ("Total", "Balance due") else N), p(b, RB if a in ("Total", "Balance due") else R)] for a, b in tot],
               colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    bt.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LINEABOVE", (0, 2 if extra > 0 else 1), (-1, 2 if extra > 0 else 1), 0.8, GREEN)]))
    story += [bt, Spacer(1, 10 * mm)]
    if order.notes:
        story += [p("Notes", S), p(order.notes)]
        story.append(Spacer(1, 6 * mm))
    story.append(p(biz.get("footer") or "Thank you!", S))
    doc.build(story)
    return buf.getvalue()

"""Counter POS: bills with GST invoice numbers, kept apart from `orders` / `item_sales`.

Petpooja stays the source of truth for sales and stock (its daily report is imported),
so POS bills live in their own tables and feed neither, which keeps a bill rung up in
both places from being counted twice. Invoice numbers run gapless per financial year
(Apr-Mar): URS/2026-27/000001. A bill is never deleted, only voided with a reason, and
the voided number stays on record.
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from app.core.clock import business_today

PAY_MODES = ("cash", "upi", "card")
ORDER_TYPES = {"dine_in": "Dine-in", "takeaway": "Takeaway"}
DEFAULTS = {
    "legal_name": "URS Majestic Pure Veg",
    "gstin": "",
    "address": "Shop no. 5, French Village food court, 200 ft Radial Road, Zamin Pallavaram, Chennai 600117",
    "phone": "",
    "gst_rate": "5",
    "prices_include_gst": "0",
    "invoice_prefix": "URS",
    "footer": "Thank you! Visit again.",
}
_Q = Decimal("0.01")


def ensure_tables(db) -> None:
    if db.execute(text("SELECT to_regclass('public.pos_bills')")).scalar() is not None:
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS pos_settings (key text PRIMARY KEY, value text NOT NULL);
        CREATE TABLE IF NOT EXISTS pos_invoice_seq (fy text PRIMARY KEY, last_no integer NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pos_bills (
            id serial PRIMARY KEY,
            invoice_no text NOT NULL UNIQUE,
            fy text NOT NULL,
            bill_date date NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            order_type text NOT NULL DEFAULT 'dine_in',
            table_no text,
            customer_name text,
            customer_phone text,
            customer_gstin text,
            subtotal numeric(10,2) NOT NULL,
            discount numeric(10,2) NOT NULL DEFAULT 0,
            taxable numeric(10,2) NOT NULL,
            gst_rate numeric(5,2) NOT NULL,
            cgst numeric(10,2) NOT NULL,
            sgst numeric(10,2) NOT NULL,
            round_off numeric(5,2) NOT NULL DEFAULT 0,
            total numeric(10,2) NOT NULL,
            payment_mode text NOT NULL,
            status text NOT NULL DEFAULT 'paid' CHECK (status IN ('paid','void')),
            void_reason text,
            voided_at timestamptz,
            created_by integer
        );
        CREATE INDEX IF NOT EXISTS ix_pos_bills_date ON pos_bills (bill_date);
        CREATE TABLE IF NOT EXISTS pos_bill_items (
            id serial PRIMARY KEY,
            bill_id integer NOT NULL REFERENCES pos_bills(id) ON DELETE CASCADE,
            menu_item_id integer,
            name text NOT NULL,
            qty integer NOT NULL CHECK (qty > 0),
            unit_price numeric(10,2) NOT NULL,
            line_total numeric(10,2) NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_pos_bill_items_bill ON pos_bill_items (bill_id);
    """))
    db.commit()


def settings(db) -> dict:
    ensure_tables(db)
    s = dict(DEFAULTS)
    s.update({k: v for k, v in db.execute(text("SELECT key, value FROM pos_settings")).all()})
    return s


def save_settings(db, values: dict) -> None:
    ensure_tables(db)
    for k, v in values.items():
        if k in DEFAULTS:
            db.execute(text("""INSERT INTO pos_settings (key, value) VALUES (:k, :v)
                               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""), {"k": k, "v": str(v).strip()})
    db.commit()


def fy_of(d) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def _r(x: Decimal) -> Decimal:
    return x.quantize(_Q, rounding=ROUND_HALF_UP)


def price(lines: list[tuple[Decimal, int]], discount: Decimal, rate: Decimal, inclusive: bool) -> dict:
    """GST on (subtotal - discount). Inclusive: the menu price already holds the tax, so the
    taxable value is backed out of it and the total is unchanged. Total is rounded to the rupee."""
    sub = _r(sum((p * q for p, q in lines), Decimal(0)))
    disc = min(max(_r(discount), Decimal(0)), sub)
    net = sub - disc
    if inclusive:
        taxable = _r(net / (1 + rate / 100))
        gst = net - taxable
    else:
        taxable = net
        gst = _r(net * rate / 100)
    cgst = _r(gst / 2)
    sgst = gst - cgst
    raw = taxable + cgst + sgst
    total = raw.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return {"subtotal": sub, "discount": disc, "taxable": taxable, "cgst": cgst, "sgst": sgst,
            "round_off": total - raw, "total": total}


def create_bill(db, user_id, items: list[dict], order_type: str, pay_mode: str, discount: Decimal,
                table_no: str = "", cust_name: str = "", cust_phone: str = "", cust_gstin: str = "") -> int:
    """items: [{id, qty}]. Prices are read from the menu here, never trusted from the browser."""
    if order_type not in ORDER_TYPES or pay_mode not in PAY_MODES:
        raise ValueError("Pick order type and payment mode")
    qty_by_id: dict[int, int] = {}
    for it in items:
        q = int(it.get("qty", 0))
        if q > 0:
            qty_by_id[int(it["id"])] = qty_by_id.get(int(it["id"]), 0) + min(q, 99)
    if not qty_by_id:
        raise ValueError("Add at least one item")
    menu = {r["id"]: r for r in db.execute(text(
        "SELECT id, name, price FROM menu_items WHERE id = ANY(:ids) AND is_active"), {"ids": list(qty_by_id)}).mappings()}
    if len(menu) != len(qty_by_id):
        raise ValueError("An item is no longer on the menu")
    s = settings(db)
    rate = Decimal(s["gst_rate"] or "0")
    calc = price([(Decimal(menu[i]["price"]), q) for i, q in qty_by_id.items()], discount, rate, s["prices_include_gst"] == "1")
    today = business_today()
    fy = fy_of(today)
    db.execute(text("INSERT INTO pos_invoice_seq (fy, last_no) VALUES (:f, 0) ON CONFLICT (fy) DO NOTHING"), {"f": fy})
    n = db.execute(text("UPDATE pos_invoice_seq SET last_no = last_no + 1 WHERE fy = :f RETURNING last_no"), {"f": fy}).scalar()
    invoice_no = f"{s['invoice_prefix'] or 'INV'}/{fy}/{n:06d}"
    bill_id = db.execute(text("""
        INSERT INTO pos_bills (invoice_no, fy, bill_date, order_type, table_no, customer_name, customer_phone, customer_gstin,
                               subtotal, discount, taxable, gst_rate, cgst, sgst, round_off, total, payment_mode, created_by)
        VALUES (:no, :fy, :d, :ot, :tb, :cn, :cp, :cg, :sub, :disc, :tx, :rate, :cgst, :sgst, :ro, :tot, :pm, :u) RETURNING id
    """), {"no": invoice_no, "fy": fy, "d": today, "ot": order_type, "tb": table_no.strip()[:20] or None,
           "cn": cust_name.strip()[:80] or None, "cp": cust_phone.strip()[:20] or None, "cg": cust_gstin.strip().upper()[:15] or None,
           "sub": calc["subtotal"], "disc": calc["discount"], "tx": calc["taxable"], "rate": rate, "cgst": calc["cgst"],
           "sgst": calc["sgst"], "ro": calc["round_off"], "tot": calc["total"], "pm": pay_mode, "u": user_id}).scalar()
    for i, q in qty_by_id.items():
        p = Decimal(menu[i]["price"])
        db.execute(text("""INSERT INTO pos_bill_items (bill_id, menu_item_id, name, qty, unit_price, line_total)
                           VALUES (:b, :m, :n, :q, :p, :t)"""),
                   {"b": bill_id, "m": i, "n": menu[i]["name"], "q": q, "p": p, "t": _r(p * q)})
    db.commit()
    return bill_id


def get_bill(db, bill_id: int):
    ensure_tables(db)
    b = db.execute(text("""SELECT b.*, u.name AS by_name FROM pos_bills b LEFT JOIN users u ON u.id = b.created_by
                           WHERE b.id = :i"""), {"i": bill_id}).mappings().first()
    if b is None:
        return None, []
    items = db.execute(text("SELECT * FROM pos_bill_items WHERE bill_id = :i ORDER BY id"), {"i": bill_id}).mappings().all()
    return dict(b), [dict(i) for i in items]


def void_bill(db, bill_id: int, reason: str) -> bool:
    n = db.execute(text("""UPDATE pos_bills SET status = 'void', void_reason = :r, voided_at = now()
                           WHERE id = :i AND status = 'paid'"""), {"i": bill_id, "r": reason.strip()[:200]}).rowcount
    db.commit()
    return n > 0


def day_view(db, day) -> dict:
    ensure_tables(db)
    bills = [dict(r) for r in db.execute(text("""
        SELECT id, invoice_no, created_at, order_type, table_no, total, payment_mode, status, void_reason
          FROM pos_bills WHERE bill_date = :d ORDER BY id DESC"""), {"d": day}).mappings()]
    paid = [b for b in bills if b["status"] == "paid"]
    by_mode = {m: sum((b["total"] for b in paid if b["payment_mode"] == m), Decimal(0)) for m in PAY_MODES}
    tax = db.execute(text("""SELECT coalesce(sum(taxable),0) t, coalesce(sum(cgst),0) c, coalesce(sum(sgst),0) s, coalesce(sum(discount),0) d
                               FROM pos_bills WHERE bill_date = :d AND status = 'paid'"""), {"d": day}).mappings().first()
    return {"bills": bills, "count": len(paid), "voids": len(bills) - len(paid),
            "total": sum((b["total"] for b in paid), Decimal(0)), "by_mode": by_mode, "tax": dict(tax)}


def menu_for_pos(db) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT id, name, category, price::float AS price FROM menu_items
         WHERE is_active ORDER BY category, name""")).mappings()]

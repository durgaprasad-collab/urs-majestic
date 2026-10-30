"""Home stock ledger, home -> restaurant transfers, location-aware purchase stock.

ingredient_stock keeps meaning "usable in the kitchen" (the restaurant). This adds
  * home_stock       append-only balance rows for stock kept at home, same shape
                     as ingredient_stock (latest row = balance);
  * stock_transfers  one row per quantity moved home -> restaurant;
  * a purchase stored at Home posts to home_stock instead of ingredient_stock
    (purchases with no location, or Restaurant, behave exactly as before);
  * move_home_to_restaurant(): one transaction that debits home, credits the
    restaurant (note 'transfer_in:<id>') and records the transfer. It never
    touches purchases or costs, and refuses to move more than is at home.

sync_purchase_to_stock() is rewritten from the LIVE definition (3-argument
purchase_covered_by_count, which compares the count time with the time the
purchase was entered) -- the copy in 0041 is stale.

Revision ID: 0056
Revises: 0055
"""
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS home_stock (
            id bigserial PRIMARY KEY,
            ingredient_id integer NOT NULL REFERENCES ingredients(id),
            on_hand_qty numeric NOT NULL CHECK (on_hand_qty >= 0),
            unit text NOT NULL,
            counted_at timestamptz NOT NULL DEFAULT now(),
            counted_by integer REFERENCES users(id),
            note text
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_home_stock_latest ON home_stock (ingredient_id, counted_at DESC, id DESC)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_transfers (
            id bigserial PRIMARY KEY,
            ingredient_id integer NOT NULL REFERENCES ingredients(id),
            qty numeric NOT NULL CHECK (qty > 0),
            unit text NOT NULL,
            qty_entered numeric NOT NULL,
            unit_entered text NOT NULL,
            from_location text NOT NULL DEFAULT 'home' CHECK (from_location IN ('home', 'restaurant')),
            to_location text NOT NULL DEFAULT 'restaurant' CHECK (to_location IN ('home', 'restaurant')),
            note text,
            created_by integer REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (from_location <> to_location)
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_stock_transfers_item ON stock_transfers (ingredient_id, created_at DESC)")

    # The unit stock is kept in for an item (same rule append_purchase_stock_delta uses).
    op.execute("""
        CREATE OR REPLACE FUNCTION stock_target_unit(p_ingredient_id integer) RETURNS text
        LANGUAGE sql STABLE AS $$
            SELECT COALESCE(v.unit::text, i.unit::text)
              FROM ingredients i LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
             WHERE i.id = p_ingredient_id
        $$""")
    # A quantity in any unit -> the item's stock unit. Same cases as the purchase
    # trigger plus pieces of a pack-sized item stocked by weight (as record_stock does).
    op.execute("""
        CREATE OR REPLACE FUNCTION stock_qty_to_target(p_ingredient_id integer, p_qty numeric, p_unit text)
        RETURNS numeric LANGUAGE plpgsql STABLE AS $$
        DECLARE v_target text := stock_target_unit(p_ingredient_id); v_pack numeric;
        BEGIN
            SELECT pack_size_g INTO v_pack FROM ingredients WHERE id = p_ingredient_id;
            IF p_unit = v_target THEN RETURN p_qty; END IF;
            IF v_target = 'pcs' AND v_pack IS NOT NULL AND p_unit = 'kg' THEN RETURN p_qty * 1000 / v_pack; END IF;
            IF v_target = 'pcs' AND v_pack IS NOT NULL AND p_unit = 'g' THEN RETURN p_qty / v_pack; END IF;
            IF p_unit = 'pcs' AND v_pack IS NOT NULL AND v_target = 'g' THEN RETURN p_qty * v_pack; END IF;
            IF p_unit = 'pcs' AND v_pack IS NOT NULL AND v_target = 'kg' THEN RETURN p_qty * v_pack / 1000; END IF;
            RETURN inventory_convert_qty(p_qty, p_unit, v_target);
        END $$""")

    op.execute("""
        CREATE OR REPLACE FUNCTION append_home_stock_delta(
            p_ingredient_id integer, p_qty numeric, p_unit text, p_sign integer, p_user_id integer, p_note text)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE v_target text := stock_target_unit(p_ingredient_id); v_prev numeric; v_prev_unit text; v_delta numeric;
        BEGIN
            PERFORM 1 FROM ingredients WHERE id = p_ingredient_id FOR UPDATE;
            SELECT on_hand_qty, unit INTO v_prev, v_prev_unit FROM home_stock
             WHERE ingredient_id = p_ingredient_id ORDER BY counted_at DESC, id DESC LIMIT 1;
            v_prev := COALESCE(inventory_convert_qty(v_prev, v_prev_unit, v_target), 0);
            v_delta := stock_qty_to_target(p_ingredient_id, p_qty, p_unit) * p_sign;
            INSERT INTO home_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, GREATEST(0, v_prev + v_delta), v_target, p_user_id, p_note);
        END $$""")

    # Same idea as purchase_covered_by_count: a physical count of the home stock taken
    # after the purchase was entered already contains it.
    op.execute("""
        CREATE OR REPLACE FUNCTION purchase_covered_by_home_count(p_ingredient_id integer, p_purchase_date date, p_entered_at timestamptz)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM home_stock
                 WHERE ingredient_id = p_ingredient_id AND counted_by IS NOT NULL AND note = 'home_count'
                   AND ( ((counted_at AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date > p_purchase_date
                      OR ( ((counted_at AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date = p_purchase_date
                           AND counted_at > coalesce(p_entered_at, now()) ) ))
        $$""")

    op.execute("""
        CREATE OR REPLACE FUNCTION purchase_stock_apply(r purchases, p_sign integer, p_user integer)
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            IF r.storage_location = 'home' THEN
                IF NOT purchase_covered_by_home_count(r.ingredient_id, r.purchase_date, r.created_at) THEN
                    PERFORM append_home_stock_delta(r.ingredient_id, r.qty, r.unit::text, p_sign, p_user, 'purchase_home:' || r.id::text);
                END IF;
            ELSIF NOT purchase_covered_by_count(r.ingredient_id, r.purchase_date, r.created_at) THEN
                PERFORM append_purchase_stock_delta(r.ingredient_id, r.qty, r.unit::text, p_sign, p_user, r.id);
            END IF;
        END $$""")

    op.execute("""
        CREATE OR REPLACE FUNCTION sync_purchase_to_stock()
        RETURNS trigger LANGUAGE plpgsql AS $function$
        DECLARE v_old_active boolean; v_new_active boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu' THEN
                    PERFORM purchase_stock_apply(NEW, 1, NEW.entered_by_user_id);
                END IF;
                RETURN NEW;
            END IF;

            v_old_active := (OLD.deleted_at IS NULL AND OLD.usage_type::text = 'menu');
            v_new_active := (NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu');

            IF OLD.ingredient_id IS DISTINCT FROM NEW.ingredient_id
               OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
               OR OLD.qty IS DISTINCT FROM NEW.qty
               OR OLD.unit IS DISTINCT FROM NEW.unit
               OR OLD.usage_type IS DISTINCT FROM NEW.usage_type
               OR OLD.purchase_date IS DISTINCT FROM NEW.purchase_date
               OR COALESCE(OLD.storage_location, 'restaurant') IS DISTINCT FROM COALESCE(NEW.storage_location, 'restaurant') THEN
                IF v_old_active THEN
                    PERFORM purchase_stock_apply(OLD, -1, COALESCE(NEW.entered_by_user_id, OLD.entered_by_user_id));
                END IF;
                IF v_new_active THEN
                    PERFORM purchase_stock_apply(NEW, 1, NEW.entered_by_user_id);
                END IF;
            END IF;
            RETURN NEW;
        END $function$""")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_purchase_to_stock ON purchases")
    op.execute("""
        CREATE TRIGGER trg_sync_purchase_to_stock
        AFTER INSERT OR UPDATE OF ingredient_id, qty, unit, usage_type, deleted_at, purchase_date, storage_location ON purchases
        FOR EACH ROW EXECUTE FUNCTION sync_purchase_to_stock()""")

    op.execute("""
        CREATE OR REPLACE FUNCTION move_home_to_restaurant(
            p_ingredient_id integer, p_qty numeric, p_unit text, p_user_id integer, p_note text)
        RETURNS bigint LANGUAGE plpgsql AS $$
        DECLARE v_target text; v_q numeric; v_home numeric; v_home_unit text; v_rest numeric; v_rest_unit text; v_tid bigint;
        BEGIN
            PERFORM 1 FROM ingredients WHERE id = p_ingredient_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Item not found'; END IF;
            IF p_qty IS NULL OR p_qty <= 0 THEN RAISE EXCEPTION 'Enter a quantity above zero'; END IF;
            v_target := stock_target_unit(p_ingredient_id);
            v_q := stock_qty_to_target(p_ingredient_id, p_qty, p_unit);
            IF v_q IS NULL OR v_q <= 0 THEN RAISE EXCEPTION 'Cannot convert % to %', p_unit, v_target; END IF;

            SELECT on_hand_qty, unit INTO v_home, v_home_unit FROM home_stock
             WHERE ingredient_id = p_ingredient_id ORDER BY counted_at DESC, id DESC LIMIT 1;
            v_home := COALESCE(inventory_convert_qty(v_home, v_home_unit, v_target), 0);
            IF v_q > v_home + 0.0001 THEN
                RAISE EXCEPTION 'Only % % at home', round(v_home, 3), v_target;
            END IF;

            INSERT INTO stock_transfers (ingredient_id, qty, unit, qty_entered, unit_entered, note, created_by)
            VALUES (p_ingredient_id, v_q, v_target, p_qty, p_unit, NULLIF(trim(p_note), ''), p_user_id)
            RETURNING id INTO v_tid;

            INSERT INTO home_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, GREATEST(0, v_home - v_q), v_target, p_user_id, 'transfer_out:' || v_tid::text);

            SELECT on_hand_qty, unit INTO v_rest, v_rest_unit FROM ingredient_stock
             WHERE ingredient_id = p_ingredient_id ORDER BY counted_at DESC, id DESC LIMIT 1;
            v_rest := COALESCE(inventory_convert_qty(v_rest, v_rest_unit, v_target), 0);
            INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, v_rest + v_q, v_target, p_user_id, 'transfer_in:' || v_tid::text);
            RETURN v_tid;
        END $$""")


def downgrade() -> None:
    # Back to the live pre-0056 trigger (restaurant-only).
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_purchase_to_stock()
        RETURNS trigger LANGUAGE plpgsql AS $function$
        DECLARE v_old_active boolean; v_new_active boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu'
                   AND NOT purchase_covered_by_count(NEW.ingredient_id, NEW.purchase_date, coalesce(NEW.created_at, now())) THEN
                    PERFORM append_purchase_stock_delta(NEW.ingredient_id, NEW.qty, NEW.unit::text, 1, NEW.entered_by_user_id, NEW.id);
                END IF;
                RETURN NEW;
            END IF;
            v_old_active := (OLD.deleted_at IS NULL AND OLD.usage_type::text = 'menu');
            v_new_active := (NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu');
            IF OLD.ingredient_id IS DISTINCT FROM NEW.ingredient_id
               OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
               OR OLD.qty IS DISTINCT FROM NEW.qty
               OR OLD.unit IS DISTINCT FROM NEW.unit
               OR OLD.usage_type IS DISTINCT FROM NEW.usage_type
               OR OLD.purchase_date IS DISTINCT FROM NEW.purchase_date THEN
                IF v_old_active AND NOT purchase_covered_by_count(OLD.ingredient_id, OLD.purchase_date, OLD.created_at) THEN
                    PERFORM append_purchase_stock_delta(OLD.ingredient_id, OLD.qty, OLD.unit::text, -1,
                        COALESCE(NEW.entered_by_user_id, OLD.entered_by_user_id), OLD.id);
                END IF;
                IF v_new_active AND NOT purchase_covered_by_count(NEW.ingredient_id, NEW.purchase_date, NEW.created_at) THEN
                    PERFORM append_purchase_stock_delta(NEW.ingredient_id, NEW.qty, NEW.unit::text, 1, NEW.entered_by_user_id, NEW.id);
                END IF;
            END IF;
            RETURN NEW;
        END $function$""")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_purchase_to_stock ON purchases")
    op.execute("""
        CREATE TRIGGER trg_sync_purchase_to_stock
        AFTER INSERT OR UPDATE OF ingredient_id, qty, unit, usage_type, deleted_at, purchase_date ON purchases
        FOR EACH ROW EXECUTE FUNCTION sync_purchase_to_stock()""")
    for fn in ("move_home_to_restaurant(integer, numeric, text, integer, text)",
               "purchase_stock_apply(purchases, integer, integer)",
               "purchase_covered_by_home_count(integer, date, timestamptz)",
               "append_home_stock_delta(integer, numeric, text, integer, integer, text)",
               "stock_qty_to_target(integer, numeric, text)",
               "stock_target_unit(integer)"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute("DROP TABLE IF EXISTS stock_transfers")
    op.execute("DROP TABLE IF EXISTS home_stock")

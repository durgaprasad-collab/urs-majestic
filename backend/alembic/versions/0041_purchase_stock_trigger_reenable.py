"""Re-enable purchase -> stock, additive and count-aware.

Found 2026-10-09: trg_sync_purchase_to_stock had been DISABLED in the
database since ~22 Sep 2026 (last purchase_auto row 21 Sep 22:49). Every
purchase since then (~130) never reached stock while sales deductions kept
running, so estimates slid to zero across the board.

This migration
  * records the live, already-correct additive functions in the repo
    (append_purchase_stock_delta(6 args) adds the purchase to the latest
    balance; the old 2-arg "replace with the purchase quantity" version was
    already neutered in the DB on 2026-09-10 -- 0025's copy is stale);
  * adds purchase_covered_by_count(): a purchase dated on or before the
    business evening of the item's latest physical count is already on the
    shelf in that count, so adding/removing it again would double count;
  * re-enables the trigger.

Idempotent: CREATE OR REPLACE + ENABLE TRIGGER. Balances are rebuilt once by
scripts/rebuild_stock_balances.py.

Revision ID: 0041
Revises: 0040
"""
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION purchase_covered_by_count(p_ingredient_id integer, p_purchase_date date)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            -- Physical counts are rows a person entered: anything that is not an
            -- automatic row. A count before 05:00 IST belongs to the previous
            -- evening (same rule as services/stock_count.count_day).
            SELECT EXISTS (
                SELECT 1 FROM ingredient_stock
                 WHERE ingredient_id = p_ingredient_id
                   AND COALESCE(note, '') NOT LIKE 'petpooja_usage:%'
                   AND COALESCE(note, '') NOT LIKE 'zomato_usage:%'
                   AND COALESCE(note, '') NOT LIKE 'swiggy_usage:%'
                   AND COALESCE(note, '') NOT LIKE 'purchase_auto:%'
                   AND COALESCE(note, '') NOT LIKE 'SYSTEM%'
                   AND ((counted_at AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date >= p_purchase_date
            )
        $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION append_purchase_stock_delta(
            p_ingredient_id integer, p_qty numeric, p_unit text, p_sign integer, p_user_id integer, p_purchase_id integer)
        RETURNS void LANGUAGE plpgsql AS $function$
        DECLARE v_target_unit text; v_pack_size_g numeric; v_previous_qty numeric := 0; v_previous_unit text; v_delta numeric;
        BEGIN
            SELECT COALESCE(v.unit::text, i.unit::text), i.pack_size_g INTO v_target_unit, v_pack_size_g
              FROM ingredients i LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
             WHERE i.id = p_ingredient_id;
            SELECT on_hand_qty, unit::text INTO v_previous_qty, v_previous_unit FROM ingredient_stock
             WHERE ingredient_id = p_ingredient_id ORDER BY counted_at DESC, id DESC LIMIT 1 FOR UPDATE;
            v_previous_qty := COALESCE(inventory_convert_qty(v_previous_qty, v_previous_unit, v_target_unit), 0);
            v_delta := CASE
                WHEN v_target_unit = 'pcs' AND v_pack_size_g IS NOT NULL AND p_unit = 'kg' THEN (p_qty * 1000 / v_pack_size_g) * p_sign
                WHEN v_target_unit = 'pcs' AND v_pack_size_g IS NOT NULL AND p_unit = 'g' THEN (p_qty / v_pack_size_g) * p_sign
                ELSE inventory_convert_qty(p_qty, p_unit, v_target_unit) * p_sign END;
            INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, GREATEST(0, v_previous_qty + v_delta), v_target_unit::unit_type, p_user_id,
                    'purchase_auto:' || p_purchase_id::text);
        END $function$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_purchase_to_stock()
        RETURNS trigger LANGUAGE plpgsql AS $function$
        DECLARE v_old_active boolean; v_new_active boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu'
                   AND NOT purchase_covered_by_count(NEW.ingredient_id, NEW.purchase_date) THEN
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
                -- Reverse the old version and apply the new one, each only if a
                -- later physical count doesn't already reflect it.
                IF v_old_active AND NOT purchase_covered_by_count(OLD.ingredient_id, OLD.purchase_date) THEN
                    PERFORM append_purchase_stock_delta(OLD.ingredient_id, OLD.qty, OLD.unit::text, -1,
                        COALESCE(NEW.entered_by_user_id, OLD.entered_by_user_id), OLD.id);
                END IF;
                IF v_new_active AND NOT purchase_covered_by_count(NEW.ingredient_id, NEW.purchase_date) THEN
                    PERFORM append_purchase_stock_delta(NEW.ingredient_id, NEW.qty, NEW.unit::text, 1, NEW.entered_by_user_id, NEW.id);
                END IF;
            END IF;
            RETURN NEW;
        END $function$
    """)
    # The trigger also needs to fire when only purchase_date changes.
    op.execute("DROP TRIGGER IF EXISTS trg_sync_purchase_to_stock ON purchases")
    op.execute("""
        CREATE TRIGGER trg_sync_purchase_to_stock
        AFTER INSERT OR UPDATE OF ingredient_id, qty, unit, usage_type, deleted_at, purchase_date ON purchases
        FOR EACH ROW EXECUTE FUNCTION sync_purchase_to_stock()
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE purchases DISABLE TRIGGER trg_sync_purchase_to_stock")

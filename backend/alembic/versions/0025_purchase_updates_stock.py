"""Apply every menu purchase change to the append-only stock balance.

A purchase is inventory arriving. Keeping purchases and Stock Log independent
made a newly entered purchase lose to an older physical count until somebody
counted the shelf again. This trigger appends a new balance in the same
transaction for inserts, edits, soft deletes, and restores.
"""

from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION inventory_convert_qty(
            p_qty numeric, p_from text, p_to text
        ) RETURNS numeric
        LANGUAGE sql IMMUTABLE AS $$
            SELECT CASE
                WHEN p_from = p_to THEN p_qty
                WHEN p_from = 'kg' AND p_to = 'g' THEN p_qty * 1000
                WHEN p_from = 'g' AND p_to = 'kg' THEN p_qty / 1000
                WHEN p_from = 'l' AND p_to = 'ml' THEN p_qty * 1000
                WHEN p_from = 'ml' AND p_to = 'l' THEN p_qty / 1000
                ELSE p_qty
            END
        $$
    """))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION append_purchase_stock_delta(
            p_ingredient_id integer,
            p_qty numeric,
            p_unit text,
            p_sign integer,
            p_user_id integer,
            p_purchase_id integer
        ) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            v_target_unit text;
            v_pack_size_g numeric;
            v_previous_qty numeric := 0;
            v_previous_unit text;
            v_delta numeric;
        BEGIN
            SELECT COALESCE(v.unit::text, i.unit::text), i.pack_size_g
              INTO v_target_unit, v_pack_size_g
              FROM ingredients i
              LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
             WHERE i.id = p_ingredient_id;

            SELECT on_hand_qty, unit::text
              INTO v_previous_qty, v_previous_unit
              FROM ingredient_stock
             WHERE ingredient_id = p_ingredient_id
             ORDER BY counted_at DESC, id DESC
             LIMIT 1
             FOR UPDATE;

            v_previous_qty := COALESCE(
                inventory_convert_qty(v_previous_qty, v_previous_unit, v_target_unit), 0
            );
            v_delta := CASE
                WHEN v_target_unit = 'pcs' AND v_pack_size_g IS NOT NULL AND p_unit = 'kg'
                    THEN (p_qty * 1000 / v_pack_size_g) * p_sign
                WHEN v_target_unit = 'pcs' AND v_pack_size_g IS NOT NULL AND p_unit = 'g'
                    THEN (p_qty / v_pack_size_g) * p_sign
                ELSE inventory_convert_qty(p_qty, p_unit, v_target_unit) * p_sign
            END;

            INSERT INTO ingredient_stock
                (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES
                (p_ingredient_id, GREATEST(0, v_previous_qty + v_delta),
                 v_target_unit::unit_type, p_user_id,
                 'purchase_auto:' || p_purchase_id::text);
        END
        $$
    """))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION sync_purchase_to_stock()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu' THEN
                    PERFORM append_purchase_stock_delta(
                        NEW.ingredient_id, NEW.qty, NEW.unit::text, 1,
                        NEW.entered_by_user_id, NEW.id
                    );
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.deleted_at IS NULL AND OLD.usage_type::text = 'menu' THEN
                PERFORM append_purchase_stock_delta(
                    OLD.ingredient_id, OLD.qty, OLD.unit::text, -1,
                    COALESCE(NEW.entered_by_user_id, OLD.entered_by_user_id), OLD.id
                );
            END IF;
            IF NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu' THEN
                PERFORM append_purchase_stock_delta(
                    NEW.ingredient_id, NEW.qty, NEW.unit::text, 1,
                    NEW.entered_by_user_id, NEW.id
                );
            END IF;
            RETURN NEW;
        END
        $$
    """))
    op.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_sync_purchase_to_stock ON purchases;
        CREATE TRIGGER trg_sync_purchase_to_stock
        AFTER INSERT OR UPDATE OF ingredient_id, qty, unit, usage_type, deleted_at
        ON purchases
        FOR EACH ROW EXECUTE FUNCTION sync_purchase_to_stock()
    """))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_purchase_to_stock ON purchases")
    op.execute("DROP FUNCTION IF EXISTS sync_purchase_to_stock()")
    op.execute("DROP FUNCTION IF EXISTS append_purchase_stock_delta(integer, numeric, text, integer, integer, integer)")
    op.execute("DROP FUNCTION IF EXISTS inventory_convert_qty(numeric, text, text)")

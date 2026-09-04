"""Apply every menu purchase change to the append-only stock balance.

A purchase is inventory arriving. The stock log should treat the latest
dated purchase as the new baseline for that ingredient, not the most recently
entered row. Sales deductions then continue to reduce that new baseline.
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
            p_user_id integer
        ) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            v_target_unit text;
            v_pack_size_g numeric;
            v_qty numeric;
            v_purchase_id integer;
            v_purchase_qty numeric;
            v_purchase_unit text;
        BEGIN
            SELECT p.id, p.qty, p.unit::text,
                   COALESCE(v.unit::text, i.unit::text), i.pack_size_g
              INTO v_purchase_id, v_purchase_qty, v_purchase_unit, v_target_unit, v_pack_size_g
              FROM purchases p
              JOIN ingredients i ON i.id = p.ingredient_id
              LEFT JOIN v_ingredient_reorder_forecast v ON v.ingredient_id = i.id
             WHERE p.deleted_at IS NULL
               AND p.usage_type::text = 'menu'
               AND p.ingredient_id = p_ingredient_id
            ORDER BY p.purchase_date DESC, GREATEST(p.created_at, p.purchase_date::timestamp) DESC, p.id DESC
             LIMIT 1;

            IF v_purchase_id IS NULL THEN
                RETURN;
            END IF;

            v_qty := CASE
                WHEN v_target_unit = 'pcs' AND v_pack_size_g IS NOT NULL AND v_purchase_unit = 'kg'
                    THEN (v_purchase_qty * 1000 / v_pack_size_g)
                WHEN v_target_unit = 'pcs' AND v_pack_size_g IS NOT NULL AND v_purchase_unit = 'g'
                    THEN (v_purchase_qty / v_pack_size_g)
                ELSE inventory_convert_qty(v_purchase_qty, v_purchase_unit, v_target_unit)
            END;

            INSERT INTO ingredient_stock
                (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES
                (p_ingredient_id, GREATEST(0, v_qty),
                 v_target_unit::unit_type, p_user_id,
                 'purchase_auto:' || v_purchase_id::text);
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
                        NEW.ingredient_id, NEW.entered_by_user_id
                    );
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.ingredient_id IS DISTINCT FROM NEW.ingredient_id THEN
                IF OLD.deleted_at IS NULL AND OLD.usage_type::text = 'menu' THEN
                    PERFORM append_purchase_stock_delta(
                        OLD.ingredient_id, COALESCE(NEW.entered_by_user_id, OLD.entered_by_user_id)
                    );
                END IF;
                IF NEW.deleted_at IS NULL AND NEW.usage_type::text = 'menu' THEN
                    PERFORM append_purchase_stock_delta(
                        NEW.ingredient_id, NEW.entered_by_user_id
                    );
                END IF;
            ELSIF OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
               OR OLD.qty IS DISTINCT FROM NEW.qty
               OR OLD.unit IS DISTINCT FROM NEW.unit
               OR OLD.usage_type IS DISTINCT FROM NEW.usage_type
               OR OLD.ingredient_id IS NOT NULL THEN
                PERFORM append_purchase_stock_delta(
                    COALESCE(NEW.ingredient_id, OLD.ingredient_id),
                    COALESCE(NEW.entered_by_user_id, OLD.entered_by_user_id)
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

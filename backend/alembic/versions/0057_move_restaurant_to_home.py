"""Restaurant -> home transfer (the reverse of 0056's move_home_to_restaurant).

Debits the kitchen balance (ingredient_stock, note 'transfer_out:<id>'), credits
home_stock (note 'transfer_in:<id>') and records the transfer with from_location
'restaurant'. Refuses to move more than the restaurant balance holds.

Revision ID: 0057
Revises: 0056
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION move_restaurant_to_home(
            p_ingredient_id integer, p_qty numeric, p_unit text, p_user_id integer, p_note text)
        RETURNS bigint LANGUAGE plpgsql AS $$
        DECLARE v_target text; v_q numeric; v_rest numeric; v_rest_unit text; v_home numeric; v_home_unit text; v_tid bigint;
        BEGIN
            PERFORM 1 FROM ingredients WHERE id = p_ingredient_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'Item not found'; END IF;
            IF p_qty IS NULL OR p_qty <= 0 THEN RAISE EXCEPTION 'Enter a quantity above zero'; END IF;
            v_target := stock_target_unit(p_ingredient_id);
            v_q := stock_qty_to_target(p_ingredient_id, p_qty, p_unit);
            IF v_q IS NULL OR v_q <= 0 THEN RAISE EXCEPTION 'Cannot convert % to %', p_unit, v_target; END IF;

            SELECT on_hand_qty, unit INTO v_rest, v_rest_unit FROM ingredient_stock
             WHERE ingredient_id = p_ingredient_id ORDER BY counted_at DESC, id DESC LIMIT 1;
            v_rest := COALESCE(inventory_convert_qty(v_rest, v_rest_unit, v_target), 0);
            IF v_q > v_rest + 0.0001 THEN
                RAISE EXCEPTION 'Only % % at the restaurant', round(v_rest, 3), v_target;
            END IF;

            INSERT INTO stock_transfers (ingredient_id, qty, unit, qty_entered, unit_entered, from_location, to_location, note, created_by)
            VALUES (p_ingredient_id, v_q, v_target, p_qty, p_unit, 'restaurant', 'home', NULLIF(trim(p_note), ''), p_user_id)
            RETURNING id INTO v_tid;

            INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, GREATEST(0, v_rest - v_q), v_target, p_user_id, 'transfer_out:' || v_tid::text);

            SELECT on_hand_qty, unit INTO v_home, v_home_unit FROM home_stock
             WHERE ingredient_id = p_ingredient_id ORDER BY counted_at DESC, id DESC LIMIT 1;
            v_home := COALESCE(inventory_convert_qty(v_home, v_home_unit, v_target), 0);
            INSERT INTO home_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, v_home + v_q, v_target, p_user_id, 'transfer_in:' || v_tid::text);
            RETURN v_tid;
        END $$""")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS move_restaurant_to_home(integer, numeric, text, integer, text)")

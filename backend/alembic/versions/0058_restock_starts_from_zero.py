"""A restock starts from the restocked quantity when the balance had gone negative.

A negative balance means the dishes sold used more than was recorded in (a missing
purchase, or a recipe portion that needs recalibrating). It stays visible as a
shortfall until the item is restocked; the restock then begins from zero plus what
arrived, instead of paying off the shortfall. Applies to purchases and to quantity
moved into the restaurant. Subtracting (a purchase deleted or edited down) is unchanged.

Revision ID: 0058
Revises: 0057
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

_PURCHASE_FN = """
CREATE OR REPLACE FUNCTION public.append_purchase_stock_delta(p_ingredient_id integer, p_qty numeric, p_unit text, p_sign integer, p_user_id integer, p_purchase_id integer)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
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
            %s
            INSERT INTO ingredient_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (p_ingredient_id, v_previous_qty + v_delta, v_target_unit::unit_type, p_user_id,
                    'purchase_auto:' || p_purchase_id::text);
        END $function$
"""
_FLOOR = "IF v_delta > 0 THEN v_previous_qty := GREATEST(v_previous_qty, 0); END IF;"


def upgrade() -> None:
    op.execute(_PURCHASE_FN % _FLOOR)
    # Home -> restaurant: the kitchen credit starts from zero when the kitchen was negative.
    op.execute("""
        DO $$
        DECLARE d text;
        BEGIN
            d := pg_get_functiondef('move_home_to_restaurant(integer, numeric, text, integer, text)'::regprocedure);
            d := replace(d, 'VALUES (p_ingredient_id, v_rest + v_q, v_target', 'VALUES (p_ingredient_id, GREATEST(v_rest, 0) + v_q, v_target');
            EXECUTE d;
        END $$""")


def downgrade() -> None:
    op.execute(_PURCHASE_FN % "")
    op.execute("""
        DO $$
        DECLARE d text;
        BEGIN
            d := pg_get_functiondef('move_home_to_restaurant(integer, numeric, text, integer, text)'::regprocedure);
            d := replace(d, 'GREATEST(v_rest, 0) + v_q', 'v_rest + v_q');
            EXECUTE d;
        END $$""")

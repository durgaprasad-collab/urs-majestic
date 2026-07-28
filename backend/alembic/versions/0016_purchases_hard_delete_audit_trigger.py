"""Audit any hard DELETE on purchases, and refuse TRUNCATE.

The admin panel soft-deletes (an UPDATE that sets deleted_at), so nothing in
the application issues a DELETE against this table. Anything that does has
bypassed the panel -- the Supabase SQL editor, psql, a maintenance script.
Ten purchase ids were found missing with no audit record on 2026-07-28, which
is what prompted this. Owner ruling the same day: allow hard deletes, but
force the whole row into cost_base_repair_log first.

The trigger is BEFORE DELETE ... FOR EACH ROW, so it fires whoever issues the
statement and from wherever. Row triggers do not fire on TRUNCATE, so a second
statement-level trigger refuses TRUNCATE outright rather than let one command
erase the table silently.

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# actor_user_id is filled from the app.user_id session setting when present.
# The application does not currently set it, and neither does the Supabase SQL
# editor, so it lands NULL. That NULL is itself informative: it means the
# delete did not announce itself. db_role, application_name and client_addr are
# captured into the reason text, which is what distinguishes a dashboard delete
# (application_name = mgmt-api) from a backend one.
_AUDIT_FN = """
CREATE OR REPLACE FUNCTION public.log_purchase_hard_delete()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  ing_name text;
  actor    integer;
BEGIN
  SELECT name INTO ing_name FROM public.ingredients WHERE id = OLD.ingredient_id;

  BEGIN
    actor := NULLIF(current_setting('app.user_id', true), '')::integer;
  EXCEPTION WHEN others THEN
    actor := NULL;
  END;

  INSERT INTO public.cost_base_repair_log
      (batch, target_table, target_id, field, old_value, new_value,
       reason, actor_user_id)
  VALUES
      ('hard_delete_trigger',
       'purchases',
       OLD.id,
       'ROW HARD DELETED',
       to_jsonb(OLD)::text,
       'DELETED',
       format(
         'Hard DELETE captured by trigger. ingredient=%s; qty=%s %s; total_price=%s; purchase_date=%s; usage_type=%s; entered_by_user_id=%s; db_role=%s; application_name=%s; client_addr=%s',
         COALESCE(ing_name, '(unknown id ' || OLD.ingredient_id || ')'),
         OLD.qty, OLD.unit, OLD.total_price, OLD.purchase_date,
         OLD.usage_type, OLD.entered_by_user_id,
         current_user,
         COALESCE(NULLIF(current_setting('application_name', true), ''), '(none)'),
         COALESCE(inet_client_addr()::text, '(local)')
       ),
       actor);

  RETURN OLD;
END
$fn$;

COMMENT ON FUNCTION public.log_purchase_hard_delete() IS
  'Copies the entire purchase row into cost_base_repair_log before any hard DELETE, whoever issues it and from wherever. The application soft-deletes (an UPDATE), so this fires only for deletes that bypass the admin panel.';

DROP TRIGGER IF EXISTS trg_purchases_hard_delete_audit ON public.purchases;
CREATE TRIGGER trg_purchases_hard_delete_audit
  BEFORE DELETE ON public.purchases
  FOR EACH ROW
  EXECUTE FUNCTION public.log_purchase_hard_delete();
"""

_TRUNCATE_GUARD = """
CREATE OR REPLACE FUNCTION public.block_purchase_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  RAISE EXCEPTION
    'TRUNCATE on purchases is blocked: it would erase financial rows with no audit trail. Use DELETE (audited) or the admin panel soft delete.';
END
$fn$;

DROP TRIGGER IF EXISTS trg_purchases_no_truncate ON public.purchases;
CREATE TRIGGER trg_purchases_no_truncate
  BEFORE TRUNCATE ON public.purchases
  FOR EACH STATEMENT
  EXECUTE FUNCTION public.block_purchase_truncate();
"""


def upgrade() -> None:
    op.execute(sa.text(_AUDIT_FN))
    op.execute(sa.text(_TRUNCATE_GUARD))


def downgrade() -> None:
    raise NotImplementedError(
        "0016 is forward-only. Dropping these triggers would restore silent, "
        "unrecorded deletion of financial rows. Drop them by hand if genuinely "
        "needed, and write down why."
    )

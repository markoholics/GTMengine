"""credit_ledger writer. Idempotent per (client, provider, unit_type, ref): a retried job never double counts.

The budget guard that reads this table sits in front of every Apollo call (Phase 4).
"""
from core.db import conn

def record(client_id, provider, unit_type, units, ref_type, ref_id, cost_estimate=None, cost_basis=None):
    """Returns True if a row was written, False if this ref was already recorded."""
    with conn() as c:
        row = c.execute(
            """insert into credit_ledger (client_id, provider, unit_type, units, cost_estimate, cost_basis, ref_type, ref_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (client_id, provider, unit_type, ref_type, ref_id) where ref_id is not null do nothing
               returning id""",
            (client_id, provider, unit_type, units, cost_estimate, cost_basis, ref_type, str(ref_id)),
        ).fetchone()
        return row is not None

def spent_this_month(client_id, provider, unit_type="credits"):
    with conn() as c:
        return c.execute(
            """select coalesce(sum(units),0) from credit_ledger
               where client_id=%s and provider=%s and unit_type=%s
                 and spent_at >= date_trunc('month', now())""",
            (client_id, provider, unit_type)).fetchone()[0]

import os, psycopg
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

DSN = os.environ.get("DATABASE_URL")

@contextmanager
def conn():
    if not DSN:
        raise RuntimeError("DATABASE_URL not set. Copy .env.example to .env.")
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c

def client_id_for(c, name=None):
    """Resolve a client slug (default: GTM_CLIENT env var, then 'byosync') to its uuid."""
    name = name or os.environ.get("GTM_CLIENT", "byosync")
    row = c.execute("select id from clients where name = %s", (name,)).fetchone()
    if not row:
        raise RuntimeError(f"client '{name}' not found. Run: python -m scripts.seed_client config/clients/{name}.yaml")
    return row[0]

def upsert_company(c, domain, name=None, country="IN", meta=None, client_id=None):
    client_id = client_id or client_id_for(c)
    with c.cursor() as cur:
        cur.execute(
            """insert into company (client_id, domain, name, country, meta)
               values (%s, %s, %s, %s, coalesce(%s::jsonb, '{}'::jsonb))
               on conflict (client_id, domain) do update
                 set last_seen = now(),
                     name = coalesce(company.name, excluded.name)
               returning id""",
            (client_id, domain.lower().strip(), name, country, meta),
        )
        return cur.fetchone()[0]

def insert_signal(c, company_id, source, signal_type, strength, observed_at,
                  payload, evidence_url, dedupe_key):
    """Idempotent. Re-running a collector never double counts. The signal inherits the company's client."""
    with c.cursor() as cur:
        cur.execute(
            """insert into signal_event
               (client_id, company_id, source, signal_type, strength, observed_at,
                payload, evidence_url, dedupe_key)
               select co.client_id, co.id, %s,%s,%s,%s,%s::jsonb,%s,%s
               from company co where co.id = %s
               on conflict (dedupe_key) do nothing
               returning id""",
            (source, signal_type, strength, observed_at, payload, evidence_url, dedupe_key, company_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

def is_suppressed(c, email=None, domain=None):
    with c.cursor() as cur:
        cur.execute(
            """select 1 from suppression
               where (email is not null and lower(email) = lower(%s))
                  or (domain is not null and lower(domain) = lower(%s))
               limit 1""",
            (email or "", domain or ""),
        )
        return cur.fetchone() is not None

"""Apply supabase/migrations/*.sql in order. Idempotent: applied files are recorded and skipped.

    python -m scripts.migrate                 apply everything pending
    python -m scripts.migrate --status        list applied and pending
    python -m scripts.migrate --mark-applied 0002   record files up to and including 0002 without running them
                                                    (for a live database that already holds the baseline)
"""
import argparse, hashlib, os, sys
from pathlib import Path
import psycopg

DIR = Path(__file__).parent.parent / "supabase" / "migrations"

def files():
    return sorted(DIR.glob("*.sql"))

def checksum(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def ensure_table(c):
    c.execute("""create table if not exists schema_migrations (
                   version text primary key, checksum text not null,
                   applied_at timestamptz not null default now())""")

def applied(c):
    return dict(c.execute("select version, checksum from schema_migrations").fetchall())

def run(dsn, mark_up_to=None):
    done_now = []
    with psycopg.connect(dsn, autocommit=True) as c:
        ensure_table(c)
        done = applied(c)
        for p in files():
            v = p.stem
            if v in done:
                if done[v] != checksum(p):
                    raise SystemExit(f"{v}: file changed after it was applied. Write a new migration instead.")
                continue
            if mark_up_to and v.split("_")[0] <= mark_up_to:
                c.execute("insert into schema_migrations (version, checksum) values (%s,%s)", (v, checksum(p)))
                done_now.append(f"{v} (marked)")
                continue
            with c.transaction():
                c.execute(p.read_text())
                c.execute("insert into schema_migrations (version, checksum) values (%s,%s)", (v, checksum(p)))
            done_now.append(v)
    return done_now

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--mark-applied", metavar="PREFIX")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL") or sys.exit("DATABASE_URL not set")
    if a.status:
        with psycopg.connect(dsn, autocommit=True) as c:
            ensure_table(c)
            d = applied(c)
        for p in files():
            print(("applied  " if p.stem in d else "pending  ") + p.stem)
    else:
        for v in run(dsn, a.mark_applied):
            print("applied", v)

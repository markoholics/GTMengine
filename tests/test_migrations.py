import psycopg, pytest
from scripts.migrate import run, files
from tests.conftest import apply_files

def q(dsn, sql, *a):
    with psycopg.connect(dsn, autocommit=True) as c:
        return c.execute(sql, a).fetchall()

def test_applies_to_empty_database_and_is_idempotent(newdb):
    dsn = newdb()
    first = run(dsn)
    assert len(first) == len(files())
    assert run(dsn) == []

def test_edited_migration_is_refused(newdb, tmp_path, monkeypatch):
    import scripts.migrate as m
    d = tmp_path / "migrations"; d.mkdir()
    (d / "0001_x.sql").write_text("create table x(i int);")
    monkeypatch.setattr(m, "DIR", d)
    dsn = newdb(); m.run(dsn)
    (d / "0001_x.sql").write_text("create table x(i int, j int);")
    with pytest.raises(SystemExit):
        m.run(dsn)

def test_mark_applied_skips_baseline(newdb):
    dsn = newdb()
    apply_files(dsn, ["0001_baseline.sql", "0002_page_watch.sql"])
    out = run(dsn, mark_up_to="0002")
    assert out[0].endswith("(marked)") and out[1].endswith("(marked)")
    assert len(out) == len(files())

LEGACY = """
insert into client_lens (client, weights, half_life_days, min_score) values ('byosync','{"source":"x"}',30,0.45);
insert into company (domain, name) values ('a.ai','A'), ('b.ai','B');
insert into person (company_id, full_name, email, source) select id, 'P '||domain, 'p@'||domain, 'yc' from company;
insert into signal_event (company_id, source, signal_type, strength, observed_at, dedupe_key)
  select id, 'news', 'funding_round', 0.8, now() - interval '3 days', 'k-'||domain from company;
insert into send (client, person_id, company_id, arm) select 'byosync', p.id, p.company_id, 'signal' from person p;
insert into outcome (send_id, outcome_type) select id, 'delivered' from send;
insert into suppression (email, reason) values ('x@y.com','bounce');
insert into page_snapshot (company_id, page_kind, url, content_hash) select id, 'home', 'https://a', 'h' from company limit 1;
"""

def test_upgrade_of_populated_legacy_database_loses_nothing(newdb):
    dsn = newdb()
    apply_files(dsn, ["0001_baseline.sql", "0002_page_watch.sql"])
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(LEGACY)
    tables = ["company", "person", "signal_event", "send", "outcome", "suppression", "page_snapshot"]
    before = {t: q(dsn, f"select count(*) from {t}")[0][0] for t in tables}
    run(dsn, mark_up_to="0002")
    run(dsn)
    for t in tables:
        assert q(dsn, f"select count(*) from {t}")[0][0] == before[t], t
    for t in ["company", "person", "signal_event", "send", "outcome", "page_snapshot"]:
        assert q(dsn, f"select count(*) from {t} where client_id is null")[0][0] == 0, f"{t} has orphans"
    assert q(dsn, "select count(*) from suppression where client_id is null")[0][0] == 1   # stays global
    assert q(dsn, "select count(*) from signal_event where created_at is null")[0][0] == 0
    assert q(dsn, "select count(*) from clients")[0][0] == 1
    assert q(dsn, "select count(*) from signals")[0][0] == 2
    assert q(dsn, "select count(*) from contacts")[0][0] == 2
    assert q(dsn, "select count(*) from events")[0][0] == 2

def test_every_client_scoped_table_has_client_id_and_created_at(migrated):
    for t in ["company", "person", "signal_event", "send", "outcome", "suppression", "page_snapshot",
              "run_log", "dead_letter", "credit_ledger", "clients"]:
        cols = {r[0] for r in q(migrated, "select column_name from information_schema.columns where table_name=%s", t)}
        assert "created_at" in cols, t
        if t != "clients":
            assert "client_id" in cols, t

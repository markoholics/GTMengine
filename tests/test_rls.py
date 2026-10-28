import json, uuid
import psycopg, pytest

@pytest.fixture
def two_clients(migrated):
    with psycopg.connect(migrated, autocommit=True) as c:
        a = c.execute("insert into clients (name) values ('alpha') returning id").fetchone()[0]
        b = c.execute("insert into clients (name) values ('beta') returning id").fetchone()[0]
        for cid, dom in ((a, "a.ai"), (b, "b.ai")):
            co = c.execute("insert into company (client_id, domain) values (%s,%s) returning id", (cid, dom)).fetchone()[0]
            c.execute("""insert into signal_event (client_id, company_id, source, signal_type, strength, observed_at, dedupe_key)
                         values (%s,%s,'t','funding_round',0.5,now(),%s)""", (cid, co, f"k-{dom}"))
            c.execute("insert into credit_ledger (client_id, provider, unit_type, units, ref_type, ref_id) values (%s,'apollo','credits',1,'t','1')", (cid,))
            c.execute("insert into run_log (client_id, layer) values (%s,'harvest')", (cid,))
        c.execute("insert into suppression (email, reason) values ('global@x.com','manual')")
        c.execute("insert into suppression (client_id, email, reason) values (%s,'b-only@x.com','manual')", (b,))
    return migrated, a, b

def as_client(dsn, cid):
    c = psycopg.connect(dsn, autocommit=True)
    c.execute("set role authenticated")
    c.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"client_id": str(cid)}),))
    return c

@pytest.mark.parametrize("table", ["company", "signal_event", "credit_ledger", "run_log", "signals"])
def test_tenant_sees_only_own_rows(two_clients, table):
    dsn, a, b = two_clients
    with as_client(dsn, a) as c:
        ids = {r[0] for r in c.execute(f"select client_id from {table}").fetchall()}
    assert ids == {a}

def test_tenant_cannot_read_other_client_row(two_clients):
    dsn, a, b = two_clients
    with as_client(dsn, a) as c:
        assert [r[0] for r in c.execute("select id from clients").fetchall()] == [a]

def test_tenant_cannot_write_into_other_tenant(two_clients):
    dsn, a, b = two_clients
    with as_client(dsn, a) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into credit_ledger (client_id, provider, unit_type, units) values (%s,'apollo','credits',1)", (b,))

def test_tenant_cannot_update_other_tenant_rows(two_clients):
    dsn, a, b = two_clients
    with as_client(dsn, a) as c:
        n = c.execute("update company set name='hijack' where client_id=%s", (b,)).rowcount
    assert n == 0

def test_no_claim_sees_nothing(two_clients):
    dsn, a, b = two_clients
    c = psycopg.connect(dsn, autocommit=True)
    c.execute("set role authenticated")
    assert c.execute("select count(*) from company").fetchone()[0] == 0

def test_global_suppression_visible_but_other_tenant_private_rows_are_not(two_clients):
    dsn, a, b = two_clients
    with as_client(dsn, a) as c:
        emails = {r[0] for r in c.execute("select email from suppression").fetchall()}
    assert emails == {"global@x.com"}
    with as_client(dsn, b) as c:
        emails = {r[0] for r in c.execute("select email from suppression").fetchall()}
    assert emails == {"global@x.com", "b-only@x.com"}

def test_tenant_cannot_write_global_suppression(two_clients):
    dsn, a, b = two_clients
    with as_client(dsn, a) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into suppression (email, reason) values ('z@x.com','manual')")

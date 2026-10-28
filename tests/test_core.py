import json
import psycopg, pytest, requests
from core import credits, db
from core.config import load_client, ConfigError
from core.http import Client, HttpError, RateLimiter
from core.runlog import run_logged
from scripts.seed_client import seed

def mk_client(name="alpha"):
    with db.conn() as c:
        return c.execute("insert into clients (name) values (%s) returning id", (name,)).fetchone()[0]

# ---- tenancy helpers
def test_same_domain_two_clients_are_separate_companies(dbready):
    a, b = mk_client("alpha"), mk_client("beta")
    with db.conn() as c:
        ia = db.upsert_company(c, "x.ai", "X", client_id=a)
        ib = db.upsert_company(c, "x.ai", "X", client_id=b)
        assert ia != ib
        assert db.upsert_company(c, "X.ai ", client_id=a) == ia

def test_signal_insert_is_idempotent_and_inherits_client(dbready):
    a = mk_client()
    with db.conn() as c:
        co = db.upsert_company(c, "x.ai", client_id=a)
        args = (c, co, "news", "funding_round", 0.8, "2026-10-01T00:00:00Z", "{}", "https://e", "k1")
        assert db.insert_signal(*args) is not None
        assert db.insert_signal(*args) is None
        assert c.execute("select client_id from signal_event").fetchone()[0] == a

# ---- credit ledger
def test_credit_record_is_idempotent(dbready):
    a = mk_client()
    assert credits.record(a, "apollo", "credits", 1, "contact", "p1") is True
    assert credits.record(a, "apollo", "credits", 1, "contact", "p1") is False
    assert credits.record(a, "apollo", "credits", 1, "contact", "p2") is True
    assert credits.spent_this_month(a, "apollo") == 2

def test_credit_rejects_unknown_provider(dbready):
    a = mk_client()
    with pytest.raises(psycopg.errors.CheckViolation):
        credits.record(a, "openai", "credits", 1, "contact", "p1")

# ---- run_log
def last(status_col="status"):
    with db.conn() as c:
        return c.execute("select status, error, detail from run_log order by id desc limit 1").fetchone()

def test_runlog_success_failure_and_empty(dbready):
    with run_logged("harvest") as h:
        h.detail["rows"] = 3
    assert last()[0] == "succeeded" and last()[2]["rows"] == 3
    with pytest.raises(RuntimeError):
        with run_logged("harvest"):
            raise RuntimeError("boom")
    assert last()[0] == "failed" and "boom" in last()[1]
    with run_logged("harvest") as h:
        h.mark_empty("actor returned 0 items")
    assert last()[0] == "empty" and last()[2]["empty_reason"]

# ---- http
class Resp:
    def __init__(self, code, headers=None, text=""):
        self.status_code, self.headers, self.text = code, headers or {}, text

class FakeSession:
    def __init__(self, seq): self.seq, self.calls = list(seq), 0
    def request(self, *a, **k):
        self.calls += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception): raise x
        return x

def client(seq, **kw):
    sleeps = []
    s = FakeSession(seq)
    return Client("t", per_second=1000, session=s, sleep=sleeps.append, **kw), s, sleeps

def test_retries_then_succeeds_with_backoff():
    c, s, sleeps = client([Resp(503), requests.ConnectionError("x"), Resp(200)])
    assert c.get("http://x").status_code == 200
    assert s.calls == 3 and len(sleeps) == 2 and sleeps[1] > 0

def test_retry_after_is_honoured():
    c, s, sleeps = client([Resp(429, {"Retry-After": "7"}), Resp(200)])
    c.get("http://x")
    assert sleeps == [7]

def test_client_error_is_not_retried():
    c, s, _ = client([Resp(404, text="no")])
    with pytest.raises(HttpError): c.get("http://x")
    assert s.calls == 1

def test_gives_up_loudly():
    c, s, _ = client([Resp(500)] * 3, max_attempts=3)
    with pytest.raises(HttpError, match="failed after 3 attempts"): c.get("http://x")
    assert s.calls == 3

def test_rate_limiter_spaces_calls():
    t = [0.0]; slept = []
    def sleep(d): slept.append(d); t[0] += d
    rl = RateLimiter(2, clock=lambda: t[0], sleep=sleep)   # 2 per second = 0.5s apart
    for _ in range(3): rl.wait()
    assert slept == [0.5, 0.5]

# ---- config
def test_byosync_config_valid_and_safe_by_default():
    cfg = load_client("config/clients/byosync.yaml")
    assert cfg["status"] == "paused" and cfg["monthly_credit_budget"] == 0 and cfg["daily_send_cap"] == 0

def test_bad_config_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("name: Bad Name\nstatus: live\n")
    with pytest.raises(ConfigError):
        load_client(p)

def test_seed_is_idempotent_and_second_client_is_config_only(dbready):
    cfg = load_client("config/clients/byosync.yaml")
    assert seed(cfg) == seed(cfg)
    second = {**cfg, "name": "acme", "display_name": "Acme"}
    assert seed(second) != seed(cfg)
    with db.conn() as c:
        assert c.execute("select count(*) from clients").fetchone()[0] == 2

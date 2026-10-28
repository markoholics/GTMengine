"""run_log writer. Wrap every layer's job in `with run_logged(...)` so a failure is recorded, not swallowed.

An empty harvest is a distinct status ('empty'), because a silent zero is the failure mode that hides a broken source.
"""
import json, uuid
from contextlib import contextmanager
from core.db import conn
from core.log import log

class RunHandle:
    def __init__(self, run_id):
        self.run_id = run_id
        self.detail = {}
        self.empty = False

    def mark_empty(self, reason):
        self.empty = True
        self.detail["empty_reason"] = reason

@contextmanager
def run_logged(layer, client_id=None, run_id=None):
    h = RunHandle(run_id or str(uuid.uuid4()))
    with conn() as c:
        c.execute("insert into run_log (client_id, layer, run_id) values (%s,%s,%s)", (client_id, layer, h.run_id))
    log("run_start", layer=layer, run_id=h.run_id, client_id=str(client_id) if client_id else None)
    try:
        yield h
    except Exception as e:
        _finish(h, "failed", f"{type(e).__name__}: {e}")
        log("run_failed", level="error", layer=layer, run_id=h.run_id, error=str(e))
        raise
    else:
        status = "empty" if h.empty else "succeeded"
        _finish(h, status, None)
        log("run_end", layer=layer, run_id=h.run_id, status=status, **h.detail)

def _finish(h, status, error):
    with conn() as c:
        c.execute("""update run_log set status=%s, error=%s, detail=%s::jsonb, finished_at=now()
                     where run_id=%s""", (status, error, json.dumps(h.detail, default=str), h.run_id))

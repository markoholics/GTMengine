"""Structured JSON logs to stdout. One line per event, so Actions, Make and any log drain can parse them."""
import json, sys, datetime as dt

def log(event, level="info", **fields):
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "level": level, "event": event, **fields}
    print(json.dumps(rec, default=str), file=sys.stdout, flush=True)

"""Create or update a client from a config file. Idempotent. Never changes status to 'active' unless the file says so.

    python -m scripts.seed_client config/clients/byosync.yaml
"""
import json, sys
from core.config import load_client
from core.db import conn

def seed(cfg):
    with conn() as c:
        row = c.execute(
            """insert into clients (name, display_name, status, monthly_credit_budget, daily_credit_budget,
                                    daily_send_cap, judge_threshold, half_life_days, icp_config, signal_weights)
               values (%(name)s, %(display_name)s, %(status)s, %(monthly_credit_budget)s, %(daily_credit_budget)s,
                       %(daily_send_cap)s, %(judge_threshold)s, %(half_life_days)s, %(icp)s::jsonb, %(w)s::jsonb)
               on conflict (name) do update set
                   display_name = excluded.display_name, status = excluded.status,
                   monthly_credit_budget = excluded.monthly_credit_budget,
                   daily_credit_budget = excluded.daily_credit_budget, daily_send_cap = excluded.daily_send_cap,
                   judge_threshold = excluded.judge_threshold, half_life_days = excluded.half_life_days,
                   icp_config = excluded.icp_config, signal_weights = excluded.signal_weights, updated_at = now()
               returning id""",
            {**{k: cfg.get(k) for k in ("name", "display_name", "status", "monthly_credit_budget",
                                        "daily_credit_budget", "daily_send_cap", "judge_threshold")},
             "half_life_days": cfg.get("half_life_days", 30),
             "icp": json.dumps(cfg["icp_config"]), "w": json.dumps(cfg["signal_weights"])},
        ).fetchone()
    return row[0]

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cfg = load_client(sys.argv[1])
    print(f"client {cfg['name']} seeded: {seed(cfg)}")

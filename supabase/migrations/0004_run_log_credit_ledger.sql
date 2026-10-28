-- Observability and spend. Every layer writes run_log; every paid call writes credit_ledger.

create table if not exists run_log (
    id          bigserial primary key,
    client_id   uuid references clients(id),               -- null for platform wide jobs
    layer       text not null check (layer in
                  ('harvest','normalise','judge','reach','draft','send','wire','attribution','learning','system')),
    run_id      uuid not null unique default gen_random_uuid(),
    status      text not null default 'running' check (status in ('running','succeeded','failed','empty','skipped')),
    error       text,
    detail      jsonb not null default '{}'::jsonb,        -- counts, ids, anything structured
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists idx_run_log_layer on run_log (layer, started_at desc);
create index if not exists idx_run_log_client on run_log (client_id, started_at desc);

-- Failed webhook payloads and failed hops. Replayed by scripts/replay_dead_letter.py (Phase 6).
create table if not exists dead_letter (
    id              bigserial primary key,
    client_id       uuid references clients(id),
    hop             text not null,
    idempotency_key text not null,
    payload         jsonb not null,
    error           text,
    attempts        integer not null default 0,
    status          text not null default 'pending' check (status in ('pending','replayed','abandoned')),
    first_failed_at timestamptz not null default now(),
    last_attempt_at timestamptz,
    replayed_at     timestamptz,
    created_at      timestamptz not null default now(),
    unique (hop, idempotency_key)
);

create table if not exists credit_ledger (
    id            bigserial primary key,
    client_id     uuid not null references clients(id),
    provider      text not null check (provider in ('apollo','apify','anthropic')),
    unit_type     text not null,                            -- credits | tokens_in | tokens_out | cache_read_tokens | compute_units
    units         numeric not null check (units >= 0),
    cost_estimate numeric(12,4),                            -- USD, estimate. null when the price is unconfirmed
    cost_basis    text,                                     -- where the price came from (docs/COSTS.md section)
    ref_type      text,                                     -- signal | contact | draft | run
    ref_id        text,
    spent_at      timestamptz not null default now(),
    created_at    timestamptz not null default now()
);
-- A retried job must not double count.
create unique index if not exists idx_credit_ledger_ref
    on credit_ledger (client_id, provider, unit_type, ref_type, ref_id) where ref_id is not null;
create index if not exists idx_credit_ledger_month on credit_ledger (client_id, provider, spent_at);

-- Dashboard views.
create or replace view run_log_dashboard with (security_invoker = true) as
    select distinct on (client_id, layer)
           client_id, layer, status, error, started_at, finished_at, detail
    from run_log order by client_id, layer, started_at desc;

create or replace view credit_usage_month with (security_invoker = true) as
    select client_id, provider, unit_type,
           date_trunc('month', spent_at) as month,
           sum(units) as units, sum(cost_estimate) as cost_estimate
    from credit_ledger group by 1, 2, 3, 4;

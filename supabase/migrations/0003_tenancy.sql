-- Multi tenancy. Additive only: no table is dropped or recreated.
-- client_id is nullable here because the legacy collectors do not set it yet.
-- It becomes NOT NULL in a later migration, once every writer passes it.

create table if not exists clients (
    id                    uuid primary key default gen_random_uuid(),
    name                  text unique not null,          -- slug, e.g. 'byosync'
    display_name          text,
    icp_config            jsonb not null default '{}'::jsonb,
    signal_weights        jsonb not null default '{}'::jsonb,
    half_life_days        integer not null default 30,
    judge_threshold       integer not null default 70 check (judge_threshold between 0 and 100),
    monthly_credit_budget integer not null default 0 check (monthly_credit_budget >= 0), -- Apollo credits
    daily_credit_budget   integer check (daily_credit_budget >= 0),                      -- Apollo credits, null = no daily cap
    daily_send_cap        integer not null default 0 check (daily_send_cap >= 0),
    status                text not null default 'paused' check (status in ('active', 'paused', 'archived')),
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

-- Carry existing client lenses forward. client_lens stays in place (legacy, read by core/score.py).
insert into clients (name, signal_weights, half_life_days)
select client, weights, half_life_days from client_lens
on conflict (name) do nothing;

-- Tenant column on every existing table.
alter table company       add column if not exists client_id uuid references clients(id);
alter table person        add column if not exists client_id uuid references clients(id);
alter table signal_event  add column if not exists client_id uuid references clients(id);
alter table send          add column if not exists client_id uuid references clients(id);
alter table outcome       add column if not exists client_id uuid references clients(id);
alter table suppression   add column if not exists client_id uuid references clients(id);  -- null = global
alter table page_snapshot add column if not exists client_id uuid references clients(id);

-- created_at on every table that lacked one. Backfilled from the nearest real timestamp.
alter table company       add column if not exists created_at timestamptz;
alter table signal_event  add column if not exists created_at timestamptz;
alter table outcome       add column if not exists created_at timestamptz;
alter table page_snapshot add column if not exists created_at timestamptz;
update company       set created_at = first_seen  where created_at is null;
update signal_event  set created_at = observed_at where created_at is null;
update outcome       set created_at = occurred_at where created_at is null;
update page_snapshot set created_at = captured_at where created_at is null;
alter table company       alter column created_at set default now(), alter column created_at set not null;
alter table signal_event  alter column created_at set default now(), alter column created_at set not null;
alter table outcome       alter column created_at set default now(), alter column created_at set not null;
alter table page_snapshot alter column created_at set default now(), alter column created_at set not null;

-- Backfill: everything that exists today belongs to the ByoSync lens.
do $$
declare cid uuid;
begin
    select id into cid from clients where name = 'byosync';
    if cid is not null then
        update company       set client_id = cid where client_id is null;
        update person        set client_id = cid where client_id is null;
        update signal_event  set client_id = cid where client_id is null;
        update outcome       set client_id = cid where client_id is null;
        update page_snapshot set client_id = cid where client_id is null;
    end if;
end $$;
-- send.client is free text; resolve it to the client row. Unknown names stay null and are reported by the tests.
update send s set client_id = c.id from clients c where c.name = s.client and s.client_id is null;
-- suppression rows stay global (client_id null) on purpose.

-- Company uniqueness becomes per client. NULLS NOT DISTINCT keeps legacy null rows unique by domain.
alter table company drop constraint if exists company_domain_key;
alter table company add constraint company_client_domain_key unique nulls not distinct (client_id, domain);

-- Fields the Reach layer will fill. Never fabricate: when unknown store null and say why.
alter table person add column if not exists apollo_person_id text;
alter table person add column if not exists linkedin_url text;
alter table person add column if not exists source_signal_id bigint references signal_event(id);
alter table person add column if not exists enriched_at timestamptz;
alter table person add column if not exists email_status_reason text;
create unique index if not exists idx_person_apollo on person (client_id, apollo_person_id) where apollo_person_id is not null;

alter table suppression add column if not exists source text;   -- brevo | apollo | manual | reply

alter table signal_event add column if not exists signal_date date;
update signal_event set signal_date = observed_at::date where signal_date is null;

create index if not exists idx_company_client on company (client_id);
create index if not exists idx_person_client on person (client_id);
create index if not exists idx_signal_client_time on signal_event (client_id, observed_at desc);
create index if not exists idx_send_client on send (client_id, arm, sent_at);
create index if not exists idx_outcome_client on outcome (client_id, occurred_at);

-- Names used by the target architecture. Views, so the existing code keeps working.
-- security_invoker makes the underlying table's RLS apply to the caller.
create or replace view signals with (security_invoker = true) as
    select se.id, se.client_id, se.company_id, c.domain as company_domain, se.signal_type,
           se.signal_date, se.evidence_url, se.payload as normalised, se.dedupe_key,
           se.source, se.strength, se.observed_at, se.created_at
    from signal_event se left join company c on c.id = se.company_id;

create or replace view contacts with (security_invoker = true) as
    select p.id, p.client_id, p.company_id, c.domain as company_domain, p.apollo_person_id,
           p.full_name as name, p.role_title as title, p.email, p.email_status,
           p.email_status_reason, p.linkedin_url, p.source_signal_id, p.enriched_at,
           p.source, p.consent_basis, p.created_at
    from person p left join company c on c.id = p.company_id;

create or replace view events with (security_invoker = true) as
    select id, client_id, send_id, outcome_type as event_type, occurred_at, notes, source, created_at
    from outcome;

create or replace view suppression_list with (security_invoker = true) as
    select id, client_id, email, domain, reason, source, created_at as added_at from suppression;

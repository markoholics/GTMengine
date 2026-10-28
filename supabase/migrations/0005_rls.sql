-- Row level security keyed on client_id.
-- The caller's client comes from the JWT claim "client_id" (request.jwt.claims is what Supabase sets).
-- The service role bypasses RLS, so server code must still filter by client_id explicitly.

do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;       -- exists already on Supabase
    end if;
end $$;

create or replace function auth_client_id() returns uuid
language sql stable as $$
    select nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'client_id', '')::uuid
$$;

do $$
declare t text;
begin
    foreach t in array array['company','person','signal_event','send','outcome','page_snapshot',
                             'run_log','dead_letter','credit_ledger'] loop
        execute format('alter table %I enable row level security', t);
        execute format('drop policy if exists tenant_isolation on %I', t);
        execute format($p$create policy tenant_isolation on %I for all to authenticated
                          using (client_id = auth_client_id()) with check (client_id = auth_client_id())$p$, t);
        execute format('grant select, insert, update on %I to authenticated', t);
    end loop;
end $$;

-- clients: a tenant reads only its own row and cannot write it.
alter table clients enable row level security;
drop policy if exists tenant_isolation on clients;
create policy tenant_isolation on clients for select to authenticated using (id = auth_client_id());
grant select on clients to authenticated;

-- suppression: global rows (client_id null) are visible to every tenant, writes only to own rows.
alter table suppression enable row level security;
drop policy if exists tenant_read on suppression;
drop policy if exists tenant_write on suppression;
create policy tenant_read  on suppression for select to authenticated
    using (client_id is null or client_id = auth_client_id());
create policy tenant_write on suppression for insert to authenticated
    with check (client_id = auth_client_id());
grant select, insert on suppression to authenticated;

grant usage, select on all sequences in schema public to authenticated;
grant select on signals, contacts, events, suppression_list, run_log_dashboard, credit_usage_month to authenticated;

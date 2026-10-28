# Phase 0 audit: Signal-Engine to multi-client GTM engine

Audited: `markoholics/Signal-Engine` at commit `28080ad` (shallow clone, read only), 1,219 lines of Python and TypeScript, 4 GitHub Actions workflows, one SQL schema file.

## 0. Read this first: what I could and could not inspect

| Item | Status |
|---|---|
| Repo code, SQL, workflows, docs | Read in full |
| Live Supabase schema | **Not inspected.** No connector or credentials in this session. I audited `sql/schema.sql` and assume the live database matches it. Please paste `pg_dump --schema-only` output (no data) or give me a read-only connection string before Phase 1 |
| Apify, Apollo, Make, GA4, Search Console docs | **Not yet verified.** Nothing in this audit relies on them. Verification happens at the start of each phase that needs them |
| Where this work lands | The session is attached to `markoholics/GTMengine` (empty, no commits). Signal-Engine is attached read only. See open question 1 |

## 1. What exists

A deliberately small 90 day experiment for one client (ByoSync): "do emails that cite a real recent signal earn more positive replies than the same email with no signal?"

```
collectors/   5 free public sources (ATS boards, crt.sh, GitHub metadata, RSS, launch feeds) + page_watch
core/         db.py (psycopg), fit.py (structural ICP 0..1), score.py (decayed weighted score + combos)
outreach/     queue.py (random signal/control arm), draft.py (templates + copy guardrails), ledger.py (z test readout)
scripts/      discover_targets, discover_contacts, probe_sources
supabase/     brevo-webhook edge function (writes outcome + suppression)
sql/          schema.sql, seed_lens.sql, page_watch.sql   (idempotent create-if-not-exists, no migration tool)
.github/      collect (daily), pagewatch (weekly), discover (weekly), probe (manual)
config/       signals.yaml (taxonomy, ByoSync weights, combos), targets.yaml (~50 companies)
docs/         runbook.md, warmup-plan.md
```

Tables in `sql/schema.sql`: `company`, `person`, `signal_event`, `client_lens`, `send`, `outcome`, `suppression`, plus `page_snapshot`.

## 2. What is reusable

| Asset | Reuse |
|---|---|
| `signal_event` with unique `dedupe_key` and `on conflict do nothing` | Idempotency pattern is already right. Becomes the base of the `signals` layer |
| `core/score.py` (decay, combos, logistic squash) | Keep as a **free pre-filter in front of Claude**. It cuts Judge tokens and doubles as a baseline the Judge must beat in the eval set |
| `core/fit.py` structural fit | Keep, but move ICP tokens and bands out of code into `clients.icp_config` |
| Five collectors | Keep as the "free source" adapters behind the new adapter interface. Apify adapters are added beside them, not instead of them |
| `outreach/draft.py` copy guardrails (`BANNED`, locked brand lines) | Carry over verbatim as a deterministic post-check on every Claude draft. Non negotiable per `CLAUDE.md` |
| `outreach/queue.py` 90 day one touch rule and suppression check | Keep and move behind the send function |
| `outreach/ledger.py` two proportion z test and the HOLD gate | Keep. Becomes part of the weekly learning report |
| `approved_by` on `send` and the two gate approval rule (Rahman every draft, Varun on positioning) | Maps directly onto `message_drafts.status/reviewer` |
| `suppression` table with partial unique indexes | Keep. Add `client_id` and `source` |
| Brevo webhook edge function | Keep as the fallback event path (see conflict C3) |
| `docs/warmup-plan.md` | Still valid for any mailbox, including those connected to Apollo |
| Legal boundaries in `CLAUDE.md` (no LinkedIn scraping, company level signals only, no recon of weaknesses) | Adopt as engine wide rules and reference from `docs/COMPLIANCE.md` |

## 3. Conflicts with the brief

| # | Conflict | Evidence | Proposed resolution |
|---|---|---|---|
| C1 | **Apollo people search is blocked on the current plan** | README: "Apollo organisation lookup (people search is blocked on the current plan)" | The Reach layer as briefed (find decision maker by persona via Apollo) cannot run on this plan. Need your decision: upgrade the plan, or Reach starts from the existing published-founder route and uses Apollo only for verification and sequences. I will not build against an endpoint the plan blocks |
| C2 | **Sending path: repo says Brevo, brief says Apollo sequences** | README, `warmup-plan.md`, `SEND_FROM`, `brevo-webhook`. The brief's assumption holds only if you want it to | Follow the brief: Apollo sequences are the send path, Brevo stays dormant. The 4 week warmup applies to the mailbox connected in Apollo. The Brevo webhook stays deployed but unused. Please confirm |
| C3 | **Randomised control arm vs "every draft cites a signal"** | `CLAUDE.md`: never remove the randomised assignment; control gets generic copy by design | Keep the experiment. Order becomes Judge gate first (only `act` accounts), then 30 percent randomised to control **after** the gate, receiving generic copy. The "cite a specific signal" rule applies to the signal arm only. Control sends still spend Apollo credits on accounts that passed the gate. Needs your confirmation because it deliberately relaxes one line of the brief |
| C4 | **Template drafts vs Claude drafts** | `draft.py` is templates; README: "LLM drafting: templates first" | Claude drafts, templates remain the control arm copy and the fallback if the API is down |
| C5 | **`continue-on-error: true` on every collector step** | `collect.yml` | Contradicts "fail loudly at that layer". Replace with per step `run_log` rows and an alert on failure or on an empty harvest |
| C6 | **No tenant column** | `company`, `person`, `signal_event`, `outcome`, `suppression` have no client; `send` and `client_lens` use a free text `client` | Add `client_id` everywhere. See schema mapping below |
| C7 | **Schema management** | `sql/schema.sql` run by `make db` with `create table if not exists` | Replace with versioned migrations under `supabase/migrations/`. The existing file becomes migration `0001_baseline.sql`, unchanged |
| C8 | **Name clash: `outcome` vs `outcomes`** | Existing `outcome` is the per send event ledger (open, reply, bounce). The brief's `events` is that, and the brief's `outcomes` is GA4 attribution | Keep `outcome` as is, expose a view `events`, and create the new GA4 table as `outcomes` per the brief. Flagging because the near-identical names will confuse future you |
| C9 | **Brevo webhook weaknesses** | `index.ts`: secret compared with `!==` (not constant time), no idempotency (Brevo retries create duplicate `outcome` rows), `spam` mapped to `unsubscribe`, unmatched `send` ids silently ignored | Fix if it stays in use; otherwise mark deprecated. Low priority under C2 |
| C10 | **Ledger gate** | `ledger.py` reads `sent_at` on `send` | Keep `send` as the sent record and link `message_drafts.id` to it, so existing reports keep working |

## 4. Schema mapping (existing to briefed)

No drops, no recreates. Every change is an additive migration.

| Briefed table | Existing | Plan |
|---|---|---|
| `clients` | `client_lens` (client text, weights, half_life_days, min_score) | Add `id`, `icp_config`, `monthly_credit_budget`, `daily_send_cap`, `status`; `weights` is exposed as `signal_weights`, `min_score` as the Judge threshold. View `clients` |
| `signal_sources` | none | New |
| `signals_raw` | none (collectors write normalised rows directly) | New. Collectors gain a raw write step |
| `signals` | `signal_event` | Extend in place (add `client_id`, `signal_date`, `normalised`), expose view `signals`. `dedupe_key` stays unique and gains the business key (domain, type, ISO week) |
| `signal_decisions` | none | New, with the credit gate constraint (section 6) |
| `contacts` | `person` | Extend (`apollo_person_id`, `linkedin_url`, `source_signal_id`, `enriched_at`, `client_id`), view `contacts` |
| `message_drafts` | partly `send.subject/body/approved_by` | New table. `send` stays as the sent record |
| `events` | `outcome` | View over `outcome`, plus `draft_id` and `client_id` columns |
| `outcomes` | none | New (GA4) |
| `credit_ledger` | none | New |
| `suppression_list` | `suppression` | Extend (`client_id` nullable where null means global, `source`), view `suppression_list` |
| `run_log` | none | New, plus `dead_letter` |
| (tenant core) `company` | `company` | Add `client_id`; uniqueness becomes `(client_id, domain)`. Costs duplicate rows per client, buys simple RLS. Alternative: keep `company` global and RLS only the client scoped tables. Recommend the former for simplicity |

RLS: enabled on every client scoped table with a policy on `client_id = (auth.jwt() ->> 'client_id')::uuid`. Service role bypasses RLS, so server code must still filter by `client_id` explicitly. I will add a test that enforces this.

## 5. Gap list

| Layer | Gap | Phase |
|---|---|---|
| All | No migrations, no tests, no CI, no `run_log`, no alerting, no structured logs | 1 |
| All | No retry/backoff, no rate limit handling | 1 (shared HTTP client) |
| Harvest | No Apify at all. No funding adapter beyond RSS, no hiring-surge window logic, no raw payload store | 2 |
| Judge | Nothing. No rubric files, no eval set, no token ledger, no DB level gate | 3 |
| Reach | No Apollo code. Plan blocks people search (C1). No budget guard | 4 |
| Draft | Templates only. No review UI, no diff capture, no feedback into drafting | 5 |
| Send | No send function. The "send" today is a human pasting into Brevo. Needs the fail closed function | 5 |
| Wire | No Make, no webhook contracts, no dead letter | 6 |
| Close the loop | No UTM generation, no GA4, no Search Console | 7 |
| Learning | `ledger.py` per signal reply rates exist; no weekly job, no rubric PR proposals | 7 |
| Tenancy | No `client_id`, no RLS, no onboarding script | 1 and 8 |
| Compliance | Unsubscribe and address rules are documented but not enforced in code; no `COMPLIANCE.md`; no retention rules | 5 |
| Metric | No cost per meeting booked | 1 (ledger) and 7 (report) |

Existing contact route, now read: `discover_contacts.py` takes founder emails only where the yc-oss mirror of YC public profiles publishes them, never guesses addresses, and records `consent_basis = legitimate_interest_b2b`. That is a sound default, but it only covers YC listed companies, so it will not supply contacts for most Indian AI SaaS targets. This reinforces C1.

## 6. Approval queue: simplest option

Recommendation: **a Supabase backed single page** (static HTML + supabase-js, Supabase Auth, RLS) listing `message_drafts` where `status='drafted'`, with Approve / Edit / Reject (reason required). Edits write `edit_diff`. Reasons:

- No new infrastructure; the database already exists.
- The diff and the reviewer are captured in the same transaction as the status change.
- A Make driven Slack/email digest is added in Phase 6 as the *notification* only (a link to the queue), never as the approval surface, so approval cannot be lost in a scenario failure.

The send function and the database both enforce it: a trigger refuses `status='sent'` unless a prior `approved` row exists with a reviewer, and the send function re-checks and fails closed.

## 7. Credit gate enforcement (design)

Defence in depth, not only prompts:

1. `signal_decisions` has a check constraint: `verdict in ('act','watch','discard')` and `score between 0 and 100`.
2. A Postgres function `reach_eligible(signal_id)` returns true only if the latest decision is `act` and `score >` the client threshold.
3. `contacts.source_signal_id` is `not null` and enforced by trigger to reference an eligible signal.
4. The Apollo client wrapper takes a `signal_id`, calls `reach_eligible`, then the budget guard, then the API. No eligible signal means no HTTP call. Tested.

## 8. Phase plan

Each phase ends with: results shown to you, assumptions, what was not verified, biggest remaining risk. Nothing spends credits or sends without asking.

| Phase | Deliverables | Acceptance checks |
|---|---|---|
| 1 | `supabase/migrations/` (0001 baseline from existing SQL, then additive), `clients` config files + seed script, `run_log`, `credit_ledger`, `dead_letter`, RLS, shared HTTP client (retry, backoff, rate limit), `.env.example`, CI running tests | Migrations apply to an empty DB and to a copy of the existing schema; RLS test shows client A cannot read client B; ByoSync backfilled with no row loss |
| 2 | Adapter interface; 3 Apify adapters (funding, hiring surge, product launch) after verifying actors; `signals_raw`; normalise and dedupe | Fixture run is idempotent (second run inserts zero); live run **only after you approve** the credit spend. If no suitable actor can be confirmed I stop and ask |
| 3 | Rubric `rubrics/byosync/v1.md`, JSON schema validation, batching, prompt caching, token cap, `credit_ledger` logging, 30 signal eval set + scoring script, DB gate | Test: signal without `act` cannot trigger an Apollo call; rubric change scored against eval set before activation |
| 4 | Apollo reach with budget guard, suppression and DNC first, paused sequences. `docs/COSTS.md` from verified docs | Test: exhausted budget halts spend; C1 resolved first |
| 5 | Claude drafts citing the signal, approval page, diff capture, fail closed send, unsubscribe + address footer, bounce circuit breaker | Test: unapproved draft cannot send |
| 6 | Make blueprints in `make/`, `docs/CONTRACTS.md`, dead letter + replay script, error routes, 10x cost notes | Replay script re-delivers a dead letter exactly once |
| 7 | UTM generation, GA4 Data API pull, Search Console pull, `docs/GA4_SETUP.md`, weekly learning report as a PR, cost per meeting | Fixture driven; no live Google calls without your keys |
| 8 | Second client dry run (config file + seed only, under 30 minutes), hardening, README with Mermaid, runbook per failure mode, `COMPLIANCE.md` | Full fixture end to end dry run, zero live sends |

## 9. Open questions (blocking Phase 1)

1. **Which repo do I build in?** The session can push only to `markoholics/GTMengine` (empty). You named `Signal-Engine`, which I can read but not push to. Note that `GTMsignalengine` and `brand-signal-engine` also exist under your account; I have not looked at them. My recommendation is to seed `GTMengine` with the Signal-Engine tree as migration 0001 baseline and build there. The alternative is for me to request push access to Signal-Engine and build in place, which keeps its Actions secrets and history.
2. **Live Supabase schema:** paste a schema-only dump or give a read-only connection string. Without it the migrations are written against `sql/schema.sql` and untested against reality.
3. **C1:** Apollo plan blocks people search. Upgrade, or accept the founder-lookup route for now?
4. **C2:** Confirm Apollo sequences as the send path and Brevo dormant.
5. **C3:** Confirm control arm is kept, drawn from gated accounts, with generic copy.
6. **Scheduler:** GitHub Actions stays as the scheduler under Make triggers (Make calls `workflow_dispatch`)? Or does Make become the scheduler and Actions the executor only? I propose the latter.

## 10. Assumptions, unverified items, biggest risk

Assumptions: live Supabase matches `sql/schema.sql`; the repo has no other branches of interest (only the default branch was cloned, depth 1); ByoSync lens in `signals.yaml` is the starting `icp_config` and weights.

Not verified: every external API (Apify actors and pricing, Apollo endpoints, rate limits and credit costs, Make limits, GA4 Data API quotas); whether the secrets named in the workflows exist.

Biggest risk: **C1**. If Apollo cannot find decision makers on the current plan, the Reach layer has no supplier of contacts and the gate protects credits that cannot be spent anyway. Resolve that before investing in Phases 4 to 6.

-- Apply ONLY to the dedicated Tynato monitoring Supabase project.
-- Browser roles intentionally have no policies for these tables; trusted server-side
-- Postgres credentials perform asynchronous mirror writes. Do not expose credentials.

create table if not exists public.monitoring_incidents (
  id uuid primary key,
  incident_key text not null,
  service text not null,
  category text not null,
  severity text not null check (severity in ('critical','high','medium','low')),
  status text not null check (status in ('open','acknowledged','resolved')),
  summary text not null,
  details jsonb not null default '{}'::jsonb,
  customer_id text,
  team_id text,
  first_detected_at timestamptz not null,
  last_observed_at timestamptz not null,
  last_success_at timestamptz,
  acknowledged_by text,
  acknowledged_at timestamptz,
  resolved_at timestamptz,
  occurrence_count integer not null default 1,
  updated_at timestamptz not null default now()
);
create index if not exists ix_monitoring_incidents_status_updated on public.monitoring_incidents(status, updated_at desc);
create index if not exists ix_monitoring_incidents_customer on public.monitoring_incidents(customer_id, first_detected_at desc);

create table if not exists public.monitoring_health_checks (
  id uuid primary key,
  check_name text not null,
  status text not null check (status in ('ok','failed')),
  checked_at timestamptz not null,
  latency_ms double precision,
  error_details jsonb
);
create index if not exists ix_monitoring_health_checks_name_time on public.monitoring_health_checks(check_name, checked_at desc);

create table if not exists public.monitoring_events (
  event_id text primary key,
  service text not null,
  category text not null,
  status text not null check (status in ('success','failure')),
  severity text not null check (severity in ('critical','high','medium','low')),
  customer_id text,
  team_id text,
  occurred_at timestamptz not null,
  summary text not null,
  details jsonb not null default '{}'::jsonb,
  received_at timestamptz not null,
  incident_id uuid references public.monitoring_incidents(id) on delete set null
);
create index if not exists ix_monitoring_events_service_time on public.monitoring_events(service, occurred_at desc);
create index if not exists ix_monitoring_events_customer_time on public.monitoring_events(customer_id, occurred_at desc);

create table if not exists public.monitoring_alert_deliveries (
  id uuid primary key,
  incident_id uuid not null references public.monitoring_incidents(id) on delete restrict,
  kind text not null check (kind in ('critical','recovery')),
  recipient text not null,
  status text not null check (status in ('pending','sent')),
  attempts integer not null default 0,
  provider_message_id text,
  last_error text,
  created_at timestamptz not null,
  sent_at timestamptz,
  updated_at timestamptz not null
);
create index if not exists ix_monitoring_alert_deliveries_incident on public.monitoring_alert_deliveries(incident_id, created_at desc);

create table if not exists public.monitoring_worker_heartbeats (
  worker_name text primary key,
  status text not null,
  last_seen_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null
);

alter table public.monitoring_incidents enable row level security;
alter table public.monitoring_health_checks enable row level security;
alter table public.monitoring_events enable row level security;
alter table public.monitoring_alert_deliveries enable row level security;
alter table public.monitoring_worker_heartbeats enable row level security;

revoke all on public.monitoring_incidents from anon, authenticated;
revoke all on public.monitoring_health_checks from anon, authenticated;
revoke all on public.monitoring_events from anon, authenticated;
revoke all on public.monitoring_alert_deliveries from anon, authenticated;
revoke all on public.monitoring_worker_heartbeats from anon, authenticated;

"use client";

import { useCallback, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { getSupabase } from "@/lib/supabase";

type Service = { service: string; status: string; checked_at: string; latency_ms?: number | null; error?: unknown };
type Incident = { id: string; service: string; category: string; severity: string; status: string; summary: string; first_detected_at: string; last_observed_at: string; customer_id?: string | null };
type Overview = { generated_at: string; active_incidents: number; open_incidents: number; failed_checks_24h: number; pending_alerts: number; services: Service[]; workers: { worker_name: string; status: string; last_seen_at: string }[] };
const API = process.env.NEXT_PUBLIC_MONITORING_API_URL?.replace(/\/$/, "");

export default function Home() {
  const supabase = getSupabase();
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [busy, setBusy] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [dataError, setDataError] = useState("");
  const [stale, setStale] = useState(false);

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, current) => setSession(current));
    return () => data.subscription.unsubscribe();
  }, [supabase]);

  const load = useCallback(async () => {
    if (!supabase || !session || !API) return;
    setDataError("");
    try {
      const { data: refreshed } = await supabase.auth.getSession();
      const token = refreshed.session?.access_token;
      if (!token) throw new Error("Your session has expired. Sign in again.");
      const headers = { Authorization: `Bearer ${token}` };
      const [ovRes, incRes] = await Promise.all([
        fetch(`${API}/v1/overview`, { headers, cache: "no-store" }),
        fetch(`${API}/v1/incidents?limit=100`, { headers, cache: "no-store" }),
      ]);
      if (!ovRes.ok || !incRes.ok) {
        const code = !ovRes.ok ? ovRes.status : incRes.status;
        throw new Error(code === 403 ? "Your account does not have a monitoring role. Ask the monitoring Super Admin to authorize it." : `Monitoring API returned HTTP ${code}.`);
      }
      const ov = await ovRes.json() as Overview;
      const inc = await incRes.json() as { items: Incident[] };
      setOverview(ov); setIncidents(inc.items); setStale(false);
    } catch (error) {
      setDataError(error instanceof Error ? error.message : "Unable to load monitoring data.");
      setStale(true);
    }
  }, [supabase, session]);

  useEffect(() => { void load(); }, [load]);

  async function signIn(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setLoginError("");
    if (!supabase) { setLoginError("Configure the public Supabase URL and publishable key in Vercel environment variables."); setBusy(false); return; }
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setLoginError(error.message);
    setBusy(false);
  }

  async function signOut() { await supabase?.auth.signOut(); setSession(null); setOverview(null); setIncidents([]); }

  async function changeIncident(id: string, action: "acknowledge" | "resolve") {
    if (!supabase || !API) return;
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (!token) return;
    const response = await fetch(`${API}/v1/incidents/${id}/${action}`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) { setDataError(`Unable to ${action} incident (HTTP ${response.status}).`); return; }
    await load();
  }

  if (!session) return <main className="login-shell"><section className="login-card"><div className="brand-mark">T</div><p className="eyebrow">OPERATIONS PLATFORM</p><h1>Tynato Monitoring</h1><p className="muted">Sign in with an authorized monitoring account.</p><form onSubmit={signIn} className="login-form"><label>Email<input autoComplete="username" type="email" required value={email} onChange={e => setEmail(e.target.value)} /></label><label>Password<input autoComplete="current-password" type="password" required value={password} onChange={e => setPassword(e.target.value)} /></label><button disabled={busy} type="submit">{busy ? "Signing in…" : "Sign in"}</button>{loginError && <p className="error-text">{loginError}</p>}</form><p className="tiny muted">Accounts must be provisioned by a monitoring administrator. Self-service registration is disabled.</p></section></main>;

  return <main className="dashboard"><header className="topbar"><div><p className="eyebrow">TYnATO / OPERATIONS</p><h1>Monitoring overview</h1></div><div className="header-actions"><span className="user-email">{session.user.email}</span><button className="secondary" onClick={() => void load()}>Refresh</button><button className="secondary" onClick={() => void signOut()}>Sign out</button></div></header>
    {dataError && <div className="notice error-notice"><strong>{stale ? "Live data unavailable" : "Request failed"}</strong><span>{dataError} {overview ? "The values below may be stale." : "No monitoring snapshot is available yet."}</span></div>}
    <section className="metrics"><Metric title="Active incidents" value={overview?.active_incidents ?? "—"} tone={(overview?.active_incidents ?? 0) > 0 ? "danger" : "good"} /><Metric title="Open incidents" value={overview?.open_incidents ?? "—"} /><Metric title="Failed checks · 24h" value={overview?.failed_checks_24h ?? "—"} tone={(overview?.failed_checks_24h ?? 0) > 0 ? "danger" : "good"} /><Metric title="Pending email alerts" value={overview?.pending_alerts ?? "—"} tone={(overview?.pending_alerts ?? 0) > 0 ? "warn" : "good"} /></section>
    <section className="panel"><div className="panel-heading"><div><h2>Service health</h2><p className="muted">Latest persisted health checks</p></div><span className={stale ? "status-pill warn" : "status-pill good"}>{stale ? "STALE / UNAVAILABLE" : "MONITORING API"}</span></div>{overview?.services.length ? <div className="table-wrap"><table><thead><tr><th>Service</th><th>Status</th><th>Latency</th><th>Last check (UTC)</th></tr></thead><tbody>{overview.services.map(s => <tr key={s.service}><td className="strong">{s.service}</td><td><span className={`status-pill ${s.status === "ok" ? "good" : "danger"}`}>{s.status}</span></td><td>{s.latency_ms == null ? "—" : `${Math.round(s.latency_ms)} ms`}</td><td>{new Date(s.checked_at).toLocaleString(undefined, { timeZone: "UTC" })}</td></tr>)}</tbody></table></div> : <Empty text="No health checks recorded. Configure CRM_API_HEALTH_URL and start the worker." />}</section>
    <section className="panel"><div className="panel-heading"><div><h2>Incidents</h2><p className="muted">Open, acknowledged and recently resolved incidents</p></div><span className="count-pill">{incidents.length} records</span></div>{incidents.length ? <div className="table-wrap"><table><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>First detected</th><th>Actions</th></tr></thead><tbody>{incidents.map(i => <tr key={i.id}><td><div className="strong">{i.summary}</div><div className="tiny muted">{i.service} · {i.category}{i.customer_id ? ` · ${i.customer_id}` : ""}</div></td><td><span className={`status-pill ${i.severity === "critical" ? "danger" : "warn"}`}>{i.severity}</span></td><td>{i.status}</td><td>{new Date(i.first_detected_at).toLocaleString(undefined, { timeZone: "UTC" })}</td><td className="actions">{i.status === "open" && <button className="small secondary" onClick={() => void changeIncident(i.id, "acknowledge")}>Acknowledge</button>}{i.status !== "resolved" && <button className="small secondary" onClick={() => void changeIncident(i.id, "resolve")}>Resolve</button>}</td></tr>)}</tbody></table></div> : <Empty text="No incidents recorded." />}</section>
    <footer className="footer">Snapshot generated: {overview ? new Date(overview.generated_at).toLocaleString(undefined, { timeZone: "UTC" }) : "not available"}. The monitor worker runs independently of CRM availability.</footer>
  </main>;
}

function Metric({ title, value, tone = "" }: { title: string; value: string | number; tone?: string }) { return <article className="metric-card"><p>{title}</p><strong className={tone}>{value}</strong></article>; }
function Empty({ text }: { text: string }) { return <div className="empty-state">{text}</div>; }

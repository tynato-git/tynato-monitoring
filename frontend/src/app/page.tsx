"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import ComponentCards from "./ComponentCards";
import { getSupabase } from "@/lib/supabase";
import styles from "./page.module.css";
import { useComponents, type MonitoredComponent } from "./useComponents";

type Service = {
  service: string;
  status: string;
  checked_at: string;
  latency_ms: number | null;
  error?: unknown;
};

type Worker = {
  worker_name: string;
  status: string;
  last_seen_at: string;
  details?: unknown;
};

type Overview = {
  generated_at: string;
  active_incidents: number;
  open_incidents: number;
  failed_checks_24h: number;
  pending_alerts: number;
  services: Service[];
  workers: Worker[];
};

type Incident = {
  id: string;
  service: string;
  category: string;
  severity: string;
  status: "open" | "acknowledged" | "resolved" | string;
  summary: string;
  first_detected_at: string;
  last_observed_at?: string | null;
  resolved_at?: string | null;
  customer_id?: string | null;
  team_id?: string | null;
  occurrence_count?: number;
  details?: Record<string, unknown> | null;
};

type IncidentList = { items?: Incident[] };
type IncidentFilter = "active" | "open" | "acknowledged" | "resolved" | "all";
type Action = "acknowledge" | "resolve";
type CoverageTone = "good" | "danger" | "warning" | "neutral";

const RAW_API = process.env.NEXT_PUBLIC_MONITORING_API_URL ?? "";
const API = RAW_API.trim().replace(/\/+$/, "");
const REFRESH_MS = 30_000;
// Worker default cycle is 30s; three missed cycles means the result is stale.
const STALE_AFTER_MS = 90_000;
const DASH = "—";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function timestamp(value?: string | null): string {
  if (!value) return DASH;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return DASH;
  return `${date.toLocaleString(undefined, {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })} UTC`;
}

function ageMs(value?: string | null): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : Math.max(0, Date.now() - parsed);
}

function errorText(value: unknown): string {
  if (typeof value === "string") return value.slice(0, 240);
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of ["message", "detail", "error", "error_code", "exception_type"]) {
      if (typeof obj[key] === "string" && obj[key]) return String(obj[key]).slice(0, 240);
    }
  }
  return "";
}

function probeStatus(service?: Service): { label: string; tone: CoverageTone; evidence: string } {
  if (!service) return { label: "NOT CONFIGURED", tone: "neutral", evidence: "No health-check result exists for this component." };
  const raw = service.status.toLowerCase();
  if (["failed", "failure", "error", "down", "unhealthy", "critical", "unavailable", "timeout", "timed_out"].includes(raw)) {
    return { label: "ERROR", tone: "danger", evidence: errorText(service.error) || `Latest probe status: ${service.status}.` };
  }
  const age = ageMs(service.checked_at);
  if (age === null || age > STALE_AFTER_MS) {
    return { label: "STALE", tone: "warning", evidence: `Last check: ${timestamp(service.checked_at)}. A stale result is not proof of current health.` };
  }
  if (["ok", "healthy", "success", "up"].includes(raw)) {
    return { label: "HEALTHY", tone: "good", evidence: `Latest successful probe: ${timestamp(service.checked_at)}.` };
  }
  if (["warning", "degraded", "pending"].includes(raw)) {
    return { label: "DEGRADED", tone: "warning", evidence: `Latest probe status: ${service.status}.` };
  }
  return { label: "UNKNOWN", tone: "neutral", evidence: `Unrecognized probe status: ${service.status}.` };
}


function incidentDetailEntries(details?: Record<string, unknown> | null): [string, string][] {
  if (!details || typeof details !== "object") return [];
  // Render only an allowlist of diagnostic fields. Never dump arbitrary payloads.
  const allow = ["error_code", "exception_type", "check_name", "source", "source_file", "function", "line", "http_status", "status_code", "provider"];
  return allow.flatMap((key) => {
    const value = details[key];
    if ((typeof value === "string" || typeof value === "number") && String(value).length) {
      return [[key.replaceAll("_", " "), String(value).slice(0, 180)] as [string, string]];
    }
    return [];
  });
}

function apiMessage(status: number, body: unknown): string {
  if (status === 401) return "Your monitoring session is missing or expired. Sign in again.";
  if (status === 403) return "This account does not have the required monitoring role. Contact the monitoring administrator.";
  if (body && typeof body === "object" && "detail" in body && typeof (body as { detail?: unknown }).detail === "string") {
    return (body as { detail: string }).detail.slice(0, 240);
  }
  return `Monitoring API returned HTTP ${status}.`;
}

async function getJson<T>(url: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    let body: unknown = null;
    try { body = await response.json(); } catch { /* not JSON */ }
    throw new ApiError(response.status, apiMessage(response.status, body));
  }
  return (await response.json()) as T;
}

function uniqueById(items: Incident[]): Incident[] {
  // Collapse only identical database records (same id).
  const byId = new Map<string, Incident>();
  for (const item of items) byId.set(item.id, item);
  const rank = (status: string) => (status === "open" ? 0 : status === "acknowledged" ? 1 : 2);
  return [...byId.values()].sort((a, b) => {
    const diff = rank(a.status) - rank(b.status);
    if (diff !== 0) return diff;
    return new Date(b.last_observed_at ?? b.first_detected_at).getTime()
      - new Date(a.last_observed_at ?? a.first_detected_at).getTime();
  });
}

export default function Home() {
  const supabase = useMemo(() => getSupabase(), []);

  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [signOutBusy, setSignOutBusy] = useState(false);

  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [activeIncidents, setActiveIncidents] = useState<Incident[]>([]);
  const [incidentFilter, setIncidentFilter] = useState<IncidentFilter>("active");
  const [dataError, setDataError] = useState("");
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<{ id: string; action: Action } | null>(null);

  const requestRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef(false);
  const mountedRef = useRef(true);

  // Stable token accessor. Shared by the coverage data and ComponentCards.
  const getToken = useCallback(async (): Promise<string | undefined> => {
    if (!supabase) return undefined;
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token;
  }, [supabase]);

  // Hooks must run unconditionally, before any early return below.
  const components = useComponents(API, getToken);

  useEffect(() => {
    mountedRef.current = true;
    if (!supabase) {
      setAuthReady(true);
      return () => { mountedRef.current = false; };
    }
    let alive = true;
    void supabase.auth.getSession()
      .then(({ data }) => { if (alive) setSession(data.session); })
      .catch(() => { if (alive) setLoginError("Could not restore your session. Please sign in again."); })
      .finally(() => { if (alive) setAuthReady(true); });

    const { data } = supabase.auth.onAuthStateChange((_event, current) => {
      if (alive) {
        setSession(current);
        setAuthReady(true);
      }
    });
    return () => {
      alive = false;
      mountedRef.current = false;
      data.subscription.unsubscribe();
    };
  }, [supabase]);

  const load = useCallback(async () => {
    if (!supabase || !session) return;
    if (!API.startsWith("https://")) {
      setDataError("Monitoring API URL is not configured as a valid HTTPS URL.");
      setInitialLoading(false);
      return;
    }
    // Never queue overlapping requests; a late response could overwrite newer data.
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    const controller = new AbortController();
    requestRef.current = controller;
    setRefreshing(true);

    try {
      const { data, error } = await supabase.auth.getSession();
      if (error) throw new Error("Unable to verify your monitoring session.");
      const token = data.session?.access_token;
      if (!token) throw new ApiError(401, "Your monitoring session is missing or expired. Sign in again.");

      const listIncidents = (status?: string) => {
        const params = new URLSearchParams({ limit: "100" });
        if (status) params.set("status", status);
        return getJson<IncidentList>(`${API}/v1/incidents?${params.toString()}`, token, controller.signal);
      };

      const viewStatus = incidentFilter === "all" || incidentFilter === "active" ? undefined : incidentFilter;
      const [overviewRes, openRes, ackRes, viewRes] = await Promise.all([
        getJson<Overview>(`${API}/v1/overview`, token, controller.signal),
        listIncidents("open"),
        listIncidents("acknowledged"),
        incidentFilter === "active" ? Promise.resolve(null) : listIncidents(viewStatus),
      ]);

      if (controller.signal.aborted || !mountedRef.current) return;

      const active = uniqueById([...(openRes.items ?? []), ...(ackRes.items ?? [])]);
      const view = incidentFilter === "active" ? active : uniqueById(viewRes?.items ?? []);

      setOverview(overviewRes);
      setActiveIncidents(active);
      setIncidents(view);
      setLastUpdated(new Date().toISOString());
      setDataError("");
    } catch (err) {
      if (controller.signal.aborted || !mountedRef.current) return;
      setDataError(err instanceof Error ? err.message : "Unable to load monitoring data.");
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        inFlightRef.current = false;
        if (mountedRef.current) {
          setRefreshing(false);
          setInitialLoading(false);
        }
      }
    }
  }, [supabase, session, incidentFilter]);

  useEffect(() => {
    if (!session) {
      setOverview(null);
      setIncidents([]);
      setActiveIncidents([]);
      setLastUpdated(null);
      setInitialLoading(true);
      return;
    }
    void load();
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      requestRef.current?.abort();
      requestRef.current = null;
      inFlightRef.current = false;
    };
  }, [session, load]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) {
      setLoginError("Supabase authentication is not configured for this deployment.");
      return;
    }
    setSigningIn(true);
    setLoginError("");
    try {
      const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
      if (error) setLoginError(error.message);
      else setPassword("");
    } catch {
      setLoginError("Sign-in failed unexpectedly. Check your connection and retry.");
    } finally {
      setSigningIn(false);
    }
  }

  async function signOut() {
    if (!supabase || signOutBusy) return;
    setSignOutBusy(true);
    try {
      const { error } = await supabase.auth.signOut();
      if (error) {
        setDataError("Sign-out failed. Please retry.");
      } else {
        requestRef.current?.abort();
        setSession(null);
        setDataError("");
      }
    } finally {
      setSignOutBusy(false);
    }
  }

  async function changeIncident(id: string, action: Action) {
    if (!supabase || !API.startsWith("https://") || pendingAction) return;
    setPendingAction({ id, action });
    setDataError("");
    try {
      const { data, error } = await supabase.auth.getSession();
      const token = data.session?.access_token;
      if (error || !token) throw new ApiError(401, "Your monitoring session is missing or expired. Sign in again.");
      const response = await fetch(`${API}/v1/incidents/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        let body: unknown = null;
        try { body = await response.json(); } catch { /* empty body */ }
        throw new ApiError(response.status, apiMessage(response.status, body));
      }
      // The server is the source of truth: reload instead of editing local rows.
      await load();
    } catch (err) {
      setDataError(err instanceof Error ? err.message : `Could not ${action} this incident.`);
    } finally {
      setPendingAction(null);
    }
  }

  if (!authReady) {
    return (
      <main className={styles.authShell}>
        <section className={styles.authCard}>
          <span className={styles.brandMark}>T</span>
          <p className={styles.eyebrow}>OPERATIONS PLATFORM</p>
          <h1>Tynato Monitoring</h1>
          <p className={styles.muted}>Restoring secure session…</p>
        </section>
      </main>
    );
  }

  if (!session) {
    return (
      <main className={styles.authShell}>
        <section className={styles.authCard}>
          <span className={styles.brandMark}>T</span>
          <p className={styles.eyebrow}>OPERATIONS PLATFORM</p>
          <h1>Tynato Monitoring</h1>
          <p className={styles.muted}>Sign in with an authorized monitoring account.</p>
          <form onSubmit={signIn} className={styles.loginForm}>
            <label>Email<input autoComplete="username" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label>Password<input autoComplete="current-password" type="password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <button disabled={signingIn} type="submit" className={styles.primaryButton}>{signingIn ? "Signing in…" : "Sign in"}</button>
            {loginError && <p className={styles.errorText} role="alert">{loginError}</p>}
          </form>
          <p className={styles.tinyMuted}>Accounts are provisioned by a monitoring administrator.</p>
        </section>
      </main>
    );
  }

  const services = overview?.services ?? [];
  const workers = overview?.workers ?? [];
  const hasSnapshot = Boolean(overview);

  return (
    <main className={styles.dashboard}>
      <header className={styles.topbar}>
        <div className={styles.headingBlock}>
          <p className={styles.eyebrow}>TYNATO / OPERATIONS</p>
          <h1>Monitoring overview</h1>
          <p className={styles.subtitle}>Production service health and incident activity</p>
        </div>
        <div className={styles.headerActions}>
          <div className={styles.sessionInfo}>
            <span className={styles.sessionDot} />
            <span title={session.user.email ?? "Authorized user"}>{session.user.email ?? "Authorized user"}</span>
          </div>
          <button className={styles.secondaryButton} onClick={() => void signOut()} disabled={signOutBusy}>
            {signOutBusy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </header>

      <div className={styles.statusLine} aria-live="polite">
        <span className={`${styles.liveDot} ${dataError ? styles.warningDot : ""}`} />
        <span>{dataError ? "Data may be stale" : refreshing ? "Updating monitoring data…" : "Automatic updates enabled"}</span>
        <span className={styles.statusSeparator}>·</span>
        <span>Every 30 seconds</span>
        <span className={styles.statusSeparator}>·</span>
        <span>Last successful update: {timestamp(lastUpdated)}</span>
      </div>

      {dataError && (
        <div className={styles.errorBanner} role="alert">
          <strong>Monitoring data unavailable</strong>
          <span>{dataError} {hasSnapshot ? "The last successful snapshot is retained." : "Waiting for the first successful snapshot."}</span>
        </div>
      )}

      <section className={styles.metricsGrid} aria-label="Incident and alert summary">
        <Metric title="Active incidents" value={overview?.active_incidents} loading={initialLoading} tone={(overview?.active_incidents ?? 0) > 0 ? "danger" : "good"} caption="Open + acknowledged" />
        <Metric title="Open incidents" value={overview?.open_incidents} loading={initialLoading} tone={(overview?.open_incidents ?? 0) > 0 ? "danger" : "neutral"} caption="Awaiting acknowledgement" />
        <Metric title="Failed checks · 24h" value={overview?.failed_checks_24h} loading={initialLoading} tone={(overview?.failed_checks_24h ?? 0) > 0 ? "danger" : "good"} caption="Recorded failed probes" />
        <Metric title="Pending email alerts" value={overview?.pending_alerts} loading={initialLoading} tone={(overview?.pending_alerts ?? 0) > 0 ? "warning" : "good"} caption="Awaiting delivery attempt" />
      </section>

      <ComponentCards api={API} getToken={getToken} />


      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div>
            <h2>Configured health checks</h2>
            <p>Latest persisted probe result. Old successful results are marked stale, not healthy.</p>
          </div>
          <span className={`${styles.pill} ${dataError ? styles.pillDanger : styles.pillInfo}`}>{services.length} checks reporting</span>
        </div>
        {services.length > 0 ? (
          <div className={styles.tableScroll}>
            <table className={styles.dataTable}>
              <thead><tr><th>Service</th><th>Status</th><th>Latency</th><th>Last check (UTC)</th></tr></thead>
              <tbody>
                {services.map((service) => {
                  const state = probeStatus(service);
                  return (
                    <tr key={service.service}>
                      <td>
                        <span className={styles.primaryCell}>{service.service}</span>
                        {(state.tone === "danger" || state.tone === "warning") && (
                          <span className={`${styles.subCell} ${state.tone === "danger" ? styles.errorText : styles.warningText}`}>{state.evidence}</span>
                        )}
                      </td>
                      <td><StatusPill value={state.label} tone={state.tone} /></td>
                      <td className={styles.numericCell}>{service.latency_ms == null ? DASH : `${Math.round(service.latency_ms)} ms`}</td>
                      <td className={styles.timeCell}>{timestamp(service.checked_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState loading={initialLoading} title="No health checks available" text="The monitoring API has not returned any persisted service checks yet." />
        )}
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div><h2>Background workers</h2><p>Latest reported heartbeat for each worker</p></div>
          <span className={styles.countPill}>{workers.length} {workers.length === 1 ? "worker" : "workers"}</span>
        </div>
        {workers.length > 0 ? (
          <div className={styles.tableScroll}>
            <table className={styles.dataTable}>
              <thead><tr><th>Worker</th><th>Status</th><th>Last heartbeat (UTC)</th></tr></thead>
              <tbody>
                {workers.map((worker) => {
                  const age = ageMs(worker.last_seen_at);
                  const stale = age === null || age > STALE_AFTER_MS;
                  const unhealthy = !["healthy", "ok"].includes(worker.status.toLowerCase());
                  const label = unhealthy ? "ERROR" : stale ? "STALE" : "HEALTHY";
                  const tone: CoverageTone = unhealthy ? "danger" : stale ? "warning" : "good";
                  return (
                    <tr key={worker.worker_name}>
                      <td className={styles.primaryCell}>{worker.worker_name}</td>
                      <td><StatusPill value={label} tone={tone} /></td>
                      <td className={styles.timeCell}>{timestamp(worker.last_seen_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState loading={initialLoading} title="No worker heartbeats" text="No background worker has reported a heartbeat to the monitoring API yet." />
        )}
      </section>

      <section className={styles.panel}>
        <div className={styles.incidentsHeader}>
          <div><h2>Incidents</h2><p>Review active issues or search the resolved incident history.</p></div>
          <span className={styles.countPill}>{incidents.length} {incidents.length === 1 ? "record" : "records"}</span>
        </div>
        <div className={styles.filterBar} role="group" aria-label="Filter incidents by status">
          {([["active", "Active"], ["open", "Open"], ["acknowledged", "Acknowledged"], ["resolved", "Resolved"], ["all", "All history"]] as [IncidentFilter, string][]).map(([value, label]) => (
            <button key={value} className={`${styles.filterButton} ${incidentFilter === value ? styles.filterActive : ""}`} onClick={() => setIncidentFilter(value)} aria-pressed={incidentFilter === value}>{label}</button>
          ))}
        </div>
        {incidents.length > 0 ? (
          <div className={styles.tableScroll}>
            <table className={`${styles.dataTable} ${styles.incidentTable}`}>
              <thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>First detected (UTC)</th><th className={styles.countColumn}>Occurrences</th><th className={styles.actionsColumn}>Actions</th></tr></thead>
              <tbody>
                {incidents.map((incident) => {
                  const details = incidentDetailEntries(incident.details);
                  return (
                    <tr key={incident.id}>
                      <td className={styles.incidentDescription}>
                        <div className={styles.primaryCell}>{incident.summary}</div>
                        <div className={styles.subCell}>
                          {incident.service} <span aria-hidden="true">·</span> {incident.category}
                          {incident.customer_id ? <> <span aria-hidden="true">·</span> Customer {incident.customer_id}</> : null}
                        </div>
                        <div className={styles.incidentMeta}>Last observed: {timestamp(incident.last_observed_at)}</div>
                        {details.length > 0 && (
                          <details className={styles.detailDisclosure}>
                            <summary>Diagnostic details</summary>
                            <dl>{details.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
                          </details>
                        )}
                      </td>
                      <td><StatusPill value={incident.severity} kind="severity" /></td>
                      <td><StatusPill value={incident.status} /></td>
                      <td className={styles.timeCell}>{timestamp(incident.first_detected_at)}</td>
                      <td className={styles.numericCell}>{incident.occurrence_count ?? DASH}</td>
                      <td>
                        <div className={styles.rowActions}>
                          {incident.status === "open" && (
                            <button className={styles.actionButton} disabled={Boolean(pendingAction)} onClick={() => void changeIncident(incident.id, "acknowledge")}>
                              {pendingAction?.id === incident.id && pendingAction.action === "acknowledge" ? "Saving…" : "Acknowledge"}
                            </button>
                          )}
                          {incident.status !== "resolved" && (
                            <button className={`${styles.actionButton} ${styles.resolveButton}`} disabled={Boolean(pendingAction)} onClick={() => void changeIncident(incident.id, "resolve")}>
                              {pendingAction?.id === incident.id && pendingAction.action === "resolve" ? "Saving…" : "Resolve"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            loading={initialLoading}
            title={incidentFilter === "active" ? "No active incidents" : "No incidents in this view"}
            text={incidentFilter === "active" ? "No open or acknowledged incidents were returned by the API." : "Try another status filter to view other incident records."}
          />
        )}
      </section>

      <footer className={styles.footer}>
        <span>Times displayed in UTC.</span>
        <span>Polling pauses while this tab is hidden and refreshes when you return. Healthy probes older than 90 seconds are marked stale.</span>
        <span>Monitoring API snapshot: {timestamp(overview?.generated_at)}</span>
      </footer>
    </main>
  );
}

function Metric({ title, value, loading, tone, caption }: {
  title: string;
  value?: number;
  loading: boolean;
  tone: "danger" | "good" | "neutral" | "warning";
  caption: string;
}) {
  return (
    <article className={styles.metricCard}>
      <div className={styles.metricLabel}>{title}</div>
      <div className={`${styles.metricValue} ${styles[`metric_${tone}`]}`}>
        {value == null ? (loading ? <span className={styles.skeletonValue} aria-label="Loading" /> : DASH) : value}
      </div>
      <div className={styles.metricCaption}>{caption}</div>
    </article>
  );
}

function StatusPill({ value, kind = "status", tone: toneOverride }: { value: string; kind?: "status" | "severity"; tone?: CoverageTone }) {
  const normalized = value.toLowerCase();
  let tone = styles.pillNeutral;
  if (toneOverride) {
    tone = toneOverride === "danger" ? styles.pillDanger : toneOverride === "warning" ? styles.pillWarning : toneOverride === "good" ? styles.pillGood : styles.pillNeutral;
  } else if (kind === "severity") {
    tone = normalized === "critical" ? styles.pillDanger : ["high", "medium"].includes(normalized) ? styles.pillWarning : styles.pillNeutral;
  } else if (["ok", "healthy", "sent", "resolved", "success", "connected"].includes(normalized)) {
    tone = styles.pillGood;
  } else if (["open", "failed", "failure", "unhealthy", "critical", "error", "down", "unavailable"].includes(normalized)) {
    tone = styles.pillDanger;
  } else if (["acknowledged", "pending", "warning", "degraded", "stale"].includes(normalized)) {
    tone = styles.pillWarning;
  }
  return <span className={`${styles.pill} ${tone}`}>{value.replaceAll("_", " ")}</span>;
}

function EmptyState({ loading, title, text }: { loading: boolean; title: string; text: string }) {
  if (loading) return <div className={styles.emptyState}><span className={styles.spinner} /> Loading monitoring data…</div>;
  return <div className={styles.emptyState}><strong>{title}</strong><span>{text}</span></div>;
}

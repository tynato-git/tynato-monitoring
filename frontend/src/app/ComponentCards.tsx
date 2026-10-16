"use client";

import { useCallback, useEffect, useState } from "react";

type Status = "red" | "amber" | "green" | "grey";
type Comp = { component: string; label: string; status: Status; message: string; last_signal_at: string | null };
type Resp = { overall: Status; components: Comp[] };

const TONE: Record<Status, string> = { red: "danger", amber: "warn", green: "good", grey: "" };
const WORD: Record<Status, string> = { red: "DOWN", amber: "DEGRADED", green: "OK", grey: "NO SIGNAL" };

export default function ComponentCards({
  api,
  getToken,
}: {
  api: string;
  getToken: () => Promise<string | undefined>;
}) {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) throw new Error("Session expired. Sign in again.");
      const res = await fetch(`${api}/v1/components`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Monitoring API returned HTTP ${res.status}.`);
      setData((await res.json()) as Resp);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Unable to load components.");
    }
  }, [api, getToken]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Components</h2>
          <p className="muted">Current status per component. Refreshes every 30 seconds.</p>
        </div>
        {data && <span className={`status-pill ${TONE[data.overall] === "good" ? "good" : TONE[data.overall]}`}>{WORD[data.overall]}</span>}
      </div>
      {err && <div className="empty-state">{err}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: 14, padding: 18 }}>
        {data?.components.map((c) => (
          <article key={c.component} className="metric-card">
            <p>{c.label}</p>
            <strong className={TONE[c.status]}>{WORD[c.status]}</strong>
            <p className="tiny muted" style={{ marginTop: 10 }}>{c.message}</p>
            {c.last_signal_at && (
              <p className="tiny muted">Last signal: {new Date(c.last_signal_at).toLocaleString(undefined, { timeZone: "UTC" })} UTC</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
"use client";

import { useEffect, useState } from "react";

export type ComponentStatus = "red" | "amber" | "green" | "grey";

export type MonitoredComponent = {
  component: string;
  label: string;
  status: ComponentStatus;
  message: string;
  last_signal_at: string | null;
};

type TokenSource = () => Promise<string | null | undefined> | string | null | undefined;

const REFRESH_MS = 30_000;

export function useComponents(api: string, getToken: TokenSource): MonitoredComponent[] {
  const [components, setComponents] = useState<MonitoredComponent[]>([]);

  useEffect(() => {
    if (!api.startsWith("https://")) return;

    let alive = true;
    const controller = new AbortController();

    const load = async () => {
      try {
        const token = await getToken();
        if (!alive) return;
        if (!token) {
          setComponents([]);
          return;
        }
        const res = await fetch(`${api}/v1/components`, {
          method: "GET",
          headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!res.ok || !alive) return;
        const body = (await res.json()) as { components?: MonitoredComponent[] };
        if (alive) setComponents(Array.isArray(body.components) ? body.components : []);
      } catch {
        // Keep the last good snapshot. The page's error banner reports API outages.
      }
    };

    void load();
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, REFRESH_MS);

    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [api, getToken]);

  return components;
}
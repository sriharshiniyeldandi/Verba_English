"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ACTIVITY_EVENT, progressApi, type ProgressSummary } from "../lib/api";
import { useAuth } from "./AuthProvider";

type ProgressState = {
  summary: ProgressSummary | null;
  loading: boolean;
  error: string;
  reload: () => void;
};

const ProgressContext = createContext<ProgressState | null>(null);

function useProgressInternal(): ProgressState {
  const { user } = useAuth();
  const [summary, setSummary] = useState<ProgressSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (!user) {
      setSummary(null);
      setLoading(false);
      return;
    }
    progressApi
      .summary()
      .then((s) => {
        setSummary(s);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // A finished recording or a completed lesson should move the streak straight away.
  useEffect(() => {
    const refresh = () => load();
    window.addEventListener(ACTIVITY_EVENT, refresh);
    window.addEventListener("verba:attempts-changed", refresh);
    return () => {
      window.removeEventListener(ACTIVITY_EVENT, refresh);
      window.removeEventListener("verba:attempts-changed", refresh);
    };
  }, [load]);

  return useMemo(() => ({ summary, loading, error, reload: load }), [summary, loading, error, load]);
}

export function ProgressProvider({ children }: { children: React.ReactNode }) {
  const value = useProgressInternal();
  return <ProgressContext.Provider value={value}>{children}</ProgressContext.Provider>;
}

/**
 * The signed-in teacher's learning statistics, always fetched from the backend for the
 * authenticated user. Shared via ProgressContext when inside <ProgressProvider> so that
 * SiteShell (sidebar streak) and page components do not duplicate GET /progress/summary calls.
 */
export function useProgress(): ProgressState {
  const ctx = useContext(ProgressContext);
  const fallback = useProgressInternalFallback(Boolean(ctx));
  return ctx ?? fallback;
}

function useProgressInternalFallback(skip: boolean): ProgressState {
  const { user } = useAuth();
  const [summary, setSummary] = useState<ProgressSummary | null>(null);
  const [loading, setLoading] = useState(!skip);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (skip || !user) {
      setSummary(null);
      setLoading(false);
      return;
    }
    progressApi
      .summary()
      .then((s) => {
        setSummary(s);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [skip, user]);

  useEffect(() => {
    if (skip) return;
    setLoading(true);
    load();
  }, [skip, load]);

  return { summary, loading, error, reload: load };
}

/** Today's date, rendered in the reader's own locale and timezone (never hardcoded). */
export function todayLabel(now: Date = new Date()) {
  return now
    .toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })
    .toUpperCase();
}

export function greeting(now: Date = new Date()) {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

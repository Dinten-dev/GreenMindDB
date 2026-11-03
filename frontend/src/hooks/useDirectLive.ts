'use client';

import { useEffect, useRef, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import { useAppText } from '@/i18n/app-text';

export type LivePoint = {
  cursor: number;
  frame: number;
  samples: number;
  recorded_us: number;
  mv: number;
};
export type LiveMarker = {
  id: string;
  session_id: string;
  frame: number;
  at_us: number;
  clicked_at_us: number;
};
export type LiveSnapshot = {
  generation: string;
  session_id: string | null;
  preview_profile: string | null;
  capability: 'checking' | 'ready' | 'offline' | 'upgrade_required';
  stale: boolean;
  window_complete: boolean;
  cursor: number;
  reset: boolean;
  server_us: number;
  points: LivePoint[];
  markers: LiveMarker[];
  rail: null | {
    start_us: number;
    end_us: number;
    raw_samples: number;
    rail_samples: number;
    percent: number | null;
    stale: boolean;
    complete: boolean;
  };
};

export function useDirectLive(deviceId: string, seconds: 30 | 60 | 180) {
  const tx = useAppText();
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null);
  const [error, setError] = useState('');
  const [paused, setPaused] = useState(false);
  const [marking, setMarking] = useState(false);
  const [marked, setMarked] = useState(false);
  const markInFlight = useRef(false);
  const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(() => performance.now());
  const [receivedAt, setReceivedAt] = useState(0);
  const windowRef = useRef<number>(seconds);
  const windowRevision = useRef(0);
  const currentLease = useRef<string | null>(null);
  const base = `/direct-ingest/devices/${deviceId}`;
  useEffect(() => {
    windowRef.current = seconds;
    windowRevision.current += 1;
  }, [seconds]);
  useEffect(() => {
    if (paused) return;
    const tick = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(tick);
  }, [paused]);
  useEffect(() => {
    let mounted = true;
    let flow = 0;
    let stopCurrent: (() => void) | undefined;
    const release = (lease: string) => {
      void fetch(`/api/v1${base}/live-leases/${lease}`, {
        method: 'DELETE',
        credentials: 'include',
        keepalive: true,
      }).catch(() => {});
    };
    const stop = () => {
      ++flow;
      stopCurrent?.();
      stopCurrent = undefined;
      currentLease.current = null;
    };
    const start = () => {
      if (stopCurrent || !mounted || document.visibilityState === 'hidden') return;
      const version = ++flow;
      const lease = crypto.randomUUID();
      const controller = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      let confirmed = false;
      let cancelled = false;
      let after = 0,
        generation = '',
        revision = -1;
      let lastRenewal = 0,
        failures = 0;
      const valid = () => mounted && version === flow && !cancelled;
      stopCurrent = () => {
        cancelled = true;
        controller.abort();
        clearTimeout(timer);
        if (confirmed) release(lease);
      };
      setSnapshot(null);
      setError('');
      setMarked(false);
      const renew = async () => {
        const requested = performance.now();
        // Let an initial PUT finish: a late acknowledgement is followed by DELETE,
        // rather than racing a DELETE against an aborted-but-committed creation.
        await apiFetch(`${base}/live-leases/${lease}`, { method: 'PUT', body: '{}' });
        confirmed = true;
        lastRenewal = requested;
        if (!valid()) release(lease);
      };
      const poll = async () => {
        const started = performance.now();
        const requestedWindow = windowRef.current;
        const requestedRevision = windowRevision.current;
        try {
          if (!confirmed || performance.now() - lastRenewal >= 10000) await renew();
          if (!valid()) return;
          currentLease.current = lease;
          if (requestedRevision !== revision) {
            after = 0;
            generation = '';
          }
          const result = await apiFetch<LiveSnapshot>(`${base}/live-preview`, {
            params: {
              lease_id: lease,
              seconds: String(requestedWindow),
              after: String(after),
              ...(generation ? { generation } : {}),
            },
            signal: controller.signal,
            cache: 'no-store',
          });
          if (!valid() || requestedRevision !== windowRevision.current) return;
          after = result.cursor;
          generation = result.generation;
          revision = requestedRevision;
          setSnapshot((previous) => {
            const old =
              result.reset || previous?.generation !== result.generation ? [] : previous.points;
            const byCursor = new Map(
              [...old, ...result.points].map((point) => [point.cursor, point])
            );
            const points = [...byCursor.values()]
              .filter((p) => p.recorded_us >= result.server_us - 180000000)
              .slice(-2251);
            return { ...result, points };
          });
          failures = 0;
          setReceivedAt(performance.now());
          setError('');
        } catch (failure) {
          if (!valid()) return;
          ++failures;
          setError(tx.error(failure));
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
            setSnapshot(null);
            stop();
            return;
          }
        } finally {
          if (valid())
            timer = setTimeout(
              () => void poll(),
              Math.max(
                0,
                (failures ? Math.min(5000, 500 * 2 ** Math.min(failures, 4)) : 500) -
                  (performance.now() - started)
              )
            );
        }
      };
      void poll();
    };
    const visibility = () => {
      const hidden = document.visibilityState === 'hidden';
      setPaused(hidden);
      if (hidden) {
        stop();
        setSnapshot(null);
      } else start();
    };
    const pageHide = () => stop();
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', pageHide);
    window.addEventListener('pageshow', visibility);
    visibility();
    return () => {
      mounted = false;
      stop();
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', pageHide);
      window.removeEventListener('pageshow', visibility);
    };
  }, [base, tx, retry]);
  const latest = snapshot?.points.at(-1);
  const signalAge =
    latest && snapshot
      ? Math.max(0, (snapshot.server_us - latest.recorded_us) / 1000) +
        Math.max(0, now - receivedAt)
      : null;
  const stale =
    !snapshot ||
    snapshot.stale ||
    now - receivedAt > 1500 ||
    signalAge === null ||
    signalAge > 1500;
  const mark = async () => {
    const lease = currentLease.current;
    if (!lease || !snapshot?.session_id || !latest || stale || markInFlight.current) return;
    markInFlight.current = true;
    setMarking(true);
    setMarked(false);
    try {
      await apiFetch(`${base}/live-markers/${crypto.randomUUID()}`, {
        method: 'POST',
        params: { lease_id: lease },
        body: JSON.stringify({
          generation: snapshot.generation,
          session_id: snapshot.session_id,
          frame: latest.frame,
        }),
      });
      if (currentLease.current === lease) setMarked(true);
    } catch (failure) {
      if (currentLease.current === lease) setError(tx.error(failure));
    } finally {
      markInFlight.current = false;
      setMarking(false);
    }
  };
  return {
    snapshot,
    error,
    paused,
    marking,
    marked,
    stale,
    signalAge,
    railStale:
      !snapshot?.rail ||
      snapshot.rail.stale ||
      now - receivedAt > 1500 ||
      (snapshot.server_us - snapshot.rail.end_us) / 1000 + Math.max(0, now - receivedAt) > 1500,
    mark,
    retry: () => setRetry((value) => value + 1),
  };
}

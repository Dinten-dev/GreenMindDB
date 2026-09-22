'use client';

import { useId, type ReactNode } from 'react';

/** One sensor layout for Gateway and Direct connections. */
export default function SensorCard({
  name,
  connection,
  status,
  online,
  lastSeen,
  expanded,
  onToggle,
  notice,
  children,
}: {
  name: string;
  connection: string;
  status: string;
  online: boolean;
  lastSeen: string | null;
  expanded: boolean;
  onToggle: () => void;
  notice?: ReactNode;
  children: ReactNode;
}) {
  const panelId = useId();
  return (
    <article className={`glass-card overflow-hidden ${expanded ? 'border-emerald-500/20' : ''}`}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={expanded ? panelId : undefined}
        onClick={onToggle}
        className="flex w-full items-start gap-3 p-4 text-left focus-visible:outline-emerald-600 sm:p-5"
      >
        <svg
          aria-hidden="true"
          className={`mt-1 h-4 w-4 shrink-0 text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="m7 4 6 6-6 6" />
        </svg>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span className="break-words text-sm font-medium text-gray-800">{name}</span>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs ${online ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${online ? 'bg-emerald-500' : 'bg-gray-400'}`}
              />
              {status}
            </span>
          </span>
          <span className="mt-1 block text-xs text-gray-500">{connection}</span>
          <span className="mt-2 block text-xs text-gray-400">
            {lastSeen
              ? `Letzter Empfang: ${new Date(lastSeen).toLocaleString('de-CH')}`
              : 'Wartet auf erste Messdaten'}
          </span>
          {notice && <span className="mt-2 block text-xs text-amber-700">{notice}</span>}
        </span>
      </button>
      {expanded && (
        <div id={panelId} className="border-t border-black/[0.04] p-3 sm:p-4">
          {children}
        </div>
      )}
    </article>
  );
}

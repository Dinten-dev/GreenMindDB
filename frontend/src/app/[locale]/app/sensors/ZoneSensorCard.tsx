'use client';

import { useEffect, useId, useState, type ReactNode } from 'react';

export default function ZoneSensorCard({
  name,
  location,
  count,
  connections,
  reveal,
  onCollapse,
  children,
}: {
  name: string;
  location?: string | null;
  count: number;
  connections: string;
  reveal: boolean;
  onCollapse: () => void;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(reveal);
  const id = useId();
  useEffect(() => {
    if (reveal) setExpanded(true);
  }, [reveal]);
  return (
    <section
      aria-labelledby={`${id}-title`}
      className="overflow-hidden rounded-2xl border border-emerald-100 bg-white/60"
    >
      <h2>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={expanded ? `${id}-sensors` : undefined}
          onClick={() => {
            setExpanded(!expanded);
            if (expanded) onCollapse();
          }}
          className="flex w-full items-center gap-3 p-4 text-left focus-visible:outline-emerald-600 sm:p-6"
        >
          <svg
            aria-hidden="true"
            className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="m7 4 6 6-6 6" />
          </svg>
          <span className="min-w-0 flex-1">
            <span id={`${id}-title`} className="block text-lg font-semibold text-gray-800">
              {name}
            </span>
            {location && (
              <span className="mt-1 block text-xs font-normal text-gray-500">{location}</span>
            )}
            {connections && (
              <span className="mt-1 block text-xs font-normal text-gray-500">{connections}</span>
            )}
          </span>
          <span className="shrink-0 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800">
            {count} {count === 1 ? 'Sensor' : 'Sensoren'}
          </span>
        </button>
      </h2>
      {expanded && (
        <div id={`${id}-sensors`} className="space-y-3 border-t border-emerald-100 p-4 sm:p-6">
          {children}
        </div>
      )}
    </section>
  );
}

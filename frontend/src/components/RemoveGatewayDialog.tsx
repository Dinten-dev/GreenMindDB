'use client';

import { useEffect, useId, useRef } from 'react';
import type { GatewayInfo } from '@/lib/api';
import type { RemovalNotice } from '@/hooks/useGatewayRemoval';
import { useAppText } from '@/i18n/app-text';

export default function RemoveGatewayDialog({
  gateway,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  gateway: GatewayInfo | null;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const tx = useAppText();
  const titleId = useId();
  const descriptionId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (gateway && element && !element.open) element.showModal();
    return () => {
      if (element?.open) element.close();
    };
  }, [gateway]);
  if (!gateway) return null;
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="m-auto w-[90vw] max-w-sm rounded-2xl border-0 bg-white p-6 shadow-xl backdrop:bg-black/30 backdrop:backdrop-blur-sm"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <h2 id={titleId} className="text-lg font-semibold text-gray-800">
        {tx('Gateway entfernen?')}
      </h2>
      <p className="mt-2 break-words font-medium text-gray-800">
        {gateway.name || gateway.hardware_id}
      </p>
      <p id={descriptionId} className="mt-3 text-sm text-gray-600">
        {tx(
          'Das Gateway wird deaktiviert und sein Zugang widerrufen. Sensoren werden als getrennt angezeigt. Messdaten, Aufnahmen und Zuordnungen bleiben erhalten.'
        )}
      </p>
      {error && (
        <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}
      <div className="mt-6 flex flex-wrap gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="flex-1 rounded-xl border border-gray-200 px-4 py-2.5 text-sm disabled:opacity-50"
        >
          {tx('Abbrechen')}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void onConfirm()}
          className="flex-1 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? tx('Entferne…') : tx('Gateway entfernen')}
        </button>
      </div>
    </dialog>
  );
}

export function GatewayRemovalNotice({
  notice,
  busy,
  onRetry,
}: {
  notice: RemovalNotice | null;
  busy: boolean;
  onRetry: () => Promise<void>;
}) {
  const tx = useAppText();
  if (!notice) return null;
  return (
    <div
      role={notice.kind === 'warning' ? 'alert' : 'status'}
      className={`rounded-xl p-4 text-sm ${notice.kind === 'warning' ? 'bg-amber-50 text-amber-900' : 'bg-emerald-50 text-emerald-900'}`}
    >
      <p>{notice.message}</p>
      {notice.kind === 'warning' && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void onRetry()}
          className="mt-2 underline disabled:opacity-50"
        >
          {tx('Erneut laden')}
        </button>
      )}
    </div>
  );
}

'use client';

import { useRef, useState } from 'react';
import { apiRemoveGateway, type GatewayInfo } from '@/lib/api';
import { useAppText } from '@/i18n/app-text';

export type RemovalNotice = { kind: 'success' | 'warning'; message: string };
export function useGatewayRemoval({
  onRemoved,
  refresh,
}: {
  onRemoved: (id: string) => void;
  refresh: () => Promise<void>;
}) {
  const tx = useAppText();
  const [target, setTarget] = useState<GatewayInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<RemovalNotice | null>(null);
  const pending = useRef(false);
  const update = async () => {
    try {
      await refresh();
      setNotice({
        kind: 'success',
        message: tx('Gateway entfernt. Messdaten und Zuordnungen bleiben erhalten.'),
      });
    } catch {
      setNotice({
        kind: 'warning',
        message: tx(
          'Gateway entfernt. Die Ansicht konnte nicht aktualisiert werden. Bitte erneut laden.'
        ),
      });
    }
  };
  const confirm = async () => {
    if (!target || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      await apiRemoveGateway(target.id);
      onRemoved(target.id);
      setTarget(null);
      await update();
    } catch (failure) {
      setError(tx.error(failure));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return {
    target,
    busy,
    error,
    notice,
    confirm,
    begin: (gateway: GatewayInfo) => {
      if (!pending.current) {
        setTarget(gateway);
        setError('');
        setNotice(null);
      }
    },
    cancel: () => {
      if (!pending.current) {
        setTarget(null);
        setError('');
      }
    },
    retry: async () => {
      if (pending.current) return;
      pending.current = true;
      setBusy(true);
      try {
        await update();
      } finally {
        pending.current = false;
        setBusy(false);
      }
    },
  };
}

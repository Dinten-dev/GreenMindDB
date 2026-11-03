'use client';
import type { RecordingCompleteness } from '@/lib/api';
import { useAppText } from '@/i18n/app-text';

export default function RecordingCompletenessBadge({
  completeness,
}: {
  completeness?: RecordingCompleteness;
}) {
  const tx = useAppText();

  if (!completeness || completeness.status === 'unknown')
    return <span className="ml-2 text-gray-500">{tx('Vollständigkeit ungeprüft')}</span>;
  const labels = {
    complete: 'Vollständig',
    partial: 'Datenlücke',
    short: 'Teilstück oder Datenlücke',
    partial_start: 'Startteilstück',
    collecting: 'Aufnahme läuft',
  };
  const warning = completeness.status === 'partial' || completeness.status === 'short';
  const number = (value: number) => value.toLocaleString(tx.locale, { maximumFractionDigits: 1 });
  return (
    <span
      className={`ml-2 inline-block rounded-md px-2 py-1 text-xs ${warning ? 'bg-amber-100 text-amber-900' : completeness.status === 'complete' ? 'bg-emerald-50 text-emerald-800' : 'bg-gray-100 text-gray-700'}`}
    >
      {completeness.basis === 'verified_manifest' && tx('Segment · ')}
      {tx(labels[completeness.status])}
      {completeness.received_seconds != null && completeness.expected_seconds != null && (
        <>
          {' '}
          · {number(completeness.received_seconds)}/{number(completeness.expected_seconds)}{' '}
          {tx('s')}
        </>
      )}
      {completeness.coverage_ratio != null && <> · {number(completeness.coverage_ratio * 100)} %</>}
    </span>
  );
}

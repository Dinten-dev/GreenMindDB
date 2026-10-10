import type { RecordingCompleteness } from '@/lib/api';

export default function RecordingCompletenessBadge({
  completeness,
}: {
  completeness?: RecordingCompleteness;
}) {
  if (!completeness || completeness.status === 'unknown')
    return <span className="ml-2 text-gray-500">Vollständigkeit ungeprüft</span>;
  const labels = {
    complete: 'Vollständig',
    partial: 'Datenlücke',
    short: 'Teilstück oder Datenlücke',
    partial_start: 'Startteilstück',
    collecting: 'Aufnahme läuft',
  };
  const warning = completeness.status === 'partial' || completeness.status === 'short';
  const number = (value: number) => value.toLocaleString('de-CH', { maximumFractionDigits: 1 });
  return (
    <span
      className={`ml-2 inline-block rounded-md px-2 py-1 text-xs ${warning ? 'bg-amber-100 text-amber-900' : completeness.status === 'complete' ? 'bg-emerald-50 text-emerald-800' : 'bg-gray-100 text-gray-700'}`}
    >
      {completeness.basis === 'verified_manifest' && 'Segment · '}
      {labels[completeness.status]}
      {completeness.received_seconds != null && completeness.expected_seconds != null && (
        <>
          {' '}
          · {number(completeness.received_seconds)}/{number(completeness.expected_seconds)} s
        </>
      )}
      {completeness.coverage_ratio != null && <> · {number(completeness.coverage_ratio * 100)} %</>}
    </span>
  );
}

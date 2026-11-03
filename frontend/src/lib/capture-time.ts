import type { SensorDataResponse } from './api';

export function captureTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

export function validCaptureStamp(value?: string | null) {
  return !!value && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value));
}

export function formatCaptureTime(value: string, locale: string, zone = captureTimeZone()) {
  if (!validCaptureStamp(value)) return '—';
  return new Intl.DateTimeFormat(locale, {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'shortOffset',
  }).format(new Date(value));
}

export function captureBounds(series: SensorDataResponse) {
  const time = series.time_info;
  const stamps = series.data
    .map((p) => p.timestamp)
    .filter(validCaptureStamp)
    .sort((a, b) => Date.parse(a) - Date.parse(b));
  return {
    first: validCaptureStamp(time?.recorded_from) ? time!.recorded_from! : stamps[0],
    last: validCaptureStamp(time?.recorded_to) ? time!.recorded_to! : stamps.at(-1),
  };
}

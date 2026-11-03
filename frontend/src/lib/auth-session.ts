/** No tokens or user data are stored here; cookies remain server-managed. */
export const SESSION_INVALIDATED = 'greenmind:session-invalidated';
let epoch = 0;

export const sessionEpoch = () => epoch;
export const advanceSessionEpoch = () => ++epoch;

export function reportUnauthorized(requestEpoch: number) {
  if (requestEpoch === epoch && typeof window !== 'undefined') {
    window.dispatchEvent(new Event(SESSION_INVALIDATED));
  }
}

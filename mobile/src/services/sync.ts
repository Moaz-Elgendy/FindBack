import NetInfo from '@react-native-community/netinfo';
import { getQueuedPending, markQueueDone, markQueueFailed } from '../db';
import { syncBatch } from './api';

let flushing = false;
let backoffMs = 1000;

export async function flushQueue(): Promise<{ flushed: number }> {
  if (flushing) return { flushed: 0 };
  flushing = true;
  try {
    const net = await NetInfo.fetch();
    if (!net.isConnected) return { flushed: 0 };
    const pending = await getQueuedPending();
    if (pending.length === 0) return { flushed: 0 };
    // batch size 20
    const batch = pending.slice(0, 20);
    const res = await syncBatch(batch.map(b => ({ client_id: b.client_id, url: b.url, preview: b.preview, title_hint: b.title_hint, captured_at: b.captured_at })));
    const doneIds = (res.mapped ?? []).map(m => m.client_id);
    if (doneIds.length) await markQueueDone(doneIds);
    // mark retried for errors
    for (const e of (res.errors ?? [])) {
      if (e.client_id) await markQueueFailed(e.client_id);
    }
    backoffMs = 1000;
    return { flushed: doneIds.length };
  } catch (e) {
    console.log('[sync] flush failed', e);
    // exponential backoff capped at 5m
    backoffMs = Math.min(backoffMs * 2, 5 * 60 * 1000);
    return { flushed: 0 };
  } finally {
    flushing = false;
  }
}

export function startSyncListeners(onFlushed?: (n: number) => void) {
  // flush on connectivity change
  const unsub = NetInfo.addEventListener(state => {
    if (state.isConnected) {
      flushQueue().then(r => { if (r.flushed && onFlushed) onFlushed(r.flushed); });
    }
  });
  // periodic flush every 30s while app foregrounded
  const interval = setInterval(() => {
    flushQueue().then(r => { if (r.flushed && onFlushed) onFlushed(r.flushed); });
  }, 30_000);
  // initial flush
  flushQueue().then(r => { if (r.flushed && onFlushed) onFlushed(r.flushed); });
  return () => { unsub(); clearInterval(interval); };
}

export function getBackoffMs() { return backoffMs; }

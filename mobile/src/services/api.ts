import Constants from 'expo-constants';
import { getAccessToken } from './auth';

async function headers(json = false): Promise<Record<string, string>> {
  const token = await getAccessToken();
  return { ...(json ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export function getApiUrl(): string {
  // expo extra or env fallback
  const fromExtra = (Constants.expoConfig?.extra as any)?.apiUrl;
  if (fromExtra) return fromExtra;
  return process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
}

export async function ingestUrl(url: string, preview?: string, title_hint?: string) {
  const api = getApiUrl();
  const res = await fetch(`${api}/api/v1/ingest`, {
    method: 'POST',
    headers: await headers(true),
    body: JSON.stringify({ url, preview, title_hint }),
  });
  if (!res.ok) throw new Error(`ingest failed: ${res.status}`);
  return res.json();
}

export async function syncBatch(items: Array<{client_id: string; url: string; preview?: string | null; title_hint?: string | null; captured_at: string}>) {
  const api = getApiUrl();
  const res = await fetch(`${api}/api/v1/sync/batch`, {
    method: 'POST',
    headers: await headers(true),
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new Error(`sync failed: ${res.status}`);
  return res.json() as Promise<{ mapped: Array<{client_id: string; id: string; status: string}>; errors: any[] }>;
}

export async function searchRemote(q: string, category?: string, limit = 10) {
  const api = getApiUrl();
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (category && category !== 'All') params.set('category', category.toLowerCase());
  const res = await fetch(`${api}/api/v1/search?${params.toString()}`, { headers: await headers() });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json() as Promise<{ results: Array<{id: string; title: string; summary: string; tags: string[]; category: string; thumbnail: string; source_domain: string; match_reason: string; score: number; created_at: string}>; took_ms: number }>;
}

export async function listItems(limit = 20) {
  const api = getApiUrl();
  const res = await fetch(`${api}/api/v1/items?limit=${limit}`, { headers: await headers() });
  if (!res.ok) throw new Error(`list failed: ${res.status}`);
  return res.json();
}

export async function getItem(id: string) {
  const api = getApiUrl();
  const res = await fetch(`${api}/api/v1/items/${id}`, { headers: await headers() });
  if (!res.ok) throw new Error(`get failed: ${res.status}`);
  return res.json();
}

export async function deleteItem(id: string) {
  const api = getApiUrl();
  const res = await fetch(`${api}/api/v1/items/${id}`, { method: 'DELETE', headers: await headers() });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
  return res.status === 204 ? null : res.json();
}

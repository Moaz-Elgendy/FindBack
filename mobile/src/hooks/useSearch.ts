import { useState, useCallback, useRef } from 'react';
import { searchRemote } from '../services/api';
import { localSearch } from '../db';
import NetInfo from '@react-native-community/netinfo';

export type SearchResult = {
  id: string;
  title: string;
  summary: string;
  tags: string[];
  category: string;
  thumbnail?: string;
  source_domain?: string;
  match_reason?: string;
  score: number;
  created_at?: string;
};

export function useSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [tookMs, setTookMs] = useState<number | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const doSearch = useCallback(async (q: string, category?: string) => {
    const trimmed = q.trim();
    if (!trimmed) { setResults([]); setTookMs(null); return; }
    setLoading(true);
    const net = await NetInfo.fetch();
    const isOffline = !net.isConnected;
    setOffline(isOffline);
    try {
      if (isOffline) {
        const rows = await localSearch(trimmed);
        setResults(rows.map((r: any) => ({
          id: r.id, title: r.title_clean ?? r.title ?? r.url, summary: r.summary ?? '',
          tags: (()=>{ try{return JSON.parse(r.tags)}catch{return []}})(),
          category: r.category ?? 'other', thumbnail: r.thumbnail_url, source_domain: r.source_domain,
          match_reason: `Offline — matched title/summary`, score: 0.5, created_at: r.created_at,
        })));
        setTookMs(null);
      } else {
        const res = await searchRemote(trimmed, category);
        setResults(res.results as SearchResult[]);
        setTookMs(res.took_ms);
      }
    } catch (e) {
      console.log('[search] failed', e);
      // fallback to local
      try {
        const rows = await localSearch(trimmed);
        setResults(rows.map((r: any) => ({
          id: r.id, title: r.title_clean ?? r.title ?? r.url, summary: r.summary ?? '',
          tags: (()=>{ try{return JSON.parse(r.tags)}catch{return []}})(),
          category: r.category ?? 'other', thumbnail: r.thumbnail_url, source_domain: r.source_domain,
          match_reason: `Offline fallback`, score: 0.3, created_at: r.created_at,
        })));
        setOffline(true);
      } catch {}
    } finally {
      setLoading(false);
    }
  }, []);

  const onChangeQuery = useCallback((q: string, category?: string) => {
    setQuery(q);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(q, category), 250);
  }, [doSearch]);

  return { query, setQuery: onChangeQuery, results, loading, offline, tookMs, doSearch };
}

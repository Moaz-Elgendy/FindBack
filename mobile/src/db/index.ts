import * as SQLite from 'expo-sqlite';

let db: SQLite.SQLiteDatabase | null = null;

export async function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (db) return db;
  db = await SQLite.openDatabaseAsync('findback.db');
  await initSchema(db);
  return db;
}

async function initSchema(database: SQLite.SQLiteDatabase) {
  await database.execAsync(`
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS items (
      id TEXT PRIMARY KEY,
      url TEXT NOT NULL,
      canonical_url TEXT,
      title TEXT,
      title_clean TEXT,
      summary TEXT,
      category TEXT,
      tags TEXT,
      source_domain TEXT,
      thumbnail_url TEXT,
      status TEXT DEFAULT 'pending',
      created_at TEXT,
      match_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS sync_queue (
      client_id TEXT PRIMARY KEY,
      url TEXT NOT NULL,
      preview TEXT,
      title_hint TEXT,
      captured_at TEXT NOT NULL,
      retries INTEGER DEFAULT 0,
      status TEXT DEFAULT 'pending'
    );
    CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_queue_status ON sync_queue(status);
  `);
}

// Local-first helpers
export async function queueSave(url: string, preview?: string, title_hint?: string) {
  const database = await getDb();
  const client_id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const now = new Date().toISOString();
  await database.runAsync(
    `INSERT INTO sync_queue (client_id, url, preview, title_hint, captured_at, status) VALUES (?, ?, ?, ?, ?, 'pending')`,
    [client_id, url, preview ?? null, title_hint ?? null, now]
  );
  // optimistic local item so search works offline (lite)
  const id = `local-${client_id}`;
  await database.runAsync(
    `INSERT INTO items (id, url, canonical_url, title, summary, category, tags, source_domain, status, created_at)
     VALUES (?, ?, ?, ?, ?, 'other', '[]', ?, 'pending', ?)`,
    [id, url, url, title_hint ?? preview?.slice(0, 80) ?? url, preview?.slice(0, 200) ?? '', extractDomain(url), now]
  );
  return client_id;
}

export async function getQueuedPending(): Promise<Array<{client_id: string; url: string; preview: string | null; title_hint: string | null; captured_at: string}>> {
  const database = await getDb();
  return await database.getAllAsync(`SELECT client_id, url, preview, title_hint, captured_at FROM sync_queue WHERE status='pending' ORDER BY captured_at ASC`);
}

export async function markQueueDone(clientIds: string[]) {
  if (clientIds.length === 0) return;
  const database = await getDb();
  const placeholders = clientIds.map(() => '?').join(',');
  await database.runAsync(`UPDATE sync_queue SET status='done' WHERE client_id IN (${placeholders})`, clientIds);
}

export async function markQueueFailed(clientId: string) {
  const database = await getDb();
  await database.runAsync(`UPDATE sync_queue SET retries = retries + 1 WHERE client_id = ?`, [clientId]);
}

export async function localSearch(query: string): Promise<any[]> {
  const database = await getDb();
  const pat = `%${query}%`;
  return await database.getAllAsync(
    `SELECT * FROM items WHERE title LIKE ? OR title_clean LIKE ? OR summary LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT 20`,
    [pat, pat, pat, pat]
  );
}

export async function upsertRemoteItems(remoteItems: any[]) {
  const database = await getDb();
  for (const it of remoteItems) {
    await database.runAsync(
      `INSERT OR REPLACE INTO items (id, url, canonical_url, title, title_clean, summary, category, tags, source_domain, thumbnail_url, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [it.id, it.url, it.canonical_url, it.title, it.title_clean, it.summary, it.category, JSON.stringify(it.tags ?? []), it.source_domain, it.thumbnail_url, it.status, it.created_at]
    );
  }
}

function extractDomain(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

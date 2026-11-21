// The sync loop, with HealthKit and the network passed in, so it runs (and is
// tested) in plain Node: `npm test`. No React Native imports in this file.
//
// For each kind: ask Health for up to PAGE readings added or deleted since the
// saved anchor, upload them (≤500 readings + ≤500 deletions per request, the
// server's cap), and only then save the new anchor. A crash or a refused
// upload therefore re-sends the page next time, which is harmless: the server
// stores each reading once (keyed by Apple's id) and deleting a missing id is
// a no-op.

export const PAGE = 500;            // = HEALTH_BATCH_MAX on the server
export const BACKFILL_DAYS = 365;   // SPEC-health-data.md H7: the first sync reaches back 1 year
export const MAX_ATTEMPTS = 5;      // per request, then the sync stops and resumes next time
const MAX_PAGES = 5_000;            // a guard against a query that never runs dry

export type Kind = 'hrv_sdnn' | 'blood_glucose';
export const KINDS: readonly Kind[] = ['hrv_sdnn', 'blood_glucose'];

export type Reading = { id: string; start: string; end: string; value: number; source?: string };

// The slice of a HealthKit quantity sample the upload needs.
export type HealthSampleLike = {
  uuid: string;
  startDate: Date;
  endDate: Date;
  quantity: number;
  sourceRevision?: { source?: { name?: string } };
};

export type Page = { samples: Reading[]; deleted: string[]; newAnchor: string };

export type Counts = { stored: number; already_had: number; deleted: number; rejected: number };

// Errors the upload can throw: anything with an HTTP status and a code.
export type UploadErrorLike = { status: number; code: string; retryAfterSeconds?: number | null };

export type SyncDeps = {
  query: (kind: Kind, anchor: string | null, since: Date) => Promise<Page>;
  upload: (kind: Kind, samples: Reading[], deleted: string[]) =>
    Promise<{ stored: number; already_had: number; deleted: number; rejected: unknown[] }>;
  getAnchor: (kind: Kind) => Promise<string | null>;
  setAnchor: (kind: Kind, anchor: string) => Promise<void>;
  sleep: (ms: number) => Promise<void>;
  now?: () => Date;
  onProgress?: (kind: Kind, counts: Counts) => void;
};

export function toReading(s: HealthSampleLike): Reading {
  const r: Reading = {
    id: s.uuid,
    start: s.startDate.toISOString(),
    end: s.endDate.toISOString(),
    value: s.quantity,
  };
  const source = s.sourceRevision?.source?.name?.trim();
  if (source) r.source = source.slice(0, 100);
  return r;
}

export function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** How long to wait before retrying a failed upload, or null to stop now.
 *  429 (rate limited): the server's Retry-After, else 1 min doubling to 15 min.
 *  409 (two uploads collided on the server): 1 s doubling — it clears quickly.
 *  Network failures and 5xx: 10 s doubling to 5 min.
 *  Anything else (401 signed out, 403 sync off or consent missing, 400): stop. */
export function retryDelayMs(err: UploadErrorLike, attempt: number): number | null {
  if (attempt >= MAX_ATTEMPTS) return null;
  const doubling = (base: number, cap: number) => Math.min(base * 2 ** (attempt - 1), cap) * 1000;
  if (err.status === 429) {
    return err.retryAfterSeconds ? Math.min(err.retryAfterSeconds, 3600) * 1000 : doubling(60, 900);
  }
  if (err.status === 409) return doubling(1, 30);
  if (err.status === 0 || err.status >= 500) return doubling(10, 300);
  return null;
}

async function withRetry<T>(deps: SyncDeps, fn: () => Promise<T>): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      const err = e as UploadErrorLike;
      const wait = typeof err?.status === 'number' ? retryDelayMs(err, attempt) : null;
      if (wait === null) throw e;
      await deps.sleep(wait);
    }
  }
}

/** Sync one kind until Health has nothing newer than the saved anchor. */
export async function syncKind(kind: Kind, deps: SyncDeps): Promise<Counts> {
  const counts: Counts = { stored: 0, already_had: 0, deleted: 0, rejected: 0 };
  const now = deps.now ? deps.now() : new Date();
  const since = new Date(now.getTime() - BACKFILL_DAYS * 24 * 3600 * 1000);
  let anchor = await deps.getAnchor(kind);
  for (let page = 0; page < MAX_PAGES; page++) {
    const { samples, deleted, newAnchor } = await deps.query(kind, anchor, since);
    if (samples.length === 0 && deleted.length === 0) {
      // Never save an anchor from an empty FIRST query: if Health access was
      // denied it returns nothing, and an anchor saved then would skip the
      // whole year once access is granted later (QA W1).
      if (anchor !== null && newAnchor && newAnchor !== anchor) await deps.setAnchor(kind, newAnchor);
      return counts;
    }
    if (newAnchor === anchor) {
      // Defensive: results without progress would loop and spend the upload
      // budget re-sending the same page.
      throw { status: -1, code: 'anchor_stuck', message: 'Apple Health returned the same position twice.' };
    }
    const sampleChunks = chunk(samples, PAGE);
    const deletedChunks = chunk(deleted, PAGE);
    const requests = Math.max(sampleChunks.length, deletedChunks.length);
    for (let i = 0; i < requests; i++) {
      const res = await withRetry(deps, () => deps.upload(kind, sampleChunks[i] ?? [], deletedChunks[i] ?? []));
      counts.stored += res.stored;
      counts.already_had += res.already_had;
      counts.deleted += res.deleted;
      counts.rejected += res.rejected.length;
      deps.onProgress?.(kind, { ...counts });
    }
    await deps.setAnchor(kind, newAnchor);
    anchor = newAnchor;
  }
  return counts;
}

/** Sync every kind in turn. A refusal that isn't worth retrying (signed out,
 *  sync turned off, consent missing) stops the whole run and is re-thrown. */
export async function syncAll(deps: SyncDeps): Promise<Record<Kind, Counts>> {
  const out = {} as Record<Kind, Counts>;
  for (const kind of KINDS) out[kind] = await syncKind(kind, deps);
  return out;
}

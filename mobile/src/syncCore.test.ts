// Sync-loop tests: plain Node, fake HealthKit + fake server. Run: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  BACKFILL_DAYS,
  MAX_ATTEMPTS,
  chunk,
  retryDelayMs,
  syncAll,
  syncKind,
  toReading,
  type Kind,
  type Page,
  type Reading,
  type SyncDeps,
} from './syncCore.ts';

const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const reading = (n: number): Reading => ({
  id: uuid(n),
  start: '2026-11-01T08:00:00.000Z',
  end: '2026-11-01T08:00:00.000Z',
  value: 40,
});

// A fake Health store: a queue of pages per anchor, anchor "a<i>" after page i.
function fakeHealth(pages: Omit<Page, 'newAnchor'>[]) {
  const seenAnchors: (string | null)[] = [];
  const seenSince: Date[] = [];
  const query = async (_k: Kind, anchor: string | null, since: Date): Promise<Page> => {
    seenAnchors.push(anchor);
    seenSince.push(since);
    const i = anchor === null ? 0 : Number(anchor.slice(1));
    const page = pages[i] ?? { samples: [], deleted: [] };
    return { ...page, newAnchor: `a${i + 1}` };
  };
  return { query, seenAnchors, seenSince };
}

function deps(over: Partial<SyncDeps> & Pick<SyncDeps, 'query'>) {
  const anchors: Record<string, string> = {};
  const uploads: { kind: Kind; samples: Reading[]; deleted: string[] }[] = [];
  const sleeps: number[] = [];
  const d: SyncDeps = {
    upload: async (kind, samples, deleted) => {
      uploads.push({ kind, samples, deleted });
      return { stored: samples.length, already_had: 0, deleted: deleted.length, rejected: [] };
    },
    getAnchor: async (k) => anchors[k] ?? null,
    setAnchor: async (k, a) => {
      anchors[k] = a;
    },
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    ...over,
  };
  return { d, anchors, uploads, sleeps };
}

test('toReading maps a HealthKit sample to the upload shape', () => {
  const r = toReading({
    uuid: uuid(1),
    startDate: new Date('2026-11-01T08:00:00Z'),
    endDate: new Date('2026-11-01T08:01:00Z'),
    quantity: 42.5,
    sourceRevision: { source: { name: '  Apple Watch  ' } },
  });
  assert.deepEqual(r, {
    id: uuid(1),
    start: '2026-11-01T08:00:00.000Z',
    end: '2026-11-01T08:01:00.000Z',
    value: 42.5,
    source: 'Apple Watch',
  });
  const noSource = toReading({ uuid: uuid(2), startDate: new Date(0), endDate: new Date(0), quantity: 1 });
  assert.equal('source' in noSource, false);
});

test('chunk splits into ≤ size pieces', () => {
  assert.deepEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepEqual(chunk([], 500), []);
});

test('backfill: pages are uploaded in order and the anchor advances after each', async () => {
  const p1 = Array.from({ length: 500 }, (_, i) => reading(i));
  const p2 = Array.from({ length: 120 }, (_, i) => reading(1000 + i));
  const h = fakeHealth([{ samples: p1, deleted: [] }, { samples: p2, deleted: [uuid(7)] }]);
  const now = new Date('2026-11-21T12:00:00Z');
  const { d, anchors, uploads } = deps({ query: h.query, now: () => now });
  const counts = await syncKind('hrv_sdnn', d);
  assert.equal(uploads.length, 2);
  assert.equal(uploads[0].samples.length, 500);
  assert.deepEqual(uploads[1].deleted, [uuid(7)]);
  assert.deepEqual(counts, { stored: 620, already_had: 0, deleted: 1, rejected: 0 });
  assert.deepEqual(h.seenAnchors, [null, 'a1', 'a2']);
  assert.equal(anchors.hrv_sdnn, 'a3');
  const days = (now.getTime() - h.seenSince[0].getTime()) / 86_400_000;
  assert.equal(days, BACKFILL_DAYS, 'first sync reaches back one year');
});

test('a page with more than 500 deletions is split; samples ride the first request', async () => {
  const del = Array.from({ length: 1200 }, (_, i) => uuid(i));
  const h = fakeHealth([{ samples: [reading(1)], deleted: del }]);
  const { d, uploads } = deps({ query: h.query });
  await syncKind('blood_glucose', d);
  assert.deepEqual(
    uploads.map((u) => [u.samples.length, u.deleted.length]),
    [
      [1, 500],
      [0, 500],
      [0, 200],
    ],
  );
});

test('an empty FIRST query saves no anchor (access may be denied; keep the year for later)', async () => {
  const h = fakeHealth([]);
  const { d, anchors, uploads } = deps({ query: h.query });
  await syncKind('hrv_sdnn', d);
  assert.equal(uploads.length, 0);
  assert.equal(anchors.hrv_sdnn, undefined);
});

test('nothing new after an earlier sync: the anchor still moves on', async () => {
  const h = fakeHealth([{ samples: [reading(1)], deleted: [] }]);
  const { d, anchors } = deps({ query: h.query });
  await syncKind('hrv_sdnn', d);
  assert.equal(anchors.hrv_sdnn, 'a2');
});

test('results without anchor progress stop the run instead of looping', async () => {
  const query = async (): Promise<Page> => ({ samples: [reading(1)], deleted: [], newAnchor: 'same' });
  const { d, uploads, anchors } = deps({ query, getAnchor: async () => 'same' });
  await assert.rejects(syncKind('hrv_sdnn', d), (e: any) => e.code === 'anchor_stuck');
  assert.equal(uploads.length, 0);
  assert.equal(anchors.hrv_sdnn, undefined);
});

test('429 waits (Retry-After when given) and retries the same batch', async () => {
  const h = fakeHealth([{ samples: [reading(1)], deleted: [] }]);
  let calls = 0;
  const { d, sleeps, anchors } = deps({
    query: h.query,
    upload: async (_k, samples) => {
      calls++;
      if (calls === 1) throw { status: 429, code: 'rate_limited', retryAfterSeconds: 120 };
      if (calls === 2) throw { status: 429, code: 'rate_limited' };
      return { stored: samples.length, already_had: 0, deleted: 0, rejected: [] };
    },
  });
  const c = await syncKind('hrv_sdnn', d);
  assert.equal(calls, 3);
  assert.deepEqual(sleeps, [120_000, 120_000]); // Retry-After, then 60 s doubled for attempt 2
  assert.equal(c.stored, 1);
  assert.equal(anchors.hrv_sdnn, 'a2');
});

test('409 retries quickly; gives up after MAX_ATTEMPTS without saving the anchor', async () => {
  const h = fakeHealth([{ samples: [reading(1)], deleted: [] }]);
  const { d, sleeps, anchors } = deps({
    query: h.query,
    upload: async () => {
      throw { status: 409, code: 'conflict' };
    },
  });
  await assert.rejects(syncKind('hrv_sdnn', d), (e: any) => e.status === 409);
  assert.equal(sleeps.length, MAX_ATTEMPTS - 1);
  assert.deepEqual(sleeps.slice(0, 3), [1000, 2000, 4000]);
  assert.equal(anchors.hrv_sdnn, undefined, 'the page is re-sent next time');
});

test('401 / 403 stop immediately, no retry, and syncAll stops too', async () => {
  for (const err of [
    { status: 401, code: 'unauthorized' },
    { status: 403, code: 'health_sync_not_enabled' },
    { status: 403, code: 'health_consent_required' },
    { status: 400, code: 'batch_too_large' },
  ]) {
    const h = fakeHealth([{ samples: [reading(1)], deleted: [] }]);
    let calls = 0;
    const { d, sleeps } = deps({
      query: h.query,
      upload: async () => {
        calls++;
        throw err;
      },
    });
    await assert.rejects(syncAll(d), (e: any) => e.code === err.code);
    assert.equal(calls, 1, `${err.code}: one attempt only`);
    assert.equal(sleeps.length, 0);
  }
});

test('rejected readings are counted, not retried, and the anchor still moves on', async () => {
  const h = fakeHealth([{ samples: [reading(1), reading(2)], deleted: [] }]);
  let calls = 0;
  const { d, anchors } = deps({
    query: h.query,
    upload: async () => {
      calls++;
      return { stored: 1, already_had: 0, deleted: 0, rejected: [{ id: uuid(2), reason: 'out_of_range' }] };
    },
  });
  const c = await syncKind('blood_glucose', d);
  assert.equal(calls, 1);
  assert.equal(c.rejected, 1);
  assert.equal(anchors.blood_glucose, 'a2');
});

test('retry delays are capped', () => {
  assert.equal(retryDelayMs({ status: 429, code: 'x' }, 4), 480_000);
  assert.equal(retryDelayMs({ status: 429, code: 'x', retryAfterSeconds: 99_999 }, 1), 3_600_000);
  assert.equal(retryDelayMs({ status: 0, code: 'network' }, 4), 80_000);
  assert.equal(retryDelayMs({ status: 503, code: 'server_error' }, 1), 10_000);
  assert.equal(retryDelayMs({ status: 503, code: 'server_error' }, MAX_ATTEMPTS), null);
});

test('syncAll runs every kind', async () => {
  const h = fakeHealth([{ samples: [reading(1)], deleted: [] }]);
  const { d, uploads } = deps({ query: h.query });
  const out = await syncAll(d);
  assert.deepEqual(Object.keys(out), ['hrv_sdnn', 'blood_glucose']);
  assert.deepEqual(
    uploads.map((u) => u.kind),
    ['hrv_sdnn', 'blood_glucose'],
  );
});

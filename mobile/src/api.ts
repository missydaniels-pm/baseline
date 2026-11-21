// Baseline /api/v1 client (CONVENTIONS "API routes"). Every response is
// {ok: true, ...} or {ok: false, code, error}; the app branches on `code`.

// Which Baseline server the app talks to. Set at build time with
// EXPO_PUBLIC_API_URL (e.g. http://localhost:5001 for the Simulator against a
// local server); anything else gets production. Not a secret.
const PRODUCTION_URL = 'https://mybaselineapp.com';
const configured = (process.env.EXPO_PUBLIC_API_URL || PRODUCTION_URL).replace(/\/+$/, '');
// A release build only ever talks https: a mis-set URL falls back to
// production rather than sending a password over plain http (code review
// 11/21/26 — ATS still allows local networking, which a dev build needs).
declare const __DEV__: boolean | undefined;
const isRelease = typeof __DEV__ !== 'undefined' && !__DEV__;
export const API_URL: string = isRelease && !configured.startsWith('https://') ? PRODUCTION_URL : configured;

export type ApiUser = {
  id: number;
  email: string;
  name: string | null;
  health_sync_enabled: boolean;
  consent_current: boolean;
  health_consent_current: boolean;
};

export type HealthConsent = {
  version: string;
  title: string;
  body: string;
  points: string[];
  agree_label: string;
  current: boolean;
  given_at: string | null;
};

export type UploadResult = {
  stored: number;
  already_had: number;
  deleted: number;
  rejected: { id: string | null; reason: string }[];
};

export class ApiError extends Error {
  status: number;                    // 0 = never reached the server
  code: string;
  retryAfterSeconds: number | null;
  constructor(status: number, code: string, message: string, retryAfterSeconds: number | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

const TIMEOUT_MS = 30_000;

async function request<T>(
  method: string,
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(API_URL + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    clearTimeout(timer);
    throw new ApiError(0, 'network', "Couldn't reach Baseline. Check your connection and try again.");
  }
  let data: any = null;
  try {
    data = await res.json();      // still under the timeout: a stalled body aborts too
  } catch {
    // Not JSON (a proxy error page, or the body timed out) — handled below by status.
  } finally {
    clearTimeout(timer);
  }
  if (res.ok && data && data.ok) return data as T;
  if (res.ok && data === null) {
    // A success whose body never arrived: treat it like a dropped connection
    // so the sync retries (re-sending a batch is harmless).
    throw new ApiError(0, 'network', "Couldn't reach Baseline. Check your connection and try again.");
  }
  const retryAfter = Number(res.headers.get('Retry-After'));
  throw new ApiError(
    res.status,
    (data && data.code) || (res.status >= 500 ? 'server_error' : 'unexpected'),
    (data && data.error) || `Baseline answered with an error (${res.status}).`,
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  );
}

export async function signIn(email: string, password: string, deviceName: string | null, timezone: string | null) {
  return request<{ token: string; expires_at: string; user: ApiUser }>('POST', '/api/v1/auth/token', null, {
    email,
    password,
    ...(deviceName ? { device_name: deviceName } : {}),
    ...(timezone ? { timezone } : {}),
  });
}

export const signOut = (token: string) => request<{}>('DELETE', '/api/v1/auth/token', token);
export const me = (token: string) => request<{ user: ApiUser }>('GET', '/api/v1/me', token);
export const syncStatus = (token: string) =>
  request<{ last_synced_at: string | null; health_consent_current: boolean }>('GET', '/api/v1/health/sync-status', token);
export const getConsent = (token: string) => request<HealthConsent>('GET', '/api/v1/health/consent', token);
export const giveConsent = (token: string, version: string) =>
  request<{ version: string; given_at: string }>('POST', '/api/v1/health/consent', token, { version });
export const withdrawConsent = (token: string) =>
  request<{ deleted: number }>('DELETE', '/api/v1/health/consent', token);

export type UploadReading = { id: string; start: string; end: string; value: number; source?: string };

export const uploadSamples = (token: string, kind: string, samples: UploadReading[], deleted: string[]) =>
  request<UploadResult>('POST', '/api/v1/health/samples', token, { kind, samples, deleted });

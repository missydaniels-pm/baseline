// Baseline Health sync — the whole app (Apple Health plan step 4).
// One job: sign in, record the Apple Health consent, ask Health for read
// permission, and sync HRV + blood glucose to Baseline. No other features until
// the React web frontend ships (BACKLOG, owner decision 11/21/26).
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Alert,
  AppState,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Device from 'expo-device';
import { getCalendars } from 'expo-localization';

import * as api from './src/api';
import { ApiError, API_URL, type ApiUser, type HealthConsent } from './src/api';
import { askHealthPermission, healthAvailable, queryPage } from './src/health';
import * as store from './src/storage';
import { KINDS, syncAll, type Counts, type Kind } from './src/syncCore';

type Screen =
  | { name: 'loading' }
  | { name: 'signIn' }
  | { name: 'error' }             // couldn't reach Baseline on launch
  | { name: 'policy' }            // privacy policy changed: re-accept on the web
  | { name: 'notEnabled' }        // the admin hasn't turned Health sync on
  | { name: 'consent'; consent: HealthConsent }
  | { name: 'notNow' }            // chose "Not now" on the consent screen
  | { name: 'status' };

const KIND_LABEL: Record<Kind, string> = { hrv_sdnn: 'Heart rate variability', blood_glucose: 'Blood glucose' };
const SETTINGS_PATH = 'iPhone Settings › Health › Data Access & Devices';
const SIGNED_OUT = 'You were signed out. Sign in again.';
const RATE_LIMIT_PAUSE_MS = 15 * 60 * 1000;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function phoneTimeZone(): string | null {
  try {
    return getCalendars()[0]?.timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}

function formatWhen(d: Date | string | null): string {
  if (!d) return 'None yet';
  return new Date(d).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function describe(c: Counts): string {
  const parts = [`${plural(c.stored, 'new reading')} added`];
  if (c.already_had) parts.push(`${c.already_had} already in Baseline`);
  if (c.deleted) parts.push(`${c.deleted} removed (deleted in Health)`);
  if (c.rejected) parts.push(`${c.rejected} skipped (values Baseline can’t store)`);
  return parts.join(' · ');
}

export default function App() {
  const [screen, setScreenRaw] = useState<Screen>({ name: 'loading' });
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<ApiUser | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<React.ComponentRef<typeof ScrollView>>(null);

  // Every screen change replaces the banner: a message belongs to the screen
  // it was shown with, never the next one (UX review).
  const go = useCallback((next: Screen, note: string | null = null) => {
    setScreenRaw(next);
    setMessage(note);
  }, []);

  // Read the banner out (VoiceOver doesn't announce new text on its own) and
  // scroll it into view.
  useEffect(() => {
    if (!message) return;
    AccessibilityInfo.announceForAccessibility(message);
    scrollRef.current?.scrollTo({ y: 0, animated: true });
  }, [message]);

  // Sync state (status screen).
  const [syncing, setSyncing] = useState(false);
  const [lastReading, setLastReading] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [counts, setCounts] = useState<Partial<Record<Kind, Counts>>>({});
  const syncingRef = useRef(false);
  // Bumped on sign-out and withdrawal: a sync still running from before must
  // not save its anchors or upload again afterwards.
  const generation = useRef(0);
  // After the server rate-limits us past every retry, don't start again until
  // this time — reopening the app would otherwise restart the retries.
  const pausedUntil = useRef(0);

  const signedOut = useCallback(
    async (why: string | null = null) => {
      generation.current++;
      syncingRef.current = false;
      setSyncing(false);
      await store.clearAll(KINDS);
      setToken(null);
      setUser(null);
      setCounts({});
      setLastReading(null);
      setLastChecked(null);
      go({ name: 'signIn' }, why);
    },
    [go],
  );

  // Decide where a signed-in user belongs, from the server's view of them.
  const route = useCallback(
    async (t: string, note: string | null = null) => {
      try {
        const { user: u } = await api.me(t);
        setUser(u);
        if (!u.consent_current) return go({ name: 'policy' }, note);
        if (!u.health_sync_enabled) return go({ name: 'notEnabled' }, note);
        if (!u.health_consent_current) {
          // No current consent means the server holds no readings (withdrawing
          // — here or on the web — deletes them all), so the next sync must be
          // a full backfill, not a resume (QA B1).
          generation.current++;
          await store.clearAnchors(KINDS);
          const consent = await api.getConsent(t);
          return go({ name: 'consent', consent }, note);
        }
        go({ name: 'status' }, note);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return signedOut(SIGNED_OUT);
        go({ name: 'error' }, e instanceof ApiError ? e.message : 'Something went wrong.');
      }
    },
    [go, signedOut],
  );

  useEffect(() => {
    (async () => {
      const t = await store.getToken();
      if (!t) return go({ name: 'signIn' });
      setToken(t);
      await route(t);
    })();
  }, [go, route]);

  const runSync = useCallback(
    async (t: string) => {
      if (syncingRef.current) return;
      if (Date.now() < pausedUntil.current) {
        setMessage(`Baseline asked the app to slow down. Sync resumes after ${formatWhen(new Date(pausedUntil.current))}.`);
        return;
      }
      const gen = generation.current;
      const stale = () => gen !== generation.current;
      syncingRef.current = true;
      setSyncing(true);
      setMessage(null);
      try {
        if (!healthAvailable()) {
          setMessage('Apple Health isn’t available on this device.');
          return;
        }
        // A second phone or a reinstall has consent on the server but has never
        // shown Apple's sheet; this only prompts when iOS hasn't asked yet (QA B2).
        await askHealthPermission();
        const result = await syncAll({
          query: queryPage,
          upload: (kind, samples, deleted) => {
            if (stale()) throw new ApiError(0, 'cancelled', 'Sync stopped.');
            return api.uploadSamples(t, kind, samples, deleted);
          },
          getAnchor: store.getAnchor,
          setAnchor: async (kind, anchor) => {
            if (!stale()) await store.setAnchor(kind, anchor);
          },
          // Wake every second so a cancelled sync stops waiting promptly.
          sleep: async (ms) => {
            const end = Date.now() + ms;
            while (!stale() && Date.now() < end) await sleep(Math.min(1000, end - Date.now()));
          },
          onProgress: (kind, c) => {
            if (!stale()) setCounts((prev) => ({ ...prev, [kind]: c }));
          },
        });
        if (stale()) return;
        setCounts(result);
        setLastChecked(new Date());
      } catch (e) {
        if (stale()) return;
        if (e instanceof ApiError) {
          if (e.status === 401) return signedOut(SIGNED_OUT);
          // Sync turned off, policy or consent changed: say why, then move on.
          if (e.status === 403) return route(t, e.message);
          if (e.status === 429) {
            pausedUntil.current = Date.now() + (e.retryAfterSeconds ? e.retryAfterSeconds * 1000 : RATE_LIMIT_PAUSE_MS);
          }
          setMessage(`${e.message} Sync will pick up where it left off.`);
        } else {
          setMessage('Couldn’t read Apple Health. Sync will try again next time.');
        }
      } finally {
        if (gen === generation.current) {
          syncingRef.current = false;
          setSyncing(false);
          try {
            const s = await api.syncStatus(t);
            if (!stale()) setLastReading(s.last_synced_at);
          } catch {
            // The status line just stays as it was.
          }
        }
      }
    },
    [route, signedOut],
  );

  // On the status screen: sync now, and again whenever the app comes back to
  // the foreground. (Background delivery is step 5 — it needs a real device.)
  useEffect(() => {
    if (screen.name !== 'status' || !token) return;
    runSync(token);
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') runSync(token);
    });
    return () => sub.remove();
  }, [screen.name, token, runSync]);

  // ── actions ──
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [agreed, setAgreed] = useState(false);

  async function doSignIn() {
    if (busy || !email.trim() || !password) return;   // the keyboard's Go key skips the button's guard
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.signIn(email.trim(), password, Device.deviceName ?? null, phoneTimeZone());
      generation.current++;
      await store.clearAnchors(KINDS);       // a fresh sign-in starts a fresh backfill
      await store.setToken(res.token);
      setPassword('');
      setToken(res.token);
      pausedUntil.current = 0;
      await route(res.token);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }

  async function doConsent(consent: HealthConsent) {
    if (!token || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.giveConsent(token, consent.version);
      // Consent is recorded before Apple's permission sheet (SPEC-health-data §8).
      await askHealthPermission();
      setAgreed(false);
      go({ name: 'status' });
    } catch (e) {
      if (e instanceof ApiError && e.code === 'consent_version_changed') {
        setAgreed(false);
        await route(token, 'The consent wording has changed. Please read it again.');
      } else if (e instanceof ApiError && e.status === 401) {
        await signedOut(SIGNED_OUT);
      } else if (e instanceof ApiError) {
        await route(token, e.message);   // e.g. sync turned off meanwhile
      } else {
        // Consent is recorded; only Apple's sheet failed. The status screen
        // shows what Health shares.
        go({ name: 'status' }, `Couldn’t open Apple Health permissions. You can allow Baseline in ${SETTINGS_PATH}.`);
      }
    } finally {
      setBusy(false);
    }
  }

  function confirmWithdraw() {
    Alert.alert(
      'Withdraw Apple Health consent?',
      'Baseline stops syncing and deletes every Health reading it has stored (encrypted backups expire within ' +
        'about 30 days). Your episodes and everything else in Baseline are not affected. This can’t be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Withdraw and delete', style: 'destructive', onPress: doWithdraw },
      ],
    );
  }

  async function doWithdraw() {
    if (!token) return;
    setBusy(true);
    generation.current++;          // stop a sync in flight from saving anchors
    syncingRef.current = false;
    setSyncing(false);
    try {
      const { deleted } = await api.withdrawConsent(token);
      await store.clearAnchors(KINDS);
      setCounts({});
      setLastReading(null);
      setLastChecked(null);
      await route(
        token,
        `Consent withdrawn. ${plural(deleted, 'reading')} deleted from Baseline. ` +
          `To also stop this app reading Health, use ${SETTINGS_PATH}.`,
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) await signedOut(SIGNED_OUT);
      else setMessage(`${e instanceof ApiError ? e.message : 'Something went wrong.'} Your consent was not withdrawn — try again.`);
    } finally {
      setBusy(false);
    }
  }

  async function doSignOut() {
    const t = token;
    await signedOut();
    if (t) {
      try {
        await api.signOut(t);
      } catch {
        // Signed out on the phone regardless; the token expires on its own.
      }
    }
  }

  async function refresh() {
    if (!token || busy) return;
    setBusy(true);
    await route(token);
    setBusy(false);
  }

  const openWeb = (path: string) => Linking.openURL(API_URL + path);

  // ── screens ──
  let body: React.ReactNode;
  switch (screen.name) {
    case 'loading':
      body = <ActivityIndicator color={C.accentLight} accessibilityLabel="Loading" />;
      break;

    case 'signIn':
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            Baseline
          </Text>
          <Text style={s.p}>
            Sign in with your Baseline account to sync Apple Health. Health sync is by invitation.
          </Text>
          <TextInput
            style={s.input}
            placeholder="Email"
            placeholderTextColor={C.muted}
            accessibilityLabel="Email"
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="email"
            keyboardType="email-address"
            textContentType="username"
            value={email}
            onChangeText={setEmail}
          />
          <TextInput
            style={s.input}
            placeholder="Password"
            placeholderTextColor={C.muted}
            accessibilityLabel="Password"
            secureTextEntry
            autoCapitalize="none"
            autoComplete="current-password"
            textContentType="password"
            value={password}
            onChangeText={setPassword}
            onSubmitEditing={doSignIn}
          />
          <Button label="Sign in" onPress={doSignIn} disabled={busy || !email.trim() || !password} busy={busy} />
          <LinkButton label="Forgot password?" onPress={() => openWeb('/forgot-password')} />
          <LinkButton label="No account? Register on the Baseline website" onPress={() => openWeb('/register')} />
        </>
      );
      break;

    case 'error':
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            Can’t reach Baseline
          </Text>
          <Text style={s.p}>Nothing is being synced right now. Check your connection and try again.</Text>
          <Button label="Try again" onPress={refresh} busy={busy} />
          <Button label="Sign out" kind="secondary" onPress={doSignOut} />
        </>
      );
      break;

    case 'policy':
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            Privacy policy updated
          </Text>
          <Text style={s.p}>
            Baseline’s privacy policy has changed. Nothing is synced until you accept it: sign in on the Baseline
            website, accept the updated policy, then come back here.
          </Text>
          <Button label="Open the Baseline website" onPress={() => openWeb('/login')} />
          <Button label="I’ve accepted it" kind="secondary" onPress={refresh} busy={busy} />
          <Button label="Sign out" kind="secondary" onPress={doSignOut} />
        </>
      );
      break;

    case 'notEnabled':
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            Health sync isn’t on yet
          </Text>
          <Text style={s.p}>
            Health sync is by invitation and isn’t turned on for {user?.email ?? 'this account'}. Nothing is being
            synced. Ask the person who invited you to turn it on, then check again.
          </Text>
          <Button label="Check again" onPress={refresh} busy={busy} />
          <Button label="Sign out" kind="secondary" onPress={doSignOut} />
        </>
      );
      break;

    case 'consent': {
      const c = screen.consent;
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            {c.title}
          </Text>
          <Text style={s.p}>{c.body}</Text>
          {c.points.map((p) => (
            <View key={p} style={s.bulletRow}>
              <Text style={s.bullet} accessible={false}>
                •
              </Text>
              <Text style={[s.p, s.bulletText]}>{p}</Text>
            </View>
          ))}
          <LinkButton label="Read Baseline’s privacy policy" onPress={() => openWeb('/privacy')} />
          <Pressable
            style={s.checkRow}
            onPress={() => setAgreed((a) => !a)}
            accessible
            accessibilityRole="checkbox"
            accessibilityLabel={c.agree_label}
            accessibilityState={{ checked: agreed }}
          >
            <View style={[s.box, agreed && s.boxOn]} importantForAccessibility="no-hide-descendants">
              {agreed && <Text style={s.tick}>✓</Text>}
            </View>
            <Text style={[s.p, s.bulletText]}>{c.agree_label}</Text>
          </Pressable>
          <Button
            label="Agree and continue"
            onPress={() => doConsent(c)}
            disabled={!agreed || busy}
            busy={busy}
            hint={agreed ? undefined : 'Tick the agreement first.'}
          />
          <Button
            label="Not now"
            kind="secondary"
            onPress={() => {
              setAgreed(false);
              go({ name: 'notNow' });
            }}
          />
        </>
      );
      break;
    }

    case 'notNow':
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            Apple Health not connected
          </Text>
          <Text style={s.p}>Nothing is being synced. Connect whenever you’re ready.</Text>
          <Button label="Connect Apple Health" onPress={refresh} busy={busy} />
          <Button label="Sign out" kind="secondary" onPress={doSignOut} />
        </>
      );
      break;

    case 'status':
      body = (
        <>
          <Text style={s.title} accessibilityRole="header">
            Apple Health sync
          </Text>
          <Text style={s.p}>Syncing heart rate variability and blood glucose for {user?.email}.</Text>
          <View style={s.card}>
            <Text style={s.label}>Last checked</Text>
            <Text style={s.value}>{syncing ? 'Syncing…' : lastChecked ? formatWhen(lastChecked) : 'Not this session'}</Text>
            <Text style={s.small}>Newest reading Baseline received: {formatWhen(lastReading)}</Text>
            {KINDS.map((k) =>
              counts[k] ? (
                <Text key={k} style={s.small}>
                  {KIND_LABEL[k]}: {describe(counts[k]!)}
                </Text>
              ) : null,
            )}
          </View>
          <Button label="Sync now" onPress={() => token && runSync(token)} disabled={syncing} busy={syncing} />
          <Text style={s.small}>
            For now the app syncs while it’s open. Baseline only reads what you allowed in Apple Health; to change
            that, use {SETTINGS_PATH}.
          </Text>
          <View style={s.spacer} />
          <Button label="Withdraw Health consent" kind="danger" onPress={confirmWithdraw} disabled={busy} />
          <Button label="Sign out" kind="secondary" onPress={doSignOut} disabled={busy} />
        </>
      );
      break;
  }

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar style="light" />
      <KeyboardAvoidingView style={s.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView ref={scrollRef} contentContainerStyle={s.container} keyboardShouldPersistTaps="handled">
          {message && <Text style={s.message}>{message}</Text>}
          {body}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Button(props: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
  hint?: string;
  kind?: 'primary' | 'secondary' | 'danger';
}) {
  const kind = props.kind ?? 'primary';
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={props.label}
      accessibilityHint={props.hint}
      accessibilityState={{ disabled: !!props.disabled, busy: !!props.busy }}
      disabled={props.disabled}
      onPress={props.onPress}
      style={({ pressed }) => [
        s.button,
        kind === 'secondary' && s.buttonSecondary,
        kind === 'danger' && s.buttonDanger,
        props.disabled ? s.buttonDisabled : pressed && s.buttonPressed,
      ]}
    >
      <View style={s.buttonInner}>
        {props.busy && <ActivityIndicator color={C.text} />}
        <Text style={[s.buttonText, kind === 'danger' && { color: C.high }]}>{props.label}</Text>
      </View>
    </Pressable>
  );
}

function LinkButton(props: { label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="link" onPress={props.onPress} style={s.linkButton} hitSlop={8}>
      <Text style={s.link}>{props.label}</Text>
    </Pressable>
  );
}

// Baseline's web palette (static/css/style.css). The primary button uses a
// darker accent than the web so its label passes WCAG AA (UX review).
const C = {
  bg: '#0f0f13',
  surface: '#1a1a24',
  border: '#2e2e3e',
  accent: '#7c5cbf',
  accentButton: '#6a49ae',
  accentLight: '#a07de0',
  text: '#e8e6f0',
  muted: '#9a9aab',
  high: '#e05252',
};

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },
  flex: { flex: 1 },
  container: { padding: 24, paddingTop: 24, gap: 14 },
  title: { color: C.text, fontSize: 26, fontWeight: '700', marginBottom: 4 },
  p: { color: C.text, fontSize: 16, lineHeight: 23 },
  small: { color: C.muted, fontSize: 14, lineHeight: 20 },
  link: { color: C.accentLight, fontSize: 15, textDecorationLine: 'underline' },
  linkButton: { minHeight: 44, justifyContent: 'center' },
  message: {
    color: C.text,
    backgroundColor: C.surface,
    borderColor: C.accent,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    fontSize: 15,
    lineHeight: 21,
  },
  input: {
    color: C.text,
    backgroundColor: C.surface,
    borderColor: C.border,
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    fontSize: 16,
  },
  button: { backgroundColor: C.accentButton, borderRadius: 10, padding: 15, alignItems: 'center', minHeight: 50 },
  buttonInner: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  buttonSecondary: { backgroundColor: 'transparent', borderWidth: 1, borderColor: C.border },
  buttonDanger: { backgroundColor: 'transparent', borderWidth: 1, borderColor: C.high },
  buttonDisabled: { opacity: 0.5 },
  buttonPressed: { opacity: 0.8 },
  buttonText: { color: C.text, fontSize: 16, fontWeight: '600' },
  bulletRow: { flexDirection: 'row', gap: 8 },
  bullet: { color: C.accentLight, fontSize: 16, lineHeight: 23 },
  bulletText: { flex: 1 },
  checkRow: { flexDirection: 'row', gap: 12, alignItems: 'flex-start', paddingVertical: 10, minHeight: 44 },
  box: {
    width: 24,
    height: 24,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: C.accentLight,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  boxOn: { backgroundColor: C.accentButton, borderColor: C.accentButton },
  tick: { color: C.text, fontWeight: '700' },
  card: { backgroundColor: C.surface, borderColor: C.border, borderWidth: 1, borderRadius: 10, padding: 16, gap: 6 },
  label: { color: C.muted, fontSize: 14, textTransform: 'uppercase', letterSpacing: 0.5 },
  value: { color: C.text, fontSize: 20, fontWeight: '600' },
  spacer: { height: 16 },
});

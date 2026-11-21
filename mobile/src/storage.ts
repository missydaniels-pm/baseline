// What the phone remembers between launches, in the iOS Keychain.
// - the sign-in token (a secret: Keychain, never AsyncStorage);
// - one HealthKit anchor per kind ("where the last sync stopped"), so each
//   sync asks Health only for readings added or deleted since then.
// AFTER_FIRST_UNLOCK so a background sync (step 5) can read them while the
// phone is locked.
import * as SecureStore from 'expo-secure-store';

const OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
};

const TOKEN = 'baseline.token';
const anchorKey = (kind: string) => `baseline.anchor.${kind}`;

export const getToken = () => SecureStore.getItemAsync(TOKEN, OPTS);
export const setToken = (t: string) => SecureStore.setItemAsync(TOKEN, t, OPTS);

export const getAnchor = (kind: string) => SecureStore.getItemAsync(anchorKey(kind), OPTS);
export const setAnchor = (kind: string, anchor: string) => SecureStore.setItemAsync(anchorKey(kind), anchor, OPTS);

export async function clearAnchors(kinds: readonly string[]) {
  await Promise.all(kinds.map((k) => SecureStore.deleteItemAsync(anchorKey(k), OPTS)));
}

// Signing out forgets the token and the anchors: whoever signs in next starts
// a fresh 1-year backfill (re-sent readings are harmless — the server upserts).
export async function clearAll(kinds: readonly string[]) {
  await SecureStore.deleteItemAsync(TOKEN, OPTS);
  await clearAnchors(kinds);
}

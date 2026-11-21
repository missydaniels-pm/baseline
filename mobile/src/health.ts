// HealthKit, through @kingstinct/react-native-healthkit (owner decision 11/21/26).
import {
  isHealthDataAvailable,
  queryQuantitySamplesWithAnchor,
  requestAuthorization,
} from '@kingstinct/react-native-healthkit';

import { PAGE, toReading, type Kind, type Page } from './syncCore';

// Each Baseline kind, its HealthKit type and the unit the server stores
// (HEALTH_SAMPLE_KINDS in database.py: ms and mg/dL — HealthKit converts).
const TYPES = {
  hrv_sdnn: { id: 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN', unit: 'ms' },
  blood_glucose: { id: 'HKQuantityTypeIdentifierBloodGlucose', unit: 'mg/dL' },
} as const;

export const healthAvailable = () => isHealthDataAvailable();

/** Shows Apple's own permission sheet (only the first time — after that iOS
 *  answers without asking, and never tells an app what was refused). Read
 *  only: Baseline never writes to Health. */
export async function askHealthPermission(): Promise<void> {
  await requestAuthorization({ toRead: [TYPES.hrv_sdnn.id, TYPES.blood_glucose.id] });
}

export async function queryPage(kind: Kind, anchor: string | null, since: Date): Promise<Page> {
  const t = TYPES[kind];
  const res = await queryQuantitySamplesWithAnchor(t.id, {
    limit: PAGE,
    unit: t.unit,
    ...(anchor ? { anchor } : {}),
    filter: { date: { startDate: since } },
  });
  return {
    samples: res.samples.map(toReading),
    deleted: res.deletedSamples.map((d) => d.uuid),
    newAnchor: res.newAnchor,
  };
}

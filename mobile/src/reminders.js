// Nightly stock-count reminder, scheduled on the phone itself (no server
// push needed): one notification at 22:00 for each of the next 14 days.
// Re-run on every app open and after a count is finished, so tonight's
// reminder is dropped once the count is done and the window keeps rolling.
import { Capacitor } from "@capacitor/core";
import { LocalNotifications } from "@capacitor/local-notifications";
import { L } from "./labels";

const DAYS_AHEAD = 14;
const BASE_ID = 22000; // ids 22000..22013, one per day

export async function scheduleCountReminders({ doneTonight, hour = 22, total }) {
  if (!Capacitor.isNativePlatform()) return { scheduled: 0, supported: false };
  try {
    let perm = await LocalNotifications.checkPermissions();
    if (perm.display !== "granted") perm = await LocalNotifications.requestPermissions();
    if (perm.display !== "granted") return { scheduled: 0, supported: true, denied: true };

    await LocalNotifications.cancel({
      notifications: Array.from({ length: DAYS_AHEAD }, (_, i) => ({ id: BASE_ID + i })),
    });

    const now = new Date();
    const notifications = [];
    for (let i = 0; i < DAYS_AHEAD; i++) {
      const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i, hour, 0, 0);
      if (at <= now) continue;               // tonight already past 10 PM
      if (i === 0 && doneTonight) continue;  // tonight's count is done
      const monday = at.getDay() === 1;
      notifications.push({
        id: BASE_ID + i,
        title: L.reminderTitle,
        body: monday ? L.reminderBodyFull : `${total ? total + " " : ""}${L.reminderBody}`,
        schedule: { at, allowWhileIdle: true },
        extra: { open: "count" },
      });
    }
    if (notifications.length) await LocalNotifications.schedule({ notifications });
    return { scheduled: notifications.length, supported: true };
  } catch (err) {
    console.warn("reminder scheduling failed", err);
    return { scheduled: 0, supported: true, error: true };
  }
}

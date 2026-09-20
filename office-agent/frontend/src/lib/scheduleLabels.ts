import type { ScheduleKind, ScheduleRule } from "../types/settings";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** "HH:MM" (24h, however the backend stores `at`) -> "9:00 AM". */
function formatTimeOfDay(at: string): string {
  const [hourStr, minuteStr] = at.split(":");
  const hour = Number(hourStr);
  const minute = Number(minuteStr);
  if (Number.isNaN(hour) || Number.isNaN(minute)) return at;
  const period = hour >= 12 ? "PM" : "AM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${hour12}:${String(minute).padStart(2, "0")} ${period}`;
}

function ordinal(day: number): string {
  const rem100 = day % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${day}th`;
  switch (day % 10) {
    case 1:
      return `${day}st`;
    case 2:
      return `${day}nd`;
    case 3:
      return `${day}rd`;
    default:
      return `${day}th`;
  }
}

/** Short, single-word badge for a tight space (sidebar row, portal card
 * corner) -- just the schedule kind, title-cased. */
export function scheduleKindLabel(kind: ScheduleKind): string {
  if (kind === "once") return "Once";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** Full sentence description, e.g. "Monthly on the 19th at 9:00 AM" --
 * matches docs/ui-references/sheduled-main-portal.png's green pill text
 * and sheduled-display-tasks.png's "Repeats" value. */
export function describeSchedule(schedule: ScheduleRule): string {
  switch (schedule.kind) {
    case "manual":
      return "Manual";
    case "once":
      return `Once, ${new Date(schedule.at).toLocaleString()}`;
    case "hourly":
      return `Hourly at :${schedule.at.split(":")[1] ?? "00"}`;
    case "daily":
      return `Daily at ${formatTimeOfDay(schedule.at)}`;
    case "weekdays":
      return `Weekdays at ${formatTimeOfDay(schedule.at)}`;
    case "weekly":
      return `Weekly on ${WEEKDAYS[schedule.weekday ?? 0]} at ${formatTimeOfDay(schedule.at)}`;
    case "monthly":
      return `Monthly on the ${ordinal(schedule.day_of_month ?? 1)} at ${formatTimeOfDay(schedule.at)}`;
    default:
      return schedule.kind;
  }
}

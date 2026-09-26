/** Local hour, not UTC -- a thread opened at 8am should say "morning"
 * regardless of what timezone the backend or server clock is in. */
function greetingForHour(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

export function EmptyState() {
  const greeting = greetingForHour(new Date().getHours());
  return (
    <div className="flex h-full items-center justify-center px-4">
      <h1 className="gradient-text text-xl font-semibold">{greeting}</h1>
    </div>
  );
}

/** The new-conversation heading above the centered message box. */
export function HomeGreeting() {
  return (
    <h1
      className="mb-7 px-4 text-center text-[40px] font-normal leading-tight text-[var(--fg)]"
      style={{ fontFamily: "Georgia, 'Times New Roman', serif" }}
    >
      {greetingForHour(new Date().getHours())}!
    </h1>
  );
}

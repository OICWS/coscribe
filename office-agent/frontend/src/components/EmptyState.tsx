/** Local hour, not UTC -- a thread opened at 8am should say "morning"
 * regardless of what timezone the backend or server clock is in. */
function greetingForHour(hour: number): string {
  if (hour < 5) return "Good night";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
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

// Each service's own app icon, bundled so the page doesn't depend on the
// network. A connector without one (a custom server) gets its first letter.
const ICONS = import.meta.glob("../../assets/connectors/*.{png,jpg}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

function iconUrl(name: string): string | undefined {
  for (const [path, url] of Object.entries(ICONS)) {
    if (path.split("/").pop()?.replace(/\.\w+$/, "") === name) return url;
  }
  return undefined;
}

export function ConnectorIcon({ name, title, size = "sm" }: { name: string; title: string; size?: "sm" | "md" | "lg" }) {
  const box = size === "lg" ? "h-16 w-16 rounded-2xl" : size === "md" ? "h-10 w-10 rounded-xl" : "h-7 w-7 rounded-lg";
  const url = iconUrl(name);
  if (url) {
    return <img src={url} alt="" className={`${box} shrink-0 border border-[var(--border)] bg-white object-cover`} />;
  }
  const text = size === "lg" ? "text-2xl" : size === "md" ? "text-base" : "text-xs";
  return (
    <span
      className={`${box} ${text} flex shrink-0 items-center justify-center border border-[var(--border)] bg-[var(--field-bg)] font-medium text-[var(--muted)]`}
    >
      {title.slice(0, 1).toUpperCase()}
    </span>
  );
}

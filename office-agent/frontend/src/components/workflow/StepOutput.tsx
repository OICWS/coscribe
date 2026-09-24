import { Fragment } from "react";
import { formatValue } from "../../lib/workflowLabels";

const MAX_ROWS = 8;
const MAX_COLUMNS = 4;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isScalar(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function Chips({ items }: { items: unknown[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item, index) => (
        <span key={index} className="rounded-[5px] bg-[var(--card-bg)] px-1.5 py-px text-[13px] tabular-nums">
          {formatValue(item)}
        </span>
      ))}
    </div>
  );
}

function Cell({ value }: { value: unknown }) {
  if (Array.isArray(value) && value.every(isScalar)) return <Chips items={value} />;
  if (typeof value === "string" && value.length > 120) {
    return <p className="whitespace-pre-wrap leading-relaxed">{value}</p>;
  }
  if (isScalar(value)) return <span className="tabular-nums">{formatValue(value)}</span>;
  return (
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[12px] text-[var(--muted)]">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function FieldGrid({ value }: { value: Record<string, unknown> }) {
  return (
    <dl className="grid grid-cols-[minmax(0,160px)_minmax(0,1fr)] overflow-hidden rounded-[10px] border border-[var(--border)] text-sm">
      {Object.entries(value).map(([key, item], index) => {
        const rule = index > 0 ? "border-t border-[var(--border)]" : "";
        return (
          <Fragment key={key}>
            <dt className={`px-3 py-2 font-mono text-[12.5px] text-[var(--muted)] ${rule}`}>{key}</dt>
            <dd className={`min-w-0 px-3 py-2 ${rule}`}>
              <Cell value={item} />
            </dd>
          </Fragment>
        );
      })}
    </dl>
  );
}

function RecordTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Object.keys(rows[0]).slice(0, MAX_COLUMNS);
  const shown = rows.slice(0, MAX_ROWS);
  return (
    <div className="overflow-x-auto rounded-[10px] border border-[var(--border)]">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="bg-[var(--card-bg)] text-xs text-[var(--muted)]">
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className="px-3 py-1.5 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, index) => (
            <tr key={index} className="border-t border-[var(--border)]">
              {columns.map((column) => (
                <td key={column} className="max-w-[360px] truncate px-3 py-1.5 tabular-nums">
                  {formatValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_ROWS && (
        <div className="border-t border-[var(--border)] px-3 py-1.5 text-xs text-[var(--muted)]">
          and {rows.length - MAX_ROWS} more
        </div>
      )}
    </div>
  );
}

/** A step's (trimmed) output, in the most readable shape for what it is. */
export function StepOutput({ value }: { value: unknown }) {
  if (isRecord(value)) return <FieldGrid value={value} />;
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="text-sm text-[var(--muted)]">Nothing found.</p>;
    if (value.every(isRecord)) return <RecordTable rows={value} />;
    return <Chips items={value} />;
  }
  if (typeof value === "string") {
    return (
      <p className="max-h-72 overflow-auto whitespace-pre-wrap rounded-[10px] border border-[var(--border)] px-3 py-2 text-sm leading-relaxed">
        {value}
      </p>
    );
  }
  return <p className="text-sm tabular-nums">{formatValue(value)}</p>;
}

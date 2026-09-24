import { controlClass } from "../../lib/formStyles";
import { OP_LABEL, operandReference } from "../../lib/workflowLabels";
import type { CheckOp, Condition, Operand } from "../../types/workflow";
import { RemoveButton } from "./fields";

type OperandMode = "ref" | "count" | "value";
type LiteralType = "text" | "number" | "boolean";

const MODE_LABEL: Record<OperandMode, string> = { ref: "Value of", count: "Count of", value: "Fixed" };
const OPS = Object.keys(OP_LABEL) as CheckOp[];

function modeOf(operand: Operand): OperandMode {
  if ("ref" in operand) return "ref";
  if ("count" in operand) return "count";
  return "value";
}

function literalTypeOf(value: unknown): LiteralType {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  return "text";
}

function OperandEditor({
  operand,
  onChange,
  valuesListId,
  label,
  defaultReference,
}: {
  operand: Operand;
  onChange: (operand: Operand) => void;
  valuesListId: string;
  label: string;
  defaultReference: string;
}) {
  const mode = modeOf(operand);
  const setMode = (next: OperandMode) => {
    if (next === mode) return;
    if (next === "value") {
      onChange({ value: "" });
      return;
    }
    const reference = operandReference(operand) ?? defaultReference;
    onChange(next === "ref" ? { ref: reference } : { count: reference });
  };

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <select
        aria-label={`${label}: kind`}
        className={`${controlClass} py-1 text-[13px]`}
        value={mode}
        onChange={(e) => setMode(e.target.value as OperandMode)}
      >
        {(Object.keys(MODE_LABEL) as OperandMode[]).map((key) => (
          <option key={key} value={key}>
            {MODE_LABEL[key]}
          </option>
        ))}
      </select>
      {"value" in operand ? (
        <LiteralEditor value={operand.value} label={label} onChange={(value) => onChange({ value })} />
      ) : (
        <input
          aria-label={`${label}: name`}
          list={valuesListId}
          spellCheck={false}
          className={`${controlClass} w-44 py-1 font-mono text-[12.5px]`}
          placeholder="name or name.field"
          value={operandReference(operand) ?? ""}
          onChange={(e) => onChange(mode === "ref" ? { ref: e.target.value.trim() } : { count: e.target.value.trim() })}
        />
      )}
    </span>
  );
}

function LiteralEditor({
  value,
  label,
  onChange,
}: {
  value: unknown;
  label: string;
  onChange: (value: unknown) => void;
}) {
  const type = literalTypeOf(value);
  const setType = (next: LiteralType) => {
    if (next === "boolean") onChange(true);
    else if (next === "number") onChange(Number(value) || 0);
    else onChange(String(value ?? ""));
  };
  return (
    <>
      <select
        aria-label={`${label}: type`}
        className={`${controlClass} py-1 text-[13px]`}
        value={type}
        onChange={(e) => setType(e.target.value as LiteralType)}
      >
        <option value="text">text</option>
        <option value="number">number</option>
        <option value="boolean">yes / no</option>
      </select>
      {type === "boolean" && (
        <select
          aria-label={`${label}: value`}
          className={`${controlClass} py-1 text-[13px]`}
          value={value ? "yes" : "no"}
          onChange={(e) => onChange(e.target.value === "yes")}
        >
          <option value="yes">yes</option>
          <option value="no">no</option>
        </select>
      )}
      {type === "number" && (
        <input
          aria-label={`${label}: value`}
          type="number"
          className={`${controlClass} w-24 py-1`}
          value={String(value)}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
      )}
      {type === "text" && (
        <input
          aria-label={`${label}: value`}
          className={`${controlClass} w-36 py-1`}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </>
  );
}

/** One editable comparison: two operands and how they must relate. */
export function ConditionRow({
  condition,
  onChange,
  onRemove,
  valuesListId,
  label,
  defaultReference,
}: {
  condition: Condition;
  onChange: (condition: Condition) => void;
  onRemove?: () => void;
  valuesListId: string;
  label: string;
  defaultReference: string;
}) {
  const setOp = (op: CheckOp) => {
    if (op === "not_empty") onChange({ ...condition, op, right: null });
    else onChange({ ...condition, op, right: condition.right ?? { value: "" } });
  };
  return (
    <div className="flex items-start gap-2 rounded-lg border border-[var(--border)] px-3 py-2">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1.5 text-sm">
        <OperandEditor
          operand={condition.left}
          onChange={(left) => onChange({ ...condition, left })}
          valuesListId={valuesListId}
          label={`${label}, left`}
          defaultReference={defaultReference}
        />
        <select
          aria-label={`${label}: comparison`}
          className={`${controlClass} py-1`}
          value={condition.op}
          onChange={(e) => setOp(e.target.value as CheckOp)}
        >
          {OPS.map((op) => (
            <option key={op} value={op}>
              {OP_LABEL[op]}
            </option>
          ))}
        </select>
        {condition.right && (
          <OperandEditor
            operand={condition.right}
            onChange={(right) => onChange({ ...condition, right })}
            valuesListId={valuesListId}
            label={`${label}, right`}
            defaultReference={defaultReference}
          />
        )}
      </div>
      {onRemove && <RemoveButton label={`Remove ${label.toLowerCase()}`} onClick={onRemove} />}
    </div>
  );
}

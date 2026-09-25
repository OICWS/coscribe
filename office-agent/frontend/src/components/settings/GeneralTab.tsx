import { useEffect, useState } from "react";
import { getProviders } from "../../lib/rest";
import { ToggleSwitch } from "../ToggleSwitch";
import { BACKGROUND_ON_CLOSE_KEY, GENERAL_FIELDS, LOG_LEVEL_KEY, LOG_LEVELS } from "./fields";
import { GlobalInstructionsSection } from "./GlobalInstructionsSection";
import { SegmentedControl } from "./SegmentedControl";
import { SettingRow, SettingRowInput } from "./SettingRow";

interface GeneralTabProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

type LogLevel = (typeof LOG_LEVELS)[number]["value"];

const DEFAULT_MODEL_KEY = "COSCRIBE_DEFAULT_MODEL";

/** "provider:model" for every provider set up with a default model. */
function useConfiguredModels(): string[] {
  const [models, setModels] = useState<string[]>([]);
  useEffect(() => {
    getProviders()
      .then((providers) =>
        setModels(
          Object.entries(providers)
            .filter(([, info]) => info.default_model)
            .map(([key, info]) => `${key}:${info.default_model}`),
        ),
      )
      .catch(() => {});
  }, []);
  return models;
}

function DefaultModelSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const models = useConfiguredModels();
  const options = value && !models.includes(value) ? [value, ...models] : models;
  return (
    <select
      aria-label="Default model"
      className="w-56 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none focus:border-[var(--accent)]"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.length === 0 && <option value="">Set up a provider first</option>}
      {options.map((model) => (
        <option key={model} value={model}>
          {model.split(":").slice(1).join(":")} · {model.split(":")[0]}
        </option>
      ))}
    </select>
  );
}

/** Coerces a stored value into one SegmentedControl actually recognizes,
 * so a case difference (config.py stores/accepts either case) or an
 * unset/unrecognized value never leaves the control with nothing
 * selected -- INFO (this app's own real default, config.py's log_level)
 * is the fallback either way. */
function currentLogLevel(raw: string | undefined): LogLevel {
  const upper = raw?.toUpperCase();
  return (LOG_LEVELS.find((level) => level.value === upper)?.value ?? "INFO") as LogLevel;
}

export function GeneralTab({ values, onChange }: GeneralTabProps) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-[var(--muted)]">
        These write to <code>.env</code>. Default Model and Max Turns apply to new sessions right away; Log Level needs
        a restart of coscribe-web. <code>HTTPS_PROXY</code>/<code>HTTP_PROXY</code> aren't configurable here.
      </p>
      <div className="flex flex-col divide-y divide-[var(--border)]">
        {GENERAL_FIELDS.map((field) => (
          <SettingRow
            key={field.key}
            label={field.label}
            description={field.description}
            control={
              field.key === DEFAULT_MODEL_KEY ? (
                <DefaultModelSelect value={values[field.key] ?? ""} onChange={(v) => onChange(field.key, v)} />
              ) : (
                <SettingRowInput
                  value={values[field.key] ?? ""}
                  placeholder={field.placeholder}
                  onChange={(v) => onChange(field.key, v)}
                />
              )
            }
          />
        ))}
        <SettingRow
          label="Log Level"
          description="How much detail coscribe-web writes to its terminal/log file -- doesn't affect what you see in the chat UI itself. Info is the right choice for normal use; switch to Debug only while troubleshooting something (e.g. a connector that won't connect), since it prints every tool call's raw arguments and every provider request. Warning/Error trim it down to problems only."
          control={
            <SegmentedControl
              value={currentLogLevel(values[LOG_LEVEL_KEY])}
              options={LOG_LEVELS}
              onChange={(v) => onChange(LOG_LEVEL_KEY, v)}
            />
          }
        />
        <SettingRow
          label="Keep Running in Background"
          description="Desktop app only. When on, closing the window keeps coscribe running so scheduled tasks continue -- reopen it from the tray icon. Turn off to fully quit when you close the window."
          control={
            <ToggleSwitch
              on={values[BACKGROUND_ON_CLOSE_KEY] !== "false"}
              onClick={() =>
                onChange(BACKGROUND_ON_CLOSE_KEY, values[BACKGROUND_ON_CLOSE_KEY] === "false" ? "true" : "false")
              }
            />
          }
        />
        <GlobalInstructionsSection active />
      </div>
    </div>
  );
}

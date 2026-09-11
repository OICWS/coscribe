import { ToggleSwitch } from "../ToggleSwitch";
import { BACKGROUND_ON_CLOSE_KEY, GENERAL_FIELDS } from "./fields";
import { GlobalInstructionsSection } from "./GlobalInstructionsSection";
import { SettingRow, SettingRowInput } from "./SettingRow";

interface GeneralTabProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

export function GeneralTab({ values, onChange }: GeneralTabProps) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-[var(--muted)]">
        These write to <code>.env</code> and require restarting coscribe-web to take effect.
        <code>HTTPS_PROXY</code>/<code>HTTP_PROXY</code> aren't configurable here.
      </p>
      <div className="flex flex-col divide-y divide-[var(--border)]">
        {GENERAL_FIELDS.map((field) => (
          <SettingRow
            key={field.key}
            label={field.label}
            description={field.description}
            control={
              <SettingRowInput
                value={values[field.key] ?? ""}
                placeholder={field.placeholder}
                onChange={(v) => onChange(field.key, v)}
              />
            }
          />
        ))}
        <SettingRow
          label="Keep Running in Background"
          description="Desktop app only. When on, closing the window keeps coscribe running so scheduled tasks continue -- reopen it from the tray icon. Turn off to fully quit when you close the window."
          control={
            <ToggleSwitch
              on={values[BACKGROUND_ON_CLOSE_KEY] !== "false"}
              onClick={() => onChange(BACKGROUND_ON_CLOSE_KEY, values[BACKGROUND_ON_CLOSE_KEY] === "false" ? "true" : "false")}
            />
          }
        />
        <GlobalInstructionsSection active />
      </div>
    </div>
  );
}

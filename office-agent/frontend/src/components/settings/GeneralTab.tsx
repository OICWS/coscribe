import { useEffect, useState } from "react";
import { getProviders } from "../../lib/rest";
import { setAppearance, useAppearance, type ChatFontChoice } from "../../lib/appearance";
import { MonitorIcon, MoonIcon, SunIcon } from "../icons";
import { ToggleSwitch } from "../ToggleSwitch";
import {
  BACKGROUND_ON_CLOSE_KEY,
  GENERAL_FIELDS,
  LOG_LEVEL_KEY,
  LOG_LEVELS,
  PERMISSION_MODE_KEY,
  PERMISSION_MODES,
} from "./fields";
import { GlobalInstructionsSection } from "./GlobalInstructionsSection";
import { SegmentedControl } from "./SegmentedControl";
import { SettingRow, SettingRows, SettingsSection, fieldClass } from "./SettingRow";

interface GeneralTabProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

type LogLevel = (typeof LOG_LEVELS)[number]["value"];
type PermissionMode = (typeof PERMISSION_MODES)[number]["value"];

const DEFAULT_MODEL_KEY = "COSCRIBE_DEFAULT_MODEL";
const MAX_TURNS_KEY = "COSCRIBE_MAX_TURNS";

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
      className={`${fieldClass} w-64`}
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

function currentPermissionMode(raw: string | undefined): PermissionMode {
  return PERMISSION_MODES.find((mode) => mode.value === raw)?.value ?? "auto";
}

const THEMES = [
  { value: "system", label: <MonitorIcon className="h-4 w-4" />, title: "Match the system" },
  { value: "light", label: <SunIcon className="h-4 w-4" />, title: "Light" },
  { value: "dark", label: <MoonIcon className="h-4 w-4" />, title: "Dark" },
] as const;

const CHAT_FONTS: { value: ChatFontChoice; label: string }[] = [
  { value: "sans", label: "IBM Plex Sans" },
  { value: "serif", label: "Source Serif" },
  { value: "system", label: "System font" },
];

const MOTION = [
  { value: "system", label: "System" },
  { value: "reduced", label: "Reduced" },
] as const;

/** Applied the moment it's picked, not with Save. */
function AppearanceSection() {
  const appearance = useAppearance();
  return (
    <SettingsSection title="Appearance">
      <SettingRows>
        <SettingRow
          label="Theme"
          control={<SegmentedControl value={appearance.theme} options={THEMES} onChange={(v) => void setAppearance("theme", v)} />}
        />
        <SettingRow
          label="Chat font"
          control={
            <select
              aria-label="Chat font"
              className={`${fieldClass} w-48`}
              value={appearance.chatFont}
              onChange={(e) => void setAppearance("chatFont", e.target.value as ChatFontChoice)}
            >
              {CHAT_FONTS.map((font) => (
                <option key={font.value} value={font.value}>
                  {font.label}
                </option>
              ))}
            </select>
          }
        />
        <SettingRow
          label="Motion"
          description="Reduce animation in streaming responses and other parts of the interface."
          control={<SegmentedControl value={appearance.motion} options={MOTION} onChange={(v) => void setAppearance("motion", v)} />}
        />
      </SettingRows>
    </SettingsSection>
  );
}

export function GeneralTab({ values, onChange }: GeneralTabProps) {
  const field = (key: string) => GENERAL_FIELDS.find((f) => f.key === key)!;
  const maxTurns = field(MAX_TURNS_KEY);
  return (
    <div className="flex flex-col gap-10">
      <AppearanceSection />

      <SettingsSection title="Model">
        <SettingRows>
          <SettingRow
            label={field(DEFAULT_MODEL_KEY).label}
            description={field(DEFAULT_MODEL_KEY).description}
            control={<DefaultModelSelect value={values[DEFAULT_MODEL_KEY] ?? ""} onChange={(v) => onChange(DEFAULT_MODEL_KEY, v)} />}
          />
          <SettingRow
            label="Default mode"
            description="The permission mode a new conversation starts in. Auto lets a reviewer model approve routine work and stop risky actions; Manual asks before every change."
            control={
              <SegmentedControl
                value={currentPermissionMode(values[PERMISSION_MODE_KEY])}
                options={PERMISSION_MODES}
                onChange={(v) => onChange(PERMISSION_MODE_KEY, v)}
              />
            }
          />
          <SettingRow
            label={maxTurns.label}
            description={maxTurns.description}
            control={
              <input
                aria-label={maxTurns.label}
                inputMode="numeric"
                className={`${fieldClass} w-24 text-right`}
                value={values[MAX_TURNS_KEY] ?? ""}
                placeholder={maxTurns.placeholder}
                onChange={(e) => onChange(MAX_TURNS_KEY, e.target.value)}
              />
            }
          />
        </SettingRows>
      </SettingsSection>

      <SettingsSection title="Instructions">
        <GlobalInstructionsSection active />
      </SettingsSection>

      <SettingsSection title="Desktop app">
        <SettingRows>
          <SettingRow
            label="Keep running in the background"
            description="Closing the window keeps coscribe in the tray, so scheduled tasks still run. Turn off to quit when you close it."
            control={
              <ToggleSwitch
                on={values[BACKGROUND_ON_CLOSE_KEY] !== "false"}
                onClick={() =>
                  onChange(BACKGROUND_ON_CLOSE_KEY, values[BACKGROUND_ON_CLOSE_KEY] === "false" ? "true" : "false")
                }
              />
            }
          />
        </SettingRows>
      </SettingsSection>

      <SettingsSection title="Advanced">
        <SettingRows>
          <SettingRow
            label="Log level"
            description="How much detail goes into coscribe's log file. Use Debug only while troubleshooting. Takes effect after a restart."
            control={
              <SegmentedControl
                value={currentLogLevel(values[LOG_LEVEL_KEY])}
                options={LOG_LEVELS}
                onChange={(v) => onChange(LOG_LEVEL_KEY, v)}
              />
            }
          />
        </SettingRows>
      </SettingsSection>
    </div>
  );
}

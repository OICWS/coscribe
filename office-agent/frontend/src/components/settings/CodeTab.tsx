import { useEffect } from "react";
import { useCodeStatus } from "../../lib/useCodeStatus";
import { useConfiguredModels } from "../../lib/useConfiguredModels";
import { CodeDownload } from "../CodeModuleStatus";
import { ToggleSwitch } from "../ToggleSwitch";
import { CODE_MODEL_KEY, CODE_MODULE_ENABLED_KEY, CODE_SEES_MEMORY_KEY } from "./fields";
import { SettingRow, SettingRows, SettingsSection, fieldClass } from "./SettingRow";

interface CodeTabProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

function CodeModelSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const models = useConfiguredModels();
  const options = value && !models.includes(value) ? [value, ...models] : models;
  return (
    <select aria-label="Model for code" className={`${fieldClass} w-64`} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Same as the default model</option>
      {options.map((model) => (
        <option key={model} value={model}>
          {model.split(":").slice(1).join(":")} · {model.split(":")[0]}
        </option>
      ))}
    </select>
  );
}

/** The code module: Codex as coscribe's second agent, in its own code
 * conversations and, when allowed, for tasks a chat hands it. How it may
 * act follows the permission mode, as a chat's tools do. */
export function CodeTab({ values, onChange }: CodeTabProps) {
  const code = useCodeStatus();
  const model = values[CODE_MODEL_KEY] ?? "";
  const { refresh } = code;
  // Settings save a moment after a change; re-check the model after that.
  useEffect(() => {
    const timer = window.setTimeout(refresh, 1200);
    return () => window.clearTimeout(timer);
  }, [model, refresh]);
  const problem = code.status?.model_problem;
  return (
    <div className="flex flex-col gap-10">
      <SettingsSection
        title="Code module"
        description="Writes and runs code in your folder: scripts that process your files, tools you'll reuse, fixes for a script that fails. It runs on Codex, downloaded the first time it's needed."
      >
        <div className="py-3.5">
          <CodeDownload code={code} />
        </div>
      </SettingsSection>

      <SettingsSection title="Code conversations">
        <SettingRows>
          <SettingRow
            label="Model for code"
            description={
              <>
                What a new code conversation starts with; each can switch from its model picker. The code module works with
                OpenAI models and providers that serve the Responses API (DeepSeek does).
                {problem && <span className="mt-1 block text-[var(--danger)]">{problem}</span>}
              </>
            }
            control={<CodeModelSelect value={model} onChange={(v) => onChange(CODE_MODEL_KEY, v)} />}
          />
          <SettingRow
            label="Use your instructions and memory"
            description="A new code conversation is told your instructions and what coscribe remembers, as a chat is. A change reaches code conversations started after it."
            control={
              <ToggleSwitch
                label="Use your instructions and memory"
                on={values[CODE_SEES_MEMORY_KEY] !== "false"}
                onClick={() => onChange(CODE_SEES_MEMORY_KEY, values[CODE_SEES_MEMORY_KEY] === "false" ? "true" : "false")}
              />
            }
          />
        </SettingRows>
      </SettingsSection>

      <SettingsSection title="In chats">
        <SettingRows>
          <SettingRow
            label="Chats can hand tasks to Code"
            description="A chat passes work that needs real code -- processing data across files, a script to reuse -- to the code module and gets its report back. You approve its commands either way."
            control={
              <ToggleSwitch
                label="Chats can hand tasks to Code"
                on={values[CODE_MODULE_ENABLED_KEY] === "true"}
                onClick={() => onChange(CODE_MODULE_ENABLED_KEY, values[CODE_MODULE_ENABLED_KEY] === "true" ? "false" : "true")}
              />
            }
          />
        </SettingRows>
      </SettingsSection>
    </div>
  );
}

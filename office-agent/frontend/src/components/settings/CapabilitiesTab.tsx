import { useEffect, useState } from "react";
import {
  getCapabilities,
  getNodeEnvPackages,
  getScriptEnvPackages,
  installNodeEnvPackage,
  installScriptEnvPackage,
  removeNodeEnvPackage,
  removeScriptEnvPackage,
  setCapability,
} from "../../lib/rest";
import type { Capabilities } from "../../types/settings";
import { ConfirmDialog } from "../ConfirmDialog";
import { ToggleSwitch } from "../ToggleSwitch";
import { CodeTab } from "./CodeTab";
import { CommandSandboxRow } from "./CommandSandboxRow";
import { PackageListSection } from "./PackageListSection";
import { PythonInterpreterSection } from "./PythonInterpreterSection";
import { ScriptIsolationCard } from "./ScriptIsolationCard";
import { SettingRow, SettingRows, SettingsSection } from "./SettingRow";

function useCapabilities(active: boolean) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (active) getCapabilities().then(setCapabilities, () => setError("Could not read the capabilities."));
  }, [active]);
  const set = (name: keyof Capabilities, on: boolean) => {
    setError(null);
    setCapability(name, on).then(setCapabilities, (err: unknown) =>
      setError(err instanceof Error ? err.message : "Could not save."),
    );
  };
  return { capabilities, error, set };
}

function ScriptsSection({ active, on, onToggle }: { active: boolean; on: boolean; onToggle: () => void }) {
  return (
    <SettingsSection
      title="Scripts"
      description="When a task is too big or too specific for the built-in tools, coscribe writes a Python or Node.js script and runs it after you approve it. On Windows a script runs isolated: it can use only the folders of its environment, can't delete or rename files there, and can't reach the rest of this computer."
    >
      <SettingRows>
        <SettingRow
          label="Run scripts"
          description="Off, scripts are refused and coscribe uses its built-in tools only."
          control={<ToggleSwitch label="Run scripts" on={on} onClick={onToggle} />}
        />
      </SettingRows>
      <div className="flex flex-col gap-6 pt-4">
        <ScriptIsolationCard active={active} />
        <PythonInterpreterSection active={active} />
        <PackageListSection
          title="Python packages"
          description="Libraries Python scripts can use."
          placeholder="Library name, e.g. numpy"
          emptyStateText="None yet -- scripts can use Python's standard library only."
          active={active}
          getPackages={getScriptEnvPackages}
          installPackage={installScriptEnvPackage}
          removePackage={removeScriptEnvPackage}
        />
        <PackageListSection
          title="Node.js packages"
          description="Libraries Node.js scripts can use, besides pptxgenjs. Node.js scripts need Node.js installed on this computer."
          placeholder="Package name, e.g. sharp"
          emptyStateText="None added beyond pptxgenjs."
          active={active}
          getPackages={getNodeEnvPackages}
          installPackage={installNodeEnvPackage}
          removePackage={removeNodeEnvPackage}
        />
      </div>
    </SettingsSection>
  );
}

function CommandsSection({
  active,
  capabilities,
  onToggle,
  onToggleWithoutSandbox,
}: {
  active: boolean;
  capabilities: Capabilities;
  onToggle: () => void;
  onToggleWithoutSandbox: () => void;
}) {
  const [sandboxReady, setSandboxReady] = useState(false);
  return (
    <SettingsSection
      title="Commands"
      description="Command lines in cmd or PowerShell, for what a script can't do: build tools, git, installers. In the command sandbox a command can write only its environment's folders. Without it, a command runs as you and can change or delete anything you can, so you approve each one, in every mode."
    >
      <SettingRows>
        <SettingRow
          label="Run commands"
          description="Off by default. Turning it on is recorded in the audit log."
          control={<ToggleSwitch label="Run commands" on={capabilities.commands} onClick={onToggle} />}
        />
        <CommandSandboxRow active={active} onChange={setSandboxReady} />
        {!sandboxReady && (
          <SettingRow
            label="Run commands without the sandbox"
            description="Off, commands are refused until the sandbox is set up."
            control={
              <ToggleSwitch
                label="Run commands without the sandbox"
                on={capabilities.commands_without_sandbox}
                onClick={onToggleWithoutSandbox}
              />
            }
          />
        )}
      </SettingRows>
    </SettingsSection>
  );
}

// Turning commands on, and then letting them run outside the sandbox, are two separate risks,
// each confirmed once (decision 0022).
const CONFIRMATIONS = {
  commands: {
    title: "Turn on commands?",
    description:
      "In the command sandbox a command can write only its environment's folders. Without it, a command runs as you: one you approve can change or delete any of your files.",
    confirmLabel: "Turn on",
  },
  commands_without_sandbox: {
    title: "Run commands without the sandbox?",
    description:
      "A command would run as you, with nothing to hold it to its folders: one you approve can change or delete any of your files and reach the internet. coscribe asks before each command, in every mode, and never lets Auto mode approve one.",
    confirmLabel: "Allow",
  },
} as const;

interface CapabilitiesTabProps {
  active: boolean;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

/** Everything coscribe may run on this computer, in one place (#588). An environment or a mode
 * can only narrow what is on here. */
export function CapabilitiesTab({ active, values, onChange }: CapabilitiesTabProps) {
  const { capabilities, error, set } = useCapabilities(active);
  const [confirm, setConfirm] = useState<keyof typeof CONFIRMATIONS | null>(null);
  if (capabilities === null) {
    return <p className="text-sm text-[var(--muted)]">{error ?? "Loading…"}</p>;
  }
  return (
    <div className="flex flex-col gap-10">
      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}
      <ScriptsSection active={active} on={capabilities.scripts} onToggle={() => set("scripts", !capabilities.scripts)} />
      <CommandsSection
        active={active}
        capabilities={capabilities}
        onToggle={() => (capabilities.commands ? set("commands", false) : setConfirm("commands"))}
        onToggleWithoutSandbox={() =>
          capabilities.commands_without_sandbox
            ? set("commands_without_sandbox", false)
            : setConfirm("commands_without_sandbox")
        }
      />
      <CodeTab values={values} onChange={onChange} />
      <SettingsSection
        title="Built-in tools"
        description="Reading and writing files, Word, Excel, PowerPoint and PDF, the web and connectors are always on. They use only the folders of a conversation's environment."
      >
        {null}
      </SettingsSection>
      {confirm && (
        <ConfirmDialog
          {...CONFIRMATIONS[confirm]}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            set(confirm, true);
          }}
        />
      )}
    </div>
  );
}

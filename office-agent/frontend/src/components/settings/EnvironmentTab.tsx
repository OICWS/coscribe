import {
  getNodeEnvPackages,
  getScriptEnvPackages,
  installNodeEnvPackage,
  installScriptEnvPackage,
  removeNodeEnvPackage,
  removeScriptEnvPackage,
} from "../../lib/rest";
import { PackageListSection } from "./PackageListSection";
import { PythonInterpreterSection } from "./PythonInterpreterSection";
import { SettingsSection } from "./SettingRow";

interface EnvironmentTabProps {
  active: boolean;
}

export function EnvironmentTab({ active }: EnvironmentTabProps) {
  return (
    <div className="flex flex-col gap-10">
      <SettingsSection
        title="Python"
        description="When a task is too big or too specific for coscribe's built-in tools, it can write and run a Python script -- you approve each one first. Scripts run in their own space, apart from coscribe, so nothing added here can break the app."
      >
        <PythonInterpreterSection active={active} />
        <PackageListSection
          title="Packages"
          description="Libraries scripts can use."
          placeholder="Library name, e.g. numpy"
          emptyStateText="None yet -- scripts can use Python's standard library only."
          active={active}
          getPackages={getScriptEnvPackages}
          installPackage={installScriptEnvPackage}
          removePackage={removeScriptEnvPackage}
        />
      </SettingsSection>
      <SettingsSection
        title="Node.js"
        description="For slide decks beyond write_pptx's layouts, coscribe can write a Node.js script with pptxgenjs (already installed) -- custom shapes, multi-column layouts, icons. You approve each script first. Needs Node.js installed on this computer."
      >
        <PackageListSection
          title="Packages"
          description="Libraries those scripts can use, besides pptxgenjs."
          placeholder="Package name, e.g. sharp"
          emptyStateText="None added beyond pptxgenjs."
          active={active}
          getPackages={getNodeEnvPackages}
          installPackage={installNodeEnvPackage}
          removePackage={removeNodeEnvPackage}
        />
      </SettingsSection>
    </div>
  );
}

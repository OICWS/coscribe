import {
  getNodeEnvPackages,
  getScriptEnvPackages,
  installNodeEnvPackage,
  installScriptEnvPackage,
  removeNodeEnvPackage,
  removeScriptEnvPackage,
} from "../../lib/rest";
import { PackageListSection } from "./PackageListSection";

interface EnvironmentTabProps {
  active: boolean;
}

export function EnvironmentTab({ active }: EnvironmentTabProps) {
  return (
    <div className="flex flex-col gap-6">
      <PackageListSection
        title="Python packages"
        description="When the assistant writes and runs a Python script for you (for a task too big or too specific for its built-in tools), it can only use libraries installed here. Add anything the assistant might need -- you'll still be asked to approve each script before it runs."
        note="This is a separate space, kept apart from coscribe itself, so installing something here never breaks the app."
        placeholder="Library name, e.g. numpy"
        emptyStateText="Nothing added yet -- the assistant's scripts can only use Python's built-in features until you add something here."
        active={active}
        getPackages={getScriptEnvPackages}
        installPackage={installScriptEnvPackage}
        removePackage={removeScriptEnvPackage}
      />
      <PackageListSection
        title="Node.js packages"
        description="For richer slide decks than write_pptx's built-in layouts can produce, the assistant can write and run a Node.js script using pptxgenjs (already installed) for custom shapes, multi-column layouts, and icons. Add anything else its scripts might need -- you'll still be asked to approve each script before it runs."
        note="Also kept separate from coscribe itself, and from this app's own frontend build -- installing something here can't break either one. Requires Node.js installed on this machine; if it isn't, adding a package here will say so."
        placeholder="Package name, e.g. sharp"
        emptyStateText="Nothing added yet beyond pptxgenjs."
        active={active}
        getPackages={getNodeEnvPackages}
        installPackage={installNodeEnvPackage}
        removePackage={removeNodeEnvPackage}
      />
    </div>
  );
}

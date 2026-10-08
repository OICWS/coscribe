import type { McpCatalogEntry, McpSetup } from "../types/settings";

/** What to tell an admin whose approval the service asks for. */
export function adminMessage(entry: McpCatalogEntry, setup: McpSetup, redirectUri: string): string {
  const title = entry.title ?? entry.name;
  const lines = [
    `Hi, I'd like to connect ${title} to coscribe, the desktop assistant I use at work.`,
    `It needs an app registered with ${setup.provider} and your approval to let me sign in with it.`,
    `App name: coscribe`,
    `Redirect address: ${redirectUri}`,
  ];
  if (setup.scopes?.length) lines.push(`Permissions requested:\n${setup.scopes.join("\n")}`);
  lines.push("It only acts as me and can see what my own account can see.");
  return lines.join("\n");
}

/** Turns what a service answered into what the person can do next. */
export function friendlySignInError(text: string, setup: McpSetup, redirectUri: string): string {
  if (/redirect[_ ]uri/i.test(text)) {
    return `${setup.provider} says the redirect address doesn't match. In the app's settings it must be exactly ${redirectUri}`;
  }
  if (/access_denied|admin|not approved|not allowed|consent/i.test(text)) {
    return `${text} If you use a work account, your administrator may need to approve the app -- use "Copy a message for your admin" under the setup steps.`;
  }
  return text;
}

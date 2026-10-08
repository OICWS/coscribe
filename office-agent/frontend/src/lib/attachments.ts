/** What an attached file looks like to the person: an image and a PDF can be
 * seen, anything else is a name and a type. */
export type AttachmentView =
  | { kind: "image"; name: string; src: string }
  | { kind: "pdf"; name: string; path: string }
  | { kind: "file"; name: string; path: string };

export function attachmentKind(name: string): "pdf" | "file" {
  return name.toLowerCase().endsWith(".pdf") ? "pdf" : "file";
}

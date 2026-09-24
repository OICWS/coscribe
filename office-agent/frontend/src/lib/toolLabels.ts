import { capitalize } from "./runLabels";

const WORD_LABELS: Record<string, string> = {
  pdf: "PDF",
  xlsx: "Excel",
  docx: "Word",
  pptx: "PowerPoint",
  python: "Python",
  node: "Node",
  xml: "XML",
  url: "URL",
  csv: "CSV",
  mcp: "MCP",
};

/** "edit_xlsx_cells" -> "Edit Excel cells". */
export function toolLabel(name: string): string {
  const words = name.split("_").filter(Boolean);
  return capitalize(words.map((w) => WORD_LABELS[w] ?? w).join(" "));
}

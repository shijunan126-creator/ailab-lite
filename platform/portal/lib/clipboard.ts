// Writing to the clipboard. Rich content goes as HTML and plain text together, so the place it is pasted picks:
// spreadsheets and documents take the HTML, plain-text and Markdown editors the text.

export type Clip = { plain: string; html?: string };

/** Without a secure context (plain http) there is no async clipboard: a selected textarea and a copy event that
 * sets every flavour stand in for it. */
function legacyCopy({ plain, html }: Clip) {
  const area = document.createElement("textarea");
  area.value = plain;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  const onCopy = (e: ClipboardEvent) => {
    if (!e.clipboardData) return;
    e.clipboardData.setData("text/plain", plain);
    if (html) e.clipboardData.setData("text/html", html);
    e.preventDefault();
  };
  document.addEventListener("copy", onCopy, true);
  try {
    area.select();
    if (!document.execCommand("copy")) throw new Error("copy refused");
  } finally {
    document.removeEventListener("copy", onCopy, true);
    area.remove();
  }
}

export async function writeClipboard(clip: Clip): Promise<void> {
  const secure = typeof window !== "undefined" && window.isSecureContext && !!navigator.clipboard;
  if (secure) {
    try {
      if (clip.html && typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
        await navigator.clipboard.write([
          new ClipboardItem({ "text/plain": new Blob([clip.plain], { type: "text/plain" }), "text/html": new Blob([clip.html], { type: "text/html" }) }),
        ]);
      } else await navigator.clipboard.writeText(clip.plain);
      return;
    } catch {
      // a refused permission or a document without focus: the legacy path may still be allowed
    }
  }
  legacyCopy(clip);
}

const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const escapeCell = (s: string) => s.replace(/\\/g, "\\\\").replace(/\|/g, "\\|");

/** A cell as read: marks that only have a label (a check, a cross) count by their label; text kept for screen readers
 * and decorations hidden from them do not count; a badge is set apart by a space. */
export function cellText(cell: Element): string {
  let out = "";
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? "";
      return;
    }
    if (!(node instanceof Element)) return;
    if (node.classList.contains("sr-only") || node.getAttribute("aria-hidden") === "true" || node.tagName === "svg") return;
    const label = node.getAttribute("aria-label");
    if (label && !(node.textContent ?? "").trim()) {
      out += label;
      return;
    }
    if (node.classList.contains("gu-badge") && out && !/\s$/.test(out)) out += " ";
    node.childNodes.forEach(walk);
  };
  cell.childNodes.forEach(walk);
  return out.replace(/\s+/g, " ").trim();
}

/** A rendered table as clipboard content: an HTML table with plain borders, and a Markdown table (unless the
 * Markdown it was written in is given, which is copied as is). Numeric columns align right in both. */
export function tableClip(table: HTMLTableElement, markdown?: string): Clip {
  const rows = Array.from(table.rows).map((row) => Array.from(row.cells).map((cell) => ({ text: cellText(cell), head: cell.tagName === "TH", num: cell.classList.contains("num") })));
  const width = Math.max(0, ...rows.map((r) => r.length));
  const headRows = table.tHead?.rows.length ?? 0;
  const right = Array.from({ length: width }, (_, i) => {
    const body = rows.slice(headRows).map((r) => r[i]).filter(Boolean);
    return body.length > 0 && body.every((c) => c.num);
  });
  const cellStyle = (i: number, head: boolean) =>
    `border:1px solid #d0d0d4;padding:4px 8px;text-align:${right[i] ? "right" : "left"}${head ? ";font-weight:600;background:#f4f4f5" : ""}`;
  const html =
    '<table style="border-collapse:collapse">' +
    rows
      .map((r, ri) => {
        const head = ri < headRows;
        return "<tr>" + r.map((c, i) => `<${c.head ? "th" : "td"} style="${cellStyle(i, head)}">${escapeHtml(c.text)}</${c.head ? "th" : "td"}>`).join("") + "</tr>";
      })
      .join("") +
    "</table>";
  if (markdown) return { plain: markdown, html };
  const line = (cells: string[]) => "| " + Array.from({ length: width }, (_, i) => escapeCell(cells[i] ?? "")).join(" | ") + " |";
  const [first, ...rest] = rows.length ? rows : [[]];
  const plain = [line(first.map((c) => c.text)), "| " + right.map((r) => (r ? "---:" : "---")).join(" | ") + " |", ...rest.map((r) => line(r.map((c) => c.text)))].join("\n");
  return { plain, html };
}

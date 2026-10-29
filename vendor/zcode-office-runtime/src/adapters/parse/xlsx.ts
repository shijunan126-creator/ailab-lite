import ExcelJS from "exceljs";
import JSZip from "jszip";
import { OfficeError, type OutlineItem } from "#office/contract.ts";
import {
  parseLocator,
  columnName,
  formatCell,
  formatLocator,
  formatSheet,
  type Locator,
} from "#office/domain/anchor.ts";
import {
  markdownTable,
  matchAll,
  paginate,
  snippet,
  type Hit,
  type ParsedDocument,
} from "#office/domain/model.ts";
import { openPackage } from "#office/adapters/parse/ooxml.ts";

const ROWS_PER_UNIT = 25;
const MAX_COLS = 60;

interface CellInfo {
  display: string;
  formula?: string;
}

function display(value: ExcelJS.CellValue): string {
  if (value === null || value === undefined) return "";
  if (value instanceof Date)
    return Number.isNaN(value.getTime()) ? "" : value.toISOString().replace(/T00:00:00\.000Z$/, "");
  if (typeof value === "object") {
    if ("richText" in value) return value.richText.map((run) => run.text).join("");
    if ("error" in value) return String(value.error);
    if ("hyperlink" in value) return String(value.text ?? value.hyperlink);
    if ("formula" in value || "sharedFormula" in value) {
      const result = (value as { result?: ExcelJS.CellValue }).result;
      return result === undefined ? "" : display(result);
    }
    return JSON.stringify(value);
  }
  return String(value);
}

function readCell(cell: ExcelJS.Cell): CellInfo {
  if (cell.isMerged && cell.master !== cell) return { display: "" };
  const formula = cell.formula || undefined;
  const value = cell.value;
  const cached =
    typeof value === "object" && value !== null && "result" in value ? value.result : undefined;
  return {
    display: display(value),
    ...(formula
      ? { formula: `=${formula}${cached === undefined ? "  → (not calculated in file)" : ""}` }
      : {}),
  };
}

interface Bounds {
  top: number;
  left: number;
  bottom: number;
  right: number;
}

function usedBounds(sheet: ExcelJS.Worksheet): Bounds | null {
  const dims = sheet.dimensions as unknown as Bounds | undefined;
  if (!dims || !sheet.actualRowCount) return null;
  return { top: dims.top, left: dims.left, bottom: dims.bottom, right: dims.right };
}

function renderRange(sheet: ExcelJS.Worksheet, bounds: Bounds, intro: string): string[] {
  // AILab: full-sheet reads must cover horizontal ranges as well as rows.
  if (bounds.right - bounds.left >= MAX_COLS) {
    const batches: string[] = [];
    for (let left = bounds.left; left <= bounds.right; left += MAX_COLS) {
      batches.push(...renderRange(sheet, {...bounds, left, right: Math.min(bounds.right, left + MAX_COLS - 1)}, intro));
    }
    return batches;
  }
  const right = bounds.right;
  const units: string[] = [];
  for (let start = bounds.top; start <= bounds.bottom; start += ROWS_PER_UNIT) {
    const end = Math.min(bounds.bottom, start + ROWS_PER_UNIT - 1);
    const header = [
      "",
      ...Array.from({ length: right - bounds.left + 1 }, (_, i) => columnName(bounds.left + i)),
    ];
    const rows: string[][] = [header];
    const formulas: string[] = [];
    for (let r = start; r <= end; r++) {
      const row = sheet.getRow(r);
      const line = [String(r)];
      for (let c = bounds.left; c <= right; c++) {
        const info = readCell(row.getCell(c));
        line.push(info.display);
        if (info.formula) formulas.push(`${formatCell({ row: r, col: c })}: ${info.formula}`);
      }
      rows.push(line);
    }
    const parts = [
      `${intro} rows ${start}–${end}${right < bounds.right ? ` (columns ${columnName(right + 1)}–${columnName(bounds.right)} omitted; read a narrower range)` : ""}`,
      markdownTable(rows),
    ];
    if (formulas.length) parts.push(`Formulas:\n${formulas.join("\n")}`);
    units.push(parts.join("\n"));
  }
  return units;
}

export async function parseXlsx(bytes: Uint8Array, label: string): Promise<ParsedDocument> {
  const pkg = await openPackage(bytes, label); // zip-bomb guard before ExcelJS inflates everything
  const packageVisuals = pkg.paths().some(p => /^xl\/(drawings|charts|media)\//.test(p));
  // Read cells independently of drawing interoperability. For example, openpyxl
  // emits an unprefixed wsDr that ExcelJS 4.4 cannot reconcile. Originals remain
  // unchanged and the system visual renderer consumes their full package.
  let textBytes = bytes;
  if (packageVisuals) {
    const textPackage = await JSZip.loadAsync(bytes);
    for (const path of pkg.paths()) if (/^xl\/(drawings|charts|media)\//.test(path)) textPackage.remove(path);
    textBytes = await textPackage.generateAsync({type:"uint8array", compression:"DEFLATE"});
  }
  const workbook = new ExcelJS.Workbook();
  try {
    await workbook.xlsx.load(
      textBytes.buffer.slice(textBytes.byteOffset, textBytes.byteOffset + textBytes.byteLength) as ArrayBuffer,
      // Drawing relationships from other producers are not consistently supported by
      // ExcelJS. Preserve them as visual coverage, never feed them to its text reader.
      {ignoreNodes: ["drawing"]},
    );
  } catch (error) {
    throw new OfficeError(
      "corrupt_file",
      `${label} could not be read as a workbook: ${(error as Error).message}`,
    );
  }
  const sheets = workbook.worksheets;
  const parseSheetAnchor = (anchor: string) => { const loc = parseLocator("xlsx", anchor); if (loc.kind !== "xlsx") throw new Error("invalid sheet"); return loc.sheet; };
  const merges = (sheet: ExcelJS.Worksheet) => (sheet.model as { merges?: string[] }).merges ?? [];

  const sheetLabel = (sheet: ExcelJS.Worksheet) => {
    const bounds = usedBounds(sheet);
    const hidden = sheet.state && sheet.state !== "visible" ? `, ${sheet.state}` : "";
    return bounds
      ? `${sheet.name} (${formatCell({ row: bounds.top, col: bounds.left })}:${formatCell({ row: bounds.bottom, col: bounds.right })}${hidden})`
      : `${sheet.name} (empty${hidden})`;
  };

  const outline: OutlineItem[] = sheets.map((sheet) => ({
    at: formatSheet(sheet.name),
    label: sheetLabel(sheet),
    level: 1,
  }));

  function findSheet(name: string) {
    const sheet =
      sheets.find((item) => item.name === name) ??
      sheets.find((item) => item.name.toLowerCase() === name.toLowerCase());
    if (!sheet)
      throw new OfficeError(
        "invalid_anchor",
        `No sheet named "${name}". Sheets: ${sheets.map((item) => item.name).join(", ")}.`,
      );
    return sheet;
  }

  function select(locator: Locator | null): string[] {
    if (!locator) {
      return sheets.flatMap((sheet) => {
        const bounds = usedBounds(sheet);
        const head = `## Sheet ${sheetLabel(sheet)}`;
        if (!bounds) return [head];
        const preview = {
          ...bounds,
          bottom: Math.min(bounds.bottom, bounds.top + ROWS_PER_UNIT - 1),
        };
        const more =
          bounds.bottom > preview.bottom
            ? `\n(${bounds.bottom - preview.bottom} more rows; read ${formatLocator({ kind: "xlsx", sheet: sheet.name })} for all)`
            : "";
        const merged = merges(sheet);
        return [
          `${head}\n${renderRange(sheet, preview, `${formatSheet(sheet.name)}`)[0]}${more}${merged.length ? `\nMerged cells: ${merged.slice(0, 30).join(", ")}` : ""}`,
        ];
      });
    }
    if (locator.kind !== "xlsx")
      throw new OfficeError("invalid_anchor", "Not a spreadsheet location.");
    const sheet = findSheet(locator.sheet);
    const used = usedBounds(sheet);
    const bounds = locator.range
      ? {
          top: locator.range.start.row,
          left: locator.range.start.col,
          bottom: locator.range.end.row,
          right: locator.range.end.col,
        }
      : used;
    if (!bounds) return [`## Sheet ${sheetLabel(sheet)}\n(empty)`];
    if (used && locator.range) {
      // Do not page through millions of empty rows when a whole column range is requested.
      bounds.bottom = Math.min(bounds.bottom, Math.max(used.bottom, bounds.top));
      bounds.right = Math.min(bounds.right, Math.max(used.right, bounds.left));
    }
    return renderRange(sheet, bounds, formatSheet(sheet.name));
  }

  return {
    kind: "xlsx",
    contentInfo(anchor) {
      const locator = parseSheetAnchor(anchor);
      const sheet = findSheet(locator);
      return {has_text: !!usedBounds(sheet), visual: sheet.getImages().length > 0 || packageVisuals};
    },
    title: workbook.title || null,
    units: sheets.length,
    outline,
    read: (locator, cursor) => paginate(select(locator), cursor),
    check(locator) {
      if (locator.kind !== "xlsx")
        throw new OfficeError("invalid_anchor", "Not a spreadsheet location.");
      return { ...locator, sheet: findSheet(locator.sheet).name };
    },
    search(query, limit) {
      const hits: Hit[] = [];
      for (const sheet of sheets) {
        sheet.eachRow({ includeEmpty: false }, (row, r) => {
          if (hits.length >= limit) return;
          row.eachCell({ includeEmpty: false }, (cell, c) => {
            if (hits.length >= limit) return;
            const info = readCell(cell);
            const text = info.formula ? `${info.display} ${info.formula}` : info.display;
            const index = matchAll(text, query)[0];
            if (index !== undefined)
              hits.push({
                at: formatLocator({
                  kind: "xlsx",
                  sheet: sheet.name,
                  range: { start: { row: r, col: c }, end: { row: r, col: c } },
                }),
                snippet: snippet(text, index, query.length),
              });
          });
        });
        if (hits.length >= limit) break;
      }
      return hits;
    },
  };
}

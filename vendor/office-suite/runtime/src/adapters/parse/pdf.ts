import { OfficeError, type OutlineItem } from "#office/contract.ts";
import type { Locator } from "#office/domain/anchor.ts";
import {
  clampRange,
  matchAll,
  normalise,
  paginate,
  snippet,
  type Hit,
  type ParsedDocument,
} from "#office/domain/model.ts";
import { cut } from "#office/domain/text.ts";
import { normalizeCjkCompat } from "#office/domain/cjk.ts";
import { installDomMatrix } from "#office/adapters/parse/dommatrix.ts";

/** Directory with pdf.js `cmaps/` and `standard_fonts/`, ending in a slash. */
export interface PdfAssets {
  cMapUrl: string;
  standardFontDataUrl: string;
  /** AILab native CLI: explicit extraction scope; absent keeps the legacy reader. */
  indexedPages?: number[];
  nativeProbe?: boolean;
  consumeBytes?: boolean;
  inspectVisuals?: boolean;
  maxNativeChars?: number;
  maxNativeBlocks?: number;
}

type PdfJs = typeof import("pdfjs-dist/legacy/build/pdf.mjs");
let pdfjs: Promise<PdfJs> | undefined;

async function loadPdfJs(): Promise<PdfJs> {
  return (pdfjs ??= (async () => {
    installDomMatrix();
    // Run the worker code on the main thread: a stdio server has no use for a separate worker,
    // and bundling keeps the worker module available without a file path.
    const worker = await import("pdfjs-dist/legacy/build/pdf.worker.mjs");
    (globalThis as { pdfjsWorker?: unknown }).pdfjsWorker = worker;
    return import("pdfjs-dist/legacy/build/pdf.mjs");
  })());
}

const MAX_PAGES_INDEXED = 3000;

export async function parsePdf(
  bytes: Uint8Array,
  label: string,
  assets: PdfAssets,
): Promise<ParsedDocument> {
  const { getDocument, OPS } = await loadPdfJs();
  let doc, loading;
  try {
    loading = getDocument({
      data: assets.consumeBytes ? bytes : bytes.slice(),
      cMapUrl: assets.cMapUrl,
      cMapPacked: true,
      standardFontDataUrl: assets.standardFontDataUrl,
      isEvalSupported: false,
      useSystemFonts: false,
      verbosity: 0,
    });
    doc = await loading.promise;
  } catch (error) {
    await loading?.destroy().catch(() => undefined);
    const name = (error as Error).name;
    throw new OfficeError(
      "corrupt_file",
      name === "PasswordException"
        ? `${label} is password protected.`
        : `${label} could not be read as a PDF: ${(error as Error).message}`,
    );
  }
  try {
    const scoped = assets.indexedPages !== undefined;
    const selected = scoped ? [...new Set(assets.indexedPages)] :
      Array.from({length: assets.nativeProbe && doc.numPages > 8 ? 0 : Math.min(doc.numPages, MAX_PAGES_INDEXED)}, (_, i) => i + 1);
    if ((scoped && selected.length > 20) || selected.some(n => !Number.isSafeInteger(n) || n < 1 || n > doc.numPages))
      throw new Error('invalid_page_selection');
    selected.sort((a,b) => a-b);
    const pages = new Map<number, string>();
    const visuals = new Map<number, boolean>();
    let chars = 0, visualUnknown = false;
    for (const n of selected) {
      const page = await doc.getPage(n);
      try {
        const content = await page.getTextContent();
        let text = "";
        for (const item of content.items) {
          if (!("str" in item)) continue;
          text += item.str + (item.hasEOL ? "\n" : "");
        }
        text = normalizeCjkCompat(text.replace(/[ \t]+\n/g, "\n").trim());
        chars += text.length;
        if (assets.maxNativeChars !== undefined && chars > assets.maxNativeChars) throw new Error('document_text_limit');
        if (assets.maxNativeBlocks !== undefined && pages.size >= assets.maxNativeBlocks) throw new Error('document_structure_limit');
        pages.set(n, text);
        if (assets.inspectVisuals !== false) {
          const operators = await page.getOperatorList();
          const images = operators.fnArray.some(op => [OPS.paintImageXObject, OPS.paintInlineImageXObject,
            OPS.paintImageMaskXObject, OPS.shadingFill].includes(op));
          // Paths can be table borders or vector diagrams. Preserve uncertainty
          // instead of automatically requiring images for every ruled table.
          const paths = operators.fnArray.some(op => op === OPS.constructPath);
          const native = assets.nativeProbe || scoped;
          visuals.set(n, images || (!native && paths));
          visualUnknown ||= !!native && paths && !images;
        } else {
          visualUnknown = true;
        }
      } finally {
        page.cleanup();
      }
    }

    const outline: OutlineItem[] = [];
    const walk = async (items: Awaited<ReturnType<typeof doc.getOutline>>, level: number) => {
      for (const item of items ?? []) {
        if (outline.length >= 200) return;
        let pageNumber: number | null = null;
        try {
          const dest =
            typeof item.dest === "string" ? await doc.getDestination(item.dest) : item.dest;
          const ref = Array.isArray(dest) ? dest[0] : null;
          if (ref && typeof ref === "object") pageNumber = (await doc.getPageIndex(ref)) + 1;
          else if (typeof ref === "number") pageNumber = ref + 1;
        } catch {
          pageNumber = null;
        }
        if (pageNumber)
          outline.push({
            at: `page:${pageNumber}`,
            label: cut(normalise(item.title), 0, 120),
            level,
          });
        if (level < 3) await walk(item.items, level + 1);
      }
    };
    await walk(await doc.getOutline(), 1);
    const info = (await doc.getMetadata().catch(() => null))?.info as
      | { Title?: string }
      | undefined;
    const numPages = doc.numPages;

    const render = (n: number) =>
      `## Page ${n}\n${pages.get(n) || "(no extractable text; the page may be scanned)"}`;
    const select = (locator: Locator | null) => {
      if (!locator) return selected.map(render);
      if (locator.kind !== "pdf") throw new OfficeError("invalid_anchor", "Not a PDF location.");
      const { from, to } = clampRange(locator.from, locator.to, numPages, "page");
      const prepared = selected.filter(n => n >= from && n <= to);
      if ((scoped || assets.nativeProbe) && prepared.length !== to-from+1)
        throw new Error('document_range_not_prepared');
      return prepared.map(render);
    };

    return {
      kind: "pdf",
      indexedPages: selected,
      coverageComplete: selected.length === numPages,
      visualStatus: selected.length !== numPages || visualUnknown ? 'unknown' : [...visuals.values()].some(Boolean) ? 'present' : 'none',
      contentInfo(anchor) { const n = Number(anchor.split(":")[1]); return {has_text: !!pages.get(n)?.trim(), visual: !!visuals.get(n)}; },
      title: info?.Title?.trim() || null,
      units: numPages,
      outline,
      read: (locator, cursor) => paginate(select(locator), cursor),
      check: (locator) => (select(locator), locator),
      search(query, limit) {
        const hits: Hit[] = [];
        pages.forEach((text, n) => {
          for (const index of matchAll(text, query)) {
            if (hits.length >= limit) return;
            hits.push({ at: `page:${n}`, snippet: snippet(text, index, query.length) });
          }
        });
        return hits;
      },
    };
  } finally {
    await doc.destroy();
  }
}

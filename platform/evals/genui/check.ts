// P6 checker using the live Portal catalog. Does not execute generated HTML.
import { readFileSync } from "node:fs";
import { createParser, createStore, evaluateRoot } from "../../portal/node_modules/@openuidev/lang-core/dist/index.mjs";
import { splitAnswerParts } from "../../portal/lib/answer-parts.ts";
import { toProgram } from "../../portal/components/genui/catalog/program.ts";
import { schemaLibrary, shapeOk, DEFINED_NAMES, MAX_BLOCK_CHARS, MAX_HTML_CHARS, MAX_FALLBACK_CHARS } from "../../portal/components/genui/catalog/schema.ts";

const parser = createParser(schemaLibrary.toJSONSchema(), "Stack");
const known = new Set(DEFINED_NAMES);
const strings = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/gs;
const file = process.argv[2];
if (!file) throw new Error("Usage: node platform/evals/genui/check.ts <answers.jsonl>");
for (const line of readFileSync(file, "utf8").split("\n").filter(Boolean)) {
  const row = JSON.parse(line);
  const blocks = splitAnswerParts(row.answer).filter((p) => p.kind === "ui").map((part) => {
    const bare = part.text.replace(strings, '""');
    const calls = [...bare.matchAll(/(?<![\w@])([A-Za-z_][A-Za-z_0-9]*)\s*\(/g)].map((m) => m[1]);
    const components = [...new Set(calls.filter((n) => known.has(n)))];
    const unknownComponents = [...new Set(calls.filter((n) => !known.has(n)))];
    const result: any = { index: part.index, closed: part.closed, chars: [...part.text].length,
      bytes: Buffer.byteLength(part.text), withinBlockLimit: [...part.text].length <= MAX_BLOCK_CHARS, components, unknownComponents, rootPresent: false, errors: [], html: [], data: [] };
    try {
      const parsed = parser.parse(toProgram(part.text));
      // any component may be the root; the Portal renders `root = Ranking(...)` as it is
      result.rootPresent = /^\s*root\s*=/m.test(bare) && typeof parsed.root?.typeName === "string";
      result.errors = parsed.meta.errors;
      result.incomplete = parsed.meta.incomplete;
      result.statementCount = parsed.meta.statementCount;
      result.unresolved = parsed.meta.unresolved;
      result.orphaned = parsed.meta.orphaned;
      result.state = parsed.stateDeclarations;
      const store = createStore();
      store.initialize(parsed.stateDeclarations, {});
      const runtimeErrors: any[] = [];
      result.propViolations = [];
      result.malformedData = [];
      result.conclusions = [];
      const root = evaluateRoot(parsed, { library: schemaLibrary, store, ctx: { getState: (n: string) => store.get(n), resolveRef: () => undefined }, errors: runtimeErrors });
      result.runtimeErrors = runtimeErrors;
      function walk(value: any): void {
        if (Array.isArray(value)) return value.forEach(walk);
        if (!value || typeof value !== "object") return;
        if (value.type === "element") {
          const p = value.props ?? {};
          // the renderer's own check: what it would show as "数据格式有误"
          if (!shapeOk(value.typeName, p)) result.malformedData.push({ component: value.typeName, props: p });
          if (["Text", "Callout", "Highlights"].includes(value.typeName)) result.conclusions.push({ component: value.typeName, props: p });
          const decimals = p.opts?.decimals;
          if (decimals !== undefined && decimals !== null && (!Number.isInteger(decimals) || decimals < 0 || decimals > 4))
            result.propViolations.push(`${value.typeName}: decimals must be an integer from 0 to 4`);
          if (value.typeName === "HtmlArtifact") {
            const doc = typeof p.document === "string" ? p.document : "";
            const fallback = typeof p.fallbackText === "string" ? p.fallbackText : "";
            // SVG namespace declarations are identifiers, not resource requests; keep the literal alert separately.
            const networkDoc = doc.replace(/(?:createElementNS\(\s*|(?:var|let|const)\s+\w+\s*=\s*)["']http:\/\/www\.w3\.org\/2000\/svg["']/g, "");
            result.html.push({ documentNonempty: !!doc.trim(), fallbackNonempty: !!fallback.trim(), documentChars: [...doc].length, fallbackChars: [...fallback].length, withinLimits: doc.length <= MAX_HTML_CHARS && fallback.length <= MAX_FALLBACK_CHARS,
              violations: [ /https?:\/\//i.test(doc) ? "http(s) reference" : null, /\bfetch\b/.test(doc) ? "fetch" : null,
                /\blocalStorage\b/.test(doc) ? "localStorage" : null ].filter(Boolean),
              networkViolations: [ /https?:\/\//i.test(networkDoc) ? "http(s) reference" : null, /\bfetch\b/.test(doc) ? "fetch" : null,
                /\blocalStorage\b/.test(doc) ? "localStorage" : null ].filter(Boolean),
              themeFg: /var\(\s*--fg\s*[,)]/.test(doc), themeAccent: /var\(\s*--accent\s*[,)]/.test(doc), fallbackText: fallback });
          } else {
            if (!["Stack", "Grid", "Card", "Tabs", "Tab", "Details", "Divider", "Heading", "Text", "Callout", "Badge", "Quote", "Series", "Button"].includes(value.typeName))
              result.data.push({ component: value.typeName, props: p });
            Object.values(p).forEach(walk);
          }
        }
      }
      walk(root);
      const initialData = result.data;
      const initialHtml = result.html;
      const initialConclusions = result.conclusions;
      const initialMalformed = result.malformedData;
      result.probes = [];
      for (const control of initialData.filter((d: any) => ["Select", "Slider", "Toggle"].includes(d.component))) {
        const p = control.props;
        const target = p.value?.target;
        if (!target) continue;
        const value = control.component === "Select" ? p.options.at(-1) : control.component === "Slider" ? p.max : !store.get(target);
        for (const [name, initial] of Object.entries(parsed.stateDeclarations)) store.set(name, initial);
        store.set(target, value);
        result.data = [];
        result.html = [];
        result.conclusions = [];
        result.malformedData = [];
        const errors: any[] = [];
        walk(evaluateRoot(parsed, { library: schemaLibrary, store, ctx: { getState: (n: string) => store.get(n), resolveRef: () => undefined }, errors }));
        result.probes.push({ target, value, data: result.data, conclusions: result.conclusions, malformedData: result.malformedData, errors });
      }
      result.data = initialData;
      result.html = initialHtml;
      result.conclusions = initialConclusions;
      result.malformedData = initialMalformed;
      result.displaysCorrectly = !initialMalformed.length && result.probes.every((p: any) => !p.malformedData.length);
      store.dispose();
      result.safe = result.html.every((h: any) => !h.networkViolations.length);
      result.valid = part.closed && result.withinBlockLimit && result.rootPresent && !result.incomplete && !result.errors.length &&
        !result.unresolved.length && !unknownComponents.length && !runtimeErrors.length && !result.propViolations.length &&
        result.statementCount <= 60 &&
        result.html.every((h: any) => h.documentNonempty && h.fallbackNonempty && h.withinLimits);
    } catch (error) {
      result.errors.push({ message: error instanceof Error ? error.message : String(error) });
      result.valid = false;
    }
    return result;
  });
  console.log(JSON.stringify({ case: row.case, kind: row.kind, turn: row.turn, error: row.error, blocks }));
}

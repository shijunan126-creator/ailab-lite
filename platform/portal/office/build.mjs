// Builds the side-pane document viewer (app/frame/office) from the vendored office runtime UI.
// The upstream shell and viewers are bundled as they are; office/host.ts, office/assets.ts, office/profile.ts and a
// stub of @modelcontextprotocol/ext-apps replace the desktop transport, and PATCHES below are the only source edits.
// Output: office/dist/ (git-ignored), served by app/office-viewer/[...path].
import { build, transform } from "esbuild";
import { brotliCompressSync, gzipSync, constants } from "node:zlib";
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const portal = resolve(here, "..");
const runtime = resolve(process.env.OFFICE_RUNTIME_SRC || join(portal, "../../vendor/office-suite/upstream/office-runtime"));
const dist = join(here, "dist");
const require = createRequire(join(portal, "package.json"));
const pdfjsRoot = dirname(require.resolve("pdfjs-office/package.json"));
const xlsxRoot = dirname(dirname(require.resolve("@extend-ai/react-xlsx")));
const cjkFontRoot = dirname(require.resolve("@fontsource/noto-sans-sc/package.json"));

/** Text edits to upstream files: [file suffix, needle, replacement, reason]. A missing needle fails the build. */
export const PATCHES = [
  [
    "ui/main.tsx",
    'if (path) void load(path);\n      else for (const result of buffered)',
    'const reveal = buffered.findLast((result) => !result.isError && (result.structuredContent as ViewerSnapshot | undefined)?.reveal && (!path || (result.structuredContent as ViewerSnapshot).document?.path === path));\n      if (reveal) applySnapshot(reveal.structuredContent as ViewerSnapshot);\n      else if (path) void load(path);\n      else for (const result of buffered)',
    "An authorized live reveal must survive startup; restoring the same document path must not discard its buffered targets.",
  ],
  [
    "ui/viewers/XlsxView.tsx",
    'formula && single ? `${displayValue} (=${formula.replace(/^=/, "")})` : displayValue,',
    'formula ? `=${formula.replace(/^=/, "")}` : displayValue,',
    "Quote source formulas consistently with server validation; browser-calculated results may have no cached source value.",
  ],
  [
    "ui/main.tsx",
    'const result = await host.app.sendMessage({\n          role: "user",',
    'const result = await host.app.sendMessage({\n          ailabRef: { question, anchor, text },\n          role: "user",',
    "Ask sends the selection as a document reference through the Portal outbox, not only as prose.",
  ],
  [
    "ui/viewers/PdfView.tsx",
    'new Worker(url, { type: "module" })',
    "new Worker(url)",
    "Module workers from blob URLs do not start in an opaque-origin frame; the pdf.js worker is built as a classic script.",
  ],
  [
    "ui/main.tsx",
    "const current = docRef.current;\n    if (!current || !picked) return;\n    const anchor = formatAnchor(current.info.path, picked.selection.at);\n    const text = picked.selection.text;\n    const next = [",
    "const current = docRef.current;\n    if (!current || !picked || !host.capabilities?.message) return;\n    const anchor = formatAnchor(current.info.path, picked.selection.at);\n    const text = picked.selection.text;\n    const next = [",
    "Without a conversation (library, shares) there is nothing to add a reference to; ⌘/Ctrl+Enter does nothing.",
  ],
  [
    "ui/main.tsx",
    "{doc && ready && !failure && picked?.detail.user ? (",
    "{doc && ready && !failure && picked?.detail.user && host.capabilities?.message ? (",
    "Without a conversation the selection bar would offer actions that cannot work; the text layer still copies natively.",
  ],
  [
    "ui/main.tsx",
    "const mine = ++loadSeq.current;",
    "const mine = ++loadSeq.current;\n      let revision: string | undefined;",
    "Remember which version a load was for, so its failure can be reported against that version.",
  ],
  [
    "ui/main.tsx",
    '}>("load_document", { path: target });',
    '}>("load_document", { path: target });\n        revision = loaded.info.revision;',
    "Remember which version a load was for, so its failure can be reported against that version.",
  ],
  [
    "ui/main.tsx",
    "setFailure(describeFailure(t, error, target));\n      } finally {",
    'host.fail("load", error, revision);\n        setFailure(describeFailure(t, error, target));\n      } finally {',
    "A failed byte read or renderer prepare is terminal for that version: the Portal shows its paged preview or the source error.",
  ],
  [
    "ui/main.tsx",
    "onError: (error: Error) =>\n              setFailure(describeFailure(t, error, docRef.current?.info.path ?? null)),",
    'onError: (error: Error) => {\n              host.fail("render", error, docRef.current?.info.revision);\n              setFailure(describeFailure(t, error, docRef.current?.info.path ?? null));\n            },',
    "A renderer that cannot draw the document is terminal for that version; the Portal shows its paged preview instead.",
  ],
  ["ui/i18n.ts", 'contextFailed: "ZCode did not accept the reference.",', 'contextFailed: "The reference was not added.",', "Host-neutral wording."],
  ["ui/i18n.ts", 'contextFailed: "ZCode 未接受该引用。",', 'contextFailed: "未能添加引用。",', "Host-neutral wording."],
  [
    "ui/i18n.ts",
    '  agent: "Agent",\n  agentOf: (index, total) => `Agent · 第 ${index} 处，共 ${total} 处`,',
    '  agent: "AILab",\n  agentOf: (index, total) => `AILab · 第 ${index} 处，共 ${total} 处`,',
    "The Portal names the assistant AILab in Chinese UI.",
  ],
  [
    "ui/main.tsx",
    '      ) : (\n        <header className="docbar">',
    '      ) : host.titleBar === "portal" ? null : (\n        <header className="docbar">',
    "The Portal's title bar already names the file and holds the document controls; a second bar repeated the name and stacked two rows of look-alike icons.",
  ],
  [
    "ui/main.tsx",
    "  useShellKeys({\n    docRef,",
    "  useEffect(\n    () =>\n      host.onCommand((name) => {\n        if (name === \"drawer\") drawerAvailable && setDrawer((state) => ({ ...state, open: !state.open }));\n        else if (find.open) closeFind();\n        else openFind();\n      }),\n    [drawerAvailable, find.open, openFind, closeFind],\n  );\n  const findAvailable = Boolean(doc && ready && !failure);\n  useEffect(() => {\n    host.reportTools({ drawer: drawerAvailable ? drawer.open : null, find: findAvailable ? find.open : null });\n  }, [drawerAvailable, drawer.open, findAvailable, find.open]);\n  useShellKeys({\n    docRef,",
    "The pages/outline drawer and find are pressed from the Portal's title bar, which shows whether each applies and is on.",
  ],
];

const ui = (name) => join(runtime, "ui", name);
let format;
const adapters = {
  name: "ailab-office-adapters",
  setup(builder) {
    const swap = { "#ui/host.ts": join(here, "host.ts"), "#ui/assets.ts": join(here, "assets.ts"), "#office/profile.ts": join(here, "profile.ts") };
    builder.onResolve({ filter: /^#(ui\/(host|assets)|office\/profile)\.ts$/ }, (args) => ({ path: swap[args.path] }));
    builder.onResolve({ filter: /^@modelcontextprotocol\/ext-apps$/ }, () => ({ path: join(here, "ext-apps.ts") }));
    // The viewer is pinned to the pdf.js release it was written against; the Portal's own PDF preview uses another.
    builder.onResolve({ filter: /^pdfjs-dist(\/.*)?$/ }, (args) => ({ path: require.resolve(args.path.replace(/^pdfjs-dist/, "pdfjs-office")) }));
    builder.onLoad({ filter: /[\\/]ui[\\/].*\.tsx?$/ }, async (args) => {
      if (!args.path.startsWith(join(runtime, "ui"))) return undefined;
      // Upstream's internal names (font families, a result meta key) carry its product prefix; the bundle uses a neutral one.
      let source = (await readFile(args.path, "utf8")).replace(/\bzcode(?=[-/])/g, "office");
      // Upstream tags registry lines for per-format plugins.
      if (args.path.endsWith("viewers/registry.ts"))
        source = source.split("\n").filter((line) => !line.includes("// kind:") || line.includes(`// kind:${format}`)).join("\n");
      for (const [file, needle, replacement] of PATCHES) {
        if (!args.path.replaceAll("\\", "/").endsWith(file)) continue;
        if (!source.includes(needle)) throw new Error(`office patch for ${file} no longer applies: ${needle.slice(0, 60)}`);
        source = source.replace(needle, () => replacement);
      }
      return { contents: source, loader: args.path.endsWith(".tsx") ? "tsx" : "ts" };
    });
  },
};

// The runtime's own build (scripts/build.mjs) makes the same two library edits: the xlsx worker comes from a blob URL
// the page creates, and pdf.js asks ui/cjkFontRuntime.ts for the font chain of non-embedded CJK text. Here the worker
// is also a classic script (see the PdfView patch), and substituted standard fonts load through office/pdf-fonts.ts.
const xlsxWorkerUrl = {
  name: "xlsx-worker-url",
  setup(builder) {
    builder.onLoad({ filter: /@extend-ai[\\/]react-xlsx[\\/]dist[\\/]index\.js$/ }, async (args) => {
      const source = await readFile(args.path, "utf8");
      const needle = 'new Worker(new URL("./xlsx-worker.js", import.meta.url), { type: "module" })';
      if (!source.includes(needle)) throw new Error("react-xlsx worker URL not found; update the build patch");
      return { contents: source.replace(needle, "new Worker(globalThis.__officeXlsxWorkerUrl)"), loader: "js" };
    });
  },
};
const pdfjsCanvasFonts = {
  name: "pdfjs-canvas-fonts",
  setup(builder) {
    builder.onLoad({ filter: /pdfjs-office[\\/]build[\\/]pdf\.mjs$/ }, async (args) => {
      const source = await readFile(args.path, "utf8");
      const chain = 'fontObj.systemFontInfo?.css || `"${name}", ${fontObj.fallbackName}`';
      const needle = `const typeface = ${chain};`;
      if (!source.includes(needle)) throw new Error("pdf.js canvas font chain not found; update the build patch");
      const face = "const fontFace = new FontFace(loadedName, src, style);";
      if (!source.includes(face)) throw new Error("pdf.js system font face not found; update the build patch");
      const hook = JSON.stringify(ui("cjkFontRuntime.ts").replaceAll("\\", "/"));
      const fonts = JSON.stringify(join(here, "pdf-fonts.ts").replaceAll("\\", "/"));
      const patched = source
        .replace(needle, () => `const typeface = __officePdfTypeface(fontObj, ${chain});`)
        .replace(face, () => "const fontFace = new FontFace(loadedName, await __officeFontSrc(src), style);");
      return {
        contents: `import { pdfTypeface as __officePdfTypeface } from ${hook};\nimport { officeFontSrc as __officeFontSrc } from ${fonts};\n${patched}`,
        loader: "js",
      };
    });
  },
};

const common = {
  bundle: true,
  platform: "browser",
  target: "es2022",
  minify: true,
  legalComments: "none",
  define: { "process.env.NODE_ENV": '"production"' },
  nodePaths: [join(portal, "node_modules")],
  write: false,
  metafile: true,
  logOverride: { "direct-eval": "silent" },
};

await rm(dist, { recursive: true, force: true });
await mkdir(join(dist, "fonts"), { recursive: true });

for (const folder of ["cmaps", "standard_fonts", "wasm"])
  await cp(join(pdfjsRoot, folder), join(dist, "pdfjs", folder), { recursive: true, filter: (path) => !path.endsWith(".js") });
const pdfWorker = await build({ ...common, entryPoints: [join(pdfjsRoot, "build/pdf.worker.mjs")], format: "iife" });
await writeFile(join(dist, "pdf-worker.js"), pdfWorker.outputFiles[0].contents);

const faces = [];
const fontCss = await readFile(join(cjkFontRoot, "400.css"), "utf8");
for (const [, file, unicodeRange] of fontCss.matchAll(/url\(\.\/files\/(noto-sans-sc-\d+-400-normal\.woff2)\)[^;]*;\s*unicode-range:\s*([^;]+);/g)) {
  await cp(join(cjkFontRoot, "files", file), join(dist, "fonts", file));
  faces.push({ file: `fonts/${file}`, unicodeRange });
}
if (faces.length < 50) throw new Error(`Noto Sans SC: only ${faces.length} slices; check 400.css`);
await writeFile(join(dist, "cjk-fallback.json"), JSON.stringify({ faces }));

const xlsxWorker = await build({ ...common, entryPoints: [join(xlsxRoot, "dist/xlsx-worker.js")], format: "iife" });
await writeFile(join(dist, "xlsx-worker.js"), xlsxWorker.outputFiles[0].contents);
await cp(join(xlsxRoot, "dist/duke_sheets_wasm_bg.wasm"), join(dist, "sheets.wasm"));

const apps = [];
for (format of ["docx", "xlsx", "pptx", "pdf"]) {
  const app = await build({ ...common, plugins: [adapters, xlsxWorkerUrl, pdfjsCanvasFonts], entryPoints: [ui("main.tsx")], format: "iife", outdir: dist });
  apps.push(app);
  await writeFile(join(dist, `viewer-${format}.js`), app.outputFiles.find((file) => file.path.endsWith(".js")).contents);
}
const css = await transform((await readFile(ui("panel.css"), "utf8")).replace(/\bzcode(?=[-/])/g, "office"), { loader: "css", minify: true, legalComments: "none" });
await writeFile(join(dist, "panel.css"), css.code);

for (const file of ["viewer-docx.js", "viewer-xlsx.js", "viewer-pptx.js", "viewer-pdf.js", "panel.css"])
  if (/zcode/i.test(await readFile(join(dist, file), "utf8"))) throw new Error(`upstream product name left in ${file}; extend the rename`);

// Licences of every bundled package plus the data files copied verbatim.
const notices = new Map();
for (const input of [...apps.map((app) => app.metafile), xlsxWorker.metafile, pdfWorker.metafile].flatMap((meta) => Object.keys(meta.inputs))) {
  if (!input.includes("node_modules/")) continue;
  let directory = dirname(resolve(portal, input));
  while (directory.includes("node_modules")) {
    try {
      const pkg = JSON.parse(await readFile(join(directory, "package.json"), "utf8"));
      if (pkg.name) {
        notices.set(`${pkg.name}@${pkg.version}`, { directory, pkg });
        break;
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    directory = dirname(directory);
  }
}
const parts = [`office runtime UI (${runtime.split("/").slice(-1)[0]}) — MIT\n${await readFile(join(runtime, "LICENSE"), "utf8")}`];
for (const [key, { directory, pkg }] of [...notices].sort()) {
  const files = (await readdir(directory)).filter((name) => /^(license|licence|copying|notice)([.-]|$)/i.test(name));
  const texts = await Promise.all(files.map((file) => readFile(join(directory, file), "utf8")));
  if (!texts.length && !pkg.license) throw new Error(`Missing license for bundled dependency ${key}`);
  parts.push(`${key} — ${typeof pkg.license === "string" ? pkg.license : JSON.stringify(pkg.license)}\n${texts.join("\n\n")}`);
}
parts.push(`@fontsource/noto-sans-sc (fonts/) — OFL-1.1\n${await readFile(join(cjkFontRoot, "LICENSE"), "utf8")}`);
await writeFile(join(dist, "THIRD_PARTY_NOTICES.txt"), parts.join("\n\n----------------------------------------\n\n"));

// Include workers, wasm and fonts in the version: immutable URLs must change with any asset byte.
const digest = createHash("sha256");
async function finish(directory, prefix = "") {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) { await finish(join(directory, entry.name), name + "/"); continue; }
    const bytes = await readFile(join(directory, entry.name));
    digest.update(name).update(bytes);
    // Compression costs build CPU only; requests only read the selected representation.
    if (/\.(js|css|json|wasm|txt|bcmap)$/.test(name)) {
      await writeFile(join(directory, entry.name + ".gz"), gzipSync(bytes, { level: 9 }));
      await writeFile(join(directory, entry.name + ".br"), brotliCompressSync(bytes, { params: { [constants.BROTLI_PARAM_QUALITY]: 9 } }));
    }
  }
}
await finish(dist);
const version = digest.digest("hex").slice(0, 16);
await writeFile(join(dist, "manifest.json"), JSON.stringify({ version }));
process.stdout.write(`office viewer ${version}: four format bundles\n`);

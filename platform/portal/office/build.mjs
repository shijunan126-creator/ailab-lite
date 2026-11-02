// Builds the side-pane document viewer (app/frame/office) from the vendored office runtime UI.
// The upstream shell and viewers are bundled as they are; office/host.ts, office/assets.ts, office/profile.ts and a
// stub of @modelcontextprotocol/ext-apps replace the desktop transport, and PATCHES below are the only source edits.
// Output: office/dist/ (git-ignored), served by app/office-viewer/[...path].
import { build, transform } from "esbuild";
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
  ["ui/i18n.ts", 'contextFailed: "ZCode did not accept the reference.",', 'contextFailed: "The reference was not added.",', "Host-neutral wording."],
  ["ui/i18n.ts", 'contextFailed: "ZCode 未接受该引用。",', 'contextFailed: "未能添加引用。",', "Host-neutral wording."],
  [
    "ui/i18n.ts",
    '  agent: "Agent",\n  agentOf: (index, total) => `Agent · 第 ${index} 处，共 ${total} 处`,',
    '  agent: "AILab",\n  agentOf: (index, total) => `AILab · 第 ${index} 处，共 ${total} 处`,',
    "The Portal names the assistant AILab in Chinese UI.",
  ],
];

const ui = (name) => join(runtime, "ui", name);
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

const app = await build({
  ...common,
  plugins: [adapters, xlsxWorkerUrl, pdfjsCanvasFonts],
  entryPoints: [ui("main.tsx")],
  format: "iife",
  outdir: dist,
});
const js = app.outputFiles.find((file) => file.path.endsWith(".js")).contents;
await writeFile(join(dist, "viewer.js"), js);
const css = await transform((await readFile(ui("panel.css"), "utf8")).replace(/\bzcode(?=[-/])/g, "office"), { loader: "css", minify: true, legalComments: "none" });
await writeFile(join(dist, "panel.css"), css.code);

for (const file of ["viewer.js", "panel.css"])
  if (/zcode/i.test(await readFile(join(dist, file), "utf8"))) throw new Error(`upstream product name left in ${file}; extend the rename`);

// Licences of every bundled package plus the data files copied verbatim.
const notices = new Map();
for (const input of [app.metafile, xlsxWorker.metafile, pdfWorker.metafile].flatMap((meta) => Object.keys(meta.inputs))) {
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

// The version names the asset URL so the browser can cache every file as immutable.
const version = createHash("sha256").update(js).update(await readFile(join(dist, "panel.css"))).digest("hex").slice(0, 16);
await writeFile(join(dist, "manifest.json"), JSON.stringify({ version, viewerBytes: js.length }));
process.stdout.write(`office viewer ${version}: ${(js.length / 1024 / 1024).toFixed(2)} MiB\n`);

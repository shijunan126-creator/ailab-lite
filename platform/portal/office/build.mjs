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
  ["ui/viewers/PptxView.tsx", "          if (!entry.isIntersecting) continue;\n          const index = slots.current.findIndex((slot) => slot.frame === entry.target);", "          const index = slots.current.findIndex((slot) => slot.frame === entry.target);\n          if (!entry.isIntersecting) {const slot = slots.current[index]; if (slot) {slot.handle?.dispose(); slot.handle = undefined; slot.ready = undefined; slot.overlay.replaceChildren(); find.current = null;} continue;}", "Release slides outside the visible neighbor window; visiting pages must not retain every rendered DOM tree."],
  ["ui/viewers/PptxView.tsx", "        await Promise.all(slots.current.map((_, i) => ensureRendered(i)));", "        if (slots.current.length > 20) throw new Error(\"大文档请使用文档搜索定位\");\n        for (let i = 0; i < slots.current.length; i++) await ensureRendered(i);", "Do not render an entire large presentation concurrently for a browser text search."],
  ["ui/viewers/types.ts", "thumbnail?(page: number, width: number): Promise<HTMLElement>;", "thumbnail?(page: number, width: number, signal?: AbortSignal): Promise<HTMLElement>;", "The visible thumbnail slot owns its decoded resources."],
  ["ui/viewers/PptxView.tsx", "async thumbnail(page, thumbWidth) {", "async thumbnail(page, thumbWidth, signal) {\n        signal?.throwIfAborted();", "Retire pending and ready PowerPoint thumbnail handles with their slot."],
  ["ui/viewers/PptxView.tsx", "        thumbs.current.set(page, { element, handle });\n        await handle.ready.catch(() => undefined);", "        thumbs.current.set(page, { element, handle });\n        const abort = () => {if (thumbs.current.get(page)?.handle === handle) thumbs.current.delete(page); handle.dispose(); element.replaceChildren();};\n        signal?.addEventListener(\"abort\", abort, {once: true});\n        await handle.ready.catch(() => undefined);\n        signal?.throwIfAborted();", "Keep only thumbnail handles whose visible slot still owns them."],
  ["ui/viewers/PdfView.tsx", "async thumbnail(page, width) {", "async thumbnail(page, width, signal) {\n        signal?.throwIfAborted();", "PDF thumbnail rendering obeys visibility cancellation."],
  ["ui/viewers/PdfView.tsx", "        const ratio = window.devicePixelRatio || 1;\n        const viewport = pdfPage.getViewport({ scale: (width / base.width) * ratio });", "        const ratio = Math.min(window.devicePixelRatio || 1, 2);\n        const viewport = pdfPage.getViewport({ scale: Math.min((width / base.width) * ratio, Math.sqrt(1_000_000 / (base.width * base.height))) });", "Bound thumbnail pixels even for extreme page aspect ratios or display DPR."],
  ["ui/viewers/PdfView.tsx", "        await pdfPage.render({ canvas, canvasContext: context, viewport }).promise;\n        return canvas;", "        const task = pdfPage.render({canvas, canvasContext: context, viewport});\n        const abort = () => {task.cancel(); canvas.width = canvas.height = 0;};\n        signal?.addEventListener(\"abort\", abort, {once: true});\n        try {signal?.throwIfAborted(); await task.promise; return canvas;}\n        catch (error) {canvas.width = canvas.height = 0; throw error;}\n        finally {signal?.removeEventListener(\"abort\", abort); pdfPage.cleanup();}", "Stop an off-screen thumbnail and release its page; its visible slot owns a completed canvas."],
  ["ui/shell/panes.tsx", "    let done = false;\n    const observer = new IntersectionObserver(\n      (entries) => {\n        if (done || !entries.some((entry) => entry.isIntersecting)) return;\n        done = true;\n        observer.disconnect();\n        void handle.thumbnail!(page, width).then(\n          (thumb) => {\n            element.replaceChildren(thumb);\n            element.style.height = \"\";\n          },\n          () => undefined,\n        );\n      },\n      { root: root.current, rootMargin: \"300px 0px\" },\n    );\n    observer.observe(element);\n    return () => observer.disconnect();", "    let request: AbortController | null = null;\n    const clear = () => {request?.abort(); request = null; element.querySelectorAll(\"canvas\").forEach(canvas => {canvas.width = canvas.height = 0;}); element.replaceChildren(); element.style.height = `${Math.round(width * ratio)}px`;};\n    const observer = new IntersectionObserver(entries => {\n      if (!entries.some(entry => entry.isIntersecting)) return clear();\n      if (request) return;\n      const current = request = new AbortController();\n      void handle.thumbnail!(page, width, current.signal).then(thumb => {\n        if (current.signal.aborted) {thumb.querySelectorAll(\"canvas\").forEach(canvas => {canvas.width = canvas.height = 0;}); if (thumb instanceof HTMLCanvasElement) thumb.width = thumb.height = 0; return;}\n        element.replaceChildren(thumb); element.style.height = \"\";\n      }, () => undefined);\n    }, {root: root.current, rootMargin: \"300px 0px\"});\n    observer.observe(element);\n    return () => {observer.disconnect(); clear();};", "Only thumbnails in the visible neighbor window retain canvases, render handles and listeners; late results are discarded."],
  ["ui/main.tsx", "const loadSeq = useRef(0);", "const loadSeq = useRef(0);\n  const loadAbort = useRef<AbortController | null>(null);\n  useEffect(() => () => loadAbort.current?.abort(), []);", "Opening/closing a source owns and cancels the bounded preflight Worker."],
  ["ui/main.tsx", "const mine = ++loadSeq.current;", "loadAbort.current?.abort();\n      const controller = new AbortController(); loadAbort.current = controller;\n      const mine = ++loadSeq.current;", "A superseding document cannot leave an old admission Worker parsing in the background."],
  ["ui/main.tsx", "readFileBytes(loaded.file),", "readFileBytes(loaded.file, controller.signal, loaded.info.kind),", "Check bounded Office structure and decoded image resources without a full server text index."],
  ["ui/viewers/types.ts", "capture(at: string | null, maxWidth: number): Promise<Capture>;", "capture(at: string | null, maxWidth: number, signal?: AbortSignal): Promise<Capture>;", "Capture ownership propagates into the viewer and native image encoder."],
  ["ui/shell/useCaptureView.ts", "viewer.current.capture(at, maxWidth)", "viewer.current.capture(at, maxWidth, signal)", "A canceled parent call stops its raster work rather than only dropping the final message."],
  ["ui/viewers/DocxView.tsx", "async capture(at, maxWidth) {", "async capture(at, maxWidth, signal) {\n        signal?.throwIfAborted();", "Every viewer receives the owning capture deadline and cancellation."],
  ["ui/viewers/PdfView.tsx", "async capture(at, maxWidth) {", "async capture(at, maxWidth, signal) {\n        signal?.throwIfAborted();", "Every viewer receives the owning capture deadline and cancellation."],
  ["ui/viewers/PptxView.tsx", "async capture(at, maxWidth) {", "async capture(at, maxWidth, signal) {\n        signal?.throwIfAborted();", "Every viewer receives the owning capture deadline and cancellation."],
  ["ui/viewers/XlsxView.tsx", "async capture(at, maxWidth) {", "async capture(at, maxWidth, signal) {\n        signal?.throwIfAborted();", "Every viewer receives the owning capture deadline and cancellation."],
  ["ui/viewers/DocxView.tsx", "rasterize(page, { maxWidth, context: body, background: \"#fff\" })", "rasterize(page, { maxWidth, context: body, background: \"#fff\", signal })", "Word raster encoding releases its buffers on cancellation."],
  ["ui/viewers/PptxView.tsx", "          context: slot.host,\n          background: \"#fff\",", "          context: slot.host,\n          background: \"#fff\",\n          signal,", "PowerPoint raster encoding shares the owning abort signal."],
  ["ui/viewers/XlsxView.tsx", "          maxWidth,\n          background: props.dark", "          maxWidth,\n          signal,\n          background: props.dark", "Excel raster encoding shares the owning abort signal."],
  ["ui/viewers/PdfView.tsx", "const viewport = pdfPage.getViewport({ scale: Math.min(3, maxWidth / base.width) });", "const viewport = pdfPage.getViewport({ scale: Math.min(3, maxWidth / base.width, Math.sqrt(5_000_000 / (base.width * base.height))) });", "Bound source capture pixels before allocating the PDF render canvas."],
  ["ui/viewers/PdfView.tsx", "        await pdfPage.render({ canvas, canvasContext: context, viewport }).promise;\n        return { ...canvasToPng(canvas, maxWidth), at: `page:${number}` };", "        const task = pdfPage.render({canvas, canvasContext: context, viewport});\n        const abort = () => task.cancel();\n        signal?.addEventListener(\"abort\", abort, {once: true});\n        try {\n          signal?.throwIfAborted();\n          await task.promise;\n          return {...(await canvasToPng(canvas, maxWidth, signal)), at: `page:${number}`};\n        } finally {\n          signal?.removeEventListener(\"abort\", abort);\n          canvas.width = canvas.height = 0; pdfPage.cleanup();\n        }", "Cancel PDF rendering and release its original canvas, page and listener after encoding or failure."],
  ["ui/viewers/DocxView.tsx", "        let anchor: HTMLElement | null = at ? (elementsFor(body, at)[0] ?? null) : null;\n        if (at && !anchor) throw new Error(`Cannot find ${at} in the rendered document.`);\n        let page: HTMLElement | null = anchor?.closest<HTMLElement>(`section.${CLASS}`) ?? null;", "        const physical = at?.match(/^page:([1-9][0-9]*)$/);\n        let anchor: HTMLElement | null = at && !physical ? (elementsFor(body, at)[0] ?? null) : null;\n        if (at && !physical && !anchor) throw new Error(`Cannot find ${at} in the rendered document.`);\n        let page: HTMLElement | null = physical ? (sections()[Number(physical[1]) - 1] ?? null) : anchor?.closest<HTMLElement>(`section.${CLASS}`) ?? null;\n        if (physical && !page) throw new Error(\"Browser page outside the rendered document\");", "Browser page numbers address browser layout only; they never imply an Office PDF page or a verified Word paragraph."],
  ["ui/viewers/DocxView.tsx", "return { ...raster, at: anchor?.dataset.at ?? `page ${sections().indexOf(page) + 1}` };", "return { ...raster, at: physical ? `page:${Number(physical[1])}` : anchor?.dataset.at ?? `page ${sections().indexOf(page) + 1}` };", "Return the exact requested browser page in the capture receipt."],
  ["ui/shell/useCaptureView.ts", "async (args) => {", "async (args, signal) => {\n      signal?.throwIfAborted();", "Check capture ownership before rendering."],
  ["ui/shell/useCaptureView.ts", "      return {\n        content:", "      signal?.throwIfAborted();\n      return {\n        content:", "Drop late pixels after cancellation before posting any screenshot data."],
  ["ui/viewers/DocxView.tsx", "      active = false;\n    };\n  }, [props.bytes]);", "      active = false;\n      body.replaceChildren(); styles.replaceChildren();\n    };\n  }, [props.bytes]);", "Release Word DOM/styles when its source is retired."],
  ["ui/viewers/PdfView.tsx", "    let loadedDoc: PDFDocumentProxy | null = null;", "    let loadedDoc: PDFDocumentProxy | null = null;\n    let loadingTask: ReturnType<typeof pdfjs.getDocument> | null = null;", "Destroy a PDF loading task even when it never resolved into a document."],
  ["ui/viewers/PdfView.tsx", "      loadedDoc = await task.promise;", "      loadingTask = task;\n      loadedDoc = await task.promise;", "Retain ownership of pending PDF parse work."],
  ["ui/viewers/PdfView.tsx", "      void loadedDoc?.destroy();", "      generation.current++;\n      for (const slot of slots.current) { slot.task?.cancel(); slot.element.querySelectorAll(\"canvas\").forEach(canvas => {canvas.width = canvas.height = 0;}); slot.element.replaceChildren(); }\n      slots.current = []; sizes.current = []; revealRanges.current.clear(); find.current = null;\n      void (loadedDoc ? loadedDoc.destroy() : loadingTask?.destroy());", "Close source parsing, render tasks, canvases, text layers and retained ranges together."],
  ["ui/viewers/PdfView.tsx", "      for (let n = 1; n <= count; n++) {\n        const page = await loadedDoc.getPage(n);\n        const viewport = page.getViewport({ scale: 1 });\n        pageSizes.push({ width: viewport.width, height: viewport.height });\n      }", "      if (count > 5000) throw new Error(\"浏览器页面导航超出资源预算\");\n      const first = await loadedDoc.getPage(1);\n      const initial = first.getViewport({scale: 1});\n      first.cleanup();\n      for (let n = 1; n <= count; n++) pageSizes.push({width: initial.width, height: initial.height});", "Open from one actual page geometry; remaining page dimensions are resolved only on demand, with bounded browser navigation."],
  ["ui/viewers/PdfView.tsx", "    const ratio = window.devicePixelRatio || 1;", "    const natural = page.getViewport({scale: 1});\n    sizes.current[index] = {width: natural.width, height: natural.height};\n    slot.element.style.width = `${viewport.width}px`; slot.element.style.height = `${viewport.height}px`;\n    const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(5_000_000 / (viewport.width * viewport.height)));", "Bound decoded pixels while correcting the requested page geometry."],
  ["ui/viewers/PdfView.tsx", "          if (!entry.isIntersecting) continue;\n          const index = slots.current.findIndex((slot) => slot.element === entry.target);", "          const index = slots.current.findIndex((slot) => slot.element === entry.target);\n          if (!entry.isIntersecting) { const slot = slots.current[index]; if (slot) {slot.task?.cancel(); slot.done = undefined; slot.textLayer = undefined; slot.element.querySelectorAll(\"canvas\").forEach(canvas => {canvas.width = canvas.height = 0;}); slot.element.replaceChildren(); find.current?.ranges.delete(index + 1); for (const [key, range] of revealRanges.current) if (!range.startContainer.isConnected) revealRanges.current.delete(key);} continue; }", "Evict decoded pages leaving the visible neighbor window instead of retaining every visited page."],
  ["ui/viewers/XlsxView.tsx", "      setWasmSource(bytes);", "      const url = (globalThis as {__officeXlsxWorkerUrl?: string}).__officeXlsxWorkerUrl;\n      window.addEventListener(\"pagehide\", () => {if (url) URL.revokeObjectURL(url);}, {once: true});\n      setWasmSource(bytes);", "The viewer iframe owns its worker script URL and releases it at retirement."],
  ["ui/viewers/DocxView.tsx", "import { alignBlocks } from \"#ui/viewers/align.ts\";", "import { alignBlocks } from \"#ui/viewers/align.ts\";\nimport { getHost } from \"#ui/host.ts\";\nimport type { DocxBlockKey } from \"#office/contract.ts\";", "Prepare Word source mapping only for an explicit selection/reveal; initial display must not wait for a full server index."],
  ["ui/viewers/DocxView.tsx", "  const glance = Boolean(props.glance);", "  const glance = Boolean(props.glance);\n  const mappedBlocks = useRef<DocxBlockKey[] | null>(props.blocks ?? null);\n  const blockRequest = useRef<Promise<void> | null>(null);\n  const ensureBlocks = () => {\n    if (mappedBlocks.current !== null) return Promise.resolve();\n    return (blockRequest.current ??= (async () => {\n      const result = await getHost().app.callServerTool({name: \"document_blocks\", arguments: {}});\n      const value = result.structuredContent as {blocks?: DocxBlockKey[]};\n      if (result.isError || !Array.isArray(value?.blocks)) throw new Error(\"无法核验文档位置\");\n      mappedBlocks.current = value.blocks;\n      annotate(bodyRef.current!);\n    })());\n  };", "Share one bounded source-mapping request per immutable view."],
  ["ui/viewers/DocxView.tsx", "      callbacks.current.blocks ?? [],", "      mappedBlocks.current ?? callbacks.current.blocks ?? [],", "A verified source mapping arriving after initial paint annotates the existing rendered nodes."],
  ["ui/viewers/DocxView.tsx", "    const select = (event: Event) => {", "    let selectionActive = true;\n    let selectionSequence = 0;\n    let selectionTimer: ReturnType<typeof setTimeout> | undefined;\n    const selectNow = (event: Event) => {", "Retire stale selection callbacks on close or superseding user actions."],
  ["ui/viewers/DocxView.tsx", "    const deferred = (event: Event) => setTimeout(() => select(event), 0);", "    const select = (event: Event) => {\n      const sequence = ++selectionSequence;\n      void ensureBlocks().then(() => {\n        if (selectionActive && sequence === selectionSequence) selectNow(event);\n      }).catch(() => { if (selectionActive) callbacks.current.onSelect(null); });\n    };\n    const deferred = (event: Event) => {\n      clearTimeout(selectionTimer);\n      selectionTimer = setTimeout(() => select(event), 0);\n    };", "Selection waits only for required precise mapping; deferred timers are owned and canceled."],
  ["ui/viewers/DocxView.tsx", "      scroll.removeEventListener(\"mouseup\", deferred);", "      selectionActive = false;\n      clearTimeout(selectionTimer);\n      scroll.removeEventListener(\"mouseup\", deferred);", "A closed view never reattaches selection results or timers."],
  ["ui/viewers/DocxView.tsx", "      async reveal(targets) {\n        this.clearReveal();", "      async reveal(targets) {\n        await ensureBlocks();\n        this.clearReveal();", "Precise Word reveal retains the same source mapping contract despite lazy initial display."],
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
  ["ui/viewers/PdfView.tsx", "    return pdfjs.PDFWorker.create({ port: new Worker(url) });", "    const port = new Worker(url);\n    URL.revokeObjectURL(url);\n    window.addEventListener(\"pagehide\", () => port.terminate(), {once: true});\n    return pdfjs.PDFWorker.create({ port });", "The iframe owns its PDF worker and releases its source URL immediately."],
  ["ui/viewers/PdfView.tsx", "    const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(5_000_000 / (viewport.width * viewport.height)));", "    const retainedPixels = slots.current.reduce((sum, item) => sum + [...item.element.querySelectorAll(\"canvas\")].reduce((n, canvas) => n + canvas.width * canvas.height, 0), 0);\n    const availablePixels = Math.min(5_000_000, 16_000_000 - retainedPixels);\n    if (availablePixels < 100000) { callbacks.current.onError(new Error(\"浏览器解码缓存超出预算\")); return; }\n    const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(availablePixels / (viewport.width * viewport.height)));", "All retained PDF canvas pixels share the 64MiB decoded-cache budget."],
  ["ui/viewers/PdfView.tsx", "        console.warn(\"pdf render\", error);\n    }", "        callbacks.current.onError(error);\n    } finally {\n      page.cleanup();\n      if (!canvas.isConnected) canvas.width = canvas.height = 0;\n      if (slot.task === task) slot.task = undefined;\n    }", "Rendering resource failures reach controlled fallback; release page and detached canvas resources after work ends."],
];

const ui = (name) => join(runtime, "ui", name);
let format;
const adapters = {
  name: "ailab-office-adapters",
  setup(builder) {
    const swap = { "#ui/host.ts": join(here, "host.ts"), "#ui/assets.ts": join(here, "assets.ts"), "#ui/raster.ts": join(here, "raster.ts"), "#office/profile.ts": join(here, "profile.ts") };
    builder.onResolve({ filter: /^#(ui\/(host|assets|raster)|office\/profile)\.ts$/ }, (args) => ({ path: swap[args.path] }));
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
const admissionWorker = await build({ ...common, entryPoints: [join(here, "admission-worker.ts")], format: "iife" });
await writeFile(join(dist, "admission-worker.js"), admissionWorker.outputFiles[0].contents);
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
for (const input of [...apps.map((app) => app.metafile), xlsxWorker.metafile, pdfWorker.metafile, admissionWorker.metafile].flatMap((meta) => Object.keys(meta.inputs))) {
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

/**
 * Render a live DOM element to a PNG without network access: the element is cloned with its
 * ancestors' class context, canvases and images are inlined as data: URLs, and the result is drawn
 * through an SVG <foreignObject>. Used by the capture_view page tool.
 */

const imageCache = new Map<string, Promise<string | null>>();

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timer = setTimeout(() => { clear(); image.src = ""; reject(new Error("截图解码超时")); }, 5000);
    const clear = () => { clearTimeout(timer); image.onload = image.onerror = null; };
    image.onload = () => { clear(); resolve(image); };
    image.onerror = () => { clear(); image.src = ""; reject(new Error("image failed to load")); };
    image.src = src;
  });
}

/** Turn a blob:/same-origin image URL into a data: URL by drawing it on a canvas. */
function inlineImage(src: string): Promise<string | null> {
  if (src.startsWith("data:")) return Promise.resolve(src);
  let cached = imageCache.get(src);
  if (!cached) {
    cached = loadImage(src)
      .then((image) => {
        const canvas = document.createElement("canvas");
        if (image.naturalWidth * image.naturalHeight > 16_000_000) throw new Error("截图像素超限");
        canvas.width = image.naturalWidth || 1;
        canvas.height = image.naturalHeight || 1;
        canvas.getContext("2d")!.drawImage(image, 0, 0);
        try { return canvas.toDataURL("image/png"); }
        finally { canvas.width = canvas.height = 0; image.src = ""; }
      })
      .catch(() => null);
    imageCache.set(src, cached);
    while (imageCache.size > 8) imageCache.delete(imageCache.keys().next().value!);
  }
  return cached;
}

function cssText(): string {
  const parts: string[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      for (const rule of Array.from(sheet.cssRules)) parts.push(rule.cssText);
    } catch {
      // Cross-origin sheets cannot be read; there are none in the sandbox.
    }
  }
  return parts.join("\n");
}

const URL_IN_CSS = /url\((['"]?)(blob:[^'")]+)\1\)/g;

async function inlineTree(original: Element, clone: Element) {
  let count = 0, encoded = 0;
  const resolveImage = async (src: string) => {
    const data = await inlineImage(src);
    encoded += data?.length ?? 0;
    if (encoded > 8 * 1024 * 1024) throw new Error("截图资源超出预算");
    return data;
  };
  const walk = async (source: Element, target: Element): Promise<void> => {
    if (++count > 10000) throw new Error("截图结构超出预算");
    if (source instanceof HTMLCanvasElement) {
      if (source.width * source.height > 16_000_000) throw new Error("截图像素超限");
      const image = document.createElement("img");
      image.src = source.toDataURL("image/png");
      encoded += image.src.length;
      if (encoded > 8 * 1024 * 1024) throw new Error("截图资源超出预算");
      image.setAttribute("style", (source.getAttribute("style") ?? "") + `;width:${source.offsetWidth}px;height:${source.offsetHeight}px`);
      image.className = source.className;
      target.replaceWith(image);
      return;
    }
    if (source instanceof HTMLImageElement && target instanceof HTMLImageElement) {
      const data = await resolveImage(source.currentSrc || source.src);
      if (data) target.src = data;
      target.removeAttribute("srcset");
    }
    if (source instanceof SVGImageElement) {
      const data = await resolveImage(source.href.baseVal);
      if (data) { target.setAttribute("href", data); target.removeAttributeNS("http://www.w3.org/1999/xlink", "href"); }
    }
    let style = target.getAttribute("style");
    if (style?.includes("blob:")) {
      for (const match of [...style.matchAll(URL_IN_CSS)]) style = style.split(match[2]).join((await resolveImage(match[2])) ?? "");
      target.setAttribute("style", style);
    }
    const children = [...target.children];
    for (let i = 0; i < source.children.length && i < children.length; i++) await walk(source.children[i], children[i]);
  };
  await walk(original, clone);
}

/** Recreate the ancestor chain (tags and classes only) so scoped selectors still match. */
function wrapInAncestors(element: Element, clone: Element, stopAt: Element | null): Element {
  let inner: Element = clone;
  for (
    let node = element.parentElement;
    node && node !== stopAt?.parentElement && node !== document.body;
    node = node.parentElement
  ) {
    const shell = document.createElement(
      node.tagName.toLowerCase() === "body" ? "div" : node.tagName.toLowerCase(),
    );
    if (node.className && typeof node.className === "string") shell.className = node.className;
    for (const name of node.getAttributeNames())
      if (name.startsWith("data-")) shell.setAttribute(name, node.getAttribute(name)!);
    shell.setAttribute(
      "style",
      "display:block;margin:0;padding:0;border:0;background:none;width:auto;height:auto;transform:none;overflow:visible;position:static",
    );
    shell.append(inner);
    inner = shell;
  }
  return inner;
}

export interface Raster {
  data: string;
  width: number;
  height: number;
}

export async function rasterize(
  element: HTMLElement,
  options: { maxWidth: number; context?: Element | null; background?: string },
): Promise<Raster> {
  const width = Math.max(1, element.offsetWidth);
  const height = Math.max(1, element.offsetHeight);
  if (width * height > 16_000_000 || element.querySelectorAll("*").length > 10000) throw new Error("截图区域超出资源预算");
  const clone = element.cloneNode(true) as HTMLElement;
  clone.style.transform = "none";
  clone.style.margin = "0";
  try { await inlineTree(element, clone); }
  catch (error) { clone.replaceChildren(); imageCache.clear(); throw error; }
  const computed = getComputedStyle(element);
  const root = document.createElement("div");
  root.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  root.setAttribute(
    "style",
    `width:${width}px;height:${height}px;overflow:hidden;font-family:${computed.fontFamily};color:${computed.color};background:${options.background ?? "#fff"}`,
  );
  const style = document.createElement("style");
  style.textContent = cssText();
  root.append(style, wrapInAncestors(element, clone, options.context ?? null));
  const markup = new XMLSerializer().serializeToString(root);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><foreignObject x="0" y="0" width="100%" height="100%">${markup}</foreignObject></svg>`;
  const image = await loadImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`);
  const scale = Math.min(2, options.maxWidth / width, Math.sqrt(5_000_000 / (width * height)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const context = canvas.getContext("2d")!;
  context.fillStyle = options.background ?? "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  try {
    const data = canvas.toDataURL("image/png").split(",")[1];
    if (data.length > 12 * 1024 * 1024) throw new Error("截图编码超限");
    return {data, width: canvas.width, height: canvas.height};
  } finally {
    canvas.width = canvas.height = 0;
    image.src = "";
    root.replaceChildren();
    clone.replaceChildren();
    imageCache.clear();
  }
}

/** Scale a canvas down to `maxWidth` and encode it. */
export function canvasToPng(source: HTMLCanvasElement, maxWidth: number): Raster {
  const scale = Math.min(1, maxWidth / source.width, Math.sqrt(5_000_000 / (source.width * source.height)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(source.width * scale));
  canvas.height = Math.max(1, Math.round(source.height * scale));
  canvas.getContext("2d")!.drawImage(source, 0, 0, canvas.width, canvas.height);
  try {
    const data = canvas.toDataURL("image/png").split(",")[1];
    if (data.length > 12 * 1024 * 1024) throw new Error("截图编码超限");
    return {data, width: canvas.width, height: canvas.height};
  } finally {
    canvas.width = canvas.height = 0;
    imageCache.clear();
  }
}

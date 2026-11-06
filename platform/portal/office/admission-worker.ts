// Existing JSZip dependency is reused only for bounded client admission. This
// Worker never reports file safety or creates a document's authoritative index.
import JSZip from "jszip";

const XML_BYTES = 4 * 1024 * 1024;
const PACKAGE_BYTES = 16 * 1024 * 1024;

function boundedEntry(entry: JSZip.JSZipObject): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const chunks: Uint8Array[] = [];
    let bytes = 0, stopped = false;
    const stream = (entry as JSZip.JSZipObject & {internalStream(type: "uint8array"): JSZip.JSZipStreamHelper<Uint8Array>}).internalStream("uint8array");
    stream.on("data", chunk => {
      if (stopped) return;
      bytes += chunk.byteLength;
      if (bytes > XML_BYTES) { stopped = true; stream.pause(); chunks.length = 0; reject(new Error("浏览器文档解压超限")); return; }
      chunks.push(chunk);
    });
    stream.on("error", error => { stopped = true; chunks.length = 0; reject(error); });
    stream.on("end", () => {
      if (stopped) return;
      const result = new Uint8Array(bytes);
      let offset = 0;
      for (const chunk of chunks) {result.set(chunk, offset); offset += chunk.byteLength;}
      chunks.length = 0;
      resolve(result);
    });
    stream.resume();
  });
}

function imagePixels(data: Uint8Array): number {
  const v = new DataView(data.buffer, data.byteOffset, data.byteLength);
  if (data.length >= 24 && v.getUint32(0) === 0x89504e47) return v.getUint32(16) * v.getUint32(20);
  if (data.length >= 10 && String.fromCharCode(...data.subarray(0, 3)) === "GIF") return v.getUint16(6, true) * v.getUint16(8, true);
  if (data.length > 4 && v.getUint16(0) === 0xffd8) {
    for (let p = 2, markers = 0; p + 4 < data.length && markers < 4096; markers++) {
      if (data[p++] !== 255) throw new Error("无效图片");
      while (data[p] === 255) p++;
      const marker = data[p++];
      if (marker === 0xd9 || marker === 0xda) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (p + 2 > data.length) break;
      const length = v.getUint16(p);
      if (length < 2 || p + length > data.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && length >= 7) return v.getUint16(p + 3) * v.getUint16(p + 5);
      p += length;
    }
  }
  throw new Error("浏览器不支持此内嵌图片，请使用后备预览");
}

self.onmessage = async (event: MessageEvent<{ bytes: ArrayBuffer; kind: string }>) => {
  const { bytes, kind } = event.data;
  try {
    const zip = await JSZip.loadAsync(bytes);
    const entries = Object.values(zip.files);
    if (entries.length > 10000) throw new Error("浏览器文档结构超限");
    let total = 0, paragraphs = 0, cells = 0, slides = 0, imageBytes = 0;
    for (const entry of entries) {
      if (entry.dir) continue;
      // The locked JSZip version exposes the central-directory size in _data.
      // Check it before async() allocates an expanded member.
      const size = (entry as unknown as { _data?: { uncompressedSize?: number } })._data?.uncompressedSize;
      if (!Number.isSafeInteger(size) || size! < 0 || size! > XML_BYTES || (total += size!) > PACKAGE_BYTES) throw new Error("浏览器文档资源超限");
      if (/\.(?:xml|rels)$/i.test(entry.name)) {
        const text = new TextDecoder().decode(await boundedEntry(entry));
        if (text.length * 2 > 2 * XML_BYTES) throw new Error("浏览器文档资源超限");
        if (kind === "docx" && /^word\/document\.xml$/.test(entry.name)) paragraphs += (text.match(/<(?:[\w.-]+:)?p(?:\s|>)/g) ?? []).length;
        if (kind === "xlsx" && /^xl\/worksheets\//.test(entry.name)) cells += (text.match(/<(?:[\w.-]+:)?c(?:\s|>)/g) ?? []).length;
        if (kind === "pptx" && /^ppt\/slides\/slide\d+\.xml$/.test(entry.name)) slides++;
        if (paragraphs > 1000 || cells > 100000 || slides > 200) throw new Error("文档超过浏览器预览规模，请使用后备预览");
      } else if (/\/(?:media)\//.test(entry.name)) {
        const data = await boundedEntry(entry);
        const pixels = imagePixels(data);
        imageBytes += pixels * 4;
        if (!Number.isFinite(pixels) || pixels <= 0 || pixels > 16_000_000 || imageBytes > 32 * 1024 * 1024) throw new Error("浏览器图片缓存超限");
      }
    }
    self.postMessage({ ok: true, bytes }, { transfer: [bytes] });
  } catch (error) {
    self.postMessage({ ok: false, error: error instanceof Error ? error.message : "浏览器文档准备失败" });
  }
};

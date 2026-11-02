// pdf.js substitutes non-embedded standard fonts with a FontFace whose src is `url(<standardFontDataUrl><file>)`. The
// frame has no network (font-src data: blob:), so the file comes through the asset channel and is passed as a blob URL.
import { readBinary } from "./assets";

const urls = new Map<string, Promise<string>>();

export async function officeFontSrc(src: string): Promise<string> {
  const parts = await Promise.all(
    src.split(/,(?=\s*(?:local|url)\()/).map(async (part) => {
      const file = /^\s*url\((["']?)(pdfjs\/standard_fonts\/[^"')]+)\1\)\s*$/.exec(part)?.[2];
      if (!file) return part;
      let url = urls.get(file);
      if (!url) {
        url = readBinary(file).then((bytes) => URL.createObjectURL(new Blob([bytes], { type: "font/ttf" })));
        url.catch(() => urls.delete(file));
        urls.set(file, url);
      }
      return url.then(
        (value) => `url(${value})`,
        () => null,
      );
    }),
  );
  return parts.filter((part) => part !== null).join(",");
}

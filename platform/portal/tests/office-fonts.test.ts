import { afterEach, expect, test, vi } from "vitest";
const { readBinary } = vi.hoisted(() => ({ readBinary: vi.fn() }));
vi.mock("../office/assets", () => ({ readBinary }));
// This adapter is built by esbuild with runtime aliases, outside the Portal TS project.
const adapter = "../office/pdf-fonts";
const { officeFontSrc } = await import(adapter);
afterEach(() => vi.restoreAllMocks());
test("only standard-font url parts become cached blobs; local, data and external sources stay intact", async () => {
  vi.mocked(readBinary).mockResolvedValue(new Uint8Array([1, 2]));
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:font");
  const unchanged = 'local("Arial"),url(data:font/ttf;base64,abcd),url(https://example.org/font.ttf),url(pdfjs/cmaps/a.bcmap)';
  expect(await officeFontSrc(unchanged)).toBe(unchanged);
  expect(readBinary).not.toHaveBeenCalled();
  expect(await officeFontSrc('local("Arial"), url("pdfjs/standard_fonts/Liberation.ttf"),url(data:font/ttf;base64,abcd)')).toBe('local("Arial"),url(blob:font),url(data:font/ttf;base64,abcd)');
  expect(await officeFontSrc("url('pdfjs/standard_fonts/Liberation.ttf')")).toBe("url(blob:font)");
  expect(readBinary).toHaveBeenCalledExactlyOnceWith("pdfjs/standard_fonts/Liberation.ttf");
});

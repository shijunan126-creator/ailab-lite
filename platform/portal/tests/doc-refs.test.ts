import { beforeEach, expect, test } from "vitest";
import { addQuote, clearQuotes, quotesKey, removeQuote, useQuotes } from "../lib/doc-refs";
beforeEach(() => useQuotes.setState({ quotes: {} }));
const quote = (n: number) => ({ ref: { file_id: "f", version: "v", at: `p:${n}`, quote: `text${n}` }, label: `q${n}` });
test("quotes cap at eight, dedupe, remove, and clear only their composer", () => {
  for (let i = 1; i <= 9; i++) addQuote("a", quote(i));
  addQuote("b", quote(1)); addQuote("a", quote(5));
  expect(useQuotes.getState().quotes.a.map(q => q.ref.at)).toEqual(["p:2", "p:3", "p:4", "p:6", "p:7", "p:8", "p:9", "p:5"]);
  removeQuote("a", 0); expect(useQuotes.getState().quotes.a).toHaveLength(7);
  clearQuotes("a"); expect(useQuotes.getState().quotes).toEqual({ b: [quote(1)] });
  expect(quotesKey("c", "p")).toBe("c"); expect(quotesKey(null, "p")).toBe("new:p");
});

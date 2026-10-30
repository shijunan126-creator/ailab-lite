// What the OpenUI parser is given. Two things the model writes naturally are lost by its lexer without an error:
//
// - Names. The lexer reads them as [A-Za-z_][A-Za-z0-9_]* and silently skips any other character, so a statement
//   named in Chinese (`表 = Table(...)`, which models do write) loses its name and the whole block renders empty.
//   Names outside strings that use other letters are renamed to ASCII, the same name to the same replacement, so
//   statement names, object keys and `.field` reads still match. Strings, where Chinese belongs, keep their text.
// - Backslashes in single-quoted strings. Those hold code (an HtmlArtifact document), and the lexer decodes `\"` to
//   `"`, `\n` to a line break and `\d` to `d`, so `html += "<div class=\"row\">"` reaches the page as broken
//   JavaScript. A single-quoted string is raw text here: `\'` is a quote and every other backslash is kept.
//   Double-quoted strings stay JSON strings.
// - Quotes inside a double-quoted string. Models quote a phrase in Chinese prose with ASCII quotes
//   (`Callout("更像"积压订单"所致")`); the lexer ends the string at the second quote and the rest spills into extra
//   arguments that are dropped. A quote closes a string only where a string can end: before an operator, a closing
//   bracket, a comma, a colon or the end of the line. Any other quote is part of the text and is escaped.

const NAME = /[\p{L}\p{N}_$]/u;
const ASCII = /^[$]?[A-Za-z_][A-Za-z0-9_]*$/;
/** What may follow the quote that ends a string, after spaces. */
const ENDS = /[,)\]}:+\-*/%?=!<>&|\r\n]/;

/** The end of the double-quoted string opening at `start`, and its text with inner quotes escaped; null when the
 * line ends first (the string is still being written, or is not one). */
function quoted(source: string, start: number): { end: number; text: string } | null {
  let text = '"';
  for (let j = start + 1; j < source.length && source[j] !== "\n"; j++) {
    const c = source[j];
    if (c === "\\") {
      text += c + (source[j + 1] ?? "");
      j++;
    } else if (c === '"') {
      let k = j + 1;
      while (source[k] === " " || source[k] === "\t") k++;
      if (k >= source.length || ENDS.test(source[k])) return { end: j + 1, text: text + '"' };
      text += '\\"';
    } else text += c;
  }
  return null;
}

export function toProgram(source: string): string {
  const renamed = new Map<string, string>();
  let next = 0;
  const rename = (word: string) => {
    let name = renamed.get(word);
    while (!name) {
      // lower case first: an upper-case name would read as a component
      const candidate = `${word.startsWith("$") ? "$" : ""}n_${next++}`;
      if (!new RegExp(`(^|[^\\w$])${candidate.replace("$", "\\$")}(?![\\w$])`).test(source)) name = candidate;
    }
    renamed.set(word, name);
    return name;
  };
  let out = "";
  let i = 0;
  while (i < source.length) {
    const c = source[i];
    if (c === "#" || (c === "/" && source[i + 1] === "/")) {
      // a comment, as the parser strips it: an apostrophe in it must not open a string
      const end = source.indexOf("\n", i);
      out += source.slice(i, end < 0 ? source.length : end);
      i = end < 0 ? source.length : end;
    } else if (c === '"') {
      const s = quoted(source, i);
      if (s) {
        out += s.text;
        i = s.end;
      } else {
        let j = i + 1;
        while (j < source.length && source[j] !== c) j += source[j] === "\\" ? 2 : 1;
        out += source.slice(i, j + 1);
        i = j + 1;
      }
    } else if (c === "'") {
      let j = i + 1;
      out += c;
      for (; j < source.length && source[j] !== "'"; j++) {
        if (source[j] !== "\\") out += source[j];
        else if (source[j + 1] === "'") out += source[j] + source[++j];
        else out += "\\\\"; // the lexer decodes this pair back to the one backslash written
      }
      if (j < source.length) out += "'";
      i = j + 1;
    } else if (NAME.test(c)) {
      let j = i + 1;
      while (j < source.length && NAME.test(source[j]) && source[j] !== "$") j++;
      const word = source.slice(i, j);
      // numbers and plain names pass; only a name the lexer would cut is replaced
      out += /^\d/.test(word) || ASCII.test(word) ? word : rename(word);
      i = j;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

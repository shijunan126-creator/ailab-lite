// A small Markdown renderer for model answers: headings, paragraphs, lists, quotes, code, tables, links,
// bold / italic / inline code, and TeX math. It builds React elements (no HTML strings), so model output cannot
// inject markup; the only HTML is KaTeX's own rendering of a formula, typeset without trusted commands.
"use client";

import { Fragment, memo, ReactNode, useMemo } from "react";
import ViewportBlock from "./viewport-block";
import { markdownChunks } from "../lib/markdown-chunks";
import CopyButton from "./copy-button";
import TableFrame from "./table-frame";
import FileCite from "./file-cite";
import { urlKey, type Source } from "../lib/work-process";
import { holdOpenMath, MATH_INLINE, opensMath, readMathBlock, tableCells, texOf } from "../lib/math";
import TexMath, { PendingMath } from "./math";

const BARE_URL = /https?:\/\/[^\s<>()[\]{}"'，。、；：！？（）《》「」]+[^\s<>()[\]{}"'，。、；：！？（）《》「」.,;:!?]/;

/** example.com/docs/guide for a bare link: readable, still the whole address on hover. */
function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    const path = u.pathname === "/" ? "" : decodeURIComponent(u.pathname).replace(/\/$/, "");
    const short = u.hostname.replace(/^www\./, "") + path;
    return short.length > 48 ? short.slice(0, 46) + "…" : short;
  } catch {
    return url;
  }
}

/** Web pages the turn read, by address: a link to one of them is a citation. */
type Cites = Map<string, Source> | undefined;

type LinkOpener = (url: string, title: string) => void;

function inline(text: string, key = "i", cites?: Cites, open?: LinkOpener): ReactNode[] {
  // Left-click hands the link to the side-pane reader; ctrl/middle/right keep the new tab.
  const click = open
    ? (href: string, title?: string) => (e: React.MouseEvent) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        open(href, title || href);
      }
    : undefined;
  const out: ReactNode[] = [];
  const re = new RegExp(
    // code first: `$x$` stays code; math before emphasis and links, whose markers are TeX inside a formula
    String.raw`(\`[^\`]+\`)|(\\\$)|(` + MATH_INLINE + String.raw`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\(((?:https?|ailab-file):\/\/[^)\s]+)(?:\s+"[^"]*")?\))|(` + BARE_URL.source + ")",
    "g"
  );
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${n++}`;
    if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push("$");
    else if (m[3]) {
      const { tex, display } = texOf(m[3]);
      out.push(<TexMath key={k} tex={tex} display={display} raw={m[3]} />);
    } else if (m[4]) out.push(<strong key={k}>{inline(m[4].slice(2, -2), k, cites, open)}</strong>);
    else if (m[5]) out.push(<em key={k}>{inline(m[5].slice(1, -1), k, cites, open)}</em>);
    else if (m[6] && m[7].startsWith("ailab-file://")) {
      // a library file the answer quotes: its name and page, opening the file at that page
      const label = m[6].slice(1, m[6].indexOf("](")).trim();
      const cite = /^ailab-file:\/\/([A-Za-z0-9_-]+)(?:\?page=(\d+))?$/.exec(m[7]);
      out.push(cite ? <FileCite key={k} fileId={cite[1]} page={cite[2] ? Number(cite[2]) : undefined} label={label} /> : label);
    } else if (m[6]) {
      const label = m[6].slice(1, m[6].indexOf("](")).trim();
      const source = cites?.get(urlKey(m[7]));
      // [1](url) is a citation: a small numbered mark, the page on hover
      const cite = /^\[?\d{1,2}\]?$/.test(label);
      if (source && !cite) {
        // a page the turn read, cited after a claim: a small pill named as the answer names it
        out.push(
          <a key={k} href={m[7]} target="_blank" rel="noreferrer noopener" className="md-src" title={source.title || m[7]} onClick={click?.(m[7], source.title || m[7])}>
            {label}
          </a>
        );
      } else out.push(
        <a key={k} href={m[7]} target="_blank" rel="noreferrer noopener" className={cite ? "md-cite" : "md-link"} title={m[7]} onClick={click?.(m[7], source?.title || (cite ? m[7] : label))}>
          {cite ? label.replace(/[[\]]/g, "") : inline(label, k, cites, open)}
        </a>
      );
    } else if (m[8]) {
      out.push(
        <a key={k} href={m[8]} target="_blank" rel="noreferrer noopener" className="md-url" title={m[8]} onClick={click?.(m[8])}>
          {shortUrl(m[8])}
        </a>
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Props = { text: string; streaming?: boolean; sources?: Source[]; virtual?: boolean; onOpenLink?: LinkOpener };
const MarkdownBlock = memo(function MarkdownBlock({ text, streaming, sources, onOpenLink }: Props) {
  const cites: Cites = sources?.length ? new Map(sources.map((s) => [urlKey(s.url), s])) : undefined;
  const normal = text.replace(/\r\n/g, "\n");
  const lines = (streaming ? holdOpenMath(normal) : normal).split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const isBreak = (l: string) => /^(#{1,4}\s|\s*```|>\s?|[-*+]\s|\d+[.)]\s|\|)/.test(l) || /^\s*(---|\*\*\*)\s*$/.test(l) || opensMath(l);
  /** A display formula starting at line i, typeset or (still streaming) held, with the line to continue from. */
  const mathAt = (at: number, k: string): { node: ReactNode; next: number } | null => {
    const math = readMathBlock(lines, at);
    if (!math || (!math.closed && !streaming)) return null;
    const all = lines.slice(at, math.next).join("\n");
    const raw = math.rest ? all.slice(0, all.lastIndexOf(math.rest)).trimEnd() : all;
    let next = math.next;
    // text after the closing delimiter on its line reads on as the next line
    if (math.rest) lines[--next] = math.rest;
    return { node: math.closed ? <TexMath key={k} tex={math.tex} display block raw={raw} /> : <PendingMath key={k} />, next };
  };
  while (i < lines.length) {
    const line = lines[i];
    const k = `b${key++}`;
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.trimStart().startsWith("```")) {
      const lang = line.trim().slice(3).trim().split(/\s/)[0];
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) body.push(lines[i++]);
      i++;
      const code = body.join("\n");
      // a fence with nothing in it (a stray closing fence after a UI block reads as an unclosed one) shows nothing
      if (!code.trim()) continue;
      blocks.push(
        <div key={k} className="md-codeblock">
          <div className="md-code-head">
            <span>{lang || "代码"}</span>
            <CopyButton text={code} label="复制代码" />
          </div>
          <pre className="md-code">
            <code>{code}</code>
          </pre>
        </div>
      );
      continue;
    }
    const math = mathAt(i, k);
    if (math) {
      blocks.push(math.node);
      i = math.next;
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(4, h[1].length + 1);
      const Tag = `h${level}` as "h2" | "h3" | "h4" | "h5";
      blocks.push(<Tag key={k}>{inline(h[2], k, cites, onOpenLink)}</Tag>);
      i++;
      continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
      blocks.push(<hr key={k} />);
      i++;
      continue;
    }
    if (line.startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) body.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={k}>{inline(body.join(" "), k, cites, onOpenLink)}</blockquote>);
      continue;
    }
    if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = tableCells(line);
      const start = i;
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(tableCells(lines[i++]));
      blocks.push(
        <TableFrame key={k} markdown={lines.slice(start, i).map((l) => l.trim()).join("\n")}>
        <div className="md-table">
          <table>
            <thead>
              <tr>{head.map((c, j) => <th key={j}>{inline(c, `${k}h${j}`, cites, onOpenLink)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{head.map((_, j) => <td key={j}>{inline(r[j] || "", `${k}${ri}${j}`, cites, onOpenLink)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
        </TableFrame>
      );
      continue;
    }
    const bullet = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    if (bullet.test(line)) {
      const first = bullet.exec(line)![2];
      const ordered = /\d/.test(first);
      const items: ReactNode[] = [];
      while (i < lines.length && bullet.test(lines[i])) {
        const m = bullet.exec(lines[i])!;
        const nested = m[1].length >= 2;
        const lk = `${k}l${items.length}`;
        const body: ReactNode[] = [inline(m[3], lk, cites, onOpenLink)];
        i++;
        // an indented display formula under the item, blank lines between allowed, belongs to it
        for (;;) {
          let j = i;
          while (j < lines.length && !lines[j].trim()) j++;
          if (j >= lines.length || !/^\s{2,}/.test(lines[j])) break;
          const math = mathAt(j, `${lk}m${body.length}`);
          if (!math) break;
          body.push(math.node);
          i = math.next;
        }
        items.push(
          <li key={items.length} className={nested ? "nested" : undefined}>
            {body}
          </li>
        );
      }
      // a list interrupted by other blocks resumes its numbering
      blocks.push(ordered ? <ol key={k} start={parseInt(first, 10) === 1 ? undefined : parseInt(first, 10)}>{items}</ol> : <ul key={k}>{items}</ul>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !isBreak(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]); // a line that only looked like a block start
    blocks.push(
      <p key={k}>
        {para.map((l, j) => (
          <Fragment key={j}>
            {j ? <br /> : null}
            {inline(l, `${k}p${j}`, cites, onOpenLink)}
          </Fragment>
        ))}
      </p>
    );
  }
  return <div className={`md ${streaming ? "streaming" : ""}`}>{blocks}</div>;
});

export default memo(function Markdown({ text, streaming, sources, virtual = false, onOpenLink }: Props) {
  const chunks = useMemo(() => virtual && text.length > 12000 ? markdownChunks(text) : [text], [text, virtual]);
  if (chunks.length < 2) return <MarkdownBlock text={text} streaming={streaming} sources={sources} onOpenLink={onOpenLink} />;
  return <div className="md md-chunks">{chunks.map((chunk, i) => (
    <ViewportBlock key={i} estimate={Math.max(100, chunk.length / 45 * 23)}>
      <MarkdownBlock text={chunk} streaming={streaming && i === chunks.length - 1} sources={sources} onOpenLink={onOpenLink} />
    </ViewportBlock>
  ))}</div>;
});

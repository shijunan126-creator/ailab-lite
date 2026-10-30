// A small Markdown renderer for model answers: headings, paragraphs, lists, quotes, code, tables, links,
// bold / italic / inline code. It builds React elements (no HTML strings), so model output cannot inject markup.
"use client";

import { Fragment, memo, ReactNode, useMemo } from "react";
import ViewportBlock from "./viewport-block";
import { markdownChunks } from "../lib/markdown-chunks";
import CopyButton from "./copy-button";
import FileCite from "./file-cite";
import { urlKey, type Source } from "../lib/work-process";

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
    String.raw`(\`[^\`]+\`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\(((?:https?|ailab-file):\/\/[^)\s]+)(?:\s+"[^"]*")?\))|(` + BARE_URL.source + ")",
    "g"
  );
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${n++}`;
    if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k}>{inline(m[2].slice(2, -2), k, cites, open)}</strong>);
    else if (m[3]) out.push(<em key={k}>{inline(m[3].slice(1, -1), k, cites, open)}</em>);
    else if (m[4] && m[5].startsWith("ailab-file://")) {
      // a library file the answer quotes: its name and page, opening the file at that page
      const label = m[4].slice(1, m[4].indexOf("](")).trim();
      const cite = /^ailab-file:\/\/([A-Za-z0-9_-]+)(?:\?page=(\d+))?$/.exec(m[5]);
      out.push(cite ? <FileCite key={k} fileId={cite[1]} page={cite[2] ? Number(cite[2]) : undefined} label={label} /> : label);
    } else if (m[4]) {
      const label = m[4].slice(1, m[4].indexOf("](")).trim();
      const source = cites?.get(urlKey(m[5]));
      // [1](url) is a citation: a small numbered mark, the page on hover
      const cite = /^\[?\d{1,2}\]?$/.test(label);
      if (source && !cite) {
        // a page the turn read, cited after a claim: a small pill named as the answer names it
        out.push(
          <a key={k} href={m[5]} target="_blank" rel="noreferrer noopener" className="md-src" title={source.title || m[5]} onClick={click?.(m[5], source.title || m[5])}>
            {label}
          </a>
        );
      } else out.push(
        <a key={k} href={m[5]} target="_blank" rel="noreferrer noopener" className={cite ? "md-cite" : "md-link"} title={m[5]} onClick={click?.(m[5], source?.title || (cite ? m[5] : label))}>
          {cite ? label.replace(/[[\]]/g, "") : inline(label, k, cites, open)}
        </a>
      );
    } else if (m[6]) {
      out.push(
        <a key={k} href={m[6]} target="_blank" rel="noreferrer noopener" className="md-url" title={m[6]} onClick={click?.(m[6])}>
          {shortUrl(m[6])}
        </a>
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

type Props = { text: string; streaming?: boolean; sources?: Source[]; virtual?: boolean; onOpenLink?: LinkOpener };
const MarkdownBlock = memo(function MarkdownBlock({ text, streaming, sources, onOpenLink }: Props) {
  const cites: Cites = sources?.length ? new Map(sources.map((s) => [urlKey(s.url), s])) : undefined;
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const isBreak = (l: string) => /^(#{1,4}\s|\s*```|>\s?|[-*+]\s|\d+[.)]\s|\|)/.test(l) || /^\s*(---|\*\*\*)\s*$/.test(l);
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
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      blocks.push(
        <div key={k} className="md-table">
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
      );
      continue;
    }
    const bullet = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    if (bullet.test(line)) {
      const ordered = /\d/.test(bullet.exec(line)![2]);
      const items: ReactNode[] = [];
      while (i < lines.length && bullet.test(lines[i])) {
        const m = bullet.exec(lines[i])!;
        const nested = m[1].length >= 2;
        items.push(
          <li key={items.length} className={nested ? "nested" : undefined}>
            {inline(m[3], `${k}l${items.length}`, cites, onOpenLink)}
          </li>
        );
        i++;
      }
      blocks.push(ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>);
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

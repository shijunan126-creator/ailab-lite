"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api, API_BASE, downloadUrl, errorMessage, FileItem, ShareFile, ShareView } from "../../../../lib/api";
import { formatBytes, formatDate } from "../../../../lib/format";
import Markdown from "../../../../components/markdown";
import { IconDownload, IconFile, IconShare } from "../../../../components/icons";
import { ExtTile } from "../../../../components/library/file-tile";
import FileViewer, { FileSource, FileSourceContext } from "../../../../components/preview/file-viewer";

/** A shared file in the shape the viewer reads; it is always a finished file. */
function asFile(f: ShareFile): FileItem {
  const local = (url?: string | null) => (url ? url.replace(/^\/api(?=\/)/, "") : url);
  return { ...f, download_url: local(f.download_url) || "", thumbnail_url: f.content_type.startsWith("image/") ? local(f.thumbnail_url) : null, status: "ready", meta: {}, created_at: "", version: 1, versions: 1 };
}

function FileCard({ file, onOpen }: { file: ShareFile; onOpen: () => void }) {
  return (
    <div className="file-card">
      <ExtTile filename={file.filename} size={38} />
      <div className="grow">
        <div className="name" title={file.filename}>{file.filename}</div>
        <div className="meta">{formatBytes(file.size)}</div>
      </div>
      <button className="secondary sm" onClick={onOpen}>预览</button>
      <a className="btn secondary sm" href={downloadUrl(asFile(file).download_url)} target="_blank" rel="noreferrer" aria-label={`下载 ${file.filename}`}>
        <IconDownload size={15} /> <span className="dl-label">下载</span>
      </a>
    </div>
  );
}

export default function SharedPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const [view, setView] = useState<ShareView | null>(null);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [viewing, setViewing] = useState<{ files: FileItem[]; file: FileItem } | null>(null);
  const more = useRef<AbortController | null>(null);

  // the share's own file endpoints: only what is in the share is reachable, nothing else of the owner's library
  const source = useMemo<FileSource>(
    () => ({
      preview: (fileId) => `/v1/shares/${id}/files/${fileId}/preview`,
      asset: (fileId, name) => `${API_BASE}/v1/shares/${id}/files/${fileId}/preview/${encodeURIComponent(name)}`,
    }),
    [id]
  );

  useEffect(() => {
    const c = new AbortController();
    api<ShareView>(`/v1/shares/${id}/view`, { signal: c.signal })
      .then((v) => {
        if (c.signal.aborted) return;
        setView(v);
        document.title = `${v.title || "分享"} · AILab`;
        if (v.file) setViewing({ files: [asFile(v.file)], file: asFile(v.file) });
      })
      .catch((err) => !c.signal.aborted && setError(/^404 /.test(String(err?.message)) ? "这个分享不存在或已停止" : errorMessage(err)));
    return () => {
      c.abort();
      more.current?.abort();
      document.title = "AILab";
    };
  }, [id]);

  async function loadMore() {
    if (!view?.next_cursor || more.current) return;
    const c = new AbortController();
    more.current = c;
    setLoadingMore(true);
    try {
      const page = await api<ShareView>(`/v1/shares/${id}/view?cursor=${encodeURIComponent(view.next_cursor)}`, { signal: c.signal });
      setView((v) => (v ? { ...v, turns: [...v.turns, ...page.turns.filter((t) => !v.turns.some((x) => x.id === t.id))], next_cursor: page.next_cursor } : v));
    } catch (err) {
      if (!c.signal.aborted) setError(errorMessage(err));
    } finally {
      more.current = null;
      setLoadingMore(false);
    }
  }

  if (error && !view) {
    return (
      <main className="page page-narrow share-page">
        <div className="empty">
          <span className="project-mark"><IconShare size={20} /></span>
          <strong>{error}</strong>
        </div>
      </main>
    );
  }
  if (!view) return <main className="page page-narrow share-page"><div className="pr-loading"><span /><span /><span /></div></main>;

  return (
    <FileSourceContext.Provider value={source}>
      <main className="share-page">
        <header className="share-head">
          <h1>{view.title || "分享"}</h1>
          <div className="share-meta">
            <span>{view.owner.display_name}</span>
            <span>{formatDate(view.share.updated_at)}</span>
          </div>
        </header>
        {view.file ? <div className="files"><FileCard file={view.file} onOpen={() => setViewing({ files: [asFile(view.file!)], file: asFile(view.file!) })} /></div> : null}
        <div className="share-turns">
          {view.turns.map((t) => (
            <div key={t.id} className="turn">
              <div className="msg user">
                <div className="bubble">{t.prompt}</div>
                {t.attachments.length ? (
                  <div className="attach-row">
                    {t.attachments.map((a, i) => (
                      <span key={i} className="attach-chip"><IconFile size={13} /> {a.filename}</span>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="msg assistant">
                <div className="avatar-ai">A</div>
                <div className="assistant-body">
                  {t.answer.trim() ? <Markdown text={t.answer} /> : null}
                  {t.artifacts.length ? (
                    <div className="files">
                      {t.artifacts.map((a) => (
                        <FileCard key={a.id} file={a} onOpen={() => setViewing({ files: t.artifacts.map(asFile), file: asFile(a) })} />
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
          {view.next_cursor ? (
            <button className="link older-turns" disabled={loadingMore} onClick={loadMore}>
              {loadingMore ? "加载中…" : "更多"}
            </button>
          ) : null}
          {error ? <div className="alert error">{error}</div> : null}
        </div>
      </main>
      {viewing ? (
        <FileViewer files={viewing.files} file={viewing.file} onOpen={(file) => setViewing((v) => (v ? { ...v, file } : v))} onClose={() => setViewing(null)} />
      ) : null}
    </FileSourceContext.Provider>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { IconArrowLeft } from "../../../../components/icons";
import RunDetails from "../../../../components/chat/run-details";
import { api, Run } from "../../../../lib/api";

export default function RunPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const [conversation, setConversation] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<Run>(`/v1/runs/${id}`, { signal: controller.signal })
      .then((run) => { if (!controller.signal.aborted) setConversation(run.conversation_id || null); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [id]);
  return (
    <main className="page">
      {conversation ? <Link className="back" href={`/c/${conversation}`}><IconArrowLeft size={16} /> 返回对话</Link> : null}
      <RunDetails key={id} id={id} />
    </main>
  );
}

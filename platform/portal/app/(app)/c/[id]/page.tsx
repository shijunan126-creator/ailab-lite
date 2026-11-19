"use client";

import { useParams, useSearchParams } from "next/navigation";
import Chat from "../../../../components/chat/chat";

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const targetRun = useSearchParams().get("run");
  return <Chat key={id} id={id} targetRun={targetRun} />;
}

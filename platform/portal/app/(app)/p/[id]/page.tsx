"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import Chat from "../../../../components/chat/chat";
import { useProjects } from "../../../../lib/projects";

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id || "");
  const projects = useProjects();
  const project = projects?.find((p) => p.id === id);

  if (projects && !project) {
    return (
      <div className="lost">
        <div>
          <h1>找不到这个项目</h1>
          <p>它可能已被删除。</p>
          <Link className="btn" href="/">
            新对话
          </Link>
        </div>
      </div>
    );
  }
  return project ? <Chat key={id} id={null} project={project} /> : null;
}

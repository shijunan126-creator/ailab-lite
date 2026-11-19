import { useEffect } from "react";

/** The browser tab and history entry name the page: "技能 · AILab", or just "AILab" while there is no name yet. */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · AILab` : "AILab";
  }, [title]);
  useEffect(
    () => () => {
      document.title = "AILab";
    },
    []
  );
}

import { useEffect } from "react";

/** Names the browser tab and history entry after what the page shows: "季度计划 · AILab".
 * For titles known only in the browser (a conversation, a project). Next writes its route metadata title after a
 * client navigation commits, which can land after this effect, so the title is held while the page is mounted; on the
 * way out it returns to the default that every such page inherits, and the next route's own title takes over. */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return;
    const wanted = `${title} · AILab`;
    const hold = () => {
      if (document.title !== wanted) document.title = wanted;
    };
    hold();
    const watcher = new MutationObserver(hold);
    watcher.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => {
      watcher.disconnect();
      document.title = "AILab";
    };
  }, [title]);
}

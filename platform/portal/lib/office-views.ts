// Live side-pane viewers by the frame's view id. The id is what the viewer reports with PUT /conversations/{id}/view,
// so a capture request from the model reaches the tab that is actually showing that view, and no other.

export type OfficeCapture = { data: string; width: number; height: number; at: string; version: string };
type Capture = (at: string | null, maxWidth: number, signal?: AbortSignal) => Promise<OfficeCapture>;

const views = new Map<string, Capture>();

export function registerView(viewId: string, capture: Capture): () => void {
  views.set(viewId, capture);
  return () => {
    if (views.get(viewId) === capture) views.delete(viewId);
  };
}

export const hasView = (viewId: string) => views.has(viewId);

export function captureView(viewId: string, at: string | null, maxWidth = 1600, signal?: AbortSignal): Promise<OfficeCapture> | null {
  return views.get(viewId)?.(at, maxWidth, signal) ?? null;
}

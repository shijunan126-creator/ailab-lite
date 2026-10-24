/** In-page confirmation, instead of the browser's confirm(): one question at a time, answered by the DialogHost. */
export type ConfirmRequest = {
  title: string;
  body?: string;
  action?: string; // the button that goes ahead, e.g. "删除"
  danger?: boolean;
};
type Pending = ConfirmRequest & { id: number; resolve: (ok: boolean) => void };

let queue: Pending[] = [];
let next = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function confirmDialog(request: ConfirmRequest): Promise<boolean> {
  return new Promise((resolve) => {
    queue = [...queue, { ...request, id: ++next, resolve }];
    emit();
  });
}

export function answer(id: number, ok: boolean) {
  const found = queue.find((p) => p.id === id);
  if (!found) return;
  queue = queue.filter((p) => p.id !== id);
  found.resolve(ok);
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export const current = (): Pending | null => queue[0] || null;

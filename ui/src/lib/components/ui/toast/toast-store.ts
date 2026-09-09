import { writable } from 'svelte/store';

export type ToastKind = 'default' | 'success' | 'error' | 'warning';
export type ToastItem = { id: number; message: string; kind: ToastKind };

function createToastStore() {
  const { subscribe, update } = writable<ToastItem[]>([]);
  let nextId = 1;

  function push(message: string, kind: ToastKind = 'default', ttl = 3000) {
    const id = nextId++;
    update((items) => [...items, { id, message, kind }]);
    setTimeout(() => dismiss(id), ttl);
  }

  function dismiss(id: number) {
    update((items) => items.filter((i) => i.id !== id));
  }

  return {
    subscribe,
    show: (m: string) => push(m, 'default'),
    success: (m: string) => push(m, 'success'),
    error: (m: string) => push(m, 'error', 5000),
    warning: (m: string) => push(m, 'warning'),
    dismiss,
  };
}

export const toastStore = createToastStore();

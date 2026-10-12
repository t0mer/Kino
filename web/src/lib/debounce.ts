import { useEffect, useState } from "react";

export interface Debounced<A extends unknown[]> {
  (...args: A): void;
  cancel(): void;
  flush(): void;
}

/** Trailing-edge debounce: `fn` runs once, `wait` ms after the last call. */
export function debounce<A extends unknown[]>(fn: (...args: A) => void, wait: number): Debounced<A> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: A | null = null;
  const run = () => {
    timer = null;
    const args = pending;
    pending = null;
    if (args) fn(...args);
  };
  const d = ((...args: A) => {
    pending = args;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(run, wait);
  }) as Debounced<A>;
  d.cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    pending = null;
  };
  d.flush = () => {
    if (timer !== null) {
      clearTimeout(timer);
      run();
    }
  };
  return d;
}

/** `value`, but only updated after it stayed unchanged for `wait` ms. */
export function useDebouncedValue<T>(value: T, wait: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), wait);
    return () => clearTimeout(t);
  }, [value, wait]);
  return v;
}

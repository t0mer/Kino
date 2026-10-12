import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { debounce } from "./debounce";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("debounce", () => {
  it("runs once with the last arguments after the quiet period", () => {
    const fn = vi.fn();
    const d = debounce(fn, 300);
    d(1);
    vi.advanceTimersByTime(200);
    d(2);
    vi.advanceTimersByTime(299);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith(2);
  });
  it("cancel drops the pending call; flush runs it now", () => {
    const fn = vi.fn();
    const d = debounce(fn, 300);
    d("a");
    d.cancel();
    vi.advanceTimersByTime(500);
    expect(fn).not.toHaveBeenCalled();
    d("b");
    d.flush();
    expect(fn).toHaveBeenCalledWith("b");
    vi.advanceTimersByTime(500);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

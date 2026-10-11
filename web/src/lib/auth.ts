import { api } from "./api";

export type AuthMethod = "cloudflare" | "local";
export interface Me {
  method: AuthMethod;
  user: string;
}
export interface AuthStatus {
  setup_required: boolean;
}
export interface ReviewCount {
  total: number;
  by_kid: Record<string, number>;
}

export const authKeys = {
  me: ["auth", "me"] as const,
  status: ["auth", "status"] as const,
};

export const authApi = {
  status: () => api.get<AuthStatus>("/api/auth/status", { skipAuthRedirect: true }),
  me: () => api.get<Me>("/api/auth/me", { skipAuthRedirect: true }),
  login: (username: string, password: string) =>
    api.post<Me>("/api/auth/login", { username, password }, { skipAuthRedirect: true }),
  setup: (password: string) =>
    api.post<Me>("/api/auth/setup", { password }, { skipAuthRedirect: true }),
  logout: () => api.post<void>("/api/auth/logout", undefined, { skipAuthRedirect: true }),
};

export const reviewCount = () => api.get<ReviewCount>("/api/review/count");

/** Only same-app relative paths are allowed as a post-login target (no open redirect). */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return "/";
  if (next.startsWith("/login") || next.startsWith("/setup")) return "/";
  return next;
}

export const MIN_SETUP_PASSWORD = 12;

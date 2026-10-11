import {
  QueryClient,
  type QueryKey,
  useQuery,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { isApiError } from "./api";

export const LIVE_INTERVAL_MS = 60_000;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        // Never retry client errors (401/403/404/422...); retry network/5xx twice.
        retry: (count, err) => !(isApiError(err) && err.status >= 400 && err.status < 500) && count < 2,
      },
      mutations: { retry: false },
    },
  });
}

/** A query for live data (statuses, counts): refreshed every ~60 s while the tab is visible. */
export function useLive<T>(
  queryKey: QueryKey,
  queryFn: () => Promise<T>,
  options: Omit<UseQueryOptions<T, Error, T, QueryKey>, "queryKey" | "queryFn"> = {},
) {
  return useQuery<T, Error, T, QueryKey>({
    queryKey,
    queryFn,
    refetchInterval: LIVE_INTERVAL_MS,
    refetchIntervalInBackground: false,
    ...options,
  });
}

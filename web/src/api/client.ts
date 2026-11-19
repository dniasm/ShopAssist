import { ApiError, errorFromResponse } from "./errors";

/** Turns (method, path, query, body) into parsed JSON, or throws an ApiError. */
export type QueryValue = string | number | boolean | undefined | readonly string[];
export type Query = Record<string, QueryValue>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
}

export interface Transport {
  (method: string, path: string, opts: RequestOptions, token: string | null): Promise<unknown>;
}

let authToken: string | null = null;
let unauthenticatedHandler: (() => void) | null = null;

export function setAuthToken(token: string | null): void { authToken = token; }
/** Called when a request that carried a token comes back 401 (expired or forged session). */
export function setUnauthenticatedHandler(fn: (() => void) | null): void { unauthenticatedHandler = fn; }

export function buildQueryString(query: Query | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === "") continue;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      params.append(key, String(value));
    } else {
      for (const v of value) params.append(key, v);
    }
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

const fetchTransport: Transport = async (method, path, opts, token) => {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(`/api${path}${buildQueryString(opts.query)}`, {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network", "Network request failed");
  }

  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try { parsed = JSON.parse(text); } catch { parsed = undefined; }
  }
  if (!res.ok) throw errorFromResponse(res.status, parsed);
  if (parsed === undefined && text) throw new ApiError(res.status, "unknown", "The server sent an unreadable response");
  return parsed;
};

const CREDENTIAL_PATHS = new Set(["/session", "/signup"]);

/** path is relative to /api, e.g. "/products". Resolves to the parsed JSON body typed by the caller's endpoint wrapper. */
export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const token = authToken;
  try {
    // The one place untyped JSON becomes T: each wrapper in endpoints.ts names a type generated from the OpenAPI schema.
    return (await fetchTransport(method, path, opts, token)) as T;
  } catch (err) {
    // A 401 from the credential routes is a wrong password, not an expired session: it must not sign anyone out.
    if (err instanceof ApiError && err.isUnauthenticated && token && authToken === token && !CREDENTIAL_PATHS.has(path)) {
      unauthenticatedHandler?.();
    }
    throw err;
  }
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

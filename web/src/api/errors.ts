import type { ErrorCode } from "./types";

/** Every failure from the API client is one of these, so callers never inspect raw responses. */
export class ApiError extends Error {
  readonly status: number;
  /** The server's error code, or "network" / "unknown" for failures that never produced an envelope. */
  readonly code: ErrorCode | "network" | "unknown";
  readonly details?: Record<string, unknown>;

  constructor(status: number, code: ApiError["code"], message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isUnauthenticated(): boolean { return this.code === "unauthenticated"; }
  get isNotFound(): boolean { return this.code === "not_found"; }
  get isNetwork(): boolean { return this.code === "network" || this.code === "unavailable"; }
}

// `satisfies` makes this fail to compile if the API adds or removes an error code, so the list cannot drift.
const KNOWN_CODES = {
  invalid: true, unauthenticated: true, forbidden: true, rate_limited: true, not_found: true, conflict: true, out_of_stock: true,
  payment_declined: true, unavailable: true, internal: true,
} satisfies Record<ErrorCode, true>;

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && Object.hasOwn(KNOWN_CODES, value);
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Parses the `{error: {code, message, details?}}` envelope. Anything else becomes code "unknown". */
export function errorFromResponse(status: number, body: unknown): ApiError {
  if (isRecord(body) && isRecord(body.error)) {
    const { code, message, details } = body.error;
    return new ApiError(
      status,
      isErrorCode(code) ? code : "unknown",
      typeof message === "string" && message ? message : `Request failed (${status})`,
      isRecord(details) ? details : undefined,
    );
  }
  // No envelope at all: a gateway or proxy error page (e.g. the API process is down), not an application error.
  if (status >= 500) return new ApiError(status, "unavailable", `The shop API is not responding (HTTP ${status})`);
  return new ApiError(status, "unknown", `Request failed (${status})`);
}

/** A short sentence for the UI. Never shows stack traces or raw bodies. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "network") return "Can't reach the shop. Check your connection and try again.";
    if (err.code === "unavailable") {
      const svc = typeof err.details?.service === "string" ? ` (${err.details.service})` : "";
      return `A shop service is unavailable${svc}. Try again in a moment.`;
    }
    return err.message;
  }
  return "Something went wrong.";
}

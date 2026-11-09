import { randomUUID } from "node:crypto";

const DEFAULT_DISPATCHER_URL = "http://127.0.0.1:8081";
const DEFAULT_TIMEOUT_MS = 7_000;
const MAX_TIMEOUT_MS = 60_000;
const ERROR_CATEGORIES = new Set([
  "VALIDATION",
  "AUTHORIZATION",
  "BUSINESS",
  "INFRASTRUCTURE",
  "RESPONSE",
]);

export type ToolObservation = {
  success: boolean;
  tool: string;
  error: null | {
    code: string;
    category: string;
    message: string;
    recoverable: boolean;
    retry_hint: string | null;
    approval?: unknown;
  };
  data: unknown;
  trace_id: string;
};

export type AdapterOptions = {
  dispatcherUrl?: string;
  token?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
};

function localDispatcherOrigin(raw: string): string {
  const url = new URL(raw);
  const localHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !localHosts.has(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("Dispatcher URL must be a credential-free loopback HTTP(S) origin");
  }
  return url.origin;
}

function timeoutFromEnvironment(raw: string | undefined): number {
  if (raw === undefined || raw === "") return DEFAULT_TIMEOUT_MS;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > MAX_TIMEOUT_MS) {
    throw new Error(`OPENCLAW_ECOMMERCE_TIMEOUT_MS must be between 1 and ${MAX_TIMEOUT_MS}`);
  }
  return value;
}

function failure(tool: string, code: string, traceId: string): ToolObservation {
  const timeout = code === "UPSTREAM_TIMEOUT";
  const unavailable = code === "UPSTREAM_UNAVAILABLE";
  return {
    success: false,
    tool,
    error: {
      code,
      category: code === "INVALID_BACKEND_RESPONSE" ? "RESPONSE" : "INFRASTRUCTURE",
      message: timeout
        ? "Dispatcher request timed out"
        : unavailable
          ? "Dispatcher is unavailable"
          : code === "INVALID_BACKEND_RESPONSE"
            ? "Dispatcher response failed validation"
            : "Adapter runtime configuration is invalid",
      recoverable: timeout || unavailable,
      retry_hint:
        code === "INVALID_BACKEND_RESPONSE"
          ? "Investigate the Dispatcher response contract before retrying."
          : "A read may be retried later. Never automatically retry a write.",
    },
    data: null,
    trace_id: traceId,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseObservation(value: unknown, expectedTool: string): ToolObservation | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.success !== "boolean" ||
    value.tool !== expectedTool ||
    typeof value.trace_id !== "string" ||
    value.trace_id.length === 0
  ) return null;
  if (value.success) {
    if (value.error !== null) return null;
  } else {
    if (!isRecord(value.error)) return null;
    if (
      typeof value.error.code !== "string" ||
      !ERROR_CATEGORIES.has(String(value.error.category)) ||
      typeof value.error.message !== "string" ||
      typeof value.error.recoverable !== "boolean" ||
      !(value.error.retry_hint === null || typeof value.error.retry_hint === "string") ||
      value.data !== null
    ) return null;
  }
  return value as ToolObservation;
}

export async function executeDispatcherTool(
  tool: string,
  arguments_: Record<string, unknown>,
  options: AdapterOptions = {},
): Promise<ToolObservation> {
  const localTraceId = randomUUID();
  const token = options.token ?? process.env.OPENCLAW_ECOMMERCE_API_TOKEN;
  if (!token) {
    return {
      ...failure(tool, "UPSTREAM_UNAVAILABLE", localTraceId),
      error: {
        code: "UNAUTHENTICATED",
        category: "AUTHORIZATION",
        message: "Trusted runtime credential is not configured",
        recoverable: false,
        retry_hint: null,
      },
    };
  }

  let dispatcherOrigin: string;
  let timeoutMs: number;
  try {
    dispatcherOrigin = localDispatcherOrigin(
      options.dispatcherUrl ?? process.env.OPENCLAW_ECOMMERCE_DISPATCHER_URL ?? DEFAULT_DISPATCHER_URL,
    );
    timeoutMs = options.timeoutMs ?? timeoutFromEnvironment(process.env.OPENCLAW_ECOMMERCE_TIMEOUT_MS);
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_TIMEOUT_MS) throw new Error();
  } catch {
    return failure(tool, "INTERNAL_ERROR", localTraceId);
  }

  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort("adapter-timeout"), timeoutMs);
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeoutController.signal])
    : timeoutController.signal;
  try {
    const response = await (options.fetchImpl ?? fetch)(`${dispatcherOrigin}/tools/execute`, {
      method: "POST",
      redirect: "error",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ tool, arguments: arguments_ }),
      signal,
    });
    const responseTrace = response.headers.get("x-request-id") || localTraceId;
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      return failure(tool, "INVALID_BACKEND_RESPONSE", responseTrace);
    }
    const parsed = parseObservation(body, tool);
    if (!parsed || (response.headers.has("x-request-id") && parsed.trace_id !== responseTrace)) {
      return failure(tool, "INVALID_BACKEND_RESPONSE", responseTrace);
    }
    return parsed;
  } catch (error) {
    const timedOut = timeoutController.signal.aborted && !options.signal?.aborted;
    return failure(tool, timedOut ? "UPSTREAM_TIMEOUT" : "UPSTREAM_UNAVAILABLE", localTraceId);
  } finally {
    clearTimeout(timer);
  }
}

export const adapterInternals = { localDispatcherOrigin, parseObservation, timeoutFromEnvironment };

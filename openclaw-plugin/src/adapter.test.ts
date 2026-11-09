import { describe, expect, it, vi } from "vitest";

import { adapterInternals, executeDispatcherTool } from "./adapter.js";

const observation = (tool = "get_order") => ({
  success: true,
  tool,
  error: null,
  data: { order_id: "ORDER-1" },
  trace_id: "dispatcher-trace",
});

describe("OpenClaw Dispatcher adapter", () => {
  it("accepts only loopback Dispatcher origins", () => {
    expect(adapterInternals.localDispatcherOrigin("http://127.0.0.1:8081")).toBe("http://127.0.0.1:8081");
    expect(() => adapterInternals.localDispatcherOrigin("http://127.0.0.1:8080/v1/orders")).toThrow();
    expect(() => adapterInternals.localDispatcherOrigin("https://example.com")).toThrow();
  });

  it("forwards runtime auth outside tool arguments and preserves observations", async () => {
    const fetchImpl = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      expect(init?.headers).toMatchObject({ authorization: "Bearer runtime-secret" });
      expect(JSON.parse(String(init?.body))).toEqual({
        tool: "get_order",
        arguments: { order_id: "ORDER-1" },
      });
      expect(String(init?.body)).not.toContain("runtime-secret");
      return new Response(JSON.stringify(observation()), {
        status: 200,
        headers: { "content-type": "application/json", "x-request-id": "dispatcher-trace" },
      });
    });
    const result = await executeDispatcherTool("get_order", { order_id: "ORDER-1" }, {
      token: "runtime-secret",
      fetchImpl: fetchImpl as typeof fetch,
    });
    expect(result).toEqual(observation());
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("does not retry an unknown write outcome", async () => {
    const unknown = {
      success: false,
      tool: "refund_order",
      error: {
        code: "WRITE_OUTCOME_UNKNOWN",
        category: "INFRASTRUCTURE",
        message: "The write may have executed",
        recoverable: false,
        retry_hint: "Do not retry this write.",
      },
      data: null,
      trace_id: "write-trace",
    };
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(unknown), {
      status: 502,
      headers: { "content-type": "application/json" },
    }));
    expect(await executeDispatcherTool("refund_order", {
      order_id: "ORDER-1",
      amount: 100,
      idempotency_key: "same-operation",
    }, { token: "runtime-secret", fetchImpl: fetchImpl as typeof fetch })).toEqual(unknown);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("fails closed on malformed responses", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ success: true }), {
      status: 200,
      headers: { "content-type": "application/json", "x-request-id": "bad-response-trace" },
    }));
    const result = await executeDispatcherTool("get_order", {}, {
      token: "runtime-secret",
      fetchImpl: fetchImpl as typeof fetch,
    });
    expect(result.error?.code).toBe("INVALID_BACKEND_RESPONSE");
    expect(result.trace_id).toBe("bad-response-trace");
  });

  it("rejects a trace mismatch between the response header and observation", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(observation()), {
      status: 200,
      headers: { "content-type": "application/json", "x-request-id": "different-trace" },
    }));
    const result = await executeDispatcherTool("get_order", {}, {
      token: "runtime-secret",
      fetchImpl: fetchImpl as typeof fetch,
    });
    expect(result.error?.code).toBe("INVALID_BACKEND_RESPONSE");
    expect(result.trace_id).toBe("different-trace");
  });

  it("returns a structured timeout and performs one request", async () => {
    const fetchImpl = vi.fn((_url: string | URL | Request, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }));
    const result = await executeDispatcherTool("list_products", {}, {
      token: "runtime-secret",
      timeoutMs: 5,
      fetchImpl: fetchImpl as typeof fetch,
    });
    expect(result.error?.code).toBe("UPSTREAM_TIMEOUT");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("does not make a request without a trusted runtime credential", async () => {
    const fetchImpl = vi.fn();
    const original = process.env.OPENCLAW_ECOMMERCE_API_TOKEN;
    delete process.env.OPENCLAW_ECOMMERCE_API_TOKEN;
    try {
      const result = await executeDispatcherTool("list_products", {}, { fetchImpl: fetchImpl as typeof fetch });
      expect(result.error?.code).toBe("UNAUTHENTICATED");
      expect(fetchImpl).not.toHaveBeenCalled();
    } finally {
      if (original !== undefined) process.env.OPENCLAW_ECOMMERCE_API_TOKEN = original;
    }
  });
});

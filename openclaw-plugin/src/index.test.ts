import { describe, expect, it } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";

import plugin from "./index.js";
import { dispatcherTools } from "./registry.generated.js";

describe("tool registration metadata", () => {
  it("matches the generated Dispatcher registry exactly", () => {
    const metadata = getToolPluginMetadata(plugin);
    expect(metadata?.tools.map((item) => item.name)).toEqual(
      dispatcherTools.map((item) => item.name),
    );
    expect(metadata?.tools).toHaveLength(24);
    expect(metadata?.tools.map((item) => item.name)).not.toContain("approve_approval");
    expect(metadata?.tools.map((item) => item.name)).not.toContain("reject_approval");
  });

  it("requires idempotency keys for Agent financial writes", () => {
    for (const name of ["refund_order", "change_price"]) {
      const definition = dispatcherTools.find((item) => item.name === name);
      expect(definition?.parameters.required).toContain("idempotency_key");
    }
  });
});

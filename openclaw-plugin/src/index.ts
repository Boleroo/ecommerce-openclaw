import type { TSchema } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

import { executeDispatcherTool } from "./adapter.js";
import { dispatcherTools } from "./registry.generated.js";

export default defineToolPlugin({
  id: "ecommerce-dispatcher",
  name: "E-commerce Controlled Dispatcher",
  description: "Typed e-commerce tools routed through the authenticated controlled Dispatcher.",
  tools: (tool) =>
    dispatcherTools.map((definition) =>
      tool({
        name: definition.name,
        label: definition.name.replaceAll("_", " "),
        description: definition.description,
        parameters: definition.parameters as unknown as TSchema,
        optional: definition.optional,
        execute: (params, _config, context) =>
          executeDispatcherTool(definition.name, params as Record<string, unknown>, {
            signal: context.signal,
          }),
      }),
    ),
});

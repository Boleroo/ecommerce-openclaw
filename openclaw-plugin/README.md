# OpenClaw E-commerce Dispatcher Tool Plugin

This OpenClaw 2026.7.1 TypeScript Tool Plugin exposes the Dispatcher registry as native typed tools. `scripts/generate_openclaw_tools.py` generates its TypeBox-compatible JSON Schemas from the Python registry; do not edit `src/registry.generated.ts` manually.

The plugin sends calls only to a loopback Dispatcher origin. It reads `OPENCLAW_ECOMMERCE_API_TOKEN` from the trusted OpenClaw process environment and never accepts credentials, identity, tenant, or backend URLs as tool arguments. `OPENCLAW_ECOMMERCE_DISPATCHER_URL` defaults to `http://127.0.0.1:8081`; non-loopback origins and origins containing paths or credentials are rejected. `OPENCLAW_ECOMMERCE_TIMEOUT_MS` defaults to 7000 and may be 1–60000.

Build, validate, and test from this directory:

```sh
npm install
npm run plugin:validate
npm test
```

Financial write schemas require a stable `idempotency_key` for Agent calls. All POST/PATCH tools are optional OpenClaw tools and require explicit operator allowlisting. The plugin performs exactly one Dispatcher HTTP request per invocation and never retries writes (or reads); the Dispatcher retains its existing bounded read-only backend retry policy.

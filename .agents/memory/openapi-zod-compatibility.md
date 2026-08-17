---
name: OpenAPI/Zod compatibility
description: A generator/runtime compatibility constraint for numeric API schemas in this workspace.
---

When adding numeric fields to the OpenAPI contract, prefer `type: number` for counts and limits unless the workspace Zod version has been upgraded to support the generator's emitted integer helper.

**Why:** The current Orval output uses `zod.int()` for OpenAPI `integer`, while the installed Zod runtime does not expose that helper; codegen succeeds but the library typecheck fails.

**How to apply:** After every OpenAPI change, run codegen and the library typecheck before relying on generated client or server types.
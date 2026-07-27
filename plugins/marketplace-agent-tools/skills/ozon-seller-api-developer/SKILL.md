---
name: ozon-seller-api-developer
description: Develop or audit Ozon Seller API endpoints, schemas, pagination, buffered operations, provider configuration, snapshots, and consumers.
---

# Ozon Seller API Developer

The canonical provider is `ozon_seller_api`, and `ozon_seller_api/DESIGN.md` owns its stable client, configuration, endpoint, schema, buffering, error, lifecycle, and documentation-snapshot contracts.

Generic outbound HTTP behavior follows `project-standards:http-api-client-developer`. Generic retry behavior follows `project-standards:python-retry-developer` and `retry_runtime/DESIGN.md`. Generic runtime configuration follows `project-standards:runtime-config-developer`. Generic submodule publication uses `agent-workflows:git-commit`.

Host code follows `ozon_seller_api/DESIGN.md`, section `Host integration`, for the public provider boundary and mechanically enforceable host restrictions. This skill does not copy that stable contract.

An endpoint change is validated against the pinned OpenAPI snapshot and the current official Ozon contract. Tests cover method, path, payload, response parsing, pagination, provider failure, malformed response, timeout, allowed retry, forbidden ambiguous retry, and buffered state when applicable.

Official `api-seller.ozon.ru` operations and authenticated `seller.ozon.ru` browser flows are separate boundaries. Browser-session behavior is not moved into the official Seller API provider merely because both systems belong to Ozon.

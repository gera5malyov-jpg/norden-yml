# Implementation status

- Branch: `feat/samson-kit-sync`
- Local verification: Python core tests passed; Webasyst `yandexkitsync` v0.9.6 standalone tests passed.
- Workflow is intentionally `workflow_dispatch` only until the Webasyst v0.9.6 exclusion is deployed and the first dry-run/live smoke test succeeds.
- Required GitHub Actions secrets: `SAMSON_API_KEY`, `YANDEX_KIT_TOKEN`.
- After successful smoke test, add schedule `0 7 * * 1` (Monday 10:00 Moscow time).

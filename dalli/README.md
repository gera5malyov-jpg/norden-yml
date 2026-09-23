# Dalli: аккаунты по складам

В интеграции используются два отдельных аккаунта Dalli.

| Аккаунт | Назначение | GitHub Secret | API endpoint |
|---|---|---|---|
| 1 — СПБ | Отправка со складов Санкт-Петербурга | `DALLI_TOKEN` | `https://spbapi.dalli-service.com/v1` |
| 2 — МСК | Отправка со складов Москвы | `DALLI_TOKEN_MSK` | `https://api.dalli-service.com/v1` |

## Выбор аккаунта

В GitHub Actions workflow **Dalli API — СПБ и МСК** используется параметр `account`:

- `spb` — первый аккаунт, склады СПБ;
- `msk` — второй аккаунт, склады МСК.

Секреты не должны храниться в файлах репозитория. Для второго аккаунта токен необходимо добавить в **Settings → Secrets and variables → Actions** под именем `DALLI_TOKEN_MSK`.

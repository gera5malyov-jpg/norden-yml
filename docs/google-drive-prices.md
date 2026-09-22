# Прайсы поставщиков в Google Drive

Папка Google Drive: **Прайсы**  
Folder ID: `1iKh4RGaq1ViyWmHENjp8oeuauHmWEyj9`

Назначение: хранить XLSX/CSV/XML/PDF и другие прайсы поставщиков в Google Drive, а GitHub Actions использовать только для временного чтения файлов во время запуска. Прайсы не коммитятся в репозиторий.

## Структура

Рекомендуется создавать внутри папки отдельную подпапку на каждого поставщика, например:

- Norden
- Riva
- Самсон
- 4 Сезона
- Levmar
- DEEPHOUSE

## Авторизация GitHub Actions

В репозитории должен быть secret `GOOGLE_SERVICE_ACCOUNT_JSON` с JSON-ключом сервисного аккаунта Google Cloud. Самой папке **Прайсы** нужно дать этому сервисному аккаунту доступ «Читатель».

Workflow: `.github/workflows/google-drive-prices.yml`

Режим `check` только проверяет доступ и выводит список файлов.  
Режим `download` скачивает содержимое папки во временный каталог `runtime/prices` на GitHub runner. После завершения запуска временные файлы удаляются вместе с runner и не занимают место в Git-репозитории.

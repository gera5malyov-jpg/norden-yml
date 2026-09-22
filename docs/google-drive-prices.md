# Прайсы поставщиков в Google Drive

Папка Google Drive: **Прайсы**  
Ссылка: https://drive.google.com/drive/folders/1iKh4RGaq1ViyWmHENjp8oeuauHmWEyj9  
Folder ID: `1iKh4RGaq1ViyWmHENjp8oeuauHmWEyj9`

Назначение: хранить XLSX/CSV/XML/PDF и другие прайсы поставщиков в Google Drive, а GitHub Actions использовать только для временного чтения файлов во время запуска. Прайсы не коммитятся в репозиторий.

## Доступ

Папка настроена как **«Все, у кого есть ссылка» → «Читатель»**. Поэтому GitHub Actions читает её напрямую по публичной ссылке. Сервисный аккаунт Google и secret `GOOGLE_SERVICE_ACCOUNT_JSON` для этой папки не нужны.

## Структура

Внутри папки можно создавать отдельные подпапки поставщиков, например:

- Norden
- Riva
- Самсон
- 4 Сезона
- Levmar
- DEEPHOUSE

Workflow: `.github/workflows/google-drive-prices.yml`

Режим `check` проверяет доступ и выводит список файлов.  
Режим `download` скачивает содержимое папки во временный каталог `runtime/prices` на GitHub runner. Каталог включён в `.gitignore`; после завершения запуска временные файлы удаляются вместе с runner и не занимают место в Git-репозитории.

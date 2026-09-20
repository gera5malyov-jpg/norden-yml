# Webasyst API automation

This directory contains the Webasyst/Shop-Script API client used by GitHub Actions.

## Required GitHub secret

Create this repository secret:

`WEBASYST_API_TOKEN`

Repository path:

`Settings -> Secrets and variables -> Actions -> New repository secret`

Do not put the token into source code, workflow YAML, issues, logs, or committed files.

## Site

Default API root:

`https://profikompany.ru`

It can be overridden with the `WEBASYST_BASE_URL` environment variable.

## Connection test

Run the workflow:

`Webasyst API check`

The workflow performs a read-only call to:

`shop.type.getList`

No products, prices, stock, categories, or orders are changed by the test.

## API client

`webasyst/client.py` sends the token only in the HTTP header:

`Authorization: Bearer <token>`

API methods are called through:

`https://profikompany.ru/api.php/<method>`

This module is intended to be reused by future catalog, price, stock, category and order automations.

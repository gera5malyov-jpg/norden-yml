import os
import tempfile
from decimal import Decimal, ROUND_CEILING
from urllib.parse import urlparse

from .mapper import characteristics_from_offer
from .rules import desired_stock


def _lower(value):
    return str(value or '').strip().casefold()


def _kit_money(value):
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_CEILING)
    except Exception:
        return None


def _current_stock(variant, warehouse_id):
    for row in variant.get('stocks') or []:
        if str(row.get('warehouse_id', '')) == str(warehouse_id):
            try:
                return int(row.get('quantity', 0))
            except Exception:
                return None
    return 0


def build_price_update(offer, variant):
    if offer.price is None:
        return None
    pricing = variant.get('pricing') or {}
    if (
        _kit_money(pricing.get('price')) == _kit_money(offer.price)
        and _kit_money(pricing.get('manual_discount_price')) == _kit_money(offer.price)
    ):
        return None
    variant_id = str(variant.get('id', '')).strip()
    if not variant_id:
        return None
    desired = f'{offer.price:.2f}'
    return {
        'variant_id': variant_id,
        'price': desired,
        'manual_discount_price': desired,
    }


def build_stock_updates(offer, variant, warehouse_ids):
    variant_id = str(variant.get('id', '')).strip()
    if not variant_id:
        return []
    quantity = desired_stock(offer.available)
    out = []
    for title in ('СПБ', 'МСК'):
        warehouse_id = str(warehouse_ids[title])
        if _current_stock(variant, warehouse_id) == quantity:
            continue
        out.append({
            'variant_id': variant_id,
            'warehouse_id': warehouse_id,
            'quantity': quantity,
        })
    return out


def absent_zero_updates(index, seen_skus, warehouse_ids, *, complete):
    if not complete:
        return []
    out = []
    for sku, variant in index.items():
        if sku in seen_skus:
            continue
        variant_id = str(variant.get('id', '')).strip()
        if not variant_id:
            continue
        for title in ('СПБ', 'МСК'):
            warehouse_id = str(warehouse_ids[title])
            if _current_stock(variant, warehouse_id) == 0:
                continue
            out.append({
                'variant_id': variant_id,
                'warehouse_id': warehouse_id,
                'quantity': 0,
            })
    return out


def _category_chain(category_id, categories):
    if not category_id:
        return []
    out = []
    seen = set()
    current = str(category_id)
    while current and current not in seen:
        seen.add(current)
        row = categories.get(current)
        if row is None:
            break
        out.append(row)
        current = str(row.parent_id or '').strip()
    out.reverse()
    return out


class SyncRunner:
    def __init__(self, snapshot, kit, http, *, dry_run=False, max_items=None):
        self.snapshot = snapshot
        self.kit = kit
        self.http = http
        self.dry_run = bool(dry_run)
        self.max_items = int(max_items) if max_items not in (None, '', 0, '0') else None
        self.kit_categories = []
        self.kit_characteristics = []
        self.report = {
            'status': 'pending',
            'dry_run': self.dry_run,
            'catalog_complete': False,
            'offers_seen': 0,
            'active_offers': 0,
            'unavailable_offers': 0,
            'new_products_created': 0,
            'price_changes': 0,
            'spb_stock_changes': 0,
            'msk_stock_changes': 0,
            'unavailable_to_zero': 0,
            'absent_to_zero': 0,
            'duplicate_kit_skus': 0,
            'invalid_price_count': 0,
            'image_failure_count': 0,
            'warning_count': 0,
            'warnings': [],
            'error_count': 0,
            'errors': [],
        }

    def _warn(self, message):
        self.report['warning_count'] += 1
        if len(self.report['warnings']) < 200:
            self.report['warnings'].append(str(message)[:500])

    def _record_error(self, sku, exc):
        self.report['error_count'] += 1
        if len(self.report['errors']) < 200:
            self.report['errors'].append({
                'sku': str(sku),
                'message': str(exc)[:500],
            })

    def _ensure_category(self, offer, categories):
        chain = _category_chain(offer.category_id, categories)
        if not chain:
            raise RuntimeError(f'missing Liga category for {offer.kit_sku}')
        parent = ''
        for source in chain:
            title = str(source.name or '').strip()
            matches = [
                row for row in self.kit_categories
                if _lower(row.get('title')) == _lower(title)
                and str(row.get('parent_id') or '') == parent
            ]
            if len(matches) > 1:
                raise RuntimeError(f'ambiguous KIT category {title!r}')
            if matches:
                category_id = str(matches[0].get('id', '')).strip()
            elif self.dry_run:
                category_id = f'dry-category-{source.source_id}'
                self.kit_categories.append({
                    'id': category_id,
                    'title': title,
                    'parent_id': parent,
                })
            else:
                created = self.kit.create_category(title, parent or None)
                category_id = str(created.get('id', '')).strip()
                if not category_id:
                    raise RuntimeError(f'KIT did not return category id for {title!r}')
                normalized = dict(created)
                normalized.setdefault('parent_id', parent)
                self.kit_categories.append(normalized)
            parent = category_id
        return parent

    def _ensure_characteristics(self, offer):
        out = []
        for title, values in characteristics_from_offer(offer):
            values = [str(v).strip() for v in values if str(v).strip()]
            if not values:
                continue
            desired_type = 'MULTIPLE_STRING' if len(values) > 1 else 'STRING'
            matches = [
                row for row in self.kit_characteristics
                if _lower(row.get('title')) == _lower(title)
                and str(row.get('type', '')).strip().upper() == desired_type
            ]
            if len(matches) > 1:
                self._warn(f'ambiguous KIT characteristic skipped: {title}')
                continue
            if matches:
                characteristic_id = str(matches[0].get('id', '')).strip()
            elif self.dry_run:
                characteristic_id = f'dry-char-{len(self.kit_characteristics) + 1}'
                self.kit_characteristics.append({
                    'id': characteristic_id,
                    'title': title,
                    'type': desired_type,
                    'select_mode': 'MULTIPLE' if len(values) > 1 else 'SINGLE',
                })
            else:
                created = self.kit.create_characteristic(
                    title,
                    desired_type,
                    'MULTIPLE' if len(values) > 1 else 'SINGLE',
                )
                characteristic_id = str(created.get('id', '')).strip()
                if not characteristic_id:
                    self._warn(f'KIT characteristic creation failed: {title}')
                    continue
                self.kit_characteristics.append(created)
            out.append({
                'characteristic_id': characteristic_id,
                'value': values[0],
                'values': values,
            })
        return out

    def _prepare_media(self, offer):
        if self.dry_run:
            return []
        media = []
        failures = []
        for url in offer.images:
            try:
                suffix = os.path.splitext(urlparse(url).path)[1] or '.jpg'
                with tempfile.TemporaryDirectory(prefix='liga-img-') as td:
                    path = os.path.join(td, 'image' + suffix[:10])
                    self.http.download_to_file(url, path)
                    uploaded = self.kit.upload_image(path)
                    file_id = str(uploaded.get('id', '')).strip()
                    if not file_id:
                        raise RuntimeError('KIT did not return image file id')
                    media.append({
                        'type': 'IMAGE',
                        'display_sequence': len(media),
                        'image_id': file_id,
                    })
            except Exception as exc:
                failures.append(str(exc))
        if len(media) != len(offer.images):
            detail = failures[0] if failures else 'unknown image error'
            raise RuntimeError(
                f'incomplete image set for {offer.kit_sku}: '
                f'prepared {len(media)} of {len(offer.images)}; {detail}'
            )
        return media

    def _new_payload(self, offer, product_id, warehouse_ids, characteristics, media):
        if offer.price is None:
            raise RuntimeError(f'invalid price for {offer.kit_sku}')
        desired_price = f'{offer.price:.2f}'
        quantity = desired_stock(offer.available)
        payload = {
            'sku': offer.kit_sku,
            'name': offer.name,
            'description': offer.description,
            'status': 'PUBLISHED',
            'product_id': str(product_id),
            'pricing': {
                'price': desired_price,
                'manual_discount_price': desired_price,
            },
            'stocks': [
                {
                    'warehouse_id': str(warehouse_ids['СПБ']),
                    'quantity': quantity,
                    'reserved': 0,
                },
                {
                    'warehouse_id': str(warehouse_ids['МСК']),
                    'quantity': quantity,
                    'reserved': 0,
                },
            ],
        }
        if offer.vendor:
            payload['brand'] = offer.vendor
        if characteristics:
            payload['characteristics'] = characteristics
        if media:
            payload['media'] = media
        return payload

    def _flush_prices(self, batch):
        if batch and not self.dry_run:
            self.kit.bulk_update_prices(batch)
        batch.clear()

    def _flush_stocks(self, batch):
        if batch and not self.dry_run:
            self.kit.bulk_update_stocks(batch)
        batch.clear()

    def run(self):
        warehouse_ids = {
            'СПБ': self.kit.resolve_warehouse_exact('СПБ'),
            'МСК': self.kit.resolve_warehouse_exact('МСК'),
        }
        kit_index, duplicates = self.kit.index_liga_variants()
        self.report['duplicate_kit_skus'] = len(duplicates)
        self.kit_categories = self.kit.list_categories()
        self.kit_characteristics = self.kit.list_characteristics()

        seen = set()
        price_batch = []
        stock_batch = []
        offers = self.snapshot.offers
        if self.max_items is not None:
            offers = offers[:self.max_items]

        for offer in offers:
            seen.add(offer.kit_sku)
            self.report['offers_seen'] += 1
            if offer.available:
                self.report['active_offers'] += 1
            else:
                self.report['unavailable_offers'] += 1

            if offer.kit_sku in duplicates:
                self._warn(f'duplicate KIT Liga SKU skipped: {offer.kit_sku}')
                continue

            variant = kit_index.get(offer.kit_sku)
            if variant is not None:
                price_update = build_price_update(offer, variant)
                if offer.price is None:
                    self.report['invalid_price_count'] += 1
                    self._warn(f'invalid feed price; KIT price unchanged: {offer.kit_sku}')
                elif price_update:
                    price_batch.append(price_update)
                    self.report['price_changes'] += 1

                stock_updates = build_stock_updates(offer, variant, warehouse_ids)
                if stock_updates and not offer.available:
                    self.report['unavailable_to_zero'] += 1
                for update in stock_updates:
                    if update['warehouse_id'] == warehouse_ids['СПБ']:
                        self.report['spb_stock_changes'] += 1
                    elif update['warehouse_id'] == warehouse_ids['МСК']:
                        self.report['msk_stock_changes'] += 1
                    stock_batch.append(update)

                if len(price_batch) >= 500:
                    self._flush_prices(price_batch)
                if len(stock_batch) >= 500:
                    self._flush_stocks(stock_batch)
                continue

            if offer.price is None:
                self.report['invalid_price_count'] += 1
                self._record_error(offer.kit_sku, 'new product has invalid feed price')
                continue

            try:
                category_id = self._ensure_category(offer, self.snapshot.categories)
                characteristics = self._ensure_characteristics(offer)
                media = self._prepare_media(offer)
                if self.dry_run:
                    self._new_payload(
                        offer, 'dry-product', warehouse_ids, characteristics, media
                    )
                    self.report['new_products_created'] += 1
                else:
                    product = self.kit.create_product(category_id)
                    product_id = str(product.get('id', '')).strip()
                    if not product_id:
                        raise RuntimeError('KIT did not return product id')
                    payload = self._new_payload(
                        offer, product_id, warehouse_ids, characteristics, media
                    )
                    created = self.kit.create_variant(payload)
                    variant_id = str(created.get('id', '')).strip()
                    if not variant_id:
                        raise RuntimeError('KIT did not return variant id')
                    self.report['new_products_created'] += 1
            except Exception as exc:
                if 'image set' in str(exc):
                    self.report['image_failure_count'] += 1
                self._record_error(offer.kit_sku, exc)

        self._flush_prices(price_batch)
        self._flush_stocks(stock_batch)

        complete = bool(self.snapshot.complete and self.max_items is None)
        self.report['catalog_complete'] = complete
        absent = absent_zero_updates(
            kit_index, seen, warehouse_ids, complete=complete
        )
        absent_variants = {
            update['variant_id']
            for update in absent
        }
        self.report['absent_to_zero'] = len(absent_variants)
        for update in absent:
            if update['warehouse_id'] == warehouse_ids['СПБ']:
                self.report['spb_stock_changes'] += 1
            elif update['warehouse_id'] == warehouse_ids['МСК']:
                self.report['msk_stock_changes'] += 1
        if absent and not self.dry_run:
            self.kit.bulk_update_stocks(absent)

        self.report['status'] = (
            'ok' if not self.report['errors']
            else ('degraded' if self.report['offers_seen'] else 'failed')
        )
        return self.report

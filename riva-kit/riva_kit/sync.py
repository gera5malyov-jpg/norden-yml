import os
import tempfile
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
from urllib.parse import urlparse

from .feed import iter_offers
from .mapper import characteristics_from_offer
from .rules import calculate_prices, desired_stock


def _lower(value):
    return str(value or '').strip().casefold()


def _source_sort_key(value):
    text = str(value or '').strip()
    if text.isdigit():
        return (1, int(text))
    return (0, text)


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


def _brand_text(row):
    brand = row.get('brand')
    if isinstance(brand, dict):
        for key in ('title', 'name', 'value'):
            if brand.get(key):
                return str(brand.get(key)).strip()
        return ''
    return str(brand or '').strip()


def _char_values(row, characteristic_titles, wanted_title):
    out = []
    wanted = _lower(wanted_title)
    for char in row.get('characteristics') or []:
        if not isinstance(char, dict):
            continue
        title = str(char.get('title') or '').strip()
        if not title:
            cid = str(char.get('characteristic_id') or char.get('id') or '').strip()
            title = str(characteristic_titles.get(cid) or '').strip()
        if _lower(title) != wanted:
            continue

        values = char.get('values')
        if isinstance(values, list):
            candidates = values
        elif values not in (None, ''):
            candidates = [values]
        else:
            candidates = [char.get('value')]

        for value in candidates:
            text = str(value or '').strip()
            if text and text not in out:
                out.append(text)
    return out


def index_riva_variants(rows, characteristic_titles=None):
    characteristic_titles = characteristic_titles or {}
    by_source = {}
    by_sku = defaultdict(list)
    owned_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = str(row.get('sku', '')).strip()
        source_values = _char_values(
            row, characteristic_titles, 'ID предложения Riva'
        )
        source_id = source_values[0] if source_values else ''
        is_riva = bool(source_id) or _lower(_brand_text(row)) == 'riva'
        if not is_riva:
            continue

        owned_count += 1
        if source_id and source_id not in by_source:
            by_source[source_id] = row
        if sku:
            by_sku[sku].append(row)

    return by_source, dict(by_sku), owned_count


def resolve_variant(offer, by_source, by_sku, characteristic_titles=None):
    characteristic_titles = characteristic_titles or {}

    variant = by_source.get(offer.source_id)
    if variant is not None:
        return variant

    bucket = list(by_sku.get(offer.kit_sku) or [])
    if not bucket:
        return None

    # Код для сайта is the authoritative KIT SKU. If exactly one Riva variant
    # already owns that SKU, it is the same product even when Riva duplicated
    # the offer under another technical offer id.
    if len(bucket) == 1:
        return bucket[0]

    if offer.barcode:
        barcode_matches = [
            row for row in bucket
            if offer.barcode in _char_values(
                row, characteristic_titles, 'Штрихкод'
            )
        ]
        if len(barcode_matches) == 1:
            return barcode_matches[0]

    exact_name = [
        row for row in bucket
        if _lower(row.get('name')) == _lower(offer.name)
    ]
    if len(exact_name) == 1:
        return exact_name[0]

    return None


def build_price_update(offer, variant):
    prices = calculate_prices(offer.price)
    if prices is None:
        return None
    pricing = variant.get('pricing') or {}
    if (
        _kit_money(pricing.get('price')) == _kit_money(prices['old'])
        and _kit_money(pricing.get('manual_discount_price')) == _kit_money(prices['sale'])
    ):
        return None
    variant_id = str(variant.get('id', '')).strip()
    if not variant_id:
        return None
    return {
        'variant_id': variant_id,
        'price': f"{prices['old']:.2f}",
        'manual_discount_price': f"{prices['sale']:.2f}",
    }


def build_stock_updates(offer, variant, warehouse_ids):
    variant_id = str(variant.get('id', '')).strip()
    if not variant_id:
        return []
    quantity = desired_stock(offer.count)
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


def absent_zero_updates(source_index, seen_source_ids, warehouse_ids, *, complete):
    return []


def build_feed_canonical_state(feed_path):
    stock_by_sku = {}
    latest_by_sku = {}
    for offer in iter_offers(feed_path):
        if offer.count > 0:
            stock_by_sku[offer.kit_sku] = max(
                stock_by_sku.get(offer.kit_sku, 0),
                offer.count,
            )
        key = _source_sort_key(offer.source_id)
        current = latest_by_sku.get(offer.kit_sku)
        if current is None or key >= current[0]:
            latest_by_sku[offer.kit_sku] = (key, offer.price)
    return stock_by_sku, latest_by_sku


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
    def __init__(self, feed_path, categories, kit, http, *, dry_run=False, skip_items=0, max_items=None, max_new=None, new_only=False):
        self.feed_path = feed_path
        self.categories = categories
        self.kit = kit
        self.http = http
        self.dry_run = bool(dry_run)
        self.skip_items = max(0, int(skip_items or 0))
        self.max_items = int(max_items) if max_items not in (None, '', 0, '0') else None
        self.max_new = int(max_new) if max_new not in (None, '', 0, '0') else None
        self.new_only = bool(new_only)
        self.kit_categories = []
        self.kit_characteristics = []
        self.characteristic_titles = {}
        self.report = {
            'status': 'pending',
            'dry_run': self.dry_run,
            'catalog_complete': False,
            'offers_seen': 0,
            'in_stock_offers': 0,
            'zero_stock_offers': 0,
            'existing_variants_seen': 0,
            'sku_changes': 0,
            'duplicate_site_code_rows': 0,
            'missing_site_code_count': 0,
            'new_products_planned': 0,
            'new_products_created': 0,
            'new_limit_skipped': 0,
            'ambiguous_existing_skipped': 0,
            'price_changes': 0,
            'spb_stock_changes': 0,
            'msk_stock_changes': 0,
            'zeroed_by_feed_count': 0,
            'absent_to_zero': 0,
            'riva_variants_indexed': 0,
            'duplicate_article_buckets': 0,
            'invalid_price_count': 0,
            'image_failure_count': 0,
            'warning_count': 0,
            'warnings': [],
            'error_count': 0,
            'errors': [],
            'skip_items': self.skip_items,
            'max_new': self.max_new,
            'new_only': self.new_only,
            'stock_rule': 'per Код для сайта: max positive count across duplicates; if none positive => 100 on each managed warehouse',
            'price_rule': 'newest technical offer id wins; old=cost*1.80; sale=cost*1.26; desired minimum=cost*1.20',
            'minimum_price_api_supported': False,
        }

    def _warn(self, message):
        self.report['warning_count'] += 1
        if len(self.report['warnings']) < 200:
            self.report['warnings'].append(str(message)[:500])

    def _record_error(self, sku, exc):
        self.report['error_count'] += 1
        if len(self.report['errors']) < 200:
            self.report['errors'].append({'sku': str(sku), 'message': str(exc)[:500]})

    def _ensure_category(self, offer):
        chain = _category_chain(offer.category_id, self.categories)
        if not chain:
            raise RuntimeError(f'missing Riva category for {offer.kit_sku}')
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
                category_id = f'dry-category-riva-{source.source_id}'
                self.kit_categories.append({'id': category_id, 'title': title, 'parent_id': parent})
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
                matches = sorted(matches, key=lambda row: str(row.get('id', '')).strip())
            if matches:
                characteristic_id = str(matches[0].get('id', '')).strip()
            elif self.dry_run:
                characteristic_id = f'dry-riva-char-{len(self.kit_characteristics) + 1}'
                created = {
                    'id': characteristic_id,
                    'title': title,
                    'type': desired_type,
                    'select_mode': 'MULTIPLE' if len(values) > 1 else 'SINGLE',
                }
                self.kit_characteristics.append(created)
            else:
                created = self.kit.create_characteristic(
                    title, desired_type, 'MULTIPLE' if len(values) > 1 else 'SINGLE'
                )
                characteristic_id = str(created.get('id', '')).strip()
                if not characteristic_id:
                    self._warn(f'KIT characteristic creation failed: {title}')
                    continue
                self.kit_characteristics.append(created)

            self.characteristic_titles[str(characteristic_id)] = title
            out.append({'characteristic_id': characteristic_id, 'value': values[0], 'values': values})
        return out

    def _prepare_media(self, offer):
        if self.dry_run:
            return []
        media = []
        failures = []
        for url in offer.images:
            try:
                suffix = os.path.splitext(urlparse(url).path)[1] or '.jpg'
                with tempfile.TemporaryDirectory(prefix='riva-img-') as td:
                    path = os.path.join(td, 'image' + suffix[:10])
                    self.http.download_to_file(url, path)
                    uploaded = self.kit.upload_image(path)
                    file_id = str(uploaded.get('id', '')).strip()
                    if not file_id:
                        raise RuntimeError('KIT did not return image file id')
                    media.append({'type': 'IMAGE', 'display_sequence': len(media), 'image_id': file_id})
            except Exception as exc:
                failures.append(str(exc))
        if len(media) != len(offer.images):
            detail = failures[0] if failures else 'unknown image error'
            raise RuntimeError(
                f'incomplete image set for {offer.kit_sku}: prepared {len(media)} of {len(offer.images)}; {detail}'
            )
        return media

    def _new_payload(self, offer, product_id, warehouse_ids, characteristics, media):
        if offer.price is None:
            raise RuntimeError(f'invalid price for {offer.kit_sku}')
        prices = calculate_prices(offer.price)
        if prices is None:
            raise RuntimeError(f'invalid price for {offer.kit_sku}')
        quantity = desired_stock(offer.count)
        payload = {
            'sku': offer.kit_sku,
            'name': offer.name,
            'description': offer.description,
            'status': 'PUBLISHED',
            'product_id': str(product_id),
            'brand': 'RIVA',
            'pricing': {
                'price': f"{prices['old']:.2f}",
                'manual_discount_price': f"{prices['sale']:.2f}",
            },
            'stocks': [
                {'warehouse_id': str(warehouse_ids['СПБ']), 'quantity': quantity, 'reserved': 0},
                {'warehouse_id': str(warehouse_ids['МСК']), 'quantity': quantity, 'reserved': 0},
            ],
        }
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
        self.kit_categories = self.kit.list_categories()
        self.kit_characteristics = self.kit.list_characteristics()
        self.characteristic_titles = {
            str(row.get('id', '')).strip(): str(row.get('title', '')).strip()
            for row in self.kit_characteristics if str(row.get('id', '')).strip()
        }

        by_source, by_sku, owned_count = index_riva_variants(
            list(self.kit.iter_variants({'name': 'ЦБ-'})), self.characteristic_titles
        )
        self.report['riva_variants_indexed'] = owned_count
        self.report['duplicate_article_buckets'] = sum(1 for values in by_sku.values() if len(values) > 1)

        seen_source_ids = set()
        seen_site_codes = set()
        if self.new_only:
            global_positive_max, global_latest = {}, {}
        else:
            global_positive_max, global_latest = build_feed_canonical_state(self.feed_path)
        price_batch = []
        stock_batch = []
        stopped_early = False
        creation_limit_hit = False
        created_or_planned = 0
        created_this_run = set()

        def on_feed_skip(message):
            self.report['missing_site_code_count'] += 1
            self._warn(message)

        feed_index = 0
        for offer in iter_offers(self.feed_path, on_skip=on_feed_skip):
            if feed_index < self.skip_items:
                feed_index += 1
                continue
            if self.max_items is not None and self.report['offers_seen'] >= self.max_items:
                stopped_early = True
                break
            feed_index += 1

            seen_source_ids.add(offer.source_id)
            self.report['offers_seen'] += 1
            if offer.in_stock:
                self.report['in_stock_offers'] += 1
            else:
                self.report['zero_stock_offers'] += 1

            if offer.kit_sku in seen_site_codes:
                self.report['duplicate_site_code_rows'] += 1
            else:
                seen_site_codes.add(offer.kit_sku)

            previous_positive = global_positive_max.get(offer.kit_sku, 0)
            effective_positive = max(
                previous_positive,
                offer.count if offer.count > 0 else 0,
            )
            global_positive_max[offer.kit_sku] = effective_positive

            current_key = _source_sort_key(offer.source_id)
            previous_latest = global_latest.get(offer.kit_sku)
            if previous_latest is None or current_key >= previous_latest[0]:
                global_latest[offer.kit_sku] = (current_key, offer.price)
            latest_key, latest_price = global_latest[offer.kit_sku]

            canonical_offer = replace(
                offer,
                count=effective_positive if effective_positive > 0 else 0,
                in_stock=effective_positive > 0,
                price=latest_price,
            )
            stock_offer = canonical_offer
            use_price = True

            variant = resolve_variant(offer, by_source, by_sku, self.characteristic_titles)
            if variant is not None:
                self.report['existing_variants_seen'] += 1
                if self.new_only and offer.kit_sku not in created_this_run:
                    continue

                source_ids = _char_values(variant, self.characteristic_titles, 'ID предложения Riva')
                current_sku = str(variant.get('sku', '')).strip()
                if offer.source_id in source_ids and current_sku != offer.kit_sku:
                    variant_id = str(variant.get('id', '')).strip()
                    if not variant_id:
                        self._record_error(offer.kit_sku, 'existing Riva variant has no KIT id for SKU migration')
                        continue
                    if not self.dry_run:
                        updated = self.kit.update_variant(variant_id, {'sku': offer.kit_sku})
                        if isinstance(updated, dict):
                            variant.update(updated)
                    variant['sku'] = offer.kit_sku
                    self.report['sku_changes'] += 1
                    if current_sku in by_sku:
                        by_sku[current_sku] = [
                            row for row in by_sku[current_sku]
                            if str(row.get('id', '')) != variant_id
                        ]
                        if not by_sku[current_sku]:
                            by_sku.pop(current_sku, None)
                    by_sku.setdefault(offer.kit_sku, []).append(variant)

                if canonical_offer.price is None:
                    self.report['invalid_price_count'] += 1
                    self._warn(f'invalid feed price; KIT price unchanged: {offer.kit_sku}')
                elif use_price:
                    price_update = build_price_update(canonical_offer, variant)
                    if price_update:
                        price_batch.append(price_update)
                        self.report['price_changes'] += 1

                stock_updates = build_stock_updates(stock_offer, variant, warehouse_ids)
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

            bucket = list(by_sku.get(offer.kit_sku) or [])
            unmarked_existing = [
                row for row in bucket
                if not _char_values(row, self.characteristic_titles, 'ID предложения Riva')
            ]
            if unmarked_existing:
                self.report['ambiguous_existing_skipped'] += 1
                self._warn(
                    f'existing Riva SKU without technical offer id could not be safely matched; '
                    f'skipped: {offer.kit_sku} / offer {offer.source_id}'
                )
                continue

            if self.max_new is not None and created_or_planned >= self.max_new:
                if self.new_only:
                    creation_limit_hit = True
                    self.report['new_limit_skipped'] += 1
                    continue
                stopped_early = True
                break

            if canonical_offer.price is None:
                self.report['invalid_price_count'] += 1
                self._record_error(offer.kit_sku, 'new product has invalid feed price')
                continue

            try:
                category_id = self._ensure_category(offer)
                characteristics = self._ensure_characteristics(offer)
                media = self._prepare_media(offer)
                self.report['new_products_planned'] += 1
                created_or_planned += 1
                if self.dry_run:
                    self._new_payload(stock_offer, 'dry-product', warehouse_ids, characteristics, media)
                else:
                    product = self.kit.create_product(category_id)
                    product_id = str(product.get('id', '')).strip()
                    if not product_id:
                        raise RuntimeError('KIT did not return product id')
                    payload = self._new_payload(stock_offer, product_id, warehouse_ids, characteristics, media)
                    created = self.kit.create_variant(payload)
                    variant_id = str(created.get('id', '')).strip()
                    if not variant_id:
                        raise RuntimeError('KIT did not return variant id')
                    normalized = dict(payload)
                    normalized.update(created)
                    by_source[offer.source_id] = normalized
                    by_sku.setdefault(offer.kit_sku, []).append(normalized)
                    created_this_run.add(offer.kit_sku)
                    self.report['new_products_created'] += 1
            except Exception as exc:
                if 'image set' in str(exc):
                    self.report['image_failure_count'] += 1
                self._record_error(offer.kit_sku, exc)

        self._flush_prices(price_batch)
        self._flush_stocks(stock_batch)

        if self.new_only:
            complete = (
                self.skip_items == 0
                and self.max_items is None
                and not creation_limit_hit
            )
        else:
            complete = (not stopped_early and self.skip_items == 0)
        self.report['catalog_complete'] = complete
        absent = absent_zero_updates(by_source, seen_source_ids, warehouse_ids, complete=complete)
        absent_variants = {update['variant_id'] for update in absent}
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

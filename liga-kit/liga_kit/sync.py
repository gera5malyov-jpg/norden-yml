import os
import tempfile
from urllib.parse import urlparse

from .mapper import characteristics_from_offer


def _lower(value):
    return str(value or '').strip().casefold()


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
            'dry_run': self.dry_run,
            'warning_count': 0,
            'warnings': [],
            'error_count': 0,
            'errors': [],
        }

    def _warn(self, message):
        self.report['warning_count'] += 1
        if len(self.report['warnings']) < 200:
            self.report['warnings'].append(str(message)[:500])

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

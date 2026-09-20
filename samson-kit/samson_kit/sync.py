import os, tempfile
from decimal import Decimal, ROUND_CEILING
from urllib.parse import urlparse, unquote
from .rules import calculate_prices, calculate_stock
from .mapper import normalize_sku, category_chain

_ALLOWED_DOCUMENT_EXTENSIONS={'.pdf','.doc','.docx','.xls','.xlsx','.rtf','.txt','.jpg','.jpeg','.png','.svg','.webp'}

def _money(value):
    if value is None: return None
    try: return f'{Decimal(str(value)):.2f}'
    except Exception: return None

def _kit_money(value):
    if value is None: return None
    try: return Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_CEILING)
    except Exception: return None

def _current_stock(variant,warehouse_id):
    for row in variant.get('stocks') or []:
        if str(row.get('warehouse_id',''))==str(warehouse_id):
            try: return int(row.get('quantity',0))
            except Exception: return None
    return 0

def build_price_update(item,variant):
    prices=calculate_prices(item.purchase_price)
    if not prices: return None
    desired_price=f'{prices.old:.2f}'; desired_discount=f'{prices.sale:.2f}'; pricing=variant.get('pricing') or {}
    if _kit_money(pricing.get('price'))==_kit_money(prices.old) and _kit_money(pricing.get('manual_discount_price'))==_kit_money(prices.sale): return None
    variant_id=str(variant.get('id','')).strip()
    return {'variant_id':variant_id,'price':desired_price,'manual_discount_price':desired_discount} if variant_id else None

def build_stock_update(item,variant,warehouse_id):
    desired=calculate_stock(item.stock_parts,active=item.active,withdrawn=item.withdrawn)
    if desired is None or _current_stock(variant,warehouse_id)==desired: return None
    variant_id=str(variant.get('id','')).strip()
    return {'variant_id':variant_id,'warehouse_id':str(warehouse_id),'quantity':int(desired)} if variant_id else None

def absent_zero_updates(index,seen_skus,warehouse_id,*,complete):
    if not complete: return []
    out=[]
    for sku,variant in index.items():
        if sku in seen_skus or _current_stock(variant,warehouse_id)==0: continue
        variant_id=str(variant.get('id','')).strip()
        if variant_id: out.append({'variant_id':variant_id,'warehouse_id':str(warehouse_id),'quantity':0})
    return out

def _lower(value): return str(value).strip().casefold()

def _document_parts(url,index):
    name=unquote(os.path.basename(urlparse(str(url)).path)).strip()
    stem,ext=os.path.splitext(name)
    ext=ext.lower()
    title=(stem or f'Сертификат {index}').replace(':','-').replace('/','-').strip()
    if not title: title=f'Сертификат {index}'
    return title,ext

class SyncRunner:
    def __init__(self,samson,kit,http,*,warehouse_name='СПБ',dry_run=False,skip_items=0,max_items=None,max_new=None,new_only=False):
        self.samson=samson; self.kit=kit; self.http=http; self.warehouse_name=warehouse_name; self.dry_run=bool(dry_run); self.skip_items=max(0,int(skip_items or 0)); self.max_items=int(max_items) if max_items not in (None,'',0,'0') else None; self.max_new=int(max_new) if max_new not in (None,'',0,'0') else None; self.new_only=bool(new_only); self.kit_categories=[]; self.kit_characteristics=[]
        self.report={'dry_run':self.dry_run,'catalog_complete':False,'samson_products_seen':0,'new_products_created':0,'price_changes':0,'stock_changes':0,'active_zero_to_100':0,'withdrawn_to_zero':0,'absent_to_zero':0,'documents_attached':0,'documents_skipped':0,'duplicate_kit_skus':0,'error_count':0,'errors':[],'warning_count':0,'warnings':[],'unmapped_source_keys':{},'minimum_price_mapping_unsupported_count':0,'minimum_price_examples':{},'skip_items':self.skip_items,'max_new':self.max_new,'new_only':self.new_only}
    def _record_error(self,sku,exc):
        self.report['error_count']+=1
        if len(self.report['errors'])<200: self.report['errors'].append({'sku':sku,'message':str(exc)[:500]})
    def _warn(self,message):
        self.report['warning_count']+=1
        if len(self.report['warnings'])<200: self.report['warnings'].append(str(message)[:500])
    def _load_override(self,iterator_name,kind):
        result={}
        try:
            for row in getattr(self.samson,iterator_name)():
                try:
                    item=normalize_sku(row); result[item.source_code]=item.purchase_price if kind=='price' else item.stock_parts
                except Exception: continue
        except Exception as exc: self._warn(f'{iterator_name} unavailable; fallback to /sku/ fields: {exc}')
        return result
    def _ensure_category_chain(self,item,source_categories):
        chain=category_chain(item.category_id,source_categories)
        if not chain: raise RuntimeError('missing Samson category hierarchy')
        parent=''
        for src in chain:
            title=str(src.get('name') or src.get('title') or '').strip()
            if not title: raise RuntimeError('Samson category without name')
            matches=[x for x in self.kit_categories if _lower(x.get('title'))==_lower(title) and str(x.get('parent_id') or '')==parent]
            if len(matches)>1: raise RuntimeError(f'ambiguous KIT category {title!r}')
            if matches: cat_id=str(matches[0].get('id','')).strip()
            elif self.dry_run:
                cat_id='dry-category-'+str(src.get('id') or title); self.kit_categories.append({'id':cat_id,'title':title,'parent_id':parent})
            else:
                new=self.kit.create_category(title,parent or None); cat_id=str(new.get('id','')).strip()
                if not cat_id: raise RuntimeError(f'KIT did not return category id for {title!r}')
                self.kit_categories.append(new)
            parent=cat_id
        return parent
    def _ensure_characteristics(self,item):
        out=[]
        for title,values in item.characteristics:
            title=str(title).strip(); values=[str(x).strip() for x in values if str(x).strip()]
            if not title or not values: continue
            desired='MULTIPLE_STRING' if len(values)>1 else 'STRING'
            matches=[x for x in self.kit_characteristics if _lower(x.get('title'))==_lower(title) and str(x.get('type','')).strip().upper()==desired]
            if len(matches)>1:
                self._warn(f'ambiguous KIT characteristic skipped: {title}'); continue
            if matches: char_id=str(matches[0].get('id','')).strip()
            elif self.dry_run:
                char_id='dry-char-'+str(len(self.kit_characteristics)+1); self.kit_characteristics.append({'id':char_id,'title':title,'type':desired,'select_mode':'MULTIPLE' if len(values)>1 else 'SINGLE'})
            else:
                mode='MULTIPLE' if len(values)>1 else 'SINGLE'; new=self.kit.create_characteristic(title,desired,mode); char_id=str(new.get('id','')).strip()
                if not char_id: self._warn(f'KIT characteristic creation failed: {title}'); continue
                self.kit_characteristics.append(new)
            out.append({'characteristic_id':char_id,'value':values[0],'values':values})
        return out
    def _prepare_media(self,item):
        if self.dry_run: return []
        media=[]; failures=[]
        for url in item.image_urls:
            try:
                suffix=os.path.splitext(urlparse(url).path)[1] or '.jpg'
                with tempfile.TemporaryDirectory(prefix='samson-img-') as td:
                    path=os.path.join(td,'image'+suffix[:10])
                    self.http.download_to_file(url,path)
                    uploaded=self.kit.upload_image(path)
                    file_id=str(uploaded.get('id','')).strip()
                    if not file_id:
                        raise RuntimeError('KIT did not return image file id')
                    media.append({'type':'IMAGE','display_sequence':len(media),'image_id':file_id})
            except Exception as exc:
                failures.append(str(exc))
                self._warn(f'image failed for {item.kit_sku}: {exc}')
        if len(media)!=len(item.image_urls):
            raise RuntimeError(f'incomplete image set for {item.kit_sku}: prepared {len(media)} of {len(item.image_urls)}')
        return media

    def _repair_images_if_incomplete(self,item,variant):
        if self.dry_run or not item.image_urls:
            return False
        variant_id=str((variant or {}).get('id','')).strip()
        if not variant_id:
            raise RuntimeError(f'KIT variant id missing for image repair {item.kit_sku}')
        detail=self.kit.get_variant(variant_id)
        current_media=detail.get('media') or []
        current_images=[m for m in current_media if isinstance(m,dict) and str(m.get('type','')).strip().upper()=='IMAGE']
        non_images=[m for m in current_media if isinstance(m,dict) and str(m.get('type','')).strip().upper()!='IMAGE']
        if len(current_images)>=len(item.image_urls):
            return False
        if non_images:
            self._warn(f'image repair skipped for {item.kit_sku}: non-image media present')
            return False
        media=self._prepare_media(item)
        self.kit.update_variant(variant_id,{'media':media})
        verify=self.kit.get_variant(variant_id)
        verified=[m for m in (verify.get('media') or []) if isinstance(m,dict) and str(m.get('type','')).strip().upper()=='IMAGE']
        if len(verified)!=len(item.image_urls):
            raise RuntimeError(f'image repair verification failed for {item.kit_sku}: KIT has {len(verified)} of {len(item.image_urls)}')
        return True
    def _attach_documents(self,item,variant_id):
        if self.dry_run or not item.document_urls: return
        try:
            existing=self.kit.list_variant_attachments(variant_id)
        except Exception as exc:
            self._warn(f'documents skipped for {item.kit_sku}: cannot list KIT attachments: {exc}')
            return
        attached_ids={str(row.get('file_id','')).strip() for row in existing if isinstance(row,dict) and str(row.get('file_id','')).strip()}
        occupied={int(row.get('display_sequence')) for row in existing if isinstance(row,dict) and isinstance(row.get('display_sequence'),int)}
        count=len(existing)
        for index,url in enumerate(item.document_urls,1):
            if count>=10:
                self.report['documents_skipped']+=1
                self._warn(f'document limit reached for {item.kit_sku}; remaining certificates skipped')
                break
            title,ext=_document_parts(url,index)
            if ext not in _ALLOWED_DOCUMENT_EXTENSIONS:
                self.report['documents_skipped']+=1
                self._warn(f'document skipped for {item.kit_sku}: unsupported extension {ext or "<none>"}')
                continue
            try:
                with tempfile.TemporaryDirectory(prefix='samson-doc-') as td:
                    path=os.path.join(td,'certificate'+ext)
                    self.http.download_to_file(url,path)
                    uploaded=self.kit.upload_file(path)
                    file_id=str(uploaded.get('id','')).strip()
                    if not file_id: raise RuntimeError('KIT did not return file id')
                    if file_id in attached_ids:
                        self.report['documents_skipped']+=1
                        continue
                    sequence=0
                    while sequence in occupied: sequence+=1
                    self.kit.create_variant_attachment(variant_id,file_id,title,display_sequence=sequence)
                    attached_ids.add(file_id); occupied.add(sequence); count+=1; self.report['documents_attached']+=1
            except Exception as exc:
                self.report['documents_skipped']+=1
                self._warn(f'document skipped for {item.kit_sku}: {exc}')
    def _new_payload(self,item,product_id,warehouse_id,characteristics,media):
        payload={'sku':item.kit_sku,'name':item.name,'description':item.description,'status':'PUBLISHED','product_id':str(product_id)}
        if item.brand: payload['brand']=item.brand
        if characteristics: payload['characteristics']=characteristics
        if media: payload['media']=media
        desired=calculate_stock(item.stock_parts,active=item.active,withdrawn=item.withdrawn)
        if desired is not None: payload['stocks']=[{'warehouse_id':str(warehouse_id),'quantity':int(desired),'reserved':0}]
        prices=calculate_prices(item.purchase_price)
        if prices:
            payload['pricing']={'price':f'{prices.old:.2f}','manual_discount_price':f'{prices.sale:.2f}'}; self.report['minimum_price_mapping_unsupported_count']+=1
            if len(self.report['minimum_price_examples'])<20: self.report['minimum_price_examples'][item.kit_sku]=f'{prices.minimum:.2f}'
        return payload
    def run(self):
        warehouse_id=self.kit.resolve_warehouse_exact(self.warehouse_name); source_categories={str(x.get('id')):x for x in self.samson.iter_categories() if isinstance(x,dict) and x.get('id') not in (None,'')}; kit_index,duplicates=self.kit.index_samson_variants(); self.report['duplicate_kit_skus']=len(duplicates); self.kit_categories=self.kit.list_categories(); self.kit_characteristics=self.kit.list_characteristics(); price_override=self._load_override('iter_prices','price'); stock_override=self._load_override('iter_stock','stock'); seen=set(); price_batch=[]; stock_batch=[]; complete=False
        source_index=0
        try:
            for raw in self.samson.iter_skus():
                if source_index < self.skip_items:
                    source_index+=1
                    continue
                source_index+=1
                item0=normalize_sku(raw); item=normalize_sku(raw,price_override=price_override.get(item0.source_code),stock_override=stock_override.get(item0.source_code)); seen.add(item.kit_sku); self.report['samson_products_seen']+=1
                for key in item.raw_keys: self.report['unmapped_source_keys'][key]=self.report['unmapped_source_keys'].get(key,0)+1
                if item.kit_sku in duplicates: continue
                variant=kit_index.get(item.kit_sku)
                if variant:
                    if self.new_only: continue
                    pu=build_price_update(item,variant); su=build_stock_update(item,variant,warehouse_id)
                    if pu: price_batch.append(pu); self.report['price_changes']+=1
                    if su:
                        stock_batch.append(su); self.report['stock_changes']+=1
                        if su['quantity']==100: self.report['active_zero_to_100']+=1
                        if su['quantity']==0 and item.withdrawn: self.report['withdrawn_to_zero']+=1
                    if len(price_batch)>=500 and not self.dry_run: self.kit.bulk_update_prices(price_batch); price_batch=[]
                    if len(stock_batch)>=500 and not self.dry_run: self.kit.bulk_update_stocks(stock_batch); stock_batch=[]
                else:
                    if self.max_new is not None and self.report['new_products_created']>=self.max_new:
                        break
                    try:
                        category_id=self._ensure_category_chain(item,source_categories); chars=self._ensure_characteristics(item); media=self._prepare_media(item)
                        if self.dry_run: self._new_payload(item,'dry-product',warehouse_id,chars,media); self.report['new_products_created']+=1
                        else:
                            product=self.kit.create_product(category_id); product_id=str(product.get('id','')).strip()
                            if not product_id: raise RuntimeError('KIT did not return product id')
                            payload=self._new_payload(item,product_id,warehouse_id,chars,media)
                            created=self.kit.create_variant(payload)
                            variant_id=str(created.get('id','')).strip()
                            if not variant_id: raise RuntimeError('KIT did not return variant id')
                            self.report['new_products_created']+=1
                            normalized=dict(payload); normalized.update(created); kit_index[item.kit_sku]=normalized
                            self._attach_documents(item,variant_id)
                    except Exception as exc: self._record_error(item.kit_sku,exc)
                if self.max_items and self.report['samson_products_seen']>=self.max_items: break
            else: complete=True
        except Exception as exc: self._record_error('CATALOG',exc); complete=False
        if price_batch and not self.dry_run: self.kit.bulk_update_prices(price_batch)
        if stock_batch and not self.dry_run: self.kit.bulk_update_stocks(stock_batch)
        self.report['catalog_complete']=bool(complete and self.max_items is None and self.skip_items==0); absent=absent_zero_updates(kit_index,seen,warehouse_id,complete=(self.report['catalog_complete'] and not self.new_only)); self.report['absent_to_zero']=len(absent)
        if absent and not self.dry_run: self.kit.bulk_update_stocks(absent)
        self.report['status']='ok' if not self.report['errors'] else ('degraded' if self.report['samson_products_seen'] else 'failed')
        return self.report

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional
from .rules import to_kit_sku

def _first(row, keys, default=None):
    for key in keys:
        if key in row and row[key] not in (None,''): return row[key]
    return default

def _as_decimal(value):
    if value in (None,''): return None
    try: return Decimal(str(value).replace(',','.'))
    except (InvalidOperation,ValueError,TypeError): return None

def _flatten_strings(value):
    out=[]
    if value is None: return out
    if isinstance(value,(str,int,float,bool)):
        s=str(value).strip(); return [s] if s else []
    if isinstance(value,dict):
        for key in ('url','url_list','src','href','value','code','barcode','ean','path'):
            if key in value:
                out.extend(_flatten_strings(value[key]))
                if out: break
        return out
    if isinstance(value,(list,tuple,set)):
        for item in value: out.extend(_flatten_strings(item))
    return list(dict.fromkeys(out))

def _extract_quantity(item):
    if isinstance(item,(int,float,str)):
        try: return int(float(str(item).replace(',','.')))
        except Exception: return None
    if isinstance(item,dict):
        for key in ('quantity','qty','stock','rest','amount','available','count','value'):
            if key in item and item[key] not in (None,''): return _extract_quantity(item[key])
    return None

def _typed_value(block, wanted_types):
    wanted={str(x).strip().casefold() for x in wanted_types}
    if not isinstance(block,list): return None
    for item in block:
        if not isinstance(item,dict): continue
        typ=str(item.get('type','')).strip().casefold()
        if typ in wanted and item.get('value') not in (None,''):
            return item.get('value')
    return None

def extract_category_id(row):
    chain=row.get('category_list')
    if isinstance(chain,(list,tuple)):
        ids=[]
        for item in chain:
            if isinstance(item,dict): item=_first(item,('id','category_id','categoryId'))
            if item not in (None,'','0',0): ids.append(str(item).strip())
        if ids: return ids[-1]
    category=_first(row,('category_id','categoryId','category'))
    if isinstance(category,dict): category=_first(category,('id','category_id','categoryId'))
    return str(category).strip() if category not in (None,'') else None

def extract_purchase_price(row):
    direct=_first(row,('purchase_price','client_price','personal_price','contract_price','customer_price','price_client','price'))
    parsed=_as_decimal(direct)
    if parsed is not None: return parsed
    value=_typed_value(row.get('price_list'),('contract','purchase','client','personal'))
    return _as_decimal(value)

def extract_stock_parts(row):
    samson_stock=row.get('stock_list')
    if isinstance(samson_stock,list):
        total=_typed_value(samson_stock,('total',))
        q=_extract_quantity(total)
        if q is not None: return [q]
        vals=[]
        for item in samson_stock:
            if not isinstance(item,dict): continue
            if str(item.get('type','')).strip().casefold()=='total': continue
            q=_extract_quantity(item.get('value'))
            if q is not None: vals.append(q)
        if vals: return vals
    for key in ('stocks','warehouse_stocks','rests','warehouses'):
        value=row.get(key)
        if isinstance(value,list):
            vals=[_extract_quantity(x) for x in value]; vals=[x for x in vals if x is not None]
            if vals: return vals
        if isinstance(value,dict):
            vals=[_extract_quantity(x) for x in value.values()]; vals=[x for x in vals if x is not None]
            if vals: return vals
    if 'stock' in row:
        value=row.get('stock')
        if isinstance(value,list):
            vals=[_extract_quantity(x) for x in value]; vals=[x for x in vals if x is not None]
            if vals: return vals
        if isinstance(value,dict):
            vals=[_extract_quantity(x) for x in value.values()]; vals=[x for x in vals if x is not None]
            if vals: return vals
        q=_extract_quantity(value)
        if q is not None: return [q]
    direct=[]
    for key in ('stock_idp','stock_rc','stock_way','stock_in_way','stock_in_transit','idp_stock','rc_stock','transit_stock','quantity_idp','quantity_rc','quantity_way'):
        if key in row:
            q=_extract_quantity(row[key])
            if q is not None: direct.append(q)
    return direct or None

def _boolish(value):
    if isinstance(value,bool): return value
    if isinstance(value,(int,float)): return bool(value)
    s=str(value).strip().lower()
    if s in ('1','true','yes','y','да'): return True
    if s in ('0','false','no','n','нет',''): return False
    return None

def extract_withdrawn(row):
    if 'out_of_stock' in row and _boolish(row.get('out_of_stock')) is True: return True
    for key in ('deleted','is_deleted','discontinued','is_discontinued','withdrawn','is_withdrawn','removed','is_removed','archive','archived'):
        if key in row and _boolish(row[key]) is True: return True
    return str(_first(row,('status','state','availability_status'),'')).strip().upper() in {'DELETED','DISCONTINUED','WITHDRAWN','REMOVED','ARCHIVED','CLOSED'}

def extract_active(row,withdrawn=False):
    if withdrawn: return False
    for key in ('active','is_active','enabled','is_enabled'):
        if key in row:
            val=_boolish(row[key])
            if val is not None: return val
    return True

def extract_characteristics(row):
    result=[]; seen_names=set()
    def add(name,values):
        name=str(name).strip(); vals=_flatten_strings(values)
        if not name or not vals: return
        name_key=name.casefold()
        if name_key in seen_names: return
        seen_names.add(name_key); result.append((name,vals))
    for key in ('characteristics','properties','attributes','facets','params','parameters'):
        block=row.get(key)
        if isinstance(block,dict):
            for name,value in block.items(): add(name,value)
        elif isinstance(block,list):
            for item in block:
                if isinstance(item,dict):
                    name=_first(item,('name','title','label','key','property_name','facet_name')); value=_first(item,('value','values','text','property_value','facet_value'))
                    if name is not None: add(name,value)
    for item in row.get('characteristic_list') or []:
        if isinstance(item,str):
            name,sep,value=item.partition(':')
            if sep and name.strip() and value.strip(): add(name.strip(),value.strip())
            elif item.strip(): add('Характеристики Samson',item.strip())
        elif isinstance(item,dict):
            name=_first(item,('name','title','label')); value=_first(item,('value','values','text'))
            if name is not None: add(name,value)
    for item in row.get('facet_list') or []:
        if isinstance(item,dict):
            name=_first(item,('name','title','label')); value=_first(item,('value','values','text'))
            if name is not None: add(name,value)
    attribute_titles={'novelty':'Новинка'}
    for item in row.get('attribute_list') or []:
        if isinstance(item,dict):
            typ=str(item.get('type','')).strip(); value=item.get('value')
            if typ and value not in (None,''): add(attribute_titles.get(typ,typ),value)
    package_titles={'min_opt':'Мин. партия ОПТ','min_kor':'Мин. партия КОР','pzk':'Партия ПЗК','intermediate':'Промежуточная упаковка','transport':'Транспортная упаковка','unit':'Единица измерения'}
    for item in row.get('package_list') or []:
        if isinstance(item,dict):
            typ=str(item.get('type','')).strip(); value=item.get('value')
            if typ and value not in (None,''): add(package_titles.get(typ,typ),value)
    size_titles={'height':'Высота упаковки','width':'Ширина упаковки','depth':'Глубина упаковки','length':'Длина упаковки'}
    for item in row.get('package_size') or []:
        if isinstance(item,dict):
            typ=str(item.get('type','')).strip(); value=item.get('value')
            if typ and value not in (None,''): add(size_titles.get(typ,typ),value)
    known={'weight':'Вес товара','volume':'Объем товара','manufacturer':'Страна производства','vendor_code':'Артикул производителя','manufacturer_code':'Код производителя','nds':'НДС','ban_not_multiple':'Запрет некратного набора','sale_date':'Дата ввода товара','remove_date':'Дата удаления','expiration_date':'Срок годности'}
    for key,title in known.items():
        if key in row and row[key] not in (None,''): add(title,row[key])
    extra_lists={'file_list':'Файлы Samson','video_list':'Видео Samson'}
    for key,title in extra_lists.items():
        values=_flatten_strings(row.get(key))
        if values: add(title,'; '.join(values))
    return result

def extract_images(row):
    out=[]
    for key in ('photo_list','images','photos','pictures','image_urls','photo_urls','image','photo','picture'):
        for value in _flatten_strings(row.get(key)):
            if value.startswith(('http://','https://')) and value not in out: out.append(value)
    return out

def extract_documents(row):
    out=[]
    for key in ('certificate_list','certificate_extended_list'):
        for value in _flatten_strings(row.get(key)):
            if value.startswith(('http://','https://')) and value not in out: out.append(value)
    return out

def extract_barcodes(row):
    out=[]
    for key in ('barcodes','barcode','ean','ean13','gtin','upc'):
        for value in _flatten_strings(row.get(key)):
            if value and value not in out: out.append(value)
    return out

@dataclass
class NormalizedSku:
    source_code:str; kit_sku:str; name:str; category_id:Optional[str]; description:str; brand:str; purchase_price:Optional[Decimal]; stock_parts:Optional[list]; active:bool; withdrawn:bool; barcodes:list=field(default_factory=list); image_urls:list=field(default_factory=list); document_urls:list=field(default_factory=list); characteristics:list=field(default_factory=list); raw_keys:list=field(default_factory=list)

def normalize_sku(row,price_override=None,stock_override=None):
    code=_first(row,('sku','code','article','articul','vendor_code','id'))
    if code is None or str(code).strip()=='': raise ValueError('Samson SKU/article is missing')
    code=str(code).strip(); name=str(_first(row,('name','title','product_name'),code)).strip() or code
    category_id=extract_category_id(row)
    purchase=_as_decimal(price_override) if price_override is not None else extract_purchase_price(row)
    stock_parts=stock_override if stock_override is not None else extract_stock_parts(row)
    withdrawn=extract_withdrawn(row); active=extract_active(row,withdrawn)
    brand=str(_first(row,('brand','brand_name','vendor'),'')).strip()
    description=str(_first(row,('description','full_description','description_full','text','annotation'),'')).strip()
    description_ext=str(row.get('description_ext') or '').strip()
    if description_ext and description_ext != description: description=(description+'\n\n'+description_ext).strip()
    chars=extract_characteristics(row); barcodes=extract_barcodes(row)
    if barcodes: chars.append(('Штрихкод',barcodes))
    chars.append(('Samson ID/артикул',[code]))
    return NormalizedSku(code,to_kit_sku(code),name,category_id,description,brand,purchase,stock_parts,active,withdrawn,barcodes,extract_images(row),extract_documents(row),chars,sorted(map(str,row.keys())))

def category_chain(category_id,categories_by_id):
    if category_id in (None,''): return []
    current=str(category_id); out=[]; seen=set()
    while current and current not in seen:
        seen.add(current); row=categories_by_id.get(current)
        if not isinstance(row,dict): break
        out.append(row); parent=row.get('parent_id'); current=str(parent).strip() if parent not in (None,'','0',0) else ''
    out.reverse(); return out

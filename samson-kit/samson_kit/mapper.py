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
    if isinstance(value,(str,int,float)):
        s=str(value).strip(); return [s] if s else []
    if isinstance(value,dict):
        for key in ('url','src','href','value','code','barcode','ean'):
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
        for key in ('quantity','qty','stock','rest','amount','available','count'):
            if key in item and item[key] not in (None,''): return _extract_quantity(item[key])
    return None

def extract_stock_parts(row):
    for key in ('stocks','stock_list','warehouse_stocks','rests','warehouses'):
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
    result=[]; seen=set()
    def add(name,values):
        name=str(name).strip(); vals=_flatten_strings(values)
        if not name or not vals: return
        key=(name,tuple(vals))
        if key in seen: return
        seen.add(key); result.append((name,vals))
    for key in ('characteristics','properties','attributes','facets','params','parameters'):
        block=row.get(key)
        if isinstance(block,dict):
            for name,value in block.items(): add(name,value)
        elif isinstance(block,list):
            for item in block:
                if isinstance(item,dict):
                    name=_first(item,('name','title','label','key','property_name','facet_name')); value=_first(item,('value','values','text','property_value','facet_value'))
                    if name is not None: add(name,value)
    known={'weight':'Вес товара','width':'Ширина товара','height':'Высота товара','length':'Длина товара','depth':'Глубина товара','package_weight':'Вес упаковки','package_width':'Ширина упаковки','package_height':'Высота упаковки','package_length':'Длина упаковки','country':'Страна производства','unit':'Единица измерения','manufacturer':'Производитель','vendor':'Производитель'}
    for key,title in known.items():
        if key in row: add(title,row[key])
    return result

def extract_images(row):
    out=[]
    for key in ('images','photos','pictures','image_urls','photo_urls','image','photo','picture'):
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
    source_code:str; kit_sku:str; name:str; category_id:Optional[str]; description:str; brand:str; purchase_price:Optional[Decimal]; stock_parts:Optional[list]; active:bool; withdrawn:bool; barcodes:list=field(default_factory=list); image_urls:list=field(default_factory=list); characteristics:list=field(default_factory=list); raw_keys:list=field(default_factory=list)

def normalize_sku(row,price_override=None,stock_override=None):
    code=_first(row,('sku','code','article','articul','vendor_code','id'))
    if code is None or str(code).strip()=='': raise ValueError('Samson SKU/article is missing')
    code=str(code).strip(); name=str(_first(row,('name','title','product_name'),code)).strip() or code
    category=_first(row,('category_id','categoryId','category'))
    if isinstance(category,dict): category=_first(category,('id','category_id'))
    category_id=str(category).strip() if category not in (None,'') else None
    purchase=_as_decimal(price_override if price_override is not None else _first(row,('purchase_price','client_price','personal_price','contract_price','customer_price','price_client','price')))
    stock_parts=stock_override if stock_override is not None else extract_stock_parts(row)
    withdrawn=extract_withdrawn(row); active=extract_active(row,withdrawn)
    brand=str(_first(row,('brand','brand_name','vendor','manufacturer'),'')).strip(); description=str(_first(row,('description','full_description','description_full','text','annotation'),'')).strip()
    chars=extract_characteristics(row); barcodes=extract_barcodes(row)
    if barcodes: chars.append(('Штрихкод',barcodes))
    chars.append(('Samson ID/артикул',[code]))
    return NormalizedSku(code,to_kit_sku(code),name,category_id,description,brand,purchase,stock_parts,active,withdrawn,barcodes,extract_images(row),chars,sorted(map(str,row.keys())))

def category_chain(category_id,categories_by_id):
    if category_id in (None,''): return []
    current=str(category_id); out=[]; seen=set()
    while current and current not in seen:
        seen.add(current); row=categories_by_id.get(current)
        if not isinstance(row,dict): break
        out.append(row); parent=row.get('parent_id'); current=str(parent).strip() if parent not in (None,'','0',0) else ''
    out.reverse(); return out

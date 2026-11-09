#!/usr/bin/env python3
"""Read-only identify price strategies on selected below-floor Ozon products."""
import json
from promo_v2_reconcile import api
ids=[1100495563,1109384964,1111402303,1120896455,1131867831]
for batch in (ids,):
  try:
    d=api("/v1/pricing-strategy/strategy-ids-by-product-ids",{"product_id":[str(x) for x in batch]})
    print("STRATEGY_BY_PRODUCT",json.dumps(d,ensure_ascii=False)[:7000],flush=True)
  except Exception as e:print("STRATEGY_LOOKUP_ERROR",str(e)[:1700],flush=True)
try:
  d=api("/v1/pricing-strategy/list",{"page":1,"limit":49})
  print("STRATEGY_LIST",json.dumps(d,ensure_ascii=False)[:10000],flush=True)
except Exception as e:print("STRATEGY_LIST_ERROR",str(e)[:1700],flush=True)

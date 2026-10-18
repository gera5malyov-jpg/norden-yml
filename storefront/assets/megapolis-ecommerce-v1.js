/* Megapolis / Yandex Metrica Ecommerce. No PII, no checkout mutations. */
(function (w, d) {
  'use strict';
  if (w.location.hostname !== 'profikompany.ru' || w.__megapolisEcomV1) return;
  w.__megapolisEcomV1 = true;
  var layer = w.dataLayer = Array.isArray(w.dataLayer) ? w.dataLayer : [];
  var hooked = false, tryCount = 0, completed = {}, checkoutItems = [];
  function number(text) {
    var x = String(text == null ? '' : text).replace(/\s|\u00a0|\u202f|₽/g, '').replace(',', '.').replace(/[^0-9.]/g, '');
    var n = Number(x);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }
  function clean(s) { return String(s || '').replace(/\s+/g, ' ').trim().slice(0, 250); }
  function price(root) {
    var el = root && (root.querySelector('meta[itemprop="price"][content]') ||
      root.querySelector('[data-price]') || root.querySelector('.wa-product-price, .product-price'));
    if (!el) return null;
    var val = el.getAttribute('content') || el.getAttribute('data-price') || el.textContent;
    return number(val);
  }
  function prod(root, fallback) {
    if (!root) return null;
    var sku = root.querySelector('input[name="sku_id"]');
    var pid = (root.getAttribute('data-product-id') || root.querySelector('input[name="product_id"]')?.value ||
      root.closest('[data-product-id]')?.getAttribute('data-product-id') || (fallback && fallback.product_id) || '').trim();
    var id = pid || (sku && sku.value.trim()) || (fallback && fallback.sku_id) || '';
    if (!/^\d{1,18}$/.test(id)) return null;
    var title = root.querySelector('.wa-name, .product-item__title, [itemprop="name"], h1') ||
      d.querySelector('h1[itemprop="name"],h1.product-name,h1');
    var name = clean(title && (title.getAttribute('content') || title.getAttribute('title') || title.textContent));
    var cost = price(root);
    if (cost === null && root === d.querySelector('#s-product-form')) {
      cost = price(d.querySelector('[itemscope][itemtype*="Product"]') || d);
    }
    var item = { id: id };
    if (name) item.name = name;
    if (cost !== null) item.price = cost;
    return item;
  }
  function send(action, products, field) {
    if (!products || !products.length) return false;
    var ec = {currencyCode:'RUB'};
    if (action === 'impressions') ec.impressions = products;
    else {
      ec[action] = {products: products};
      if (field) ec[action].actionField = field;
    }
    layer.push({ecommerce:ec});
    return true;
  }
  function listing() {
    var cards = Array.from(d.querySelectorAll('.product-item[data-product-id]')).filter(function (n) {
      return !!n.querySelector('.product-item__title');
    });
    var chosen = [], ids = {};
    cards.forEach(function (card) {
      var item = prod(card);
      if (item && !ids[item.id] && chosen.length < 18) {
        ids[item.id] = true; chosen.push(item);
      }
    });
    if (chosen.length) send('impressions', chosen);
  }
  function detail() {
    var form = d.querySelector('#s-product-form');
    var item = prod(form);
    if (item) send('detail', [item]);
  }
  function cartSnapshot() {
    var result = [];
    d.querySelectorAll('#js-order-cart .wa-product[data-product-id]').forEach(function (row) {
      var item = prod(row);
      if (!item) return;
      var q = row.querySelector('.js-product-quantity, input[name^="quantity["]');
      var qty = number(q && q.value);
      if (!qty || qty <= 0) return;
      item.quantity = qty;
      result.push(item);
    });
    return result.slice(0, 50);
  }
  function captureCheckout() {
    var items = cartSnapshot();
    if (items.length) checkoutItems = items;
  }
  function decode(data) {
    if (data && typeof data === 'object') return data;
    try {return JSON.parse(data);} catch(e) {return null;}
  }
  function findAddedItem(settings) {
    var params;
    try {
      params = new URLSearchParams(typeof settings.data === 'string' ? settings.data : '');
    } catch(e) { params = new URLSearchParams(); }
    var sku = params.get('sku_id'), id = params.get('product_id');
    var forms = Array.from(d.querySelectorAll('form[action*="/cart/add"]'));
    var form = forms.find(function(f) {
      return sku && f.querySelector('input[name="sku_id"]')?.value === sku;
    }) || forms.find(function(f) {
      return id && f.querySelector('input[name="product_id"]')?.value === id;
    });
    var item = prod(form && (form.closest('.product-item[data-product-id]') || form));
    if (!item && sku) {
      var skuEl = Array.from(d.querySelectorAll('input[name="sku_id"]')).find(x=>x.value===sku);
      item = prod(skuEl?.closest('form'));
    }
    if (!item && id) item = {id:id};
    if (!item || !/^\d{1,18}$/.test(item.id)) return null;
    var quantity = number(params.get('quantity')) || number(form?.querySelector('input[name="quantity"]')?.value) || 1;
    item.quantity = quantity;
    return item;
  }
  function attach() {
    if (hooked || typeof w.jQuery !== 'function') return hooked;
    hooked = true;
    w.jQuery(d).on('ajaxSuccess.megapolisEcommerceV1', function(_event,xhr,settings,response) {
      try {
        var url = new URL(settings.url,w.location.href);
        if (url.origin !== w.location.origin || url.pathname !== '/cart/add/' ||
            String(settings.type || settings.method || 'GET').toUpperCase() !== 'POST') return;
        var data = decode(response);
        if (xhr.status !== 200 || !data || data.status !== 'ok' || data.errors) return;
        var item = findAddedItem(settings);
        if (item) send('add',[item]);
      } catch(e) {}
    });
    w.jQuery(d).on('wa_order_form_created.megapolisEcommerceV1', function(event,api) {
      if (!event.target || !d.getElementById('js-order-page') ||
          !d.getElementById('js-order-page').contains(event.target)) return;
      var id = String(api && api.order_id || '');
      if (!/^[1-9][0-9]{0,17}$/.test(id) || completed[id]) return;
      captureCheckout();
      var items = checkoutItems;
      // No purchase without verified order ID and actual order items.
      if (!items.length) return;
      completed[id] = true;
      send('purchase', items, {id:id});
    });
    w.jQuery(d).on('wa_order_form_ready.megapolisEcommerceV1 wa_order_form_reloaded.megapolisEcommerceV1', function(){
      captureCheckout();
    });
    w.jQuery(d).on('change.megapolisEcommerceV1', '#js-order-cart .js-product-quantity', captureCheckout);
    return hooked;
  }
  function ready() {
    detail();
    listing();
    captureCheckout();
    attach();
    if (!hooked) {
      var n = 0, timer = w.setInterval(function(){
        if (attach() || ++n > 30) w.clearInterval(timer);
      },500);
    }
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded',ready);
  else ready();
})(window,document);

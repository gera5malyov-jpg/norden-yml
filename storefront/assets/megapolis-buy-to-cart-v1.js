/* Megapolis buy-to-cart v1.
 * Redirect only after Webasyst confirms this product form's AJAX cart add.
 * No redirect on validation / stock / HTTP errors.
 */
(function (w, d) {
  'use strict';
  if (w.location.hostname !== 'profikompany.ru') return;

  function init() {
    var form = d.getElementById('s-product-form');
    var mobileBuy = d.getElementById('megapolis-mobile-buy');
    if (!form || !mobileBuy || !w.jQuery) return;

    var original = form.querySelector('.product-add input[type="submit"]');
    if (!original) return;
    var intent = false;
    var navigating = false;

    function isCartAdd(settings) {
      if (!settings || !settings.url) return false;
      try {
        return new URL(settings.url, w.location.href).pathname === '/cart/add/';
      } catch (e) { return false; }
    }
    function markIntent() { if (!navigating) intent = true; }

    original.addEventListener('click', markIntent, true);
    form.addEventListener('submit', markIntent, true);

    w.jQuery(d).ajaxSuccess(function (_event, xhr, settings, payload) {
      if (!intent || navigating || !isCartAdd(settings)) return;
      var data = payload;
      if (typeof data === 'string') {
        try { data = JSON.parse(data); } catch (e) { data = null; }
      }
      if (xhr.status !== 200 || !data || data.status !== 'ok' || data.errors) {
        intent = false;
        return;
      }
      navigating = true;
      w.location.assign('/order/');
    });

    w.jQuery(d).ajaxError(function (_event, _xhr, settings) {
      if (intent && isCartAdd(settings)) intent = false;
    });
  }

  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', init);
  else init();
})(window, document);

/* Megapolis storefront funnel goals, v1. No personal data or order information. */
(function (w, d) {
  'use strict';
  if (w.location.hostname !== 'profikompany.ru') return;
  var counterId = 21811312;
  var lastSent = {};
  function sendGoal(name) {
    if (typeof w.ym !== 'function') return;
    var now = Date.now();
    if (lastSent[name] && now - lastSent[name] < 1100) return;
    lastSent[name] = now;
    try { w.ym(counterId, 'reachGoal', name); } catch (e) {}
  }

  // Webasyst theme previously used the retired yaCounterNNN API.
  // Preserve all unknown goals; send the known cartAdd event to the installed counter.
  var originalSendYandex = w.sendYandex;
  w.sendYandex = function (id, target) {
    if (target === 'cartAdd') {
      sendGoal('cartAdd');
      return;
    }
    if (typeof originalSendYandex === 'function') {
      return originalSendYandex.apply(this, arguments);
    }
  };

  function initialize() {
    // Only a nonempty checkout displays this element; an empty cart is excluded.
    if (d.getElementById('js-order-page')) {
      sendGoal('checkout_started');
    }

    // Product detail's AJAX add-to-cart form. Listing cards use sendYandex above.
    d.addEventListener('submit', function (event) {
      var form = event.target;
      if (!form || !form.matches) return;
      if (form.matches('form[id^="s-product-form"]') &&
          form.getAttribute('data-add') === 'cartAdd') {
        sendGoal('cartAdd');
      }
    }, true);
  }
  if (d.readyState === 'loading') {
    d.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }
})(window, document);

/* Megapolis storefront funnel v2. Replace metrika_funnel_v1.js; never load both.
 * Goals contain only fixed names: no form values, IDs, URLs, errors or revenue.
 * order_created uses native confirmed create event; paid orders require server records.
 * Leave any native/plugin ecommerce purchase handling unchanged.
 */
(function (w, d) {
  'use strict';
  if (w.location.hostname !== 'profikompany.ru' || w.__megapolisFunnelV2) return;
  w.__megapolisFunnelV2 = true;
  var counterId = 21811312;
  var pending = [], attempts = 0, initialized = false, hooked = false, retryScheduled = false;
  var handled = new WeakSet();
  var checkoutController = null, selected = {}, created = false, validationError = false;

  function flush() {
    if (typeof w.ym !== 'function') return;
    while (pending.length) {
      try { w.ym(counterId, 'reachGoal', pending[0]); }
      catch (e) { return; }
      pending.shift();
    }
  }
  function goal(name) {
    // Bound memory if the counter is blocked indefinitely.
    if (pending.length < 100) pending.push(name);
    flush();
    scheduleRetry();
  }
  var originalSendYandex = w.sendYandex;
  w.sendYandex = function (id, target) {
    // These theme calls describe click/submit intent, not cart acceptance.
    if (target === 'cartAdd') return;
    if (typeof originalSendYandex === 'function') return originalSendYandex.apply(this, arguments);
  };
  function requestPath(settings) {
    if (!settings || String(settings.type || settings.method || 'GET').toUpperCase() !== 'POST') return '';
    try {
      var url = new URL(settings.url, w.location.href);
      var current = new URL(w.location.href);
      return url.origin === current.origin ? url.pathname : '';
    } catch (e) { return ''; }
  }
  function parse(payload) {
    if (typeof payload === 'string') {
      try { return JSON.parse(payload); } catch (e) { return null; }
    }
    return payload;
  }
  function firstResponse(xhr) {
    if (!xhr || handled.has(xhr)) return false;
    handled.add(xhr);
    return true;
  }
  function checkoutPage() { return d.getElementById('js-order-page'); }
  function insideCheckout(target) {
    var page = checkoutPage();
    return !!(page && target && (page === target || page.contains(target)));
  }
  function checkoutRequest(settings) {
    if (!checkoutPage() || !checkoutController || !checkoutController.urls) return false;
    var path = requestPath(settings);
    if (!path) return false;
    return ['create', 'calculate'].some(function (key) {
      return path === requestPath({url: checkoutController.urls[key], type: 'POST'});
    });
  }
  function visibleErrorMarker(marker) {
    // Native checkout keeps unavailable-shipping messages in hidden sections.
    // A CSS class alone is not evidence that the customer saw an error.
    if (!marker.getClientRects().length) return false;
    if (marker.matches('.wa-error-text') && !marker.textContent.trim()) return false;
    for (var element = marker; element; element = element.parentElement) {
      var style = w.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' ||
          style.visibility === 'collapse' || parseFloat(style.opacity) === 0) return false;
    }
    return true;
  }
  function scanValidationErrors() {
    var page = checkoutPage(), hasError = false;
    if (page) {
      var markers = page.querySelectorAll('.wa-error, .wa-error-text');
      for (var i = 0; i < markers.length; i++) {
        if (visibleErrorMarker(markers[i])) { hasError = true; break; }
      }
    }
    if (hasError && !validationError) goal('checkout_validation_error');
    validationError = hasError;
  }
  function attachAjax() {
    if (hooked || typeof w.jQuery !== 'function') return;
    hooked = true;
    w.jQuery(d).on('ajaxSuccess.megapolisFunnelV2', function (_event, xhr, settings, payload) {
      var path = requestPath(settings), isCheckout = checkoutRequest(settings);
      if ((path !== '/cart/add/' && !isCheckout) || !firstResponse(xhr)) return;
      var data = parse(payload);
      if (path === '/cart/add/') {
        goal(xhr.status === 200 && data && data.status === 'ok' && !data.errors ? 'cartAdd' : 'cart_add_error');
      } else if (xhr.status !== 200 || !data || data.status !== 'ok') goal('checkout_request_error');
    });
    w.jQuery(d).on('ajaxError.megapolisFunnelV2', function (_event, xhr, settings, thrownError) {
      // Native calculation aborts/restarts superseded requests; those are not errors.
      if (thrownError === 'abort' || (xhr && xhr.statusText === 'abort')) return;
      var path = requestPath(settings), isCheckout = checkoutRequest(settings);
      if ((path === '/cart/add/' || isCheckout) && firstResponse(xhr)) {
        goal(path === '/cart/add/' ? 'cart_add_error' : 'checkout_request_error');
      }
    });
    w.jQuery(d).on('wa_order_form_ready.megapolisFunnelV2 wa_order_form_reloaded.megapolisFunnelV2', function (event, controller) {
      if (insideCheckout(event.target) && controller && controller.urls) checkoutController = controller;
    });
    w.jQuery(d).on('change.megapolisFunnelV2', function (event) {
      var field = event.target;
      if (!insideCheckout(field) || !checkoutController || !field.matches || !field.checked || field.disabled) return;
      ['shipping', 'payment'].forEach(function (sectionName) {
        var section = checkoutController.sections && checkoutController.sections[sectionName];
        var wrapper = section && section.$wrapper && section.$wrapper[0];
        var selector = sectionName === 'shipping' ? '.js-type-field, .js-variant-field' : '.js-method-field';
        if (wrapper && wrapper.contains(field) && field.matches(selector) && !selected[sectionName]) {
          selected[sectionName] = true;
          goal('checkout_' + sectionName + '_selected');
        }
      });
    });
    w.jQuery(d).on('click.megapolisFunnelV2', function (event) {
      var target = event.target;
      if (!insideCheckout(target) || !checkoutController || !target.closest || selected.shipping) return;
      // Native shipping controls use hidden fields; dropdown/type clicks set them
      // without firing a change event. Scope to shipping selectors, never addresses.
      var choice = target.closest('#js-delivery-types-section .wa-type-wrapper, #js-delivery-variants-section .wa-dropdown-item, #js-delivery-short-variants-section .wa-dropdown-item');
      var section = checkoutController.sections && checkoutController.sections.shipping;
      var wrapper = section && section.$wrapper && section.$wrapper[0];
      if (choice && wrapper && wrapper.contains(choice) && !choice.disabled &&
          !(choice.classList && choice.classList.contains('is-disabled'))) {
        selected.shipping = true;
        goal('checkout_shipping_selected');
      }
    });
    w.jQuery(d).on('wa_order_form_created.megapolisFunnelV2', function (event, api) {
      if (!created && insideCheckout(event.target) && api && /^[1-9][0-9]*$/.test(String(api.order_id || ''))) {
        created = true;
        goal('order_created');
      }
    });
    w.jQuery(d).on('wa_order_cart_invalid.megapolisFunnelV2', function (event) {
      if (insideCheckout(event.target)) goal('checkout_request_error');
    });
  }
  function scheduleRetry() {
    if (!retryScheduled && attempts < 120 && (!hooked || pending.length)) {
      retryScheduled = true;
      w.setTimeout(retry, 500);
    }
  }
  function retry() {
    retryScheduled = false;
    attempts++;
    attachAjax();
    flush();
    // Late jQuery/counter loading: retry for one minute; no infinite timer.
    scheduleRetry();
  }
  function initialize() {
    if (initialized) return;
    initialized = true;
    var page = checkoutPage();
    if (page) {
      goal('checkout_started');
      scanValidationErrors();
      if (typeof w.MutationObserver === 'function') {
        new w.MutationObserver(scanValidationErrors).observe(page, {
          childList: true, subtree: true, characterData: true, attributes: true,
          attributeFilter: ['class', 'style', 'hidden', 'aria-hidden']
        });
      }
    }
    attachAjax();
    flush();
    scheduleRetry();
  }
  // Attach before DOM ready so the handler precedes the buy-to-cart redirect.
  attachAjax();
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})(window, document);

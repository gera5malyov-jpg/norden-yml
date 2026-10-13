/* Presentation only: never change shipping inputs, attributes, payload or totals.
 * Only audited exact label text inside the native checkout form is normalized.
 */
(function (w, d) {
  'use strict';
  if (w.location.hostname !== 'profikompany.ru' || w.__megapolisDeliveryV1) return;
  w.__megapolisDeliveryV1 = true;
  var initialized = false;
  var labels = {
    'Доставка от 890 руб (рассчитывается менеджером)': 'Курьерская доставка - отдельный расчёт',
    'Самовывоз от 450 руб. (рассчитывается менеджером)': 'Самовывоз - отдельный расчёт'
  };
  function normalize(wrapper) {
    var walker = d.createTreeWalker(wrapper, w.NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      var parent = node.parentElement;
      if (!parent || parent.closest('script, style, input, textarea, select, option')) continue;
      var original = node.nodeValue, exact = original.trim();
      if (Object.prototype.hasOwnProperty.call(labels, exact)) {
        node.nodeValue = original.replace(exact, labels[exact]);
      }
    }
  }
  function initialize() {
    if (initialized) return;
    initialized = true;
    var wrapper = d.getElementById('js-order-form');
    if (!wrapper) return;
    normalize(wrapper);
    if (typeof w.MutationObserver === 'function') {
      new w.MutationObserver(function () { normalize(wrapper); }).observe(wrapper, {
        subtree: true, childList: true, characterData: true
      });
    }
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})(window, document);

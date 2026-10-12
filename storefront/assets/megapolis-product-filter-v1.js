/* Exact country duplicates only. Each original ID/name/value and URL remains in
 * the form. A nameless proxy checks all originals before the existing theme AJAX
 * change handler serializes them. Materials and distinct shades are untouched.
 */
(function (w, d) {
  'use strict';
  function normalizeCountry(text) {
    var original = String(text);
    var parts = original.split(';').map(function (part) { return part.trim(); });
    if (parts.length < 2 || !parts[0]) return original;
    var key = parts[0].toLocaleLowerCase('ru');
    return parts.every(function (part) { return part.toLocaleLowerCase('ru') === key; }) ? parts[0] : original;
  }
  function countryKey(text) {
    var clean = normalizeCountry(text).trim();
    return /^(Китай|Россия|Вьетнам|Турция|Италия|China|Russia)$/i.test(clean) ? clean.toLocaleLowerCase('ru') : null;
  }
  function applySelection(inputs, checked) {
    inputs.forEach(function (input) { input.checked = checked; });
  }
  function selection(inputs) {
    var count = inputs.filter(function (input) { return input.checked; }).length;
    return {checked:count > 0, indeterminate:count > 0 && count < inputs.length};
  }
  if (typeof module === 'object' && module.exports) module.exports = {
    normalizeCountry:normalizeCountry, countryKey:countryKey, applySelection:applySelection, selection:selection
  };
  function init() {
    var root = d.getElementById('s-category-filters');
    if (!root || root.dataset.megapolisCountries === '1') return;
    root.dataset.megapolisCountries = '1';
    var groups = {};
    root.querySelectorAll('input[type="checkbox"][name="strana[]"]').forEach(function (input) {
      var label = input.parentNode.querySelector('label');
      if (!label || label.getAttribute('for') !== input.id) return;
      var target = label.querySelector('a.filter-link') || label;
      if (target.children.length) return;
      var old = target.textContent.trim(), clean = normalizeCountry(old), key = countryKey(old);
      if (clean !== old) target.textContent = clean;
      if (!key) return;
      // Do not combine the same country across separate feature groups.
      var group = input.closest('.category-filter__group');
      if (!group) return;
      if (!group.dataset.megapolisGroup) group.dataset.megapolisGroup = String(Object.keys(groups).length + 1);
      var groupKey = group.dataset.megapolisGroup + ':' + key;
      if (!groups[groupKey]) groups[groupKey] = {text:clean, inputs:[]};
      groups[groupKey].inputs.push(input);
    });
    Object.keys(groups).forEach(function (key, index) {
      var group = groups[key], inputs = group.inputs;
      if (inputs.length < 2) return;
      var firstRow = inputs[0].closest('.category-filter__group-item');
      if (!firstRow) return;
      var row = d.createElement('div');
      row.className = firstRow.className;
      var control = d.createElement('div'); control.className = 'custom-control custom-checkbox';
      var proxy = d.createElement('input');
      proxy.type = 'checkbox'; proxy.className = 'custom-control-input';
      proxy.id = 'megapolis-country-group-' + index;
      // No name means native GET and serializeArray still use only original IDs.
      var state = selection(inputs); proxy.checked = state.checked; proxy.indeterminate = state.indeterminate;
      var label = d.createElement('label'); label.className = 'custom-control-label';
      label.htmlFor = proxy.id; label.textContent = group.text;
      control.appendChild(proxy); control.appendChild(label); row.appendChild(control);
      firstRow.parentNode.insertBefore(row, firstRow);
      inputs.forEach(function (input) { input.closest('.category-filter__group-item').classList.add('megapolis-country-original'); });
      proxy.addEventListener('change', function (event) {
        // The theme listens on the form; prevent duplicate AJAX then dispatch once
        // after every original checkbox is updated, with the ordinary original target.
        event.stopPropagation();
        applySelection(inputs, proxy.checked); proxy.indeterminate = false;
        inputs[0].dispatchEvent(new w.Event('change', {bubbles:true}));
      });
    });
  }
  if (!w || !d || w.location.hostname !== 'profikompany.ru') return;
  // AJAX replaces the whole category wrapper. One observer re-applies to each new form.
  if (w.__megapolisCountryFilterV1) { w.__megapolisCountryFilterV1(); return; }
  w.__megapolisCountryFilterV1 = init;
  function boot() {
    init();
    if (w.MutationObserver) new w.MutationObserver(init).observe(d.body, {childList:true, subtree:true});
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', boot);
  else boot();
})(typeof window !== 'undefined' ? window : null, typeof document !== 'undefined' ? document : null);

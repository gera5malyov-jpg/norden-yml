'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const asset = path.join(__dirname, '../assets/megapolis-funnel-v2.js');
function browser(options = {}) {
  const events = {}, ajax = {}, timers = [], calls = [];
  let mutation;
  const page = options.checkout ? {contains: el => el && el.inside !== false, querySelector: () => page.error ? {} : null} : null;
  const document = {readyState: options.loading ? 'loading' : 'complete',
    getElementById: id => id === 'js-order-page' ? page : null,
    addEventListener: (type, fn) => (events[type] ||= []).push(fn)};
  const jquery = () => ({on: (types, fn) => types.split(' ').forEach(type => (ajax[type.split('.')[0]] ||= []).push(fn))});
  const window = {document, location: {hostname: options.host || 'profikompany.ru', href: 'https://profikompany.ru/order/'},
    jQuery: options.noJquery ? null : jquery,
    setTimeout: fn => {timers.push(fn); return timers.length;},
    sendYandex: (...args) => calls.push(['legacy', ...args]),
    ym: options.noYm ? null : (...args) => calls.push(args)};
  window.MutationObserver = function(cb) {mutation = cb; this.observe = () => {};};
  const context = vm.createContext({window, document, URL, WeakSet, Date});
  function run() {if(fs.existsSync(asset)) vm.runInContext(fs.readFileSync(asset, 'utf8'), context);}
  run();
  return {window, calls, run, document, page, jquery,
    mutate: () => mutation && mutation(),
    emitFrom: (name, target, ...args) => (ajax[name] || []).forEach(fn => fn({target}, ...args)),
    emit: (name, ...args) => (ajax[name] || []).forEach(fn => fn({}, ...args)),
    dom: (name, target) => (events[name] || []).forEach(fn => fn({target})),
    tick: () => {const pending = timers.splice(0); pending.forEach(fn => fn());},
    goals: () => calls.filter(x => x[1] === 'reachGoal').map(x => x[2])};
}
const cart = {url: '/cart/add/', type: 'POST'};
test('cart goal follows a successful same-origin response, never submit or legacy intent', () => {
  const b = browser();
  b.window.sendYandex(21811312, 'cartAdd'); b.dom('submit', {});
  assert.deepEqual(b.goals(), []);
  b.emit('ajaxSuccess', {status:200}, cart, {status:'ok',data:{total:1}});
  assert.deepEqual(b.goals(), ['cartAdd']);
});
test('application and transport errors are bounded cart errors with no response data', () => {
  const b = browser();
  b.emit('ajaxSuccess', {status:200}, cart, {status:'fail',errors:['private@example.com']});
  b.emit('ajaxError', {status:500}, cart);
  assert.deepEqual(b.goals(), ['cart_add_error','cart_add_error']);
  assert.ok(!JSON.stringify(b.calls).includes('private'));
});
test('deduplicates one response but counts separate successful actions', () => {
  const b = browser(); const xhr = {status:200};
  b.emit('ajaxSuccess', xhr, cart, '{"status":"ok"}');
  b.emit('ajaxSuccess', xhr, cart, {status:'ok'});
  b.emit('ajaxSuccess', {status:200}, cart, {status:'ok'});
  b.run(); b.emit('ajaxSuccess', {status:200}, cart, {status:'ok'});
  assert.deepEqual(b.goals(), ['cartAdd','cartAdd','cartAdd']);
});
test('ignores unrelated URLs, read requests, foreign origin, malformed responses and wrong host', () => {
  const b = browser();
  for (const settings of [{url:'/cart/add/',type:'GET'}, {url:'https://evil.example/cart/add/',type:'POST'}, {url:'/cart/additional/',type:'POST'}])
    b.emit('ajaxSuccess', {status:200}, settings, {status:'ok'});
  b.emit('ajaxSuccess', {status:200}, cart, 'not json');
  assert.deepEqual(b.goals(), ['cart_add_error']);
  const wrong = browser({host:'localhost'}); wrong.emit('ajaxSuccess', {status:200}, cart, {status:'ok'});
  assert.deepEqual(wrong.goals(), []);
});
test('starts checkout only on nonempty page and initializes just once', () => {
  assert.deepEqual(browser().goals(), []);
  const b = browser({checkout:true,loading:true}); assert.deepEqual(b.goals(), []);
  b.dom('DOMContentLoaded'); b.dom('DOMContentLoaded'); b.run();
  assert.deepEqual(b.goals(), ['checkout_started']);
});
test('retries late dependencies and delivery without counting events twice', () => {
  const b = browser({checkout:true,noYm:true,noJquery:true});
  b.window.jQuery = b.jquery;
  b.window.ym = (...args) => b.calls.push(args);
  b.tick(); b.tick();
  b.emit('ajaxSuccess', {status:200}, cart, {status:'ok'});
  assert.deepEqual(b.goals(), ['checkout_started','cartAdd']);
});
test('preserves unrelated legacy targets', () => {
  const b = browser(); b.window.sendYandex(99, 'unknown');
  assert.deepEqual(b.calls, [['legacy',99,'unknown']]);
});
test('queues late cart events when the counter appears after page initialization', () => {
  const b = browser(); b.window.ym = null;
  b.emit('ajaxSuccess', {status:200}, cart, {status:'ok'});
  b.window.ym = (...args) => b.calls.push(args); b.tick();
  assert.deepEqual(b.goals(), ['cartAdd']);
});
test('bounds retries when both dependencies stay unavailable', () => {
  const b = browser({noJquery:true,noYm:true,checkout:true});
  for(let i = 0; i < 130; i++) b.tick();
  b.window.ym = (...args) => b.calls.push(args); b.tick();
  assert.deepEqual(b.goals(), []);
});

function controller() {
  return {urls:{create:'/order/create/',calculate:'/order/calculate/'},
    sections:{shipping:{$wrapper:[{contains: el => el.section === 'shipping'}]},
      payment:{$wrapper:[{contains: el => el.section === 'payment'}]}}};
}
test('captures actual checkout selection changes without field values or identifiers', () => {
  const b = browser({checkout:true}); b.emitFrom('wa_order_form_ready', b.page, controller());
  const field = section => ({section,inside:true,checked:true,matches: () => true,value:'private@example.com'});
  const shipping = field('shipping'); shipping.checked = false;
  shipping.closest = () => shipping;
  b.emitFrom('click', shipping); b.emitFrom('click', shipping);
  b.emitFrom('change', field('payment')); b.emitFrom('change', field('auth'));
  assert.deepEqual(b.goals(), ['checkout_started','checkout_shipping_selected','checkout_payment_selected']);
  assert.ok(!JSON.stringify(b.calls).includes('private'));
});
test('tracks exact native checkout request errors, excluding autocomplete and aborts', () => {
  const b = browser({checkout:true}); b.emitFrom('wa_order_form_ready', b.page, controller());
  b.emit('ajaxSuccess',{status:200},{url:'/order/create/',type:'POST'},{status:'fail',errors:{general:'private'}});
  b.emit('ajaxError',{status:500},{url:'/order/calculate/',type:'POST'},'error');
  b.emit('ajaxError',{status:0},{url:'/order/calculate/',type:'POST'},'abort');
  b.emit('ajaxError',{status:500},{url:'/order/addressAutocomplete/',type:'POST'},'error');
  assert.deepEqual(b.goals(), ['checkout_started','checkout_request_error','checkout_request_error']);
  assert.ok(!JSON.stringify(b.calls).includes('private'));
});
test('validation error episodes are counted without their text', () => {
  const b = browser({checkout:true}); b.page.error = true; b.mutate(); b.mutate();
  b.page.error = false; b.mutate(); b.page.error = true; b.mutate();
  assert.deepEqual(b.goals(), ['checkout_started','checkout_validation_error','checkout_validation_error']);
});
test('creation goal requires native confirmed order event and never reports paid or ecommerce', () => {
  const b = browser({checkout:true});
  b.emitFrom('wa_order_form_created', b.page, {});
  b.emitFrom('wa_order_form_created', {inside:false}, {order_id:5});
  b.emitFrom('wa_order_form_created', b.page, {order_id:5});
  b.emitFrom('wa_order_form_created', b.page, {order_id:5}); b.run();
  assert.deepEqual(b.goals(), ['checkout_started','order_created']);
  assert.ok(!JSON.stringify(b.calls).includes('order_id'));
});

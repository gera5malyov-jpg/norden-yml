'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const asset = path.join(__dirname,'../assets/megapolis-delivery-v1.js');
function browser(options = {}) {
  const listeners = {}, nodes = []; let observer, observations = 0;
  function text(value,blocked=false) {
    const parent = {id:'native-method',name:'shipping[variant_id]',value:'890',
      dataset:{id:'native-provider-id'},onclick:()=>42,closest:() => blocked ? parent : null};
    const node = {nodeValue:value,parentElement:parent}; nodes.push(node); return node;
  }
  const wrapper = {nodes};
  const document = {readyState:options.loading?'loading':'complete',
    getElementById: id => id==='js-order-form' && !options.empty ? wrapper : null,
    addEventListener: (name,fn) => (listeners[name] ||= []).push(fn),
    createTreeWalker: () => {let i=0;return {nextNode:()=>nodes[i++] || null};}};
  const window = {location:{hostname: options.host || 'profikompany.ru'},NodeFilter:{SHOW_TEXT:4},
    MutationObserver:function(callback){observer=callback;this.observe=()=>observations++;}};
  const context = vm.createContext({window,document});
  function run(){if(fs.existsSync(asset))vm.runInContext(fs.readFileSync(asset,'utf8'),context);}
  return {run,text,window,wrapper, mutate:()=>observer && observer(),
    ready:()=> (listeners.DOMContentLoaded||[]).forEach(fn=>fn()),observations:()=>observations};
}
test('relabels only exact known text, preserving native values, ids, handlers and total',()=>{
  const b=browser(); const delivery=b.text('Доставка от 890 руб (рассчитывается менеджером)');
  const pickup=b.text('Самовывоз от 450 руб. (рассчитывается менеджером)');
  const total=b.text('Итого: 1890 ₽'); const method=delivery.parentElement;
  const handler=method.onclick,data=JSON.stringify(method.dataset);
  b.run();
  assert.equal(delivery.nodeValue,'Курьерская доставка - отдельный расчёт');
  assert.equal(pickup.nodeValue,'Самовывоз - отдельный расчёт');
  assert.equal(total.nodeValue,'Итого: 1890 ₽');assert.equal(method.value,'890');
  assert.equal(method.id,'native-method');assert.equal(method.name,'shipping[variant_id]');
  assert.equal(method.onclick,handler);assert.equal(JSON.stringify(method.dataset),data);
});
test('does not touch controls, scripts, foreign hosts or unknown shipping tariffs',()=>{
  const b=browser(); const control=b.text('Самовывоз от 450 руб. (рассчитывается менеджером)',true);
  const unknown=b.text('Доставка от 990 руб (рассчитывается менеджером)');b.run();
  assert.equal(control.nodeValue,'Самовывоз от 450 руб. (рассчитывается менеджером)');
  assert.equal(unknown.nodeValue,'Доставка от 990 руб (рассчитывается менеджером)');
  const wrong=browser({host:'other.example'}); const n=wrong.text('Доставка от 890 руб (рассчитывается менеджером)');wrong.run();
  assert.equal(n.nodeValue,'Доставка от 890 руб (рассчитывается менеджером)');
});
test('handles native checkout rerenders idempotently with one observer',()=>{
  const b=browser({loading:true});b.run();b.ready();b.ready();b.run();
  const n=b.text('  Самовывоз от 450 руб. (рассчитывается менеджером)  ');b.mutate();b.mutate();
  assert.equal(n.nodeValue,'  Самовывоз - отдельный расчёт  ');assert.equal(b.observations(),1);
});
test('empty checkout never initializes a delivery observer',()=>{
  const b=browser({empty:true});b.run();assert.equal(b.observations(),0);
});

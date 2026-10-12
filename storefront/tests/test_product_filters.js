'use strict';
const assert = require('node:assert/strict');
const api = require('../assets/megapolis-product-filter-v1.js');
assert.equal(api.normalizeCountry('Китай; Китай'), 'Китай');
assert.equal(api.normalizeCountry('Россия;Россия; россия'), 'Россия');
assert.equal(api.normalizeCountry('Китай; Россия'), 'Китай; Россия');
assert.equal(api.normalizeCountry('Китай;'), 'Китай;');
assert.equal(api.countryKey('Китай; Китай'),api.countryKey('Китай'));
assert.notEqual(api.countryKey('Россия'),api.countryKey('Китай'));
assert.equal(api.countryKey('Светло-серый;Тёмно-серый'),null);
assert.equal(api.countryKey('PU / экокожа'),null);
const inputs = [
 {id:'checkboxstrana17', name:'strana[]', value:'17', checked:true},
 {id:'checkboxstrana21', name:'strana[]', value:'21', checked:false}
];
const immutable = inputs.map(({id,name,value})=>({id,name,value}));
assert.deepEqual(api.selection(inputs), {checked:true,indeterminate:true});
// Partial old URLs retain their exact original selection until user acts.
assert.deepEqual(inputs.map(i=>i.checked),[true,false]);
api.applySelection(inputs,true);
assert.deepEqual(inputs.map(i=>i.checked),[true,true]);
assert.deepEqual(inputs.filter(i=>i.checked).map(i=>i.name+'='+i.value),['strana[]=17','strana[]=21']);
assert.deepEqual(inputs.map(({id,name,value})=>({id,name,value})),immutable);
api.applySelection(inputs,false);
assert.deepEqual(api.selection(inputs), {checked:false,indeterminate:false});
console.log('Product/filter OR selection and shade regressions passed');
// Run the real browser initializer at its DOM boundary. Verify exactly one theme
// change event occurs after both IDs are checked, with no submit interception.
const vm = require('node:vm');
const fs = require('node:fs');
const emitted = [], inserted = [];
const featureGroup = {dataset:{}};
const parent = {insertBefore:(row)=>inserted.push(row)};
function element(tag) {
  return {tag,children:[],className:'',listeners:{},appendChild(child){this.children.push(child);},
    addEventListener(name,fn){this.listeners[name]=fn;}};
}
function original(id,value,text,checked) {
  const row = {className:'category-filter__group-item', parentNode:parent,
    classList:{add(name){row.hiddenClass=name;}}};
  const target = {textContent:text,children:[]};
  const label = {getAttribute:()=>id,querySelector:()=>target};
  return {id,name:'strana[]',value,checked,
    parentNode:{querySelector:()=>label},
    closest:(name)=>name==='.category-filter__group'?featureGroup:row,
    dispatchEvent(event){emitted.push({type:event.type,bubbles:event.bubbles,
      values:liveInputs.filter(i=>i.checked).map(i=>i.value)});}};
}
const liveInputs=[original('checkboxstrana17','17','Китай',false), original('checkboxstrana21','21','Китай;Китай',true)];
const root = {dataset:{},querySelectorAll:()=>liveInputs};
const win = {location:{hostname:'profikompany.ru'},Event:function(type,options){this.type=type;this.bubbles=options.bubbles;}};
const doc = {readyState:'complete',body:{},getElementById:()=>root,createElement:element};
vm.runInNewContext(fs.readFileSync(require.resolve('../assets/megapolis-product-filter-v1.js'),'utf8'),{window:win,document:doc});
assert.equal(inserted.length,1);
const proxy=inserted[0].children[0].children[0];
assert.equal(proxy.name,undefined);
assert.equal(proxy.indeterminate,true);
assert.deepEqual(liveInputs.map(i=>i.checked),[false,true]);
let stopped=0;proxy.checked=true;proxy.listeners.change({stopPropagation(){stopped++;}});
assert.equal(stopped,1);
assert.deepEqual(emitted,[{type:'change',bubbles:true,values:['17','21']}]);
assert.equal(liveInputs[0].closest('.category-filter__group-item').hiddenClass,'megapolis-country-original');
assert.equal(liveInputs[0].name,'strana[]');assert.equal(liveInputs[1].value,'21');
console.log('Real DOM grouping preserves IDs and dispatches one ordinary AJAX change');

/* Scope the decorative header hover buffer override to the mobile home page. */
(function(w,d){
 'use strict';
 if(w.location.hostname!=='profikompany.ru')return;
 function init(){
  var input=d.getElementById('megapolis-home-search');
  if(!input)return;
  d.body.classList.add('megapolis-home-page');
 }
 if(d.readyState==='loading')d.addEventListener('DOMContentLoaded',init);else init();
})(window,document);

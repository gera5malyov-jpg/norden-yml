/* Keep the first-screen search clear of the theme's mobile header on focus. */
(function(w,d){
 'use strict';
 if(w.location.hostname!=='profikompany.ru')return;
 function init(){
  var input=d.getElementById('megapolis-home-search');
  if(!input)return;
  input.addEventListener('focus',function(){
   if(w.innerWidth>=768)return;
   w.requestAnimationFrame(function(){w.requestAnimationFrame(function(){
    var header=d.querySelector('header.header:not(.header--fixed)');
    if(!header)return;
    var r=input.getBoundingClientRect(),h=header.getBoundingClientRect();
    if(h.bottom>0&&r.top<h.bottom+12){
     w.scrollTo(0,Math.max(0,w.pageYOffset-(h.bottom-r.top)-16));
    }
   });});
  });
 }
 if(d.readyState==='loading')d.addEventListener('DOMContentLoaded',init);else init();
})(window,document);

/* The visible ReelShort tab keeps the desktop command channel responsive. */
(()=>{
  'use strict';
  let active=false;
  async function tick(){
    if(active)return;
    active=true;
    try{await chrome.runtime.sendMessage({type:'DRAMA_DESKTOP_POLL',pageUrl:location.href});}catch(_){}
    finally{active=false;}
  }
  tick();setInterval(tick,1500);
})();

/* Observe the current tab's own media requests continuously, including after the
   browser's Resource Timing buffer fills. No headers, cookies or response interception. */
((root)=>{
  'use strict';
  if(root.DramaHLSObserver)return;
  function trusted(value){
    try{const u=new URL(String(value||''));return u.protocol==='https:'&&!u.username&&!u.password&&u.hostname.endsWith('.crazymaplestudios.com')&&!/\/(?:undefined|null|nan|none)(?:\/|$)/i.test(u.pathname);}catch(_){return false;}
  }
  function isHLS(value){try{return trusted(value)&&/\.m3u8$/i.test(new URL(value).pathname);}catch(_){return false;}}
  function isMedia(value){try{return trusted(value)&&/\.(?:m3u8|ts|m4s|mp4|cmfv|cmfa)$/i.test(new URL(value).pathname);}catch(_){return false;}}
  const observed=[];
  function remember(entries){
    const origin=Number(root.performance?.timeOrigin)||0;
    for(const e of entries||[]){
      if(!e||!isMedia(e.name))continue;
      observed.push({name:String(e.name),absoluteStart:Number(e.absoluteStart)||(Number(e.timeOrigin)||origin)+Number(e.startTime||0)});
    }
    if(observed.length>1536)observed.splice(0,observed.length-1536);
  }
  function all(){
    const origin=Number(root.performance?.timeOrigin)||0;
    const fallback=(root.performance?.getEntriesByType('resource')||[]).filter(e=>isMedia(e.name)).map(e=>({name:String(e.name),absoluteStart:origin+Number(e.startTime||0)}));
    const unique=new Map();
    for(const e of [...fallback,...observed])unique.set(e.name+'|'+e.absoluteStart,e);
    return [...unique.values()].sort((a,b)=>a.absoluteStart-b.absoluteStart);
  }
  function resources(since=0,until=Date.now()){
    return all().filter(e=>e.absoluteStart>=since-200&&e.absoluteStart<=until+2000).map(e=>({url:e.name,absoluteStart:e.absoluteStart}));
  }
  function fromEntries(entries,minEpoch,maxEpoch=Date.now()){
    const seen=new Map();
    for(const e of entries||[]){
      if(!e||!isHLS(e.name))continue;
      const start=Number(e.absoluteStart)||(Number(e.timeOrigin||0)+Number(e.startTime||0));
      if(!(start>=minEpoch-200&&start<=maxEpoch+2000))continue;
      seen.set(String(e.name),{url:String(e.name),observedAt:new Date(start).toISOString(),absoluteStart:start});
    }
    return [...seen.values()].slice(-25);
  }
  function recent(since,end=Date.now()){return fromEntries(all(),since,end);}
  let observer;
  try{
    if(root.PerformanceObserver){observer=new root.PerformanceObserver(list=>remember(list.getEntries()));observer.observe({type:'resource',buffered:true});}
  }catch(_){/* Recent timeline entries remain a fallback on older browsers. */}
  root.DramaHLSObserver=Object.freeze({trusted,isHLS,isMedia,fromEntries,recent,resources});
})(globalThis);

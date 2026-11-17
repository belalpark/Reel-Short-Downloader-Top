/* Three leased tabs. Every snapshot is bound to its sender tab, run and series. */
(()=>{
 'use strict';
 const KEY='reelDesktopSessions',BASE='http://127.0.0.1:18765';
 let slots={},loaded=false,reconciling=false,lastPoll=0,failures=0;
 const publishes=new Map();
 async function post(path,body={},retry=true){
  const response=await fetch(chrome.runtime.getURL('desktop_config.json'),{cache:'no-store'}),cfg=await response.json();
  if(cfg.version!==chrome.runtime.getManifest().version||typeof cfg.token!=='string'||cfg.token.length<32)throw Error('setup_required');
  const res=await fetch(BASE+path,{method:'POST',mode:'cors',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json',Authorization:'Bearer '+cfg.token},
   body:JSON.stringify({version:chrome.runtime.getManifest().version,extensionId:chrome.runtime.id,...body}),signal:AbortSignal.timeout(6000)});
  if(res.status===401&&retry&&path!=='/connect'){await post('/connect',{},false);return post(path,body,false);}
  const value=await res.json();if(!res.ok)throw Error(value.error||'session_connection_failed');return value;
 }
 async function load(){if(!loaded){slots=(await chrome.storage.session.get(KEY))[KEY]||{};loaded=true;}}
 async function save(){await chrome.storage.session.set({[KEY]:slots});}
 async function stop(row){if(row?.tabId)try{await chrome.tabs.sendMessage(row.tabId,{type:'DRAMA_STOP',autoMode:true});}catch(_){}row.run='';row.started='';row.job=null;}
 async function reconcile(force=false){
  await load();
  if(reconciling||(!force&&Date.now()-lastPoll<1000))return;
  reconciling=true;lastPoll=Date.now();
  try{
   const response=await post('/sessions/poll');failures=0;
   const jobs=(response.sessions||[]).slice(0,3),wanted=new Map(jobs.map(j=>[String(j.slot),j]));
   for(const [slot,row] of Object.entries(slots))if(row.run&&!wanted.has(slot)){const old=row.run;await stop(row);await chrome.storage.local.remove('dramaScannerSession:'+old);}
   for(const job of jobs){
    if(!/^[a-f\d]{32}$/.test(job.id)||!/^https:\/\/www\.reelshort\.com\//.test(job.pageUrl))continue;
    const key=String(job.slot);let row=slots[key];let tab;
    if(row?.tabId)try{tab=await chrome.tabs.get(row.tabId);}catch(_){}
    if(tab&&row.run===job.id){row.job=job;continue;}
    const old=row?.run;
    if(row?.run)await stop(row);
    if(!tab){tab=await chrome.tabs.create({url:job.pageUrl,active:false});row={tabId:tab.id,created:true};}
    else{row=row||{};if(tab.url?.split('?')[0]===job.pageUrl)await chrome.tabs.reload(tab.id);else await chrome.tabs.update(tab.id,{url:job.pageUrl});}
    Object.assign(row,{tabId:tab.id,run:job.id,job,started:''});slots[key]=row;
    if(old&&old!==job.id)await chrome.storage.local.remove('dramaScannerSession:'+old);
   }
   await save();
  }catch(error){
   if(++failures>=3){for(const row of Object.values(slots))if(row.run)await stop(row);await save();}
  }finally{reconciling=false;}
 }
 async function tabIds(){await load();return Object.values(slots).map(s=>s.tabId);}
 async function poll(sender,pageUrl){
  await reconcile();await load();
  const row=Object.values(slots).find(s=>s.tabId===sender.tab.id);if(!row)return false;
  const job=row.job;if(!row.run||!job)return true;
  try{
   await post('/sessions/heartbeat',{sessionId:row.run,seriesId:job.seriesId,tabId:sender.tab.id,pageUrl});
   if(!String(pageUrl).includes(job.seriesId))return true;
   const bound=await chrome.tabs.sendMessage(row.tabId,{type:'DRAMA_SESSION_BIND',channel:row.run,seriesId:job.seriesId});
   if(!bound?.ok)return true;
   if(!/\/episodes\/episode-\d+/.test(pageUrl)){
    const entry=await chrome.tabs.sendMessage(row.tabId,{type:'DRAMA_LIBRARY_ENTRY',seriesId:job.seriesId});
    if(entry?.ok)await chrome.tabs.update(row.tabId,{url:entry.pageUrl});return true;
   }
   if(row.started!==row.run){
    const started=await chrome.tabs.sendMessage(row.tabId,{type:'DRAMA_START',includePaid:true,waitMs:7000,autoMode:true,maxPasses:1,collectOnly:true,
     channel:row.run,targetEpisodes:job.targets,collectedEpisodes:job.skipped,desktopCommandId:row.run});
    if(started?.ok){row.started=row.run;await save();}
   }
  }catch(_){}
  return true;
 }
 function sanitized(state){
  const rows={};for(const [n,r] of Object.entries(state.rows||{}))rows[n]={episode:r.episode,pageUrl:r.pageUrl||'',mediaUrl:r.mediaUrl||'',playbackStatus:r.playbackStatus||'',status:r.status||'',sourceType:r.sourceType||'',captureId:r.captureId||'',observedAt:r.checkedAt||''};
  return {channel:state.channel,seriesId:state.seriesId,title:state.title,total:state.total,current:state.current,rows,desktopCommandId:state.desktopCommandId,
   running:!!state.running,paused:!!state.paused,finished:!!state.finished,rateLimited:!!state.rateLimited,seriesPageUrl:state.seriesPageUrl||'',
   browserUserAgent:String(state.browserUserAgent||'').slice(0,300),note:state.note||'',updatedAt:state.updatedAt||''};
 }
 async function publish(state,sender){
  await load();const row=Object.values(slots).find(s=>s.tabId===sender.tab.id&&s.run===state?.channel);
  if(!row||row.job?.seriesId!==state.seriesId||state.desktopCommandId!==row.run)throw Error('wrong_tab_or_series_session');
  const key=row.run,previous=publishes.get(key)||Promise.resolve();
  const action=previous.catch(()=>{}).then(()=>post('/sessions/snapshot',{sessionId:key,seriesId:state.seriesId,tabId:sender.tab.id,state:sanitized(state)}));
  publishes.set(key,action);try{return await action;}finally{if(publishes.get(key)===action)publishes.delete(key);}
 }
 chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(message?.type!=='DRAMA_SESSION_SNAPSHOT')return;
  if(sender.id!==chrome.runtime.id||!sender.tab?.url?.startsWith('https://www.reelshort.com/'))return;
  publish(message.state,sender).then(reply).catch(e=>reply({ok:false,error:String(e.message||e)}));return true;
 });
 globalThis.ReelSessions=Object.freeze({poll,tabIds,reconcile});
})();

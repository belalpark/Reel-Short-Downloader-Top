/* Automatic per-user authorization. No PIN, account password or manual pairing. */
(()=>{
  'use strict';
  const BASE='http://127.0.0.1:18765';
  const STORE='dramaScannerV5Current';
  let busy=false,failures=0,publishChain=Promise.resolve();
  async function config(){
    const response=await fetch(chrome.runtime.getURL('desktop_config.json'),{cache:'no-store'});
    const data=await response.json();
    if(data.version!==chrome.runtime.getManifest().version || typeof data.token!=='string'||data.token.length<32)throw Error('setup_required');
    return data;
  }
  async function post(path,body={},retry=true){
    const settings=await config();
    const response=await fetch(BASE+path,{method:'POST',mode:'cors',credentials:'omit',cache:'no-store',
      headers:{'Content-Type':'application/json',Authorization:'Bearer '+settings.token},
      body:JSON.stringify({version:chrome.runtime.getManifest().version,extensionId:chrome.runtime.id,...body}),signal:AbortSignal.timeout(5000)});
    if(response.status===401&&retry&&path!=='/connect'){
      await post('/connect',{},false);return post(path,body,false);
    }
    const result=await response.json();
    if(!response.ok)throw Error(result.error||'connection_failed');
    return result;
  }
  async function targetTab(){
    const catalog=(await chrome.storage.local.get('reelLibraryCatalogJob')).reelLibraryCatalogJob;
    const libraryOwner=(await chrome.storage.session.get('libraryOwnerTab')).libraryOwnerTab;
    if(catalog?.running&&libraryOwner){try{return await chrome.tabs.get(libraryOwner);}catch(_){}}
    const owner=(await chrome.storage.session.get('desktopOwnerTab')).desktopOwnerTab;
    const state=(await chrome.storage.local.get(STORE))[STORE];
    if(owner&&state?.running&&state.autoMode){try{return await chrome.tabs.get(owner);}catch(_){}}
    const tabs=await chrome.tabs.query({url:'https://www.reelshort.com/*'});
    return tabs.sort((a,b)=>(b.lastAccessed||0)-(a.lastAccessed||0))[0]||null;
  }
  function sanitizedState(state){
    const rows={};
    for(const [key,row] of Object.entries(state.rows||{}))rows[key]={episode:row.episode,pageUrl:row.pageUrl||'',mediaUrl:row.mediaUrl||'',playbackStatus:row.playbackStatus||'',status:row.status||'',sourceType:row.sourceType||'',captureId:row.captureId||'',observedAt:row.checkedAt||''};
    return {seriesId:state.seriesId,title:state.title,total:state.total,rows,desktopCommandId:state.desktopCommandId,
      running:!!state.running,paused:!!state.paused,finished:!!state.finished,autoPass:state.autoPass||1,rateLimited:!!state.rateLimited,seriesPageUrl:state.seriesPageUrl||'',note:state.note||'',updatedAt:state.updatedAt||''};
  }
  function publish(state){
    if(!state?.autoMode||!state.desktopCommandId)return Promise.resolve({ok:true});
    const value=sanitizedState(state);
    const result=publishChain.catch(()=>{}).then(()=>post('/snapshot',{state:value}));
    publishChain=result;return result;
  }
  async function poll(sender,pageUrl){
    if(busy)return {ok:true};
    const target=await targetTab();
    if(!target||target.id!==sender.tab.id)return {ok:true};
    busy=true;
    try{
      const response=await post('/exchange',{pageUrl});failures=0;
      const command=response.command;
      const previous=(await chrome.storage.local.get(STORE))[STORE];
      if(previous?.running&&previous.autoMode&&previous.desktopCommandId!==response.runId){
        const stopped=await chrome.tabs.sendMessage(target.id,{type:'DRAMA_STOP',autoMode:true});
        if(!stopped?.ok)throw Error(stopped?.error||'تعذر إيقاف جلسة الفحص القديمة');
        await chrome.storage.session.remove('desktopOwnerTab');
      }
      if(command){
        if(command.kind==='open'||command.kind==='catalog'){
          const page=new URL(command.pageUrl);
          if(page.protocol!=='https:'||page.host!=='www.reelshort.com')throw Error('Invalid library page');
          if(command.kind==='catalog'){
            await chrome.storage.local.set({reelLibraryCatalogJob:{id:command.id,running:true,pages:[],visited:[],seen:[]}});
            await chrome.storage.session.set({libraryOwnerTab:target.id});
          }
          if(target.url?.split('?')[0]===page.href)await chrome.tabs.reload(target.id);else await chrome.tabs.update(target.id,{url:page.href});
          await post('/ack',{commandId:command.id,error:''});return response;
        }
        if(command.kind==='start'&&command.collectOnly&&!/\/episodes\/episode-\d+/.test(target.url||'')){
          const entry=await chrome.tabs.sendMessage(target.id,{type:'DRAMA_LIBRARY_ENTRY',seriesId:command.series_id});
          if(entry?.ok){await chrome.tabs.update(target.id,{url:entry.pageUrl});return response;}
        }
        const type=command.kind==='stop'?'DRAMA_STOP':'DRAMA_START';
        if(command.kind==='stop'){try{await chrome.tabs.sendMessage(target.id,{type:'DRAMA_LIBRARY_STOP'});}catch(_){}await chrome.storage.session.remove('libraryOwnerTab');}
        const result=await chrome.tabs.sendMessage(target.id,{type,includePaid:true,waitMs:7000,autoMode:true,maxPasses:3,patient:!!command.patient,collectOnly:!!command.collectOnly,collectedEpisodes:command.collectedEpisodes||[],targetEpisodes:command.targetEpisodes||[],completedEpisodes:command.completedEpisodes||[],desktopCommandId:command.id});
        if(command.kind==='start'&&result?.ok)await chrome.storage.session.set({desktopOwnerTab:target.id});
        if(command.kind==='stop')await chrome.storage.session.remove('desktopOwnerTab');
        await post('/ack',{commandId:command.id,error:result?.ok?'':String(result?.error||'تعذر بدء الفحص')});
      }
      const catalog=(await chrome.storage.local.get('reelLibraryCatalogJob')).reelLibraryCatalogJob;
      if(catalog?.running)try{await chrome.tabs.sendMessage(target.id,{type:'DRAMA_LIBRARY_DISCOVER'});}catch(_){}
      return response;
    }catch(error){
      failures++;
      if(failures>=3){
        const state=(await chrome.storage.local.get(STORE))[STORE];
        if(state?.running&&state.autoMode)try{await chrome.tabs.sendMessage(target.id,{type:'DRAMA_STOP'});}catch(_){}
      }
      return {ok:false,error:String(error?.message||error)};
    }finally{busy=false;}
  }
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(!['DRAMA_DESKTOP_STATUS','DRAMA_DESKTOP_POLL','DRAMA_DESKTOP_SNAPSHOT','DRAMA_DESKTOP_PROGRESS','DRAMA_LIBRARY_CATALOG'].includes(message?.type))return;
    let action;
    if(message.type==='DRAMA_DESKTOP_STATUS'){
      if(sender.id!==chrome.runtime.id||sender.url!==chrome.runtime.getURL('popup.html'))return;
      action=post('/connect');
    }else{
      if(sender.id!==chrome.runtime.id||!sender.tab?.url?.startsWith('https://www.reelshort.com/'))return;
      if(message.type==='DRAMA_LIBRARY_CATALOG'){
        action=(async()=>{const owner=(await chrome.storage.session.get('libraryOwnerTab')).libraryOwnerTab;if(owner!==sender.tab.id)throw Error('Invalid catalog owner');return post('/library/catalog',{jobId:message.jobId,items:message.items,done:!!message.done,error:message.error||''});})();
      }else action=message.type==='DRAMA_DESKTOP_POLL'?poll(sender,message.pageUrl):message.type==='DRAMA_DESKTOP_PROGRESS'?post('/progress'):publish(message.state);
    }
    action.then(reply).catch(e=>reply({ok:false,error:String(e?.message||e)}));return true;
  });
  async function alarm(){
    const current=await chrome.alarms.get('desktop-readiness');
    if(!current)await chrome.alarms.create('desktop-readiness',{periodInMinutes:0.5});
  }
  chrome.runtime.onInstalled.addListener(async()=>{
    await alarm();try{await post('/connect');}catch(_){}
    const tab=await targetTab();if(tab)try{await chrome.tabs.reload(tab.id);}catch(_){}
  });
  chrome.runtime.onStartup.addListener(()=>{alarm();post('/connect').catch(()=>{});});
  chrome.alarms.onAlarm.addListener(item=>{if(item.name==='desktop-readiness')post('/connect').catch(()=>{});});
  alarm().catch(()=>{});
  globalThis.ReelDesktop=Object.freeze({publish});
})();

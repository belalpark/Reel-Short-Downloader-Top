'use strict';
// All pages/media are local fixtures. No production account or paid content is used.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline');
const ROOT=path.resolve(__dirname,'..'),OUT=path.join(ROOT,'test-results');
const ID='0123456789abcdef01234567',UNAVAILABLE='f'.repeat(24),STOP='e'.repeat(24);
const pageURL=(n,id=ID)=>`https://www.reelshort.com/episodes/episode-${n}-test-${id}`;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function waitFor(fn,timeout=150000,label='browser assertion'){
  const start=Date.now();
  while(Date.now()-start<timeout){if(await fn())return;await delay(150);}
  throw Error('Timed out: '+label);
}
const visits=new Map();
function fixture(n,id){
  const total=id===ID?3:1;
  const accessible=id===ID&&(n<=2||(visits.get(id+'/3')||0)>=2);
  const metadata={props:{pageProps:{data:{book_id:id,book_title:'Offline Test Series',serial_number:n,chapter_count:total,paid_start:3,video_url:accessible?`https://cdn.example.org/${n}.mp4`:''}}}};
  const movie=fs.readFileSync(path.join(__dirname,'fixture.webm.b64'),'utf8').trim();
  return `<!doctype html><html><head><title>Offline Test Series | ReelShort</title></head><body>
  <script id="__NEXT_DATA__" type="application/json">${JSON.stringify(metadata)}</script>
  <h1>Offline episode ${n}</h1><video muted loop playsinline src="data:video/webm;base64,${movie}"></video>
  ${Array.from({length:total},(_,i)=>`<a href="${pageURL(i+1,id)}">Episode ${i+1}</a>`).join(' ')}
  </body></html>`;
}
(async()=>{
  fs.mkdirSync(OUT,{recursive:true});
  const profile=fs.mkdtempSync(path.join(OUT,'profile-'));
  let context,child,worker;
  const queued=[],errors=[];let authenticatedHeartbeats=0,bridgeRestarts=0;
  try{
    child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),['-m','scripts.bridge_fixture'],{cwd:ROOT,stdio:['pipe','pipe','inherit']});
    const lines=readline.createInterface({input:child.stdout});
    const first=await Promise.race([once(lines,'line'),once(child,'exit').then(([code])=>{throw Error('Bridge fixture exited: '+code);}),delay(10000).then(()=>{throw Error('Bridge startup timed out');})]);
    const settings=JSON.parse(first[0]);
    lines.on('line',line=>{
      const value=JSON.parse(line);
      if(value.authenticated_heartbeat)authenticatedHeartbeats++;
      if(value.bridge_restarted)bridgeRestarts++;
      if(value.queued)queued.push(...value.queued);
    });
    context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,args:[`--disable-extensions-except=${settings.extension_path}`,`--load-extension=${settings.extension_path}`]});
    // The service worker may sleep before Playwright attaches. Read the installed
    // extension from Chrome's own page, then opening its popup wakes it normally.
    const extensionsPage=await context.newPage();
    await extensionsPage.goto('chrome://extensions/');
    const installed=await extensionsPage.evaluate(()=>new Promise(resolve=>chrome.developerPrivate.getExtensionsInfo({includeDisabled:true,includeTerminated:true},resolve)));
    const own=installed.find(item=>item.name.startsWith('Reel Short Downloader Top'));
    fs.writeFileSync(path.join(OUT,'extension-startup.json'),JSON.stringify(installed.map(item=>({id:item.id,name:item.name,state:item.state,manifestErrors:item.manifestErrors,runtimeErrors:item.runtimeErrors})),null,2));
    assert.ok(own,'Prepared extension was not loaded by Chromium');
    const extId=own.id;
    context.on('weberror',error=>errors.push(error.error().message));
    const state=()=>worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current);
    const command=(kind,id)=>child.stdin.write(JSON.stringify({command:kind,series_id:id})+'\n');
    let popup=await context.newPage();
    await popup.goto(`chrome-extension://${extId}/popup.html`);
    worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:60000});
    await extensionsPage.close();
    assert.equal(await popup.locator('#desktopCode, #desktopPair, input[type=password]').count(),0);
    await waitFor(()=>authenticatedHeartbeats>0,15000,'automatic connection');
    const alarm=await worker.evaluate(()=>chrome.alarms.get('desktop-readiness'));
    assert.equal(alarm.periodInMinutes,0.5);
    await popup.close();
    const beforeBackground=authenticatedHeartbeats;
    await waitFor(()=>authenticatedHeartbeats>beforeBackground,45000,'background heartbeat with popup closed and no website tab');
    await context.route('https://www.reelshort.com/**',async route=>{
      const url=new URL(route.request().url());
      const n=Number(url.pathname.match(/episode-(\d+)/)?.[1]||1);
      const id=url.pathname.match(/[a-f0-9]{24}/)?.[0]||ID;
      if(route.request().isNavigationRequest())visits.set(id+'/'+n,(visits.get(id+'/'+n)||0)+1);
      await route.fulfill({contentType:'text/html',body:fixture(n,id)});
    });
    const reel=await context.newPage();
    await reel.goto(pageURL(1));
    assert.equal(await reel.locator('video').evaluate(video=>video.paused),true);
    command('start',ID);
    await waitFor(async()=>{const s=await state();return s?.seriesId===ID&&s.finished;},150000,'automatic scan and missing-only refresh');
    const scan=await state();
    assert.equal(scan.autoPass,2);
    assert.equal(Object.values(scan.rows).filter(r=>r.mediaUrl).length,3);
    assert.equal(Object.values(scan.rows).filter(r=>r.playbackStatus==='playback_verified').length,3);
    assert.equal(visits.get(ID+'/1'),1);
    assert.equal(visits.get(ID+'/2'),1);
    assert.equal(visits.get(ID+'/3'),2);
    await waitFor(()=>queued.length===3,10000,'known inbox queued without CSV selection');
    assert.deepEqual(queued,[1,2,3]);
    const inbox=path.join(settings.inbox,ID+'.csv');
    assert.ok(fs.existsSync(inbox));
    assert.match(fs.readFileSync(inbox,'utf8'),/https:\/\/cdn.example.org\/3.mp4/);
    await reel.goto(pageURL(1,UNAVAILABLE));
    command('start',UNAVAILABLE);
    await waitFor(async()=>{const s=await state();return s?.seriesId===UNAVAILABLE&&s.finished;},150000,'bounded retry exhaustion');
    const unavailable=await state();
    assert.equal(unavailable.autoPass,3);
    assert.equal(visits.get(UNAVAILABLE+'/1'),3);
    assert.equal(Object.values(unavailable.rows).filter(r=>r.mediaUrl).length,0);
    assert.deepEqual(queued,[1,2,3]);
    await worker.evaluate(({id,url})=>chrome.storage.local.set({dramaScannerV5Current:{seriesId:id,total:1,running:false,finished:true,rows:{'1':{episode:1,pageUrl:url,mediaUrl:'https://cdn.example.org/expired.mp4?token=expired'}}}}),{id:STOP,url:pageURL(1,STOP)});
    await reel.goto(pageURL(1,STOP));
    command('start',STOP);
    await waitFor(async()=>{const s=await state();return s?.seriesId===STOP&&s.autoPass===2;},60000,'retry backoff');
    command('stop',STOP);
    await waitFor(async()=>{const s=await state();return s?.seriesId===STOP&&!s.running;},10000,'stop acknowledgement');
    const beforeStop=visits.get(STOP+'/1');
    await delay(11000);
    assert.equal(visits.get(STOP+'/1'),beforeStop);
    // A restarted app must cancel the former scan and accept a new start without a manual reset.
    command('start',STOP);
    await waitFor(async()=>{const s=await state();return s?.seriesId===STOP&&s.running&&s.autoPass===2;},60000,'scan before app restart');
    const formerRun=(await state()).desktopCommandId;
    const beforeRestart=visits.get(STOP+'/1');
    command('restart',STOP);
    await waitFor(()=>bridgeRestarts===1,15000,'same-port app restart');
    await waitFor(async()=>!(await state())?.running,15000,'former scan cancelled after restart');
    await delay(11000);
    assert.equal(visits.get(STOP+'/1'),beforeRestart);
    command('start',STOP);
    await waitFor(async()=>{const s=await state();return s?.running&&s.desktopCommandId!==formerRun;},15000,'new start after app restart');
    command('stop',STOP);
    await waitFor(async()=>!(await state())?.running,10000,'final stop');
    assert.deepEqual(queued,[1,2,3],'A new run must not enqueue a cached expired URL');
    popup=await context.newPage();
    await popup.goto(`chrome-extension://${extId}/popup.html`);
    await popup.screenshot({path:path.join(OUT,'extension-popup.png'),fullPage:true});
    assert.equal(errors.length,0,errors.join('\n'));
    fs.writeFileSync(path.join(OUT,'browser.json'),JSON.stringify({browser:await context.browser().version(),extensionVersion:'5.6.1',automaticConnection:true,pairingCodeRequired:false,manualCsvSelection:false,backgroundHeartbeat:true,explicitMedia:3,queuedEpisodes:queued,missingOnlyVisits:[1,1,2],unavailablePasses:unavailable.autoPass,cancelPreventsRefresh:true,appRestartStopsOldScan:true,restartNeedsManualReset:false,cachedMediaRefreshedOnNewStart:true,realAccountTested:false,errors},null,2));
    console.log('Offline integration passed: no-PIN connection, inbox watcher, missing-only refresh, bounded retries, cancellation.');
  }catch(error){
    if(worker)try{fs.writeFileSync(path.join(OUT,'failed-state.json'),JSON.stringify(await worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current),null,2));}catch(_){}
    throw error;
  }finally{
    if(context)await context.close();
    if(child){child.stdin.end();child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}
    fs.rmSync(profile,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});

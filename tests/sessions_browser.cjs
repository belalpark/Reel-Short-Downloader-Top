'use strict';
// Real extension/session bridge/scheduler. Players and external download results are synthetic.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const ROOT=path.resolve(__dirname,'..'),OUT=path.join(ROOT,'test-results'),sid=n=>n.toString(16).padStart(24,'0');
const page=(n,ep)=>`https://www.reelshort.com/ar/episodes/episode-${ep}-trigger-${sid(n)}`;
const stream=(id,ep)=>`https://v-mps.crazymaplestudios.com/sessions/${id}/${ep}/index.m3u8?fixture=1`;
const hlsFixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/hls.json'),'utf8'));
const hlsPlayer=fs.readFileSync(require.resolve('hls.js/dist/hls.min.js'),'utf8');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,timeout=210000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await delay(150);}throw Error('Timed out: '+label);}
function html(url){
 const u=new URL(url),id=u.pathname.match(/([a-f\d]{24})(?:-|$)/)?.[1],ep=Number(u.pathname.match(/episode-(\d+)/)?.[1]);
 if(!id)return '<!doctype html><title>Trigger catalog</title>'+[1,2,3].map(n=>`<p><a href="${page(n,1)}">Trigger ${n}</a></p>`).join('');
 const n=parseInt(id,16),metadata={props:{pageProps:{data:{book_id:id,book_title:'Trigger '+n,serial_number:ep,chapter_count:3,video_url:`https://v-mps.crazymaplestudios.com/${id}/${ep}.mp4`}}}};
 const video=fs.readFileSync(path.join(__dirname,'fixture.webm.b64'),'utf8').trim();
 const player=n===1&&ep===2?`<video id="movie" playsinline></video><script src="/fixture/hls.min.js"></script><script>const h=new Hls({enableWorker:false,maxBufferLength:20});h.loadSource(${JSON.stringify(stream(id,ep))});h.attachMedia(document.querySelector('video'));h.on(Hls.Events.MANIFEST_PARSED,()=>document.querySelector('video').play().catch(()=>{}));</script>`:`<video loop playsinline src="data:video/webm;base64,${video}"></video>`;
 return `<!doctype html><title>Episode ${ep} - Trigger ${n} | ReelShort</title><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(metadata)}</script>${player}
 ${[1,2,3].map(k=>`<button aria-label="Episode ${k}" onclick="history.pushState({},'',${JSON.stringify(page(n,k)).replaceAll('"','&quot;')})">${k}</button>`).join(' ')}
 <!-- Deliberately no next-episode href: the collector must reload after a normal SPA button click. -->`;
}
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});const profile=fs.mkdtempSync(path.join(OUT,'sessions-profile-'));
 let child,context,worker,driver,lastStatus,opening=Promise.resolve(),openError;
 const queued=[],results=[],visits=[],opens=[];
 try{
  child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),['-m','scripts.session_fixture'],{cwd:ROOT,stdio:['pipe','pipe','inherit']});
  const lines=readline.createInterface({input:child.stdout});
  const first=await Promise.race([once(lines,'line'),delay(10000).then(()=>{throw Error('Session fixture startup timeout');})]);
  const settings=JSON.parse(first[0]);
  lines.on('line',line=>{const value=JSON.parse(line);if(value.status)lastStatus=value.status;if(value.queued)queued.push(...value.queued);if(value.download_result)results.push(value.download_result);
   if(value.open_request){opens.push(value.open_request);opening=opening.then(async()=>{if(!driver||driver.isClosed())driver=await context.newPage();await driver.goto(value.open_request);}).catch(e=>{openError=e;});}});
  const executablePath=process.env.HLS_BROWSER_EXECUTABLE;
  context=await chromium.launchPersistentContext(profile,{...(executablePath?{executablePath}:{channel:'chromium'}),headless:true,args:[`--disable-extensions-except=${settings.extension_path}`,`--load-extension=${settings.extension_path}`]});
  const setup=await context.newPage();await setup.goto('chrome://extensions/');
  const info=await setup.evaluate(()=>new Promise(resolve=>chrome.developerPrivate.getExtensionsInfo({includeDisabled:true,includeTerminated:true},resolve)));
  const ext=info.find(x=>x.name.startsWith('Reel Short Downloader Top'));assert.ok(ext);
  const popup=await context.newPage();await popup.goto(`chrome-extension://${ext.id}/popup.html`);
  worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:60000});await setup.close();await popup.close();
  await context.route('https://www.reelshort.com/**',async route=>{if(new URL(route.request().url()).pathname==='/fixture/hls.min.js')return route.fulfill({contentType:'application/javascript',body:hlsPlayer});if(route.request().isNavigationRequest())visits.push(route.request().url());return route.fulfill({contentType:'text/html',body:html(route.request().url())});});
  await context.route('https://v-mps.crazymaplestudios.com/**',async route=>{
   const name=new URL(route.request().url()).pathname.split('/').pop(),headers={'Access-Control-Allow-Origin':'*'};
   if(name==='index.m3u8')return route.fulfill({headers,contentType:'application/vnd.apple.mpegurl',body:hlsFixture.playlist});
   if(hlsFixture.segments[name])return route.fulfill({headers,contentType:'video/mp2t',body:Buffer.from(hlsFixture.segments[name],'base64')});
   return route.fulfill({status:403,headers,body:'Expired HTML media fixture'});
  });
  const send=v=>child.stdin.write(JSON.stringify(v)+'\n');
  async function status(){if(openError)throw openError;lastStatus=null;send({command:'status'});await until(()=>lastStatus,'backend status',5000);return lastStatus;}
  send({command:'collect'});
  send({command:'download'});
  await until(async()=>{const s=await status();return s.jobs.length===3;},'three parallel series leases');
  await until(async()=>{const s=await status();return s.series.every(b=>b.done===3)&&!s.downloading;},'automatic failure deferral and capture/download triggers without relinking');
  assert.equal(queued.length,10,'9 distinct files and one failed attempt');
  assert.ok(queued.filter(q=>q.series_id===sid(1)&&q.episode===2).every(q=>q.media_url===stream(sid(1),2)),'verified playback HLS must replace the stale HTML URL');
  const failed=results.find(r=>!r.success);assert.equal(failed.series_id,sid(1));assert.equal(failed.episode,2);
  const failedIndex=results.findIndex(r=>!r.success),repairedIndex=results.findIndex((r,i)=>i>failedIndex&&r.series_id===sid(1)&&r.episode===2&&r.success);
  assert.ok(repairedIndex>failedIndex+1,'other episodes must complete before the deferred retry');
  assert.ok(results.slice(failedIndex+1,repairedIndex).some(r=>r.series_id!==sid(1)),'another series must progress while the first source is deferred');
  for(let n=1;n<=3;n++)for(let ep=1;ep<=3;ep++)assert.equal(results.filter(r=>r.series_id===sid(n)&&r.episode===ep&&r.success).length,1);
  const latest=await status();assert.equal(latest.stats.maximum_concurrent_sessions,3);
  assert.equal(new Set(Object.values(latest.stats.source_sessions)).size,3);
  for(let n=1;n<=3;n++)for(let ep=2;ep<=3;ep++)assert.ok(visits.includes(page(n,ep)),'SPA selection must use a fresh document for '+page(n,ep));
  const maps=await worker.evaluate(async()=>(await chrome.storage.session.get('reelDesktopSessions')).reelDesktopSessions);
  assert.equal(new Set(Object.values(maps).map(s=>s.tabId)).size,3);
  const managed=[];
  for(const tab of context.pages())if(tab.url().startsWith('https://www.reelshort.com/')){
   const channel=await tab.evaluate(()=>sessionStorage.getItem('__reelDesktopSession'));
   if(channel){managed.push(channel);assert.equal(await tab.locator('video').evaluate(v=>v.muted),true,'managed background tabs must be muted for automatic playback');}
  }
  assert.equal(new Set(managed).size,3);
  const before=queued.length,beforeOpen=opens.length;
  for(const tab of context.pages())if(tab.url().startsWith('https://www.reelshort.com/'))await tab.close();
  send({command:'download'});await until(async()=>!(await status()).downloading,'completed-only resume without reopening Edge');
  assert.equal(queued.length,before);assert.equal(opens.length,beforeOpen);
  fs.writeFileSync(path.join(OUT,'sessions-browser.json'),JSON.stringify({series:3,episodesEach:3,independentInventoryAndDownload:true,maximumConcurrentSeries:3,uniqueOwnedTabs:3,sessionIdsBoundToCorrectSeries:true,managedTabsMutedForAutoplay:true,freshDocumentAfterSpaSelection:true,verifiedPlaybackPreferredToStaleHtml:true,failedEpisodeDeferredWithoutRelink:true,otherSeriesProgressedBeforeRetry:true,successfulFilesDownloadedOnce:9,totalAttempts:10,completedResumeMadeNoRequests:true,realDownloadTool:false,realAccountTested:false},null,2));
  console.log('Sessions passed: 3 isolated tabs, capture/download triggers, defer HTTP 403, progress other series, automatic fresh retry.');
 }catch(error){if(worker)try{fs.writeFileSync(path.join(OUT,'sessions-failed-state.json'),JSON.stringify(await worker.evaluate(async()=>({local:await chrome.storage.local.get(null),session:await chrome.storage.session.get(null)})),null,2));}catch(_){}throw error;}
 finally{if(context)await context.close();if(child){child.stdin.end();child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}fs.rmSync(profile,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
// One complete series at a time in a visible tab. Real extension/bridge/scheduler; synthetic player and CDN.
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
 fs.mkdirSync(OUT,{recursive:true});const profile=fs.mkdtempSync(path.join(OUT,'series-profile-'));
 let child,context,worker,driver,lastStatus,opening=Promise.resolve(),openError;
 const queued=[],results=[],visits=[],opens=[];
 try{
  child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),['-m','scripts.session_fixture'],{cwd:ROOT,stdio:['pipe','pipe','inherit']});
  const lines=readline.createInterface({input:child.stdout});
  const first=await Promise.race([once(lines,'line'),delay(10000).then(()=>{throw Error('Session fixture startup timeout');})]);
  const settings=JSON.parse(first[0]);
  lines.on('line',line=>{const value=JSON.parse(line);if(value.status)lastStatus=value.status;if(value.queued)queued.push(...value.queued);if(value.download_result)results.push(value.download_result);
   // The fixture bridge never reports "connected", so it asks to open Edge. In the app the
   // extension is connected when 3 is pressed; the user's own tab is the only one here.
   if(value.open_request)opens.push(value.open_request);});
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
  // The user has the series open in their own tab and presses 3.
  const own=await context.newPage();await own.goto(page(1,1));await own.bringToFront();
  await delay(1500);
  const ownTabs=await worker.evaluate(async url=>(await chrome.tabs.query({})).filter(t=>t.url===url).map(t=>t.id),page(1,1));
  assert.equal(ownTabs.length,1);const ownId=ownTabs[0];
  const pagesBefore=context.pages().filter(t=>t.url().startsWith('https://www.reelshort.com/')).length;
  send({command:'series',sid:sid(1),page:page(1,1)});
  await until(async()=>{const s=await status();return s.events.some(e=>e[0]==='series_complete'&&e[1]===sid(1));},'series 1 completed in the visible tab');
  let s=await status();
  const opened=s.series.find(b=>b.id===sid(1));
  assert.equal(opened.done,3,'every episode of the open series is saved');
  assert.deepEqual(s.events.find(e=>e[0]==='series_complete'),['series_complete',sid(1),'single']);
  assert.equal(s.current,null);assert.equal(s.downloading,false);
  assert.ok(queued.every(q=>q.series_id===sid(1)),'no other series is downloaded before the open one completes');
  const maps=await worker.evaluate(async()=>(await chrome.storage.session.get('reelDesktopSessions')).reelDesktopSessions);
  const slots=Object.values(maps);
  assert.equal(slots.length,1,'one capture tab');
  assert.equal(slots[0].tabId,ownId,'the user\'s own visible tab is reused, not a hidden new tab');
  assert.equal(slots[0].created,false);

  assert.equal(context.pages().filter(t=>t.url().startsWith('https://www.reelshort.com/')).length,pagesBefore,'no extra tab was opened');
  assert.equal(await own.locator('video').evaluate(v=>v.muted),true,'the capture tab plays muted');
  for(let ep=2;ep<=3;ep++)assert.ok(visits.includes(page(1,ep)),'episode '+ep+' was opened for capture');
  // "Continue with the other series": each one completely before the next.
  const mark=queued.length;
  send({command:'sequence',exclude:[sid(1)]});
  await until(async()=>{const t=await status();return t.events.some(e=>e[0]==='library_complete');},'remaining series completed in order');
  s=await status();
  for(const n of [2,3])assert.equal(s.series.find(b=>b.id===sid(n)).done,3);
  const order=queued.slice(mark).map(q=>q.series_id);
  assert.ok(order.length>=6);
  assert.equal(order.lastIndexOf(sid(2))<order.indexOf(sid(3)),true,'series 3 starts only after series 2 is complete');
  assert.deepEqual(s.events.filter(e=>e[0]==='series_complete').map(e=>[e[1],e[2]]),[[sid(1),'single'],[sid(2),'library'],[sid(3),'library']]);
  fs.writeFileSync(path.join(OUT,'series-browser.json'),JSON.stringify({openSeriesCompletedFirst:true,ownVisibleTabReused:true,extraTabsOpened:0,captureTabMuted:true,otherSeriesWaited:true,sequenceOneSeriesAtATime:true,completionEvents:s.events,realAccountTested:false},null,2));
  console.log('Series passed: open series in the user\'s visible tab, complete before others, then one complete series after another.');
 }catch(error){if(worker)try{fs.writeFileSync(path.join(OUT,'series-failed-state.json'),JSON.stringify(await worker.evaluate(async()=>({local:await chrome.storage.local.get(null),session:await chrome.storage.session.get(null)})),null,2));}catch(_){}throw error;}
 finally{if(context)await context.close();if(child){child.stdin.end();child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}fs.rmSync(profile,{recursive:true,force:true});}
})();

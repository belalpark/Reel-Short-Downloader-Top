'use strict';
// Reproduce the actual failure: first 8 HTML URLs, then blob/MSE playback with
// HLS only, stale SPA metadata and a full Resource Timing buffer.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const {spawn}=require('node:child_process');const {once}=require('node:events');const readline=require('node:readline');
const ROOT=path.resolve(__dirname,'..'),OUT=path.join(ROOT,'test-results'),ID='123456789abcdef012345678';
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/hls.json'),'utf8'));
const player=fs.readFileSync(require.resolve('hls.js/dist/hls.min.js'),'utf8');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const page=n=>`https://www.reelshort.com/ar/episodes/episode-${n}-fixture-${ID}`;
const stream=n=>`https://v-mps.crazymaplestudios.com/vod-100/${n.toString(16).padStart(32,'0')}/index.m3u8?token=fixture-${n}`;
async function until(fn,label,timeout=150000){const start=Date.now();while(Date.now()-start<timeout){if(await fn())return;await delay(100);}throw Error('Timeout: '+label);}
function html(n){
 const data={props:{pageProps:{data:{book_id:ID,book_title:'HLS regression fixture',serial_number:n,chapter_count:12,paid_start:9,video_url:n<=8?`https://cdn.example.org/${n}.mp4`:''}}}};
 return `<!doctype html><title>Episode ${n} - HLS fixture | ReelShort</title><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(data)}</script>
 <video id="movie" muted playsinline></video><h1 id="title">Episode ${n}</h1>
 ${Array.from({length:12},(_,i)=>`<button aria-label="الحلقة ${i+1}" onclick="render(${i+1})">${i+1}</button><a href="${page(i+1)}">episode ${i+1}</a>`).join(' ')}
 <script src="/fixture/hls.min.js"></script><script>
 let currentPlayer;window.fixtureDiagnostics={events:[],supported:Hls.isSupported(),mseH264:MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"'),mseAAC:MediaSource.isTypeSupported('audio/mp4; codecs="mp4a.40.2"')};
 const link=n=>'https://v-mps.crazymaplestudios.com/vod-100/'+n.toString(16).padStart(32,'0')+'/index.m3u8?token=fixture-'+n;
 function render(n){
  if(currentPlayer)currentPlayer.destroy();
  history.pushState({},'',${JSON.stringify(page(1))}.replace('episode-1-','episode-'+n+'-'));
  document.title='Episode '+n+' - HLS fixture | ReelShort';document.querySelector('#title').textContent='Episode '+n;
  const video=document.querySelector('video');
  currentPlayer=new Hls({enableWorker:false,maxBufferLength:20});
  currentPlayer.on(Hls.Events.ERROR,(_,data)=>window.fixtureDiagnostics.events.push({type:data.type,details:data.details,fatal:data.fatal,reason:data.reason,error:data.error?.message}));
  currentPlayer.loadSource(link(n));currentPlayer.attachMedia(video);
  currentPlayer.on(Hls.Events.MANIFEST_PARSED,()=>video.play().catch(error=>window.fixtureDiagnostics.events.push({playError:error.message})));
  if(n===8)fetch(link(9)); // Unverified prefetch must not reserve episode 9 for episode 8.
 }
 (async()=>{await Promise.all(Array.from({length:270},(_,i)=>fetch('/fixture/fill?i='+i)));render(${n});})();
 </script>`;
}
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});const profile=fs.mkdtempSync(path.join(OUT,'hls-profile-'));
 let context,child,worker,reel;const queued=[],errors=[];
 try{
  child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),['-m','scripts.bridge_fixture'],{cwd:ROOT,stdio:['pipe','pipe','inherit']});
  const lines=readline.createInterface({input:child.stdout});
  const first=await Promise.race([once(lines,'line'),once(child,'exit').then(()=>{throw Error('Bridge exited');}),delay(10000).then(()=>{throw Error('Bridge startup timeout');})]);
  const settings=JSON.parse(first[0]);lines.on('line',line=>{const value=JSON.parse(line);if(value.queued)queued.push(...value.queued);});
  context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,args:[`--disable-extensions-except=${settings.extension_path}`,`--load-extension=${settings.extension_path}`]});
  context.on('weberror',event=>errors.push(event.error().message));
  const setup=await context.newPage();await setup.goto('chrome://extensions/');
  const extensions=await setup.evaluate(()=>new Promise(resolve=>chrome.developerPrivate.getExtensionsInfo({includeDisabled:true,includeTerminated:true},resolve)));
  const ext=extensions.find(x=>x.name.startsWith('Reel Short Downloader Top'));assert.ok(ext);
  const popup=await context.newPage();await popup.goto(`chrome-extension://${ext.id}/popup.html`);
  worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:60000});
  await setup.close();await popup.close();
  await context.route('https://www.reelshort.com/**',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname==='/fixture/hls.min.js')return route.fulfill({contentType:'application/javascript',body:player});
   if(u.pathname==='/fixture/fill')return route.fulfill({contentType:'text/plain',body:'filled'});
   const n=Number(u.pathname.match(/episode-(\d+)/)?.[1]||1);return route.fulfill({contentType:'text/html',body:html(n)});
  });
  await context.route('https://v-mps.crazymaplestudios.com/**',async route=>{
   const u=new URL(route.request().url());const filename=u.pathname.split('/').pop();
   const headers={'Access-Control-Allow-Origin':'*','Cache-Control':'public, max-age=3600'};
   if(filename==='index.m3u8')return route.fulfill({headers,contentType:'application/vnd.apple.mpegurl',body:fixture.playlist});
   if(fixture.segments[filename])return route.fulfill({headers,contentType:'video/mp2t',body:Buffer.from(fixture.segments[filename],'base64')});
   return route.fulfill({status:404,headers,body:'not a fixture'});
  });
  reel=await context.newPage();await reel.goto(page(1));
  await reel.waitForFunction(()=>document.querySelector('video')?.currentTime>0.2,null,{timeout:30000});
  assert.equal(await reel.locator('video').evaluate(v=>v.currentSrc.startsWith('blob:')),true);
  const state=()=>worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current);
  child.stdin.write(JSON.stringify({command:'start',series_id:ID})+'\n');
  await until(async()=>(await state())?.finished,'all twelve episodes including HLS-only sources',180000);
  const result=await state();
  assert.equal(result.total,12);assert.equal(result.autoPass,1);
  assert.equal(Object.values(result.rows).filter(r=>r.mediaUrl).length,12);
  for(let n=9;n<=12;n++){
   assert.equal(result.rows[String(n)].sourceType,'playback_hls');
   assert.equal(result.rows[String(n)].mediaUrl,stream(n));
   assert.ok(result.rows[String(n)].hlsMatchedSegments>=2);
  }
  assert.ok(result.rows['8'].hlsCandidates.includes(stream(9)),'prefetched episode 9 must be observed before its own playback');
  await until(()=>queued.length===12,'all twelve inserted into automatic download queue',10000);
  assert.deepEqual(queued,Array.from({length:12},(_,i)=>i+1));
  const buffer=await reel.evaluate(()=>performance.getEntriesByType('resource').length);
  const late=await reel.evaluate(()=>performance.getEntriesByType('resource').some(e=>/00000000000000000000000000000009\/index.m3u8/.test(e.name)));
  assert.ok(buffer>=250);assert.equal(late,false,'HLS capture must work beyond the full browser timeline');
  fs.writeFileSync(path.join(OUT,'hls-browser.json'),JSON.stringify({hlsJsVersion:require('hls.js/package.json').version,initialHtmlSources:8,hlsOnlySources:4,totalSources:12,queuedEpisodes:queued,blobPlayback:true,staleSpaMetadata:true,normalTimelineBufferFull:true,lateHlsAbsentFromTimeline:true,unverifiedPrefetchDidNotClaimNextEpisode:true,verifiedDurationAndSegments:true,passes:result.autoPass,realAccountDownloadTested:false},null,2));
  console.log('HLS regression passed: 8 HTML sources plus 4 blob/HLS sources, full buffer, stale SPA metadata.');
 }catch(error){
  if(worker)try{fs.writeFileSync(path.join(OUT,'hls-failed-state.json'),JSON.stringify(await worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current)||null,null,2));}catch(_){}
  if(reel)try{const diagnostics=await reel.evaluate(()=>({fixture:window.fixtureDiagnostics,video:[...document.querySelectorAll('video')].map(v=>({src:v.currentSrc,paused:v.paused,readyState:v.readyState,duration:v.duration,currentTime:v.currentTime,error:v.error?.message})),resources:performance.getEntriesByType('resource').map(e=>e.name).slice(-20)}));fs.writeFileSync(path.join(OUT,'hls-player-failure.json'),JSON.stringify({errors,...diagnostics},null,2));console.error(JSON.stringify({errors,...diagnostics}));}catch(_){}
  throw error;
 }finally{
  if(context)await context.close();if(child){child.stdin.end();child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}
  fs.rmSync(profile,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});

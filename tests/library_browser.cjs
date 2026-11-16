'use strict';
// Exercise the actual extension, authenticated bridge, SQLite library and queue
// coordinator. The rendered catalog/player and external CDN/downloader are fixtures.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const ROOT=path.resolve(__dirname,'..'),OUT=path.join(ROOT,'test-results');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const sid=n=>n.toString(16).padStart(24,'0');
const episode=id=>`https://www.reelshort.com/ar/episodes/episode-1-library-${id}`;
const movie=id=>`https://www.reelshort.com/ar/movie/library-${id}`;
async function until(fn,label,timeout=150000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await delay(100);}throw Error('Timed out: '+label);}
function html(url){
 const path=new URL(url).pathname,id=path.match(/([a-f0-9]{24})(?:-|$)/)?.[1];
 if(!id){
  const shelf=path.includes('/shelf/');
  return '<!doctype html><title>Library catalog fixture</title>'+Array.from({length:shelf?30:60},(_,i)=>{
   const n=i+1,id=sid(n),href=n===59?movie(id):episode(id);
   return `<p><a href="${href}?playTime=1"><img alt="Library fixture ${n}"></a><a href="${href}">duplicate</a></p>`;
  }).join('')+(shelf?'':'<a href="https://www.reelshort.com/ar/shelf/library-fixture">More</a>');
 }
 if(path.includes('/movie/'))return `<!doctype html><title>Library landing</title><a href="${episode(id)}">Watch first episode</a>`;
 const metadata={props:{pageProps:{data:{book_id:id,book_title:'Library fixture '+parseInt(id,16),serial_number:1,chapter_count:1,video_url:`https://v-mps.crazymaplestudios.com/library-${id}.mp4`}}}};
 const video=fs.readFileSync(path.join(__dirname,'fixture.webm.b64'),'utf8').trim();
 return `<!doctype html><title>Episode 1 - Library fixture | ReelShort</title><a href="${movie(id)}">Series</a><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(metadata)}</script><video muted loop playsinline src="data:video/webm;base64,${video}"></video>`;
}
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});const profile=fs.mkdtempSync(path.join(OUT,'library-profile-'));
 let child,context,reel,worker,lastStatus,openChain=Promise.resolve(),openError;
 const requests=[],visits=[],queued=[];
 try{
  child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),['-m','scripts.library_fixture'],{cwd:ROOT,stdio:['pipe','pipe','inherit']});
  const lines=readline.createInterface({input:child.stdout});
  const first=await Promise.race([once(lines,'line'),delay(10000).then(()=>{throw Error('Library fixture startup timeout');})]);
  const settings=JSON.parse(first[0]);
  lines.on('line',line=>{
   const value=JSON.parse(line);
   if(value.status)lastStatus=value.status;
   if(value.queued)queued.push(...value.queued);
   if(value.open_request){requests.push(value.open_request);openChain=openChain.then(async()=>{if(!reel||reel.isClosed())reel=await context.newPage();await reel.goto(value.open_request);}).catch(e=>{openError=e;});}
  });
  context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,args:[`--disable-extensions-except=${settings.extension_path}`,`--load-extension=${settings.extension_path}`]});
  const setup=await context.newPage();await setup.goto('chrome://extensions/');
  const extensions=await setup.evaluate(()=>new Promise(resolve=>chrome.developerPrivate.getExtensionsInfo({includeDisabled:true,includeTerminated:true},resolve)));
  const ext=extensions.find(x=>x.name.startsWith('Reel Short Downloader Top'));assert.ok(ext);
  const popup=await context.newPage();await popup.goto(`chrome-extension://${ext.id}/popup.html`);
  worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:60000});
  await setup.close();await popup.close();
  await context.route('https://www.reelshort.com/**',async route=>{if(route.request().isNavigationRequest())visits.push(route.request().url());await route.fulfill({contentType:'text/html',body:html(route.request().url())});});
  const send=value=>child.stdin.write(JSON.stringify(value)+'\n');
  async function status(){if(openError)throw openError;lastStatus=null;send({command:'status'});await until(()=>lastStatus,'status',5000);return lastStatus;}
  send({command:'collect'});
  await until(async()=>{const s=await status();return s.series.length===60&&s.series.every(x=>x.total===1&&x.ready===1)&&!s.collecting&&!s.capture;},'collect only 2 missing series from a 60-series catalog');
  const collectedVisits=visits.filter(u=>u.includes('/episodes/'));
  assert.deepEqual([...new Set(collectedVisits.map(u=>u.match(/([a-f0-9]{24})(?:-|$)/)[1]))].sort(),[sid(59),sid(60)]);
  assert.ok(visits.some(u=>u.includes('/shelf/')),'normal shelf links must be visited');
  assert.ok(visits.some(u=>u.includes('/movie/')),'normal landing page must reach the observed episode link');
  await reel.close();await delay(1500);
  const beforeOffline=requests.length;
  send({command:'download'});
  await until(async()=>{const s=await status();return s.series.every(x=>x.done===1)&&!s.downloading;},'download all 60 library items with the source tab closed');
  assert.equal(requests.length,beforeOffline,'valid links must not reopen the browser');
  assert.equal(queued.length,60);assert.equal(new Set(queued).size,60);
  const beforeRefresh=visits.length;
  send({command:'refresh_one',series_id:sid(59)});
  await until(async()=>{const s=await status();return queued.length===61&&s.series.every(x=>x.done===1)&&!s.downloading;},'renew one failed source and download only its missing file');
  assert.equal(queued[60],sid(59));
  const refreshed=visits.slice(beforeRefresh).filter(u=>u.includes('/episodes/'));
  assert.ok(refreshed.length>0);assert.ok(refreshed.every(u=>u.includes(sid(59))));
  assert.equal(requests.length,beforeOffline+1);
  fs.writeFileSync(path.join(OUT,'library-browser.json'),JSON.stringify({syntheticSeries:60,seededSourcesSkipped:58,newSeriesCollected:2,duplicateCatalogCardsDeduplicated:true,renderedShelfCrawled:true,landingPageUsesObservedEpisodeLink:true,offlineQueueItems:60,noBrowserReopenedForValidLinks:true,oneFailedSourceRenewed:true,other59NotReopened:true,queuedItems:61,realDownloadTool:false,realAccountTested:false},null,2));
  console.log('Library passed: 60 identities, skip 58 collected, collect 2, offline queue 60, renew only 1.');
 }catch(error){if(worker)try{fs.writeFileSync(path.join(OUT,'library-failed-state.json'),JSON.stringify(await worker.evaluate(async()=>await chrome.storage.local.get(['dramaScannerV5Current','reelLibraryCatalogJob']))||null,null,2));}catch(_){}throw error;}
 finally{if(context)await context.close();if(child){child.stdin.end();child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}fs.rmSync(profile,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});

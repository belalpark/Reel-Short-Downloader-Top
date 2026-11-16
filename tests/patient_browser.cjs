'use strict';
// Real extension, receiver and watcher; controlled download outcomes and synthetic video.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const ROOT=path.resolve(__dirname,'..'),OUT=path.join(ROOT,'test-results'),ID='b'.repeat(24);
const page=n=>`https://www.reelshort.com/episodes/episode-${n}-patient-${ID}`;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,timeout=180000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await delay(100);}throw Error('Timed out: '+label);}
let allow48=false;
const visits=[],queued=[],results=[];let restarts=0;
function html(n){
 const metadata={props:{pageProps:{data:{book_id:ID,book_title:'Patient fixture',serial_number:n,chapter_count:48,paid_start:47,video_url:n===48&&!allow48?'':`https://cdn.example.org/${n}.webm`}}}};
 const video=fs.readFileSync(path.join(__dirname,'fixture.webm.b64'),'utf8').trim();
 return `<!doctype html><title>Episode ${n} - Patient fixture | ReelShort</title><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(metadata)}</script>
 <video muted loop playsinline src="data:video/webm;base64,${video}"></video>
 ${[46,47,48].map(ep=>`<button onclick="choose(${ep})">${ep}</button>`).join(' ')}
 ${n>=47?[47,48].map(ep=>`<a href="${page(ep)}">episode ${ep}</a>`).join(' '):''}
 <script>function choose(n){if(n===47&&location.href.includes('episode-46-')){const tries=Number(localStorage.getItem('open47')||0)+1;localStorage.setItem('open47',tries);if(tries<=2)return;}location.href=${JSON.stringify(page(1))}.replace('episode-1-','episode-'+n+'-');}</script>`;
}
(async()=>{
 fs.mkdirSync(OUT,{recursive:true});const profile=fs.mkdtempSync(path.join(OUT,'patient-profile-'));
 let context,child,worker;
 try{
  child=spawn(process.env.PYTHON||(process.platform==='win32'?'python':'python3'),['-m','scripts.bridge_fixture'],{cwd:ROOT,stdio:['pipe','pipe','inherit']});
  const lines=readline.createInterface({input:child.stdout});
  const first=await Promise.race([once(lines,'line'),delay(10000).then(()=>{throw Error('Bridge startup timeout');})]);
  const settings=JSON.parse(first[0]);lines.on('line',line=>{const value=JSON.parse(line);if(value.queued)queued.push(...value.queued);if(value.download_result)results.push(value.download_result);if(value.bridge_restarted)restarts++;});
  context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,args:[`--disable-extensions-except=${settings.extension_path}`,`--load-extension=${settings.extension_path}`]});
  const setup=await context.newPage();await setup.goto('chrome://extensions/');
  const extensions=await setup.evaluate(()=>new Promise(resolve=>chrome.developerPrivate.getExtensionsInfo({includeDisabled:true,includeTerminated:true},resolve)));
  const ext=extensions.find(x=>x.name.startsWith('Reel Short Downloader Top'));assert.ok(ext);
  const popup=await context.newPage();await popup.goto(`chrome-extension://${ext.id}/popup.html`);
  worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:60000});await setup.close();await popup.close();
  await context.route('https://www.reelshort.com/**',async route=>{const n=Number(new URL(route.request().url()).pathname.match(/episode-(\d+)/)?.[1]||46);if(route.request().isNavigationRequest())visits.push(n);return route.fulfill({contentType:'text/html',body:html(n)});});
  const reel=await context.newPage();await reel.goto(page(46));
  const state=()=>worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current);
  const send=value=>child.stdin.write(JSON.stringify(value)+'\n');
  send({command:'start',series_id:ID,patient:true,seed_completed:Array.from({length:46},(_,i)=>i+1),download_failures:{47:1}});
  await until(async()=>{const s=await state();return s?.current===48&&s.retryAt&&s.rows['47']?.downloaded;},'retry 47 until downloaded, then wait on missing 48');
  const before=await state();
  assert.equal(before.finished,false);assert.equal(before.running,true);
  assert.equal(before.events.filter(e=>e.episode===47&&e.result==='manual_open_required').length,2);
  assert.deepEqual(queued,[47,47]);assert.equal(results[0].success,false);assert.equal(results[1].success,true);
  assert.notEqual(results[0].capture_id,results[1].capture_id);
  send({command:'stop',series_id:ID});await until(async()=>!(await state())?.running,'stop retry');
  const count=visits.length;await delay(3500);assert.equal(visits.length,count,'STOP must cancel the scheduled refresh');
  const visits47=visits.filter(n=>n===47).length;
  send({command:'restart'});await until(()=>restarts===1,'fresh receiver and watcher');
  allow48=true;send({command:'start',series_id:ID,patient:true});
  await until(async()=>{const s=await state();return s?.finished&&s.patient;},'resume only remaining episode 48');
  const final=await state();
  assert.equal(Object.values(final.rows).filter(r=>r.downloaded).length,48);
  assert.equal(visits.filter(n=>n===47).length,visits47,'resume must not reopen completed episode 47');
  assert.deepEqual(queued,[47,47,48]);
  assert.ok(!queued.some(n=>n<=46));
  fs.writeFileSync(path.join(OUT,'patient-browser.json'),JSON.stringify({completedEpisodesSkipped:46,navigationFailuresBeforeSuccess:2,failedDownloadRetriedOnSameEpisode:true,freshCaptureOnSameUrl:true,queuedEpisodes:queued,stopPreventsScheduledRefresh:true,backendRestartRetainsCompletedFiles:true,resumeOnlyEpisode48:true,finalCompleted:48,realDownloadTool:false,realAccountTested:false},null,2));
  console.log('Patient scan passed: skip 46 saved, retry 47, stop on 48, reopen and scan 48 only.');
 }catch(error){if(worker)try{fs.writeFileSync(path.join(OUT,'patient-failed-state.json'),JSON.stringify(await worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current)||null,null,2));}catch(_){}throw error;}
 finally{if(context)await context.close();if(child){child.stdin.end();child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}fs.rmSync(profile,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});

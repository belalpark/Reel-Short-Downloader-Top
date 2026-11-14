'use strict';
// Uses only an offline ReelShort-shaped fixture, never a real account or remote video.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const readline = require('node:readline');
const ROOT=path.resolve(__dirname,'..');
const OUT=path.join(ROOT,'test-results');
const ID='0123456789abcdef01234567';
const pageURL=n=>`https://www.reelshort.com/episodes/episode-${n}-test-${ID}`;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function waitFor(fn,timeout=45000) {
  const started=Date.now();
  while(Date.now()-started<timeout) {if(await fn())return;await delay(150);}
  throw Error('Timed out waiting for browser assertion');
}
function fixture(n) {
  const metadata={props:{pageProps:{data:{book_id:ID,book_title:'Offline Test Series',serial_number:n,chapter_count:3,paid_start:3,video_url:n<=2?`https://cdn.example.org/${n}.mp4`:''}}}};
  const movie=fs.readFileSync(path.join(__dirname,'fixture.webm.b64'),'utf8').trim();
  return `<!doctype html><html><head><title>Offline Test Series | ReelShort</title></head><body>
  <script id="__NEXT_DATA__" type="application/json">${JSON.stringify(metadata)}</script>
  <h1>Offline episode ${n}</h1><video muted autoplay loop playsinline src="data:video/webm;base64,${movie}"></video>
  ${[1,2,3].map(i=>`<a href="${pageURL(i)}">Episode ${i}</a>`).join(' ')}
  </body></html>`;
}
(async()=>{
  fs.mkdirSync(OUT,{recursive:true});
  const profile=fs.mkdtempSync(path.join(OUT,'profile-'));
  let context, child;
  try {
    child=spawn(process.env.PYTHON || (process.platform==='win32'?'python':'python3'),['-m','scripts.bridge_fixture'],{cwd:ROOT,stdio:['ignore','pipe','inherit']});
    const lines=readline.createInterface({input:child.stdout});
    const first=await Promise.race([once(lines,'line'),once(child,'exit').then(([code])=>{throw Error('Bridge fixture exited: '+code);}),delay(10000).then(()=>{throw Error('Bridge fixture startup timed out');})]);
    const {code}=JSON.parse(first[0]);
    const extension=path.join(ROOT,'extension');
    context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
    const worker=context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    const extId=new URL(worker.url()).host;
    const errors=[];
    context.on('weberror',err=>errors.push(err.error().message));
    await context.route('https://www.reelshort.com/**', async route=>{
      const n=Number(new URL(route.request().url()).pathname.match(/episode-(\d+)/)?.[1]||1);
      await route.fulfill({contentType:'text/html',body:fixture(n)});
    });
    const reel=await context.newPage();
    await reel.goto(pageURL(1));
    await reel.waitForFunction(()=>document.querySelector('video').currentTime>0.2);
    const popup=await context.newPage();
    await popup.goto(`chrome-extension://${extId}/popup.html`);
    await popup.locator('#desktopCode').fill(code);
    await popup.locator('#desktopPair').click();
    await popup.locator('#desktopStatus').filter({hasText:'تم الربط'}).waitFor();
    // Chromium is deliberately not accepted as Edge by the production readiness gate.
    assert.match(await popup.locator('#desktopStatus').innerText(),/داخل Microsoft Edge/);
    await reel.bringToFront();
    await popup.evaluate(()=>document.querySelector('#start').click());
    await waitFor(async()=>worker.evaluate(async()=>{
      const s=(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current;
      return s?.finished;
    }));
    const scan=await worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current);
    assert.equal(scan.total,3);
    assert.equal(Object.values(scan.rows).filter(r=>r.playbackStatus==='playback_verified').length,3);
    assert.equal(Object.values(scan.rows).filter(r=>r.mediaUrl).length,2);
    const csv='SeriesID,SeriesTitle,Episode,TotalEpisodes,PageURL,MediaURL\r\n'+Array.from({length:45},(_,i)=>`${ID},Migration,${i+1},45,${pageURL(i+1)},${i<8?'https://cdn.example.org/'+(i+1)+'.mp4':''}`).join('\r\n');
    await popup.locator('#priorIndex').setInputFiles({name:'previous.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
    await reel.bringToFront();
    await popup.evaluate(()=>document.querySelector('#import').click());
    await waitFor(async()=>worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current?.total===45));
    const imported=await worker.evaluate(async()=>(await chrome.storage.local.get('dramaScannerV5Current')).dramaScannerV5Current);
    assert.equal(Object.values(imported.rows).filter(r=>r.mediaUrl).length,8);
    assert.equal(errors.length,0,errors.join('\n'));
    await popup.screenshot({path:path.join(OUT,'extension-popup.png'),fullPage:true});
    fs.writeFileSync(path.join(OUT,'browser.json'),JSON.stringify({browser:await context.browser().version(),extensionVersion:'5.3.1',offlinePlaybackEpisodes:3,explicitMedia:2,migrationEpisodes:45,migrationMedia:8,pairing:'production bridge with Chromium; does not satisfy Edge gate',errors},null,2));
    console.log('Offline extension integration passed: navigation, real fixture playback, pairing and CSV migration.');
  } finally {
    if(context)await context.close();
    if(child){child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}
    fs.rmSync(profile,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});

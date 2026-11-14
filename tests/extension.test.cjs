'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..', 'extension');
function load(...files) {
  const ctx = vm.createContext({URL, Date, setTimeout, clearTimeout, console});
  for (const file of files) vm.runInContext(fs.readFileSync(path.join(root, file),'utf8'),ctx,{filename:file});
  return ctx;
}
const ID='0123456789abcdef01234567';
const URL1=`https://www.reelshort.com/episodes/episode-1-test-${ID}`;
function index(count=45) {
  return 'SeriesID,SeriesTitle,Episode,TotalEpisodes,PageURL,MediaURL\r\n' +
    Array.from({length:count},(_,i)=>`${ID},Test,${i+1},${count},https://www.reelshort.com/episodes/episode-${i+1}-test-${ID},${i<8?'https://cdn.example.org/'+i+'.mp4':''}`).join('\r\n');
}
test('v5.2 migration preserves 45 episodes and 8 explicit media URLs',()=>{
  const {DramaCSVImporter:C}=load('csv_importer.js');
  const state=C.loadIndex('\ufeff'+index(),URL1);
  assert.equal(Object.keys(state.rows).length,45);
  assert.equal(Object.values(state.rows).filter(r=>r.mediaUrl).length,8);
  assert.equal(state.finished,true);
});
test('HLS signed query parameters import correctly and remain candidates',()=>{
  const {DramaCSVImporter:C}=load('csv_importer.js');
  const state=C.loadIndex(index(),URL1);
  const url='https://v-mps.crazymaplestudios.com/ep9.m3u8?token=private';
  assert.equal(C.mergeCandidates(`SeriesID,Episode,HLSCandidateURL\n${ID},9,${url}`,state),1);
  assert.equal(state.rows['9'].hlsCandidates[0],url);
  assert.equal(state.rows['9'].mediaUrl,'');
});
test('CSV quotation and malformed input',()=>{
  const {DramaCSVImporter:C}=load('csv_importer.js');
  assert.equal(C.parseCSV('a,b\r\n"x,y","line 1\nline 2"')[0].b,'line 1\nline 2');
  assert.throws(()=>C.parseCSV('a,b\n"open,x'));
  assert.throws(()=>C.parseCSV('a,a\n1,2'));
  assert.throws(()=>C.loadIndex(index(), 'https://www.reelshort.com/series/'+ 'f'.repeat(24)));
});
test('spreadsheet formulas are escaped in both export paths',()=>{
  const {DramaExportCore:C}=load('export_core.js');
  for(const text of ['=HYPERLINK("bad")','+1+1','@cmd','-1+1','\t=bad','  =bad']) assert.ok(C.csvEscape(text).startsWith('"\''));
  assert.equal(C.csvEscape('https://cdn.example.org/v.mp4?x=+1'),'"https://cdn.example.org/v.mp4?x=+1"');
  assert.equal(C.safeName('CON.txt'),'_CON.txt');
});
test('rotated query tokens do not reassign an HLS resource to another episode',()=>{
  const {DramaPlaybackProbe:P}=load('playback_probe.js');
  const old='https://v-mps.crazymaplestudios.com/1.m3u8?token=old';
  const result=P.distinctEpisodeCandidates([{url:old.replace('old','new')}],{'1':{hlsCandidates:[old]}},2);
  assert.equal(result.unique.length,0);
  assert.equal(result.overlap.length,1);
});
test('paused or fully buffered video is not verified as playing',async()=>{
  const {DramaPlaybackProbe:P}=load('playback_probe.js');
  let now=0;
  const video={paused:true,ended:false,readyState:4,currentTime:3,duration:30,src:'https://cdn.example.org/1.mp4'};
  const result=await P.waitForPlayback(()=>video,{now:()=>now,sleep:async ms=>{now+=ms},timeoutMs:500});
  assert.equal(result.ok,false);
});
test('slow but continuous playback accumulates progression',async()=>{
  const {DramaPlaybackProbe:P}=load('playback_probe.js');
  let now=0;
  const video={paused:false,ended:false,readyState:4,currentTime:0,duration:30,src:'https://cdn.example.org/1.mp4'};
  const result=await P.waitForPlayback(()=>video,{now:()=>now,sleep:async ms=>{now+=ms;video.currentTime+=0.05},timeoutMs:1500});
  assert.equal(result.ok,true);
});
test('a different video element cannot fake progression',async()=>{
  const {DramaPlaybackProbe:P}=load('playback_probe.js');
  let now=0;
  const result=await P.waitForPlayback(()=>({paused:false,ended:false,readyState:4,currentTime:now/100,duration:30,src:'https://cdn.example.org/1.mp4'}),{now:()=>now,sleep:async ms=>{now+=ms},timeoutMs:500});
  assert.equal(result.ok,false);
});
test('HLS observation excludes foreign hosts and stale resource timings',()=>{
  const {DramaHLSObserver:H}=load('hls_observer.js');
  const entries=[{name:'https://v-mps.crazymaplestudios.com/1.m3u8?key=x',absoluteStart:10000},{name:'https://evil.example.org/1.m3u8',absoluteStart:10000},{name:'https://v-mps.crazymaplestudios.com/old.m3u8',absoluteStart:1}];
  assert.equal(H.fromEntries(entries,9000,11000).length,1);
});
test('STOP during playback wait survives all later storage writes',async()=>{
  let stored={}, listener, token='';
  let reachedPlayback;
  const waiting = new Promise(resolve=>{reachedPlayback=resolve;});
  const ctx=load('scanner_core.js','hls_observer.js','playback_probe.js');
  ctx.location={href:URL1,assign(){throw Error('Unexpected navigation');}};
  ctx.performance={timeOrigin:Date.now()-1000,getEntriesByType:()=>[]};
  ctx.sessionStorage={getItem:()=>token,setItem:(_,v)=>{token=v;}};
  ctx.crypto={randomUUID:()=> 'test-owner'};
  ctx.document={title:'Test',body:{appendChild(){}},getElementById:id=>id==='__NEXT_DATA__'?{textContent:JSON.stringify({props:{pageProps:{data:{book_id:ID,serial_number:1,chapter_count:1,video_url:'https://cdn.example.org/1.mp4'}}}})}:null,querySelectorAll:()=>[],querySelector:()=>null,createElement:()=>({style:{},remove(){}})};
  ctx.DramaPlaybackProbe={...ctx.DramaPlaybackProbe,waitForPlayback:async(_get,opts)=>{
    reachedPlayback();
    while(!opts.cancelled()) await new Promise(resolve=>setTimeout(resolve,5));
    return {ok:false,reason:'cancelled',snapshot:{}};
  }};
  ctx.chrome={storage:{local:{get:async()=>structuredClone(stored),set:async value=>{Object.assign(stored,structuredClone(value));}}},runtime:{onMessage:{addListener:fn=>{listener=fn;}}}};
  vm.runInContext(fs.readFileSync(path.join(root,'page_agent.js'),'utf8'),ctx);
  const send=message=>new Promise(resolve=>listener(message,{},resolve));
  assert.equal((await send({type:'DRAMA_START',includePaid:true,waitMs:7000})).ok,true);
  await waiting;
  await send({type:'DRAMA_STOP'});
  await new Promise(resolve=>setTimeout(resolve,100));
  const state=stored.dramaScannerV5Current;
  assert.equal(state.running,false);
  assert.equal(state.paused,true);
  assert.equal(state.current,1);
  assert.match(state.note,/توقف يدويًا/);
});

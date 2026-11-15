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
test('automatic retries visit only missing explicit media and stop after three passes',()=>{
  const {DramaRetryPlan:R}=load('retry_plan.js');
  const state={autoMode:true,total:3,rows:{'1':{mediaUrl:'https://cdn.example.org/1.mp4'},'2':{hlsCandidates:['https://cdn.example.org/2.m3u8']}},autoPass:1,maxPasses:99};
  assert.deepEqual(Array.from(R.missing(state)),[2,3]);
  assert.equal(R.plan(state).episode,2);
  assert.equal(R.plan(state).pass,2);
  state.autoPass=3;assert.equal(R.plan(state),null);
});
test('rate limiting and complete scans never schedule refreshes',()=>{
  const {DramaRetryPlan:R}=load('retry_plan.js');
  assert.equal(R.plan({autoMode:true,total:1,rows:{},rateLimited:true}),null);
  assert.equal(R.plan({autoMode:true,total:1,rows:{'1':{mediaUrl:'https://cdn.example.org/1.mp4'}}}),null);
});
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

const STREAM='https://v-mps.crazymaplestudios.com/vod-100/'+'9'.repeat(32)+'/episode-sd.m3u8?token=fixture';
const playlist='#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment0.ts?token=one\n#EXTINF:4,\nsegment1.ts?token=two\n#EXT-X-ENDLIST\n';
function hlsContext(){return {playing:true,duration:8,episode:9,rows:{},since:10000,manifestStart:10000,sourceChanged:true,resources:[0,1].map((n)=>({url:new URL('segment'+n+'.ts?token=rotated',STREAM).href,absoluteStart:10010+n}))};}
test('stale metadata from another series does not override the visible route',()=>{
  const {DramaScannerCore:C}=load('scanner_core.js');
  const doc={title:'Episode 9 - Current | ReelShort',getElementById:()=>({textContent:JSON.stringify({props:{pageProps:{data:{book_id:'f'.repeat(24),serial_number:1,chapter_count:52,book_title:'Wrong series',video_url:'https://cdn.example.org/wrong.mp4'}}}})}),querySelectorAll:()=>[]};
  const info=C.extractDocument(doc,URL1.replace('episode-1-','episode-9-'),ID,9);
  assert.equal(info.seriesId,ID);assert.equal(info.mediaUrl,'');assert.equal(info.total,0);assert.equal(info.metadataStale,true);assert.equal(info.title,'Current');
});
test('VOD HLS is matched to playback using duration and requested segments',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  const parsed=S.parse(playlist,STREAM);const result=S.assess(parsed,hlsContext());
  assert.equal(result.ok,true);assert.equal(result.matchedSegments,2);assert.equal(result.mediaUrl,STREAM);
});
test('a prefetched URL in an unverified candidate list does not claim the next episode',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  const context=hlsContext();context.rows={'8':{hlsCandidates:[STREAM]}};
  assert.equal(S.assess(S.parse(playlist,STREAM),context).ok,true);
});
test('a verified stream cannot be assigned to a second episode by rotating its query',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  const context=hlsContext();context.rows={'8':{verifiedHlsUrl:STREAM.replace('fixture','old')}};
  assert.equal(S.assess(S.parse(playlist,STREAM),context).reason,'hls_previous_episode');
});
test('playback alone or a fetched manifest without segments is insufficient',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  const context=hlsContext();context.resources=[];
  assert.equal(S.assess(S.parse(playlist,STREAM),context).reason,'hls_segments_not_observed');
  context.playing=false;assert.equal(S.assess(S.parse(playlist,STREAM),context).ok,false);
});
test('a trailer with a different duration is not the current episode',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  const context=hlsContext();context.duration=160.5;
  assert.equal(S.assess(S.parse(playlist,STREAM),context).reason,'hls_duration_mismatch');
});
test('cached manifests need evidence that the player left its previous video',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  const context=hlsContext();context.manifestStart=100;context.sourceChanged=false;
  assert.equal(S.assess(S.parse(playlist,STREAM),context).reason,'hls_stale_playback');
  context.sourceChanged=true;assert.equal(S.assess(S.parse(playlist,STREAM),context).ok,true);
});
test('two matching independent streams stay ambiguous',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  assert.equal(S.select([{ok:true,group:'one',lastSegment:1},{ok:true,group:'two',lastSegment:2}]).reason,'hls_ambiguous');
});
test('DRM and unsafe playlist references are rejected without requesting keys',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  for(const key of ['#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key.bin"','#EXT-X-KEY:METHOD=AES-128,KEYFORMAT="com.apple.streamingkeydelivery",URI="key.bin"']){
    assert.equal(S.parse(playlist.replace('#EXTM3U','#EXTM3U\n'+key),STREAM).reason,'hls_drm_protected');
  }
  assert.equal(S.parse(playlist.replace('segment0.ts?token=one','https://127.0.0.1/private'),STREAM).reason,'hls_unsafe_manifest');
});
test('live and iframe-only playlists are not treated as complete episode downloads',()=>{
  const {DramaHLSSource:S}=load('hls_observer.js','hls_source.js');
  assert.equal(S.parse(playlist.replace('#EXT-X-ENDLIST',''),STREAM).ok,false);
  assert.equal(S.parse(playlist+'#EXT-X-I-FRAMES-ONLY\n',STREAM).ok,false);
});
test('resource observer captures media after the normal timeline buffer is full',()=>{
  let observer;
  const ctx=vm.createContext({URL,Date,performance:{timeOrigin:1000,getEntriesByType:()=>[]},PerformanceObserver:class{constructor(fn){observer=fn;}observe(options){assert.equal(options.buffered,true);}}});
  vm.runInContext(fs.readFileSync(path.join(root,'hls_observer.js'),'utf8'),ctx);
  observer({getEntries:()=>[{name:STREAM,startTime:9000},{name:new URL('segment0.ts',STREAM).href,startTime:9010}]});
  assert.equal(ctx.DramaHLSObserver.recent(9500,11000).length,1);
  assert.equal(ctx.DramaHLSObserver.resources(9500,11000).length,2);
});

test('a verification fetch cannot turn an old manifest into fresh player evidence',async()=>{
  const ctx=load('hls_observer.js');let fetched=false,calls=0;const since=Date.now();
  const resources=()=>[{url:STREAM,absoluteStart:fetched?Date.now():since-1000},...[0,1].map(n=>({url:new URL('segment'+n+'.ts',STREAM).href,absoluteStart:since+1+n}))];
  ctx.DramaHLSObserver={...ctx.DramaHLSObserver,resources};ctx.AbortController=AbortController;
  ctx.fetch=async url=>{fetched=true;calls++;return {ok:true,status:200,url,headers:{get:()=>null},text:async()=>playlist};};
  vm.runInContext(fs.readFileSync(path.join(root,'hls_source.js'),'utf8'),ctx);
  const video={paused:false,ended:false,readyState:4,currentSrc:'blob:old',duration:8,currentTime:4};
  const result=await ctx.DramaHLSSource.resolve({video:()=>video,episode:9,rows:{},since,previous:{src:'blob:old',duration:8,position:4},timeoutMs:1000});
  assert.equal(result.ok,false);assert.equal(result.reason,'hls_stale_playback');assert.equal(calls,1);
});

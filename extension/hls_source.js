/* Verify an observed VOD playlist against actual blob playback and loaded segments.
   Only URLs the current page already requested are inspected. No key requests or DRM operations. */
((root)=>{
  'use strict';
  const H=root.DramaHLSObserver;
  const identity=value=>{try{const u=new URL(value);return u.origin+u.pathname;}catch(_){return '';}};
  function group(value){
    const u=new URL(value);const match=u.pathname.match(/^\/(vod-\d+)\/([a-f0-9]{32})\//i);
    return match?u.origin+'/'+match[1]+'/'+match[2].toLowerCase():identity(value);
  }
  function attributes(line){
    const result={};for(const match of line.matchAll(/([A-Z0-9-]+)=("[^"]*"|[^,]*)/g))result[match[1]]=match[2].replace(/^"|"$/g,'');return result;
  }
  function parse(text,url){
    if(!H.isHLS(url)||typeof text!=='string'||text.length>1024*1024||!text.replace(/^\ufeff/,'').trimStart().startsWith('#EXTM3U'))return {ok:false,reason:'hls_invalid_manifest'};
    const lines=text.replace(/^\ufeff/,'').split(/\r?\n/).map(s=>s.trim());
    if(lines.some(s=>s.startsWith('#EXT-X-STREAM-INF:')))return {ok:false,reason:'hls_master_only'};
    let duration=0,pending=null;const segments=[];
    for(const line of lines){
      if(/^#EXT-X-(?:SESSION-)?KEY:/.test(line)){
        const a=attributes(line);
        if(a.METHOD!=='NONE'&&(a.METHOD!=='AES-128'||(a.KEYFORMAT&&a.KEYFORMAT!=='identity')))return {ok:false,reason:'hls_drm_protected'};
        if(a.METHOD==='AES-128'){
          try{if(!a.URI||!H.trusted(new URL(a.URI,url).href))return {ok:false,reason:'hls_unsafe_manifest'};}
          catch(_){return {ok:false,reason:'hls_unsafe_manifest'};}
        }
      }
      if(line.startsWith('#EXTINF:')){
        pending=Number(line.slice(8).split(',')[0]);
        if(!Number.isFinite(pending)||pending<=0)return {ok:false,reason:'hls_invalid_manifest'};
      }else if(line&&!line.startsWith('#')){
        if(pending===null)return {ok:false,reason:'hls_invalid_manifest'};
        let segment;try{segment=new URL(line,url).href;}catch(_){return {ok:false,reason:'hls_invalid_manifest'};}
        if(!H.trusted(segment))return {ok:false,reason:'hls_unsafe_manifest'};
        duration+=pending;segments.push(segment);pending=null;
      }
    }
    if(pending!==null||!segments.length||!lines.includes('#EXT-X-ENDLIST')||lines.includes('#EXT-X-I-FRAMES-ONLY'))return {ok:false,reason:'hls_not_vod'};
    return {ok:true,url,duration,segments,group:group(url)};
  }
  function assess(playlist,context){
    if(!playlist.ok)return playlist;
    if(!context.playing||!Number.isFinite(context.duration)||context.duration<=0)return {ok:false,reason:'playback_not_started'};
    if(Math.abs(playlist.duration-context.duration)>Math.max(1.5,context.duration*0.005))return {ok:false,reason:'hls_duration_mismatch'};
    const assigned=new Set();
    for(const [ep,row] of Object.entries(context.rows||{})){
      if(Number(ep)===Number(context.episode))continue;
      for(const value of [row.verifiedHlsUrl,row.mediaUrl].filter(H.isHLS))assigned.add(group(value));
    }
    if(assigned.has(playlist.group))return {ok:false,reason:'hls_previous_episode'};
    const requests=new Map((context.resources||[]).filter(e=>e.absoluteStart>=context.since-200).map(e=>[identity(e.url),e.absoluteStart]));
    const matched=[...new Set(playlist.segments.map(identity))].filter(s=>requests.has(s));
    const required=Math.min(2,new Set(playlist.segments.map(identity)).size);
    if(matched.length<required)return {ok:false,reason:'hls_segments_not_observed'};
    if(!context.sourceChanged&&context.manifestStart<context.since)return {ok:false,reason:'hls_stale_playback'};
    return {ok:true,mediaUrl:playlist.url,group:playlist.group,duration:playlist.duration,matchedSegments:matched.length,
      lastSegment:Math.max(...matched.map(s=>requests.get(s)))};
  }
  function select(results){
    const valid=results.filter(r=>r.ok);const groups=new Set(valid.map(r=>r.group));
    if(groups.size>1)return {ok:false,reason:'hls_ambiguous'};
    if(valid.length)return valid.sort((a,b)=>b.lastSegment-a.lastSegment)[0];
    const priority=['hls_drm_protected','hls_access_denied','hls_rate_limited','hls_duration_mismatch','hls_previous_episode','hls_stale_playback','hls_segments_not_observed'];
    return results.find(r=>priority.includes(r.reason))||{ok:false,reason:'hls_not_observed'};
  }
  async function read(url,signal){
    try{
      const response=await fetch(url,{credentials:'omit',cache:'no-store',redirect:'follow',signal});
      if([429,503].includes(response.status))return {ok:false,reason:'hls_rate_limited'};
      if(!response.ok)return {ok:false,reason:'hls_access_denied'};
      if(!H.isHLS(response.url)||identity(response.url)!==identity(url))return {ok:false,reason:'hls_unsafe_manifest'};
      if(Number(response.headers.get('Content-Length'))>1024*1024)return {ok:false,reason:'hls_invalid_manifest'};
      const reader=response.body?.getReader();let text='';
      if(reader){const decoder=new TextDecoder();let size=0;while(true){const item=await reader.read();if(item.done)break;size+=item.value.byteLength;if(size>1024*1024){await reader.cancel();return {ok:false,reason:'hls_invalid_manifest'};}text+=decoder.decode(item.value,{stream:true});}text+=decoder.decode();}
      else text=await response.text();
      return parse(text,url);
    }catch(_){return {ok:false,reason:'hls_access_denied'};}
  }
  async function resolve(options){
    const started=Date.now(),cache=new Map();let last={ok:false,reason:'hls_not_observed'};
    const limit=Math.min(8000,Math.max(1000,Number(options.timeoutMs)||6000));
    while(Date.now()-started<limit){
      if(options.cancelled?.())return {ok:false,reason:'cancelled'};
      const video=options.video();
      if(video?.mediaKeys)return {ok:false,reason:'hls_drm_protected'};
      const src=String(video?.currentSrc||video?.src||'');
      const duration=Number(video?.duration),position=Number(video?.currentTime)||0;
      const sourceChanged=!options.previous?.src||src!==options.previous.src||Math.abs(duration-Number(options.previous.duration))>0.2||position+0.25<Number(options.previous.position);
      const resources=H.resources(options.since);
      const groupsWithSegments=new Set(resources.filter(e=>!H.isHLS(e.url)).map(e=>{
        const u=new URL(e.url);return u.origin+u.pathname.slice(0,u.pathname.lastIndexOf('/')+1);
      }));
      const candidates=H.resources(0).filter(e=>H.isHLS(e.url)).reverse().filter(e=>{
        const u=new URL(e.url);return groupsWithSegments.has(u.origin+u.pathname.slice(0,u.pathname.lastIndexOf('/')+1));
      }).filter((e,i,a)=>a.findIndex(x=>x.url===e.url)===i).slice(0,6);
      const checked=[];
      for(const candidate of candidates){
        if(options.cancelled?.())return {ok:false,reason:'cancelled'};
        if(!cache.has(candidate.url)){
          const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),Math.min(2200,Math.max(100,limit-(Date.now()-started))));
          try{cache.set(candidate.url,{playlist:await read(candidate.url,controller.signal),manifestStart:candidate.absoluteStart});}finally{clearTimeout(timer);}
        }
        checked.push(assess(cache.get(candidate.url).playlist,{episode:options.episode,rows:options.rows,resources,since:options.since,manifestStart:cache.get(candidate.url).manifestStart,
          sourceChanged,playing:!!video&&!video.paused&&!video.ended&&video.readyState>=2,duration}));
      }
      last=select(checked);
      if(last.ok){
        const stable=options.video();
        if(stable===video&&String(stable.currentSrc||stable.src||'')===src&&Math.abs(stable.duration-duration)<0.1&&!options.cancelled?.())return last;
      }
      if(['hls_drm_protected','hls_rate_limited'].includes(last.reason))return last;
      await new Promise(r=>setTimeout(r,200));
    }
    return last;
  }
  root.DramaHLSSource=Object.freeze({identity,group,parse,assess,select,resolve});
})(globalThis);

/* Passive diagnostics only. No access-control, EME, decryption or cookies. */
((root) => {
  'use strict';
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function snapshot(video) {
    if (!video) return {exists:false,playing:false,sourceType:'none',durationSec:null,positionSec:0,readyState:0};
    const src=String(video.currentSrc || video.src || '');
    const sourceType=src.startsWith('blob:')?'MSE/blob':/^https?:/i.test(src)?'direct_url':src?'other':'none';
    return {
      exists:true,playing:!video.paused && !video.ended && video.readyState>=2,
      sourceType,readyState:Number(video.readyState)||0,
      durationSec:Number.isFinite(video.duration)?Math.round(video.duration):null,
      positionSec:Number(video.currentTime)||0
    };
  }
  async function waitForPlayback(getVideo, opts={}) {
    const timeout=Math.min(25000,Math.max(500,Number(opts.timeoutMs)||9000));
    const step=Math.min(500,Math.max(50,Number(opts.pollMs)||250));
    const now=opts.now||Date.now, sleep=opts.sleep||wait;
    const started=now();let last=null;let candidate=null;let candidateVideo=null;let candidateSource='';
    while(now()-started < timeout) {
      if (opts.cancelled?.()) return {ok:false,reason:'cancelled',snapshot:last||snapshot(null)};
      const video=getVideo();
      last=snapshot(video);
      const source=String(video?.currentSrc||video?.src||'');
      // Verify progression, not merely video.readyState (a paused video can be fully buffered).
      if(last.playing) {
        if(candidate && candidateVideo===video && candidateSource===source && last.positionSec>candidate.positionSec+0.12)
          return {ok:true,reason:'playback_progress',snapshot:last};
        if(!candidate || candidateVideo!==video || candidateSource!==source || last.positionSec<candidate.positionSec) {
          candidate=last;candidateVideo=video;candidateSource=source;
        }
      } else {candidate=null;candidateVideo=null;candidateSource='';}
      await sleep(step);
    }
    return {ok:false,reason:last?.exists?'playback_not_started':'video_element_missing',snapshot:last||snapshot(null)};
  }
  function distinctEpisodeCandidates(entries,rows,episode,limit=12) {
    // Rotated query tokens do not prove that a URL belongs to a different episode.
    const identity=value=>{try{const u=new URL(value);return u.origin+u.pathname;}catch(_){return String(value);}};
    const previous=new Set();
    for(const [key,row] of Object.entries(rows||{})) {
      if(Number(key)===Number(episode)) continue;
      for(const u of [...(row.hlsCandidates||[]),row.mediaUrl].filter(Boolean)) previous.add(identity(u));
    }
    const unique=[],overlap=[],seen=new Set();
    for(const item of entries||[]) {
      const url=typeof item==='string'?item:item.url;
      if(!url || seen.has(identity(url))) continue;
      seen.add(identity(url));
      (previous.has(identity(url))?overlap:unique).push(url);
    }
    return {unique:unique.slice(0,limit),overlap,detected:seen.size};
  }
  root.DramaPlaybackProbe=Object.freeze({snapshot,waitForPlayback,distinctEpisodeCandidates});
})(globalThis);

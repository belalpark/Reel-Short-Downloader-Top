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
    const started=now();let last=null;let candidate=null;
    while(now()-started < timeout) {
      if (opts.cancelled?.()) return {ok:false,reason:'cancelled',snapshot:last||snapshot(null)};
      last=snapshot(getVideo());
      // Verify progression, not merely video.readyState (a paused video can be fully buffered).
      if(last.playing) {
        if(candidate && candidate.sourceType===last.sourceType && last.positionSec>candidate.positionSec+0.12)
          return {ok:true,reason:'playback_progress',snapshot:last};
        candidate=last;
      } else candidate=null;
      await sleep(step);
    }
    return {ok:false,reason:last?.exists?'playback_not_started':'video_element_missing',snapshot:last||snapshot(null)};
  }
  function distinctEpisodeCandidates(entries,rows,episode,limit=12) {
    const previous=new Set();
    for(const [key,row] of Object.entries(rows||{})) {
      if(Number(key)===Number(episode)) continue;
      for(const u of [...(row.hlsCandidates||[]),row.mediaUrl].filter(Boolean)) previous.add(u);
    }
    const unique=[],overlap=[],seen=new Set();
    for(const item of entries||[]) {
      const url=typeof item==='string'?item:item.url;
      if(!url || seen.has(url)) continue;
      seen.add(url);
      (previous.has(url)?overlap:unique).push(url);
    }
    return {unique:unique.slice(0,limit),overlap,detected:seen.size};
  }
  root.DramaPlaybackProbe=Object.freeze({snapshot,waitForPlayback,distinctEpisodeCandidates});
})(globalThis);

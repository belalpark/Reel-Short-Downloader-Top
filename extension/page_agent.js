/* Index accessible episodes and verify observed HLS against the active player. */
(() => {
  'use strict';
  if (globalThis.__dramaScanV5Loaded) return;
  globalThis.__dramaScanV5Loaded = true;
  const C = globalThis.DramaScannerCore;
  const CHANNEL_KEY='__reelDesktopSession';
  let CHANNEL='';try{CHANNEL=sessionStorage.getItem(CHANNEL_KEY)||'';}catch(_){}
  if(!/^[a-f\d]{32}$/.test(CHANNEL))CHANNEL='';
  let STORE = CHANNEL?'dramaScannerSession:'+CHANNEL:'dramaScannerV5Current';
  const P=globalThis.DramaPlaybackProbe; // Preserved to keep v5 progress and exports
  const TAB_TOKEN_KEY = '__dramaScannerV5OwnedTab';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let busy = false;
  let cancelled = false;
  let lastDesktopPublish = 0;
  

  function token() {
    try { return sessionStorage.getItem(TAB_TOKEN_KEY) || ''; } catch (_) { return ''; }
  }
  function setToken(value) {
    try { sessionStorage.setItem(TAB_TOKEN_KEY,value); } catch (_) { /* private mode */ }
  }
  async function load() {
    return (await chrome.storage.local.get(STORE))[STORE] || null;
  }
  async function persist(state,force=false) {
    if(String(state.channel||'')!==CHANNEL)return false;
    if(cancelled && state.running) return false;
    state.updatedAt = new Date().toISOString();
    await chrome.storage.local.set(state.collectOnly||CHANNEL?{[STORE]:state}:{[STORE]: state, ['dramaScannerSeries:'+state.seriesId]: state});
    if(state.autoMode && (force || Date.now()-lastDesktopPublish>900 || !state.running)){
      lastDesktopPublish=Date.now();
      try{await chrome.runtime.sendMessage({type:CHANNEL?'DRAMA_SESSION_SNAPSHOT':'DRAMA_DESKTOP_SNAPSHOT',state});}catch(_){}
    }
    draw(state);
  }
  function pause(state, message) {
    state.running = false;
    state.paused = true;
    state.finished = false;
    state.note = message;
  }
  function recordError(state, episode, label) {
    state.events = Array.isArray(state.events) ? state.events : [];
    state.events.push({episode, result: label, time: new Date().toISOString()});
    state.events = state.events.slice(-80);
  }
  function draw(state) {
    if (!state || !state.running && !state.note) return;
    let el = document.getElementById('__dramaScannerV5Status');
    if (!el) {
      el = document.createElement('div');
      el.id = '__dramaScannerV5Status';
      el.style.cssText = 'position:fixed!important;left:12px!important;bottom:15px!important;z-index:2147483646!important;color:white!important;background:rgba(10,18,32,.96)!important;border:1px solid #3b82f6!important;border-radius:10px!important;padding:10px 14px!important;font:13px Arial,sans-serif!important;max-width:320px!important;direction:rtl!important;';
      document.body.appendChild(el);
    }
    el.textContent = `فحص الحلقات: ${Math.min(state.current || 0,state.total || 0)}/${state.total || 0} — ${state.note || ''}`;
    if (!state.running) setTimeout(() => el.remove(),5000);
  }
  function cleanTitle(s) {
    return String(s || '').replace(/^(?:الحلقة\s*\d+|Episode\s*\d+)\s*[-–:]\s*/i,'').replace(/\s*\|\s*ReelShort.*$/i,'').trim();
  }
  function discover(state) {
    const landing=[...document.querySelectorAll('a[href]')].find(a=>/\/(?:movie|full-episodes)\//.test(a.href)&&C.seriesIdFromUrl(a.href)===state.seriesId);
    if(landing)state.seriesPageUrl=landing.href.split('?')[0];
    const numbers = C.allButtons(document);
    if (numbers.length && numbers.length > state.knownButtons) state.knownButtons = numbers.length;
    for (const a of document.querySelectorAll('a[href*="/episodes/episode-"]')) {
      const ep = C.episodeNumber(a.href);
      if (ep > 0 && ep <= state.total && C.seriesIdFromUrl(a.href) === state.seriesId) {
        const old = state.rows[String(ep)] || {};
        state.rows[String(ep)] = {...old, episode:ep, pageUrl:a.href, status:old.status || 'page_only'};
      }
    }
  }
  function ownSeries(id) {
    return C.seriesIdFromUrl(location.href) === id;
  }
  function statusRow(state, ep, status, url='') {
    const old = state.rows[String(ep)] || {};
    state.rows[String(ep)] = {
      ...old, episode:ep, pageUrl:url || old.pageUrl || '', mediaUrl:old.mediaUrl || '',
      hlsCandidates: old.hlsCandidates || [], hlsObservedAt:old.hlsObservedAt || '',
      status: old.mediaUrl ? 'media_found' : status,
      checkedAt:new Date().toISOString()
    };
  }
  async function readCurrentPage(state, episode) {
    const url=location.href;
    if (!ownSeries(state.seriesId) || C.episodeNumber(url)!==episode) {
      statusRow(state,episode,'different_page'); return;
    }
    let info=C.extractDocument(document,url,state.seriesId,episode);
    // A normal HTML GET is only used for visible page metadata, not a playback API.
    if (!info?.mediaUrl) {
      try {
        const controller=new AbortController();
        const timeout=setTimeout(()=>controller.abort(),6000);
        try {
          const response=await fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'follow',signal:controller.signal});
          if(response.ok && new URL(response.url).hostname==='www.reelshort.com') {
            const fresh=C.readHTML(await response.text(),response.url,state.seriesId,episode);
            if (fresh) info=fresh;
          } else if (!response.ok) {
            recordError(state,episode,`HTML metadata HTTP ${response.status}`);
            if([429,503].includes(response.status))state.rateLimited=true;
          }
        } finally {clearTimeout(timeout);}
      } catch (_) { /* Metadata may be absent, but video can still play. */ }
    }
    const old=state.rows[String(episode)]||{};
    if(cancelled) return;
    if(state.rateLimited){
      if(state.patient){statusRow(state,episode,'hls_rate_limited');return;}
      pause(state,'الموقع يطلب الانتظار. توقف الفحص؛ حاول لاحقًا.');await persist(state);return;
    }
    const newMedia=info && C.mediaUrl(info.mediaUrl) ? info.mediaUrl : '';
    const row=state.rows[String(episode)]={
      ...old,episode,pageUrl:url.split('?')[0],mediaUrl:newMedia||old.mediaUrl||'',
      hlsCandidates:Array.isArray(old.hlsCandidates)?old.hlsCandidates:[],
      checkedAt:new Date().toISOString()
    };
    state.note=`الحلقة ${episode}: انتظار تقدم تشغيل الفيديو (حد أقصى ${Math.round((state.waitMs||9000)/1000)} ثوانٍ)`;
    await persist(state);
    if(state.autoMode){
      const player=document.querySelector('video');
      if(CHANNEL&&player)player.muted=true;
      if(player?.paused)try{player.play()?.catch(()=>{});}catch(_){}
    }
    const result=await P.waitForPlayback(()=>document.querySelector('video'),{
      timeoutMs:state.waitMs||9000,pollMs:250,
      cancelled:()=>cancelled||!ownSeries(state.seriesId)||C.episodeNumber(location.href)!==episode
    });
    if(cancelled || result.reason==='cancelled') return;
    row.playbackStatus=result.ok?'playback_verified':result.reason;
    row.sourceType=result.snapshot.sourceType;
    row.playbackDurationSec=result.snapshot.durationSec;
    row.playbackObservedAt=result.ok?new Date().toISOString():old.playbackObservedAt||'';
    if (!result.ok) {
      row.status=row.mediaUrl?'media_found':result.reason;
      recordError(state,episode,result.reason);
      if(!state.autoMode)pause(state,`الحلقة ${episode}: لم يثبت بدء التشغيل (${result.reason}). شغّلها بنفسك واضغط «استئناف»، أو تجاوز الحلقة.`);
      else state.note=`الحلقة ${episode}: لم يبدأ التشغيل؛ ستُعاد محاولة الناقص تلقائيًا.`;
      await persist(state);
      return;
    }
    const currentVideo=document.querySelector('video');
    const visibleSource=C.mediaUrl(currentVideo?.currentSrc||currentVideo?.src||'');
    if(!row.mediaUrl && visibleSource && P.distinctEpisodeCandidates([visibleSource],state.rows,episode).unique.length){
      row.mediaUrl=visibleSource;row.sourceType='video_element';
    }
    // Media requests could be triggered before the first visible frame. Limit to this
    // episode navigation window and explicitly label association as unverified.
    const entryEpoch=(state.episodeStartedFor===episode && state.epStartedAt) ? state.epStartedAt : Date.now()-6000;
    const observationStart=Math.max(performance.timeOrigin,entryEpoch-150);
    await sleep(500);
    if(cancelled) return;
    const entries=globalThis.DramaHLSObserver.recent(observationStart,Date.now());
    const distinct=P.distinctEpisodeCandidates(entries,state.rows,episode);
    row.duplicateHlsCount=distinct.overlap.length;
    if(distinct.unique.length) {
      row.hlsCandidates=[...new Set([...(old.hlsCandidates||[]),...distinct.unique])].slice(0,12);
      row.hlsObservedAt=new Date().toISOString();
    }
    const verified=globalThis.DramaHLSSource ? await globalThis.DramaHLSSource.resolve({
      video:()=>document.querySelector('video'),episode,rows:state.rows,since:entryEpoch,
      previous:state.previousPlayback,timeoutMs:row.mediaUrl?1500:7000,
      cancelled:()=>cancelled||!ownSeries(state.seriesId)||C.episodeNumber(location.href)!==episode
    }):{ok:false,reason:'hls_not_observed'};
    if(cancelled||verified.reason==='cancelled')return;
    if(verified.reason==='hls_drm_protected'){
      row.mediaUrl='';row.verifiedHlsUrl='';row.captureId='';row.collected=false;
      row.playbackStatus=row.status='hls_drm_protected';
      state.note='المشغّل أبلغ عن حماية الفيديو. أُوقفت محاولة تنزيل الحلقة.';
      await persist(state);
      return;
    }
    if(verified.ok){
      row.verifiedHlsUrl=verified.mediaUrl;
      row.hlsMatchedSegments=verified.matchedSegments;
      row.hlsDuration=verified.duration;
      if(!row.mediaUrl||CHANNEL){row.mediaUrl=verified.mediaUrl;row.sourceType='playback_hls';}
    }
    if(verified.reason==='hls_rate_limited'){state.rateLimited=true;if(!state.patient)pause(state,'الموقع يطلب الانتظار. توقف الفحص؛ حاول لاحقًا.');}
    row.status=row.mediaUrl?'media_found':verified.reason;
    row.checkedAt=new Date().toISOString();
    if (info?.title && cleanTitle(info.title).length>3 && (!state.title||state.title==='Series')) state.title=cleanTitle(info.title);
    if(!state.rateLimited)state.note=row.mediaUrl?`تم التقاط فيديو الحلقة ${episode}.`:`الحلقة ${episode}: لم يتأكد رابط الفيديو؛ ستُعاد محاولة الناقص تلقائيًا.`;
  }
  async function navigateEpisode(state, episode) {
    discover(state);
    const direct = state.rows[String(episode)]?.pageUrl;
    if(CHANNEL&&direct?.startsWith('https://www.reelshort.com/')&&C.episodeNumber(direct)===episode&&C.seriesIdFromUrl(direct)===state.seriesId){location.assign(direct);return 'navigating';}
    let btn = C.findEpisodeButton(document,episode);
    if (!btn) {
      const group = C.findGroupButton(document,episode);
      if (group) { group.click(); await sleep(350); if(cancelled)return 'cancelled'; btn = C.findEpisodeButton(document,episode); }
    }
    if (!btn) {
      if(direct?.startsWith('https://www.reelshort.com/')&&C.episodeNumber(direct)===episode&&C.seriesIdFromUrl(direct)===state.seriesId){location.assign(direct);return 'navigating';}
      return 'missing';
    }
    if(btn.disabled || btn.getAttribute('aria-disabled')==='true')return 'access_unavailable';
    const before = location.href;
    const player=document.querySelector('video');
    state.previousPlayback=player?{src:String(player.currentSrc||player.src||''),duration:Number(player.duration)||0,position:Number(player.currentTime)||0}:null;
    await persist(state);
    if(cancelled)return 'cancelled';
    state.epStartedAt=Date.now();state.episodeStartedFor=episode;
    btn.click(); // Only episode selection; NEVER click purchase/unlock controls.
    for (let i=0; i<35; i++) {
      await sleep(100);
      if(cancelled) return 'cancelled';
      if (location.href !== before) {
        if (C.episodeNumber(location.href) === episode && ownSeries(state.seriesId)){
          if(CHANNEL){await persist(state,true);location.reload();return 'navigating';}
          return 'ready';
        }
        return 'wrong_page';
      }
    }
    return 'manual_open_required';
  }
  async function stillActive(state,delayMs) {
    const until=Date.now()+delayMs;
    while(Date.now()<until){
      if(cancelled)return false;
      await sleep(Math.min(250,until-Date.now()));
    }
    const live=await load();
    return !cancelled&&live?.running&&live.owner===state.owner&&token()===state.owner;
  }
  async function retryEpisode(state,episode,label) {
    const row=state.rows[String(episode)]||{episode};
    row.attempts=(row.attempts||0)+1;
    state.rows[String(episode)]=row;
    const retry=globalThis.DramaRetryPlan.sameEpisode(state,episode);
    state.current=episode;state.retryAt=Date.now()+retry.delayMs;
    state.note=state.rateLimited?`الموقع يطلب مهلة. ستُعاد محاولة الحلقة ${episode} بعد دقيقة.`:
      `الحلقة ${episode}: ${label}؛ إعادة المحاولة ${row.attempts} خلال ${Math.ceil(retry.delayMs/1000)} ثوانٍ.`;
    await persist(state,true);
    if(!await stillActive(state,retry.delayMs))return;
    row.mediaUrl='';row.captureId='';row.awaitingDownload=false;row.verifiedHlsUrl='';row.hlsCandidates=[];
    state.rateLimited=false;state.retryAt=0;state.episodeStartedFor=episode;state.epStartedAt=Date.now();
    await persist(state,true);
    if(cancelled)return;
    const destination=row.pageUrl;
    if(destination?.startsWith('https://www.reelshort.com/')&&C.seriesIdFromUrl(destination)===state.seriesId&&C.episodeNumber(destination)===episode&&destination.split('?')[0]!==location.href.split('?')[0])location.assign(destination);
    else location.reload();
  }
  async function waitForDownload(state,episode) {
    const row=state.rows[String(episode)];
    while(!cancelled){
      let response;
      try{response=await chrome.runtime.sendMessage({type:'DRAMA_DESKTOP_PROGRESS'});}catch(_){}
      if(cancelled)return 'cancelled';
      const progress=response?.download;
      if(progress?.runId===state.desktopCommandId&&progress.seriesId===state.seriesId){
        if(progress.completedEpisodes?.includes(episode)){
          row.downloaded=true;row.awaitingDownload=false;row.status='downloaded';
          state.note=`اكتمل تنزيل الحلقة ${episode} وفحصها.`;
          await persist(state,true);return 'complete';
        }
        if(!progress.enabled)return 'cancelled';
        if(progress.episode===episode&&progress.outcome?.status==='failed'&&progress.outcome.captureId===row.captureId)return 'failed';
      }
      state.note=`الحلقة ${episode}: انتظار اكتمال التنزيل وفحص الملف قبل الانتقال.`;
      await persist(state);
      if(!await stillActive(state,1000))return 'cancelled';
    }
    return 'cancelled';
  }
  async function loop() {
    if (busy) return;
    busy=true;
    try {
      let state=await load();
      if (!state?.running || !token() || state.owner !== token()) return;
      draw(state);
      while (state.running && state.current <= state.total) {
        if(cancelled) return;
        const live = await load();
        if (!live || !live.running || live.owner !== token()) return;
        state=live;
        if (!ownSeries(state.seriesId)) {
          pause(state,'انتقلت الصفحة إلى خارج المسلسل. عد إلى صفحة الحلقة المطلوبة ثم اضغط استئناف.');
          await persist(state); break;
        }
        const ep=state.current;
        if(state.collectOnly){
          const next=globalThis.DramaRetryPlan.missing(state).find(n=>n>=ep);
          if(!next){state.current=state.total+1;await persist(state);break;}
          if(next!==ep){state.current=next;await persist(state);continue;}
        }
        if(state.collectOnly){const reached=state.rows[String(ep)]||{episode:ep};reached.tried=true;state.rows[String(ep)]=reached;}
        if(state.patient&&state.rows[String(ep)]?.downloaded){state.current++;await persist(state);continue;}
        if(state.patient&&state.rows[String(ep)]?.awaitingDownload){
          const result=await waitForDownload(state,ep);
          if(result==='complete'){state.current++;await persist(state);continue;}
          if(result==='failed')await retryEpisode(state,ep,'تعذر التنزيل؛ جارٍ تجديد الرابط');
          return;
        }
        if (state.recheckOnly) {
          const existing=state.rows[String(ep)]||{};
          if ((state.collectOnly?existing.collected:existing.mediaUrl) || (!state.autoMode && existing.playbackStatus==='playback_verified' && (existing.hlsCandidates||[]).length)) {
            state.current++; await persist(state); continue;
          }
        }
        if (!state.includePaid && state.paidStart > 0 && ep >= state.paidStart) {
          statusRow(state,ep,'paid_skipped'); state.current++;
          state.note = `تجاوز الحلقة ${ep} لأن خيار فحص VIP غير مفعّل`;
          await persist(state); continue;
        }
        if (C.episodeNumber(location.href) !== ep) {
          state.epStartedAt = Date.now();
          state.episodeStartedFor = ep;
          state.note=`الانتقال إلى الحلقة ${ep}`;
          await persist(state);
          const result=await navigateEpisode(state,ep);
          if(cancelled) return;
          if (result === 'navigating') return; // Full reload resumes in new content script.
          if (result !== 'ready') {
            statusRow(state,ep,result);
            recordError(state,ep,result);
            if(state.patient){await retryEpisode(state,ep,'تعذر فتح الحلقة');return;}
            // A paid episode the site does not open for this account. Never click unlock or
            // purchase controls; report the boundary once instead of trying every paid episode.
            if(state.collectOnly&&state.paidStart>0&&ep>=state.paidStart&&(result==='manual_open_required'||result==='access_unavailable')){
              state.lockedFrom=ep;state.current=state.total+1;
              state.note=`الحلقة ${ep} مدفوعة ولم يفتحها الموقع لهذا الحساب؛ أُوقف فحص بقية الحلقات المدفوعة.`;
              await persist(state,true);
              break;
            }
            if (state.autoMode && result==='manual_open_required')state.navigationBlocked=(state.navigationBlocked||0)+1;
            if ((!state.autoMode && result === 'manual_open_required') || result === 'wrong_page' || (!state.collectOnly&&state.navigationBlocked>=2)) {
              pause(state,state.autoMode?`تعذّر فتح الحلقة ${ep}. أكمل الوصول إليها في الموقع ثم اضغط بدء التنزيل في التطبيق.`:`تعذّر الانتقال إلى الحلقة ${ep} تلقائيًا. افتح الحلقة ${ep} يدويًا في هذا التبويب ثم اختر «استئناف»، أو اختر «تجاوز».`);
              await persist(state);
              break;
            }
            state.current++;
            await persist(state);
            await sleep(450);
            continue;
          }
        }
        if (state.episodeStartedFor !== ep) {
          state.episodeStartedFor = ep;
          state.epStartedAt = Math.max(performance.timeOrigin, Date.now() - 10000);
        }
        state.note=`فحص الحلقة ${ep} (HTML وطلبات HLS)`;
        await persist(state);
        await sleep(500);
        if(cancelled) return;
        await readCurrentPage(state,ep);
        if(cancelled) return;
        const latest=await load();
        if (!state.running) { await persist(state); break; }
        if (!latest?.running) break; // Preserve a STOP command sent during the playback wait.
        if(state.collectOnly){
          const row=state.rows[String(ep)];row.captureId=crypto.randomUUID();
          row.collected=!!row.mediaUrl&&row.playbackStatus==='playback_verified';
          await persist(state,true);
        }
        if(state.patient){
          const row=state.rows[String(ep)];
          if(!row?.mediaUrl){await retryEpisode(state,ep,'لم يتوفر الفيديو بعد');return;}
          row.captureId=crypto.randomUUID();row.awaitingDownload=true;
          await persist(state,true);
          const result=await waitForDownload(state,ep);
          if(result==='cancelled')return;
          if(result==='failed'){await retryEpisode(state,ep,'تعذر التنزيل؛ جارٍ تجديد الرابط');return;}
        }
        state.current++;
        discover(state);
        await persist(state);
        await sleep(1050); // Modest delay; no rapid-fire requests
      }
      const done=await load();
      if (done?.running && done.current > done.total && done.owner===token()) {
        const retry=globalThis.DramaRetryPlan?.plan(done);
        if(retry){
          done.autoPass=retry.pass;done.current=retry.episode;done.recheckOnly=true;
          done.episodeStartedFor=0;done.epStartedAt=0;
          done.note=`إعادة فحص ${retry.remaining} حلقة ناقصة — المحاولة ${retry.pass}/${done.maxPasses}`;
          await persist(done);
          const until=Date.now()+retry.delayMs;
          while(Date.now()<until){if(cancelled)return;await sleep(250);}
          const latest=await load();
          if(cancelled||!latest?.running||latest.owner!==token())return;
          const destination=done.rows[String(retry.episode)]?.pageUrl;
          if(destination?.startsWith('https://www.reelshort.com/')&&C.seriesIdFromUrl(destination)===done.seriesId){
            if(destination.split('?')[0]===location.href.split('?')[0])location.reload();else location.assign(destination);
          }else location.reload();
          return;
        }
        done.running=false; done.paused=false; done.finished=true;
        const missing=globalThis.DramaRetryPlan?.missing(done)||[];
        done.note=done.lockedFrom?`الحلقات من ${done.lockedFrom} غير متاحة لهذا الحساب الآن؛ لن تُعاد تلقائيًا.`:done.patient?'اكتمل تنزيل جميع الحلقات وفحصها.':done.autoMode ? (missing.length?`انتهت المحاولات؛ ${missing.length} حلقة دون رابط تنزيل متاح.`:'اكتشفت روابط جميع الحلقات؛ يتابع التطبيق التنزيل.'):'انتهى الفحص؛ يجري حفظ CSV في التنزيلات.';
        await persist(done);
        if(done.autoMode)return;
        try {
          const result=await chrome.runtime.sendMessage({type:'DRAMA_EXPORT_AUTOMATIC',state:done});
          if (!result?.ok) throw Error(result?.error || 'Auto-export failed');
          done.note='اكتمل الفحص؛ تم إرسال CSV وملف الروابط إلى التنزيلات.';
          await persist(done);
        } catch (_) { done.note='اكتمل الفحص؛ اضغط تصدير CSV من الإضافة.'; await persist(done); }
      }
    } catch (err) {
      const state=await load();
      if (state && state.owner===token()) {
        pause(state,'خطأ: '+String(err?.message || err).slice(0,140));
        recordError(state,state.current,String(err?.message || err).slice(0,140));
        await persist(state);
      }
    } finally { busy=false; }
  }
  async function start(includePaid,waitMs,options={}) {
    if(CHANNEL&&options.channel!==CHANNEL)return {ok:false,error:'wrong_session'};
    const previous=await load();
    if(options.desktopCommandId && previous?.desktopCommandId===options.desktopCommandId)return {ok:true};
    if(busy) return {ok:false,error:'انتظر توقف العملية الحالية أولًا.'};
    if(previous?.running) return {ok:false,error:'أوقف الفحص الجاري أولًا قبل بدء فحص جديد.'};
    cancelled=false;
    const info=C.extractDocument(document,location.href) || {};
    const id=info.seriesId || C.seriesIdFromUrl(location.href);
    const buttons=C.allButtons(document);
    let total=Number(info.total) || (buttons.length ? Math.max(...buttons) : 0);
    for (const btn of document.querySelectorAll('button')) {
      const m=(btn.textContent||'').trim().match(/^([1-9]\d*)\s*[-–]\s*([1-9]\d*)$/);
      if (m) total=Math.max(total,Number(m[2]));
    }
    if (!id) return {ok:false,error:'افتح صفحة مسلسل أو حلقة من ReelShort أولًا.'};
    if (!total || total>150) return {ok:false,error:'لم أجد عدد الحلقات. افتح قائمة الحلقات على الموقع أولًا، ثم أعد المحاولة.'};
    if (!includePaid && !Number(info.paidStart)) return {ok:false,error:'لم أستطع تحديد بداية الحلقات المدفوعة. فعّل خيار فحص صفحات الحلقات المدفوعة إذا كان لديك حق الوصول، أو افتح صفحة حلقة تعرض معلومات المسلسل.'};
    const title=cleanTitle(info.title || document.title).replace(/\s*الحلقات الكاملة.*$/,'').trim() || 'Series';
    const owner=crypto.randomUUID();
    setToken(owner);
    const state={owner,channel:CHANNEL,browserUserAgent:typeof navigator==='object'?navigator.userAgent:'',seriesId:id,title,total,paidStart:Number(info.paidStart)||0,includePaid:!!includePaid,
      waitMs:Math.min(25000,Math.max(3000,Number(waitMs)||9000)),recheckOnly:false,
      current:1,running:true,paused:false,finished:false,note:'بدء فحص روابط الحلقات',rows:{},events:[],knownButtons:buttons.length,
      sourceUrl:location.href,startedAt:new Date().toISOString()};
    if(options.autoMode){
      state.autoMode=true;state.autoPass=1;state.maxPasses=Math.min(3,Math.max(1,Number(options.maxPasses)||3));
      state.retryDelayMs=Number(options.retryDelayMs)||10000;
      state.desktopCommandId=String(options.desktopCommandId||'');
      state.patient=!!options.patient;
      state.collectOnly=!!options.collectOnly;state.lockedFrom=0;
      state.collectTargets=(options.targetEpisodes||[]).filter(n=>Number.isInteger(n)&&n>0&&n<=total);
      if(previous?.seriesId===id){
        state.rows=Object.fromEntries(Object.entries(previous.rows||{}).map(([key,row])=>[key,{...row,mediaUrl:'',captureId:'',awaitingDownload:false,downloaded:false,collected:false,tried:false,attempts:0,verifiedHlsUrl:'',hlsCandidates:[],status:'page_only',playbackStatus:'not_checked'}]));
        // A new run refreshes signed URLs. Missing-only retries within that run
        // still retain all media already found; the app skips completed files.
      }
      if(state.patient){
        for(const ep of options.completedEpisodes||[])if(Number.isInteger(ep)&&ep>0&&ep<=total)state.rows[String(ep)]={...(state.rows[String(ep)]||{}),episode:ep,downloaded:true,status:'downloaded',mediaUrl:''};
        state.current=globalThis.DramaRetryPlan.missing(state)[0]||total+1;
      }
      if(state.collectOnly){
        for(const ep of options.collectedEpisodes||[])if(Number.isInteger(ep)&&ep>0&&ep<=total)state.rows[String(ep)]={...(state.rows[String(ep)]||{}),episode:ep,collected:true,mediaUrl:''};
        state.current=globalThis.DramaRetryPlan.missing(state)[0]||total+1;
      }
    }
    discover(state);
    await persist(state);
    setTimeout(loop,150);
    return {ok:true,total,title,id};
  }
  async function recheckMissing(waitMs) {
    if(busy) return {ok:false,error:'انتظر توقف العملية الحالية أولًا.'};
    const state=await load();
    if(!state || !state.seriesId) return {ok:false,error:'لا يوجد فحص سابق. ابدأ فحصًا عاديًا أولًا.'};
    if(!ownSeries(state.seriesId)) return {ok:false,error:'افتح صفحة نفس المسلسل المفهرس مسبقًا.'};
    if(state.running) return {ok:false,error:'أوقف الفحص الحالي أولًا.'};
    const missing=[];
    for(let n=1;n<=state.total;n++) {
      const row=state.rows[String(n)]||{};
      if(!row.mediaUrl && !(row.playbackStatus==='playback_verified' && (row.hlsCandidates||[]).length)) missing.push(n);
    }
    if(!missing.length) return {ok:false,error:'لا توجد حلقات ناقصة حسب بيانات الفهرس الحالية.'};
    const owner=crypto.randomUUID(); setToken(owner);
    cancelled=false;
    state.owner=owner;state.running=true;state.paused=false;state.finished=false;
    state.recheckOnly=true;state.includePaid=true;
    state.waitMs=Math.min(25000,Math.max(3000,Number(waitMs)||Number(state.waitMs)||9000));
    state.current=missing[0];state.episodeStartedFor=0;state.epStartedAt=0;
    state.note=`إعادة فحص ${missing.length} حلقة ناقصة بدءًا من الحلقة ${missing[0]}`;
    await persist(state);setTimeout(loop,150);
    return {ok:true,missing:missing.length,from:missing[0]};
  }
  async function resume() {
    if(busy) return {ok:false,error:'انتظر توقف العملية الحالية أولًا.'};
    const state = await load();
    if (!state || !state.seriesId) return {ok:false,error:'لا يوجد فحص سابق لاستكماله.'};
    if (!ownSeries(state.seriesId)) return {ok:false,error:'افتح أولًا صفحة حلقة من المسلسل الذي بدأ فحصه، ثم استأنف.'};
    if (state.running) return {ok:true,message:'الفحص يعمل بالفعل.'};
    if (state.finished) return {ok:false,error:'اكتمل الفحص السابق. استخدم بدء فحص جديد لإعادة الفحص.'};
    if (state.current > state.total) return {ok:false,error:'ليس هناك حلقات متبقية.'};
    const currentEp = C.episodeNumber(location.href);
    // If a VIP/modal interrupted navigation, resume only after the user opened the expected episode.
    // For regular pauses (e.g. error), retrying requires an exact episode page as well.
    if (currentEp !== state.current) return {ok:false,error:`افتح الحلقة ${state.current} يدويًا أولًا؛ المعروض الآن ${currentEp || 'صفحة المسلسل'}. أو استخدم تجاوز الحلقة.`};
    setToken(state.owner);
    cancelled=false;
    state.running=true;
    state.paused=false;
    state.epStartedAt=Math.max(performance.timeOrigin,Date.now()-6000);
    state.episodeStartedFor=state.current;
    state.note=`استئناف فحص الحلقة ${state.current}`;
    await persist(state);
    setTimeout(loop,150);
    return {ok:true};
  }
  async function skip() {
    if(busy) return {ok:false,error:'انتظر توقف العملية الحالية أولًا.'};
    const state=await load();
    if (!state || !state.seriesId || state.finished) return {ok:false,error:'لا يوجد فحص متوقف للتجاوز.'};
    if (!ownSeries(state.seriesId)) return {ok:false,error:'عد إلى صفحة المسلسل الأصلي أولًا.'};
    if (state.running) return {ok:false,error:'أوقف الفحص أولًا قبل التجاوز.'};
    if (state.current > state.total) return {ok:false,error:'لا توجد حلقة للتجاوز.'};
    statusRow(state,state.current,'manual_skipped');
    recordError(state,state.current,'manual_skipped');
    state.current++;
    cancelled=false;
    state.paused=false;
    state.running=true;
    setToken(state.owner);
    state.note=`تم تجاوز الحلقة السابقة؛ الانتقال إلى الحلقة ${state.current}`;
    await persist(state);
    setTimeout(loop,150);
    return {ok:true,current:state.current};
  }
  async function captureCurrent() {
    if(busy) return {ok:false,error:'أوقف الفحص الجاري وانتظر توقفه قبل التشخيص اليدوي.'};
    let state=await load();
    if(state?.running) return {ok:false,error:'أوقف الفحص الجاري أولًا.'};
    cancelled=false;
    const ep=C.episodeNumber(location.href),id=C.seriesIdFromUrl(location.href);
    if(!ep||!id) return {ok:false,error:'افتح صفحة حلقة فعلية من ReelShort.'};
    if(!state || state.seriesId!==id) {
      const info=C.extractDocument(document,location.href,id,ep)||{};
      state={seriesId:id,title:cleanTitle(info.title||document.title)||'Series',total:Math.max(ep,Number(info.total)||0),
        paidStart:Number(info.paidStart)||0,rows:{},events:[],current:ep,running:false,paused:false,finished:false,
        waitMs:9000,note:'تشخيص يدوي للحلقة الحالية',startedAt:new Date().toISOString(),sourceUrl:location.href};
    }
    state.total=Math.max(state.total,ep);
    const playback=await P.waitForPlayback(()=>document.querySelector('video'),{timeoutMs:4000,pollMs:250,cancelled:()=>cancelled});
    if(cancelled) return {ok:false,error:'تم إيقاف التشخيص.'};
    if(!playback.ok) return {ok:false,error:'لم يتم التأكد من تقدم التشغيل. شغّل الحلقة ثم اضغط تشخيص مرة أخرى.'};
    const entries=globalThis.DramaHLSObserver.recent(Math.max(performance.timeOrigin,Date.now()-30000));
    const result=P.distinctEpisodeCandidates(entries,state.rows,ep);
    const previous=state.rows[String(ep)]||{};
    state.rows[String(ep)]={...previous,episode:ep,pageUrl:location.href.split('?')[0],
      hlsCandidates:[...new Set([...(previous.hlsCandidates||[]),...result.unique])].slice(0,12),
      hlsObservedAt:result.unique.length?new Date().toISOString():previous.hlsObservedAt||'',
      duplicateHlsCount:result.overlap.length,playbackStatus:'playback_verified',
      sourceType:playback.snapshot.sourceType,playbackDurationSec:playback.snapshot.durationSec,
      playbackObservedAt:new Date().toISOString(),
      status:previous.mediaUrl?'media_found':result.unique.length||previous.hlsCandidates?.length?'playback_verified_hls_unverified':'playback_verified_no_hls',
      checkedAt:new Date().toISOString()};
    state.note=`الحلقة ${ep}: تشغيل مؤكد، ${result.unique.length} مرشح HLS جديد، ${result.overlap.length} متكرر من حلقات أخرى (غير مثبت التطابق).`;
    await persist(state);
    return {ok:true,episode:ep,count:result.unique.length,duplicates:result.overlap.length};
  }

  chrome.runtime.onMessage.addListener((message,sender,respond) => {
    if(message?.type==='DRAMA_SESSION_BIND'){
      if(!/^[a-f\d]{32}$/.test(message.channel||'')||C.seriesIdFromUrl(location.href)!==message.seriesId){respond({ok:false});return;}
      if(CHANNEL!==message.channel){
        if(busy){respond({ok:false,error:'previous_session_still_running'});return;}
        CHANNEL=message.channel;STORE='dramaScannerSession:'+CHANNEL;
        sessionStorage.setItem(CHANNEL_KEY,CHANNEL);setToken('');lastDesktopPublish=0;
      }
      respond({ok:true});return;
    }
    if (message?.type === 'DRAMA_CAPTURE_HLS') {captureCurrent().then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_RECHECK_MISSING') {recheckMissing(message.waitMs).then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_RESUME') {resume().then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_SKIP') {skip().then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_START') {
      start(message.includePaid,message.waitMs,message).then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));
      return true;
    }
    if (message?.type === 'DRAMA_STOP') {
      cancelled=true;
      load().then(async state => {
        if (state && (state.owner === token() || message.autoMode)) {pause(state,message.autoMode?'توقف الفحص. يمكنك بدء التنزيل مجددًا من التطبيق.':'توقف يدويًا. افتح الحلقة المطلوبة لاستئناف الفحص.');await persist(state);}
        if(message.autoMode){
          const until=Date.now()+7500;
          while(busy&&Date.now()<until)await sleep(50);
          if(busy){respond({ok:false,error:'جارٍ إنهاء الفحص السابق؛ أعد المحاولة بعد لحظات.'});return;}
        }
        respond({ok:true});
      });
      return true;
    }
  });
  // Full page navigations automatically resume the same tab's session.
  if (token()) setTimeout(loop,1200);
})();

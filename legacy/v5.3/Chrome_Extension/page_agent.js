/* Episode indexing plus HLS request diagnostics from resource timing. No payment/DRM bypass. */
(() => {
  'use strict';
  if (globalThis.__dramaScanV5Loaded) return;
  globalThis.__dramaScanV5Loaded = true;
  const C = globalThis.DramaScannerCore;
  const STORE = 'dramaScannerV5Current';
  const P=globalThis.DramaPlaybackProbe; // Preserved to keep v5 progress and exports
  const TAB_TOKEN_KEY = '__dramaScannerV5OwnedTab';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let busy = false;
  let cancelled = false;
  

  function token() {
    try { return sessionStorage.getItem(TAB_TOKEN_KEY) || ''; } catch (_) { return ''; }
  }
  function setToken(value) {
    try { sessionStorage.setItem(TAB_TOKEN_KEY,value); } catch (_) { /* private mode */ }
  }
  async function load() {
    return (await chrome.storage.local.get(STORE))[STORE] || null;
  }
  async function persist(state) {
    state.updatedAt = new Date().toISOString();
    await chrome.storage.local.set({[STORE]: state, ['dramaScannerSeries:'+state.seriesId]: state});
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
      episode:ep, pageUrl:url || old.pageUrl || '', mediaUrl:old.mediaUrl || '',
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
          } else if (!response.ok) recordError(state,episode,`HTML metadata HTTP ${response.status}`);
        } finally {clearTimeout(timeout);}
      } catch (_) { /* Metadata may be absent, but video can still play. */ }
    }
    const old=state.rows[String(episode)]||{};
    const newMedia=info && C.mediaUrl(info.mediaUrl) ? info.mediaUrl : '';
    const row=state.rows[String(episode)]={
      ...old,episode,pageUrl:url.split('?')[0],mediaUrl:newMedia||old.mediaUrl||'',
      hlsCandidates:Array.isArray(old.hlsCandidates)?old.hlsCandidates:[],
      checkedAt:new Date().toISOString()
    };
    state.note=`الحلقة ${episode}: انتظار تقدم تشغيل الفيديو (حد أقصى ${Math.round((state.waitMs||9000)/1000)} ثوانٍ)`;
    await persist(state);
    const result=await P.waitForPlayback(()=>document.querySelector('video'),{
      timeoutMs:state.waitMs||9000,pollMs:250,
      cancelled:()=>!ownSeries(state.seriesId)||C.episodeNumber(location.href)!==episode
    });
    row.playbackStatus=result.ok?'playback_verified':result.reason;
    row.sourceType=result.snapshot.sourceType;
    row.playbackDurationSec=result.snapshot.durationSec;
    row.playbackObservedAt=result.ok?new Date().toISOString():old.playbackObservedAt||'';
    if (!result.ok) {
      row.status=row.mediaUrl?'media_found':result.reason;
      recordError(state,episode,result.reason);
      pause(state,`الحلقة ${episode}: لم يثبت بدء التشغيل (${result.reason}). شغّلها بنفسك واضغط «استئناف»، أو تجاوز الحلقة.`);
      await persist(state);
      return;
    }
    // Media requests could be triggered before the first visible frame. Limit to this
    // episode navigation window and explicitly label association as unverified.
    const entryEpoch=(state.episodeStartedFor===episode && state.epStartedAt) ? state.epStartedAt : Date.now()-6000;
    const observationStart=Math.max(performance.timeOrigin,entryEpoch-150);
    await sleep(500);
    const entries=globalThis.DramaHLSObserver.recent(observationStart,Date.now());
    const distinct=P.distinctEpisodeCandidates(entries,state.rows,episode);
    row.duplicateHlsCount=distinct.overlap.length;
    if(distinct.unique.length) {
      row.hlsCandidates=[...new Set([...(old.hlsCandidates||[]),...distinct.unique])].slice(0,12);
      row.hlsObservedAt=new Date().toISOString();
    }
    row.status=row.mediaUrl?'media_found':row.hlsCandidates.length?'playback_verified_hls_unverified':'playback_verified_no_hls';
    row.checkedAt=new Date().toISOString();
    if (info?.title && cleanTitle(info.title).length>3 && (!state.title||state.title==='Series')) state.title=cleanTitle(info.title);
    state.note=`الحلقة ${episode}: ${row.playbackStatus}، مرشحات HLS جديدة ${distinct.unique.length}، مكررة من حلقات أخرى ${distinct.overlap.length}`;
  }
  async function navigateEpisode(state, episode) {
    discover(state);
    const direct = state.rows[String(episode)]?.pageUrl;
    if (direct && C.episodeNumber(direct) === episode && C.seriesIdFromUrl(direct) === state.seriesId) {
      if (direct.split('?')[0] !== location.href.split('?')[0]) {
        location.assign(direct);
        return 'navigating';
      }
      return 'ready';
    }
    let btn = C.findEpisodeButton(document,episode);
    if (!btn) {
      const group = C.findGroupButton(document,episode);
      if (group) { group.click(); await sleep(350); btn = C.findEpisodeButton(document,episode); }
    }
    if (!btn) return 'missing';
    const before = location.href;
    btn.click(); // Only episode selection; NEVER click purchase/unlock controls.
    for (let i=0; i<35; i++) {
      await sleep(100);
      if (location.href !== before) {
        if (C.episodeNumber(location.href) === episode && ownSeries(state.seriesId)) return 'ready';
        return 'wrong_page';
      }
    }
    return 'manual_open_required';
  }
  async function loop() {
    if (busy) return;
    busy=true;
    try {
      let state=await load();
      if (!state?.running || !token() || state.owner !== token()) return;
      draw(state);
      while (state.running && state.current <= state.total) {
        cancelled=false;
        const live = await load();
        if (!live || !live.running || live.owner !== token()) return;
        state=live;
        if (!ownSeries(state.seriesId)) {
          pause(state,'انتقلت الصفحة إلى خارج المسلسل. عد إلى صفحة الحلقة المطلوبة ثم اضغط استئناف.');
          await persist(state); break;
        }
        const ep=state.current;
        if (state.recheckOnly) {
          const existing=state.rows[String(ep)]||{};
          if (existing.mediaUrl || (existing.playbackStatus==='playback_verified' && (existing.hlsCandidates||[]).length)) {
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
          if (result === 'navigating') return; // Full reload resumes in new content script.
          if (result !== 'ready') {
            statusRow(state,ep,result);
            recordError(state,ep,result);
            if (result === 'manual_open_required' || result === 'wrong_page') {
              pause(state,`تعذّر الانتقال إلى الحلقة ${ep} تلقائيًا. افتح الحلقة ${ep} يدويًا في هذا التبويب ثم اختر «استئناف»، أو اختر «تجاوز».`);
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
        await readCurrentPage(state,ep);
        const latest=await load();
        if (!state.running) { await persist(state); break; }
        if (!latest?.running) break; // Preserve a STOP command sent during the playback wait.
        state.current++;
        discover(state);
        await persist(state);
        await sleep(1050); // Modest delay; no rapid-fire requests
      }
      const done=await load();
      if (done?.running && done.current > done.total && done.owner===token()) {
        done.running=false; done.paused=false; done.finished=true;
        done.note='انتهى الفحص؛ يجري حفظ CSV في التنزيلات.';
        await persist(done);
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
  async function start(includePaid,waitMs) {
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
    const state={owner,seriesId:id,title,total,paidStart:Number(info.paidStart)||0,includePaid:!!includePaid,
      waitMs:Math.min(25000,Math.max(3000,Number(waitMs)||9000)),recheckOnly:false,
      current:1,running:true,paused:false,finished:false,note:'بدء فحص روابط الحلقات',rows:{},events:[],knownButtons:buttons.length,
      sourceUrl:location.href,startedAt:new Date().toISOString()};
    discover(state);
    await persist(state);
    setTimeout(loop,150);
    return {ok:true,total,title,id};
  }
  async function recheckMissing(waitMs) {
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
    state.owner=owner;state.running=true;state.paused=false;state.finished=false;
    state.recheckOnly=true;state.includePaid=true;
    state.waitMs=Math.min(25000,Math.max(3000,Number(waitMs)||Number(state.waitMs)||9000));
    state.current=missing[0];state.episodeStartedFor=0;state.epStartedAt=0;
    state.note=`إعادة فحص ${missing.length} حلقة ناقصة بدءًا من الحلقة ${missing[0]}`;
    await persist(state);setTimeout(loop,150);
    return {ok:true,missing:missing.length,from:missing[0]};
  }
  async function resume() {
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
    const state=await load();
    if (!state || !state.seriesId || state.finished) return {ok:false,error:'لا يوجد فحص متوقف للتجاوز.'};
    if (!ownSeries(state.seriesId)) return {ok:false,error:'عد إلى صفحة المسلسل الأصلي أولًا.'};
    if (state.running) return {ok:false,error:'أوقف الفحص أولًا قبل التجاوز.'};
    if (state.current > state.total) return {ok:false,error:'لا توجد حلقة للتجاوز.'};
    statusRow(state,state.current,'manual_skipped');
    recordError(state,state.current,'manual_skipped');
    state.current++;
    state.paused=false;
    state.running=true;
    setToken(state.owner);
    state.note=`تم تجاوز الحلقة السابقة؛ الانتقال إلى الحلقة ${state.current}`;
    await persist(state);
    setTimeout(loop,150);
    return {ok:true,current:state.current};
  }
  async function captureCurrent() {
    let state=await load();
    const ep=C.episodeNumber(location.href),id=C.seriesIdFromUrl(location.href);
    if(!ep||!id) return {ok:false,error:'افتح صفحة حلقة فعلية من ReelShort.'};
    if(!state || state.seriesId!==id) {
      const info=C.extractDocument(document,location.href,id,ep)||{};
      state={seriesId:id,title:cleanTitle(info.title||document.title)||'Series',total:Math.max(ep,Number(info.total)||0),
        paidStart:Number(info.paidStart)||0,rows:{},events:[],current:ep,running:false,paused:false,finished:false,
        waitMs:9000,note:'تشخيص يدوي للحلقة الحالية',startedAt:new Date().toISOString(),sourceUrl:location.href};
    }
    state.total=Math.max(state.total,ep);
    const playback=await P.waitForPlayback(()=>document.querySelector('video'),{timeoutMs:4000,pollMs:250});
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
    if (message?.type === 'DRAMA_CAPTURE_HLS') {captureCurrent().then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_RECHECK_MISSING') {recheckMissing(message.waitMs).then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_RESUME') {resume().then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_SKIP') {skip().then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));return true;}
    if (message?.type === 'DRAMA_START') {
      start(message.includePaid,message.waitMs).then(respond).catch(e=>respond({ok:false,error:String(e?.message||e)}));
      return true;
    }
    if (message?.type === 'DRAMA_STOP') {
      cancelled=true;
      load().then(async state => {
        if (state && state.owner === token()) {pause(state,'توقف يدويًا. افتح الحلقة المطلوبة لاستئناف الفحص.');await persist(state);}
        respond({ok:true});
      });
      return true;
    }
  });
  // Full page navigations automatically resume the same tab's session.
  if (token()) setTimeout(loop,1200);
})();

'use strict';
const KEY='dramaScannerV5Current';
const status=document.getElementById('status');
const error=document.getElementById('error');
let state=null;
async function getActiveTab() {
  const tabs=await chrome.tabs.query({active:true,currentWindow:true});
  return tabs[0]||null;
}
function csvEscape(s) { return '"'+String(s??'').replace(/"/g,'""')+'"'; }
function ordered(st) {
  const result=[];
  for (let i=1;i<=st.total;i++) {
    const row=(st.rows||{})[String(i)]||{};
    result.push({SeriesID:st.seriesId,SeriesTitle:st.title,Episode:i,TotalEpisodes:st.total,PaidStart:st.paidStart,PageURL:row.pageUrl||'',MediaURL:row.mediaUrl||'',Source:row.status||'not_discovered',PlaybackStatus:row.playbackStatus||'not_checked',SourceType:row.sourceType||'',PlaybackDurationSec:row.playbackDurationSec??'',PlaybackObservedAt:row.playbackObservedAt||'',HLSCandidateCount:(row.hlsCandidates||[]).length,HLSDuplicateCount:row.duplicateHlsCount||0,HLSObservedAt:row.hlsObservedAt||'',ObservedAt:row.checkedAt||''});
  }
  return result;
}
function fileName(st,ext) { return `Drama_${st.seriesId}_${st.finished?'complete':'partial'}.${ext}`; }
function exportFile(name,content,mime) {
  const blob=new Blob(['\ufeff'+content],{type:mime});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=name;
  document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
}
async function refresh() {
  state=(await chrome.storage.local.get(KEY))[KEY]||null;
  if (!state) {status.textContent='لا يوجد فحص محفوظ بعد.';return;}
  const active=await getActiveTab();
  const right=active?.url?.includes(state.seriesId);
  const rows=ordered(state);
  const media=rows.filter(r=>r.MediaURL).length;
  const pages=rows.filter(r=>r.PageURL).length;
  const skipped=rows.filter(r=>r.Source==='paid_skipped').length;
  const hls=Object.values(state.rows||{}).filter(r=>(r.hlsCandidates||[]).length).length;
  const played=rows.filter(r=>r.PlaybackStatus==='playback_verified').length;
  const pending=rows.filter(r=>['playback_not_started','video_element_missing'].includes(r.PlaybackStatus)).length;
  status.textContent=`${state.title}\nالمسلسل: ${state.seriesId}\nالحلقات: ${state.total} | روابط الصفحات: ${pages} | روابط الفيديو من HTML: ${media} | حلقات بها طلبات HLS مرصودة: ${hls}\nتشغيل موثّق: ${played} | لم يبدأ التشغيل: ${pending}\nالتقدم: ${Math.min(state.current,state.total)}/${state.total}\n${state.running?'جارٍ الفحص':state.finished?'اكتمل الفحص':state.paused?'الفحص متوقف مؤقتًا':'الفحص متوقف'} — ${state.note||''}${skipped?'\nتم تجاوز '+skipped+' حلقة بسبب تعطيل خيار VIP':''}${!right?'\nتنبيه: التبويب الحالي ليس المسلسل المسجّل.':''}`;
}
document.getElementById('import').addEventListener('click',async()=>{
  error.textContent='';
  try {
    const file=document.getElementById('priorIndex').files?.[0];
    if(!file)throw Error('اختر ملف series_index.csv أو complete.csv أولًا.');
    const tab=await getActiveTab();if(!tab)throw Error('افتح مسلسل ReelShort في التبويب النشط.');
    const imported=DramaCSVImporter.loadIndex(await file.text(),tab.url);
    const saved=(await chrome.storage.local.get(KEY))[KEY];
    if(saved?.running)throw Error('أوقف الفحص الجاري قبل الاستيراد.');
    // Preserve richer data if reimporting into an existing record for same series.
    if(saved?.seriesId===imported.seriesId){
      for(const [n,row] of Object.entries(saved.rows||{})){
        const fromFile=imported.rows[n]||{};
        imported.rows[n]={...fromFile,...row,
          pageUrl:row.pageUrl||fromFile.pageUrl||'',
          mediaUrl:row.mediaUrl||fromFile.mediaUrl||'',
          hlsCandidates:row.hlsCandidates||[]};
      }
    }
    await chrome.storage.local.set({[KEY]:imported,['dramaScannerSeries:'+imported.seriesId]:imported});
    await refresh();
  }catch(e){error.textContent=String(e?.message||e);}
});
document.getElementById('importHls').addEventListener('click',async()=>{
  error.textContent='';
  try{
    const file=document.getElementById('priorHls').files?.[0];
    if(!file)throw Error('اختر ملف HLS_CANDIDATES_UNVERIFIED.csv.');
    const old=(await chrome.storage.local.get(KEY))[KEY];
    if(!old)throw Error('استورد فهرس الحلقات أولًا.');
    if(old.running)throw Error('أوقف الفحص الجاري أولًا.');
    const count=DramaCSVImporter.mergeCandidates(await file.text(),old);
    old.note=`أُضيف ${count} رابط HLS مرشح سابق (غير مؤكّد).`;
    await chrome.storage.local.set({[KEY]:old,['dramaScannerSeries:'+old.seriesId]:old});
    await refresh();
  }catch(e){error.textContent=String(e?.message||e);}
});
document.getElementById('start').addEventListener('click',async()=>{
  error.textContent='';
  try {
    const tab=await getActiveTab();
    if (!tab?.url?.startsWith('https://www.reelshort.com/')) throw Error('افتح ReelShort في التبويب النشط أولًا.');
    const includePaid=document.getElementById('paid').checked;
    const waitMs=Number(document.getElementById('waitMs').value)||10000;
    const answer=await chrome.tabs.sendMessage(tab.id,{type:'DRAMA_START',includePaid,waitMs});
    if (!answer?.ok) throw Error(answer?.error||'تعذر بدء الفحص. حدث الصفحة F5 ثم جرّب.');
    await refresh();
  } catch(e){error.textContent=String(e?.message||e);}
});
document.getElementById('stop').addEventListener('click',async()=>{
  error.textContent='';
  try { const tab=await getActiveTab(); const res=await chrome.tabs.sendMessage(tab.id,{type:'DRAMA_STOP'}); await refresh(); }
  catch(e){error.textContent=String(e?.message||e);}
});
async function runAction(type) {
  error.textContent='';
  try {
    const tab=await getActiveTab();
    if(!tab?.url?.startsWith('https://www.reelshort.com/')) throw Error('افتح صفحة ReelShort الخاصة بنفس المسلسل أولًا.');
    const result=await chrome.tabs.sendMessage(tab.id,{type});
    if(!result?.ok) throw Error(result?.error||'تعذّر إكمال العملية.');
    await refresh();
  } catch(e) {error.textContent=String(e?.message||e);}
}
document.getElementById('recheck').addEventListener('click',async()=>{
  error.textContent='';
  try{const tab=await getActiveTab();const waitMs=Number(document.getElementById('waitMs').value)||10000;
    const result=await chrome.tabs.sendMessage(tab.id,{type:'DRAMA_RECHECK_MISSING',waitMs});
    if(!result?.ok) throw Error(result?.error||'تعذر إعادة الفحص.');await refresh();
  }catch(e){error.textContent=String(e?.message||e);}
});
document.getElementById('resume').addEventListener('click',()=>runAction('DRAMA_RESUME'));
document.getElementById('skip').addEventListener('click',()=>runAction('DRAMA_SKIP'));
document.getElementById('csv').addEventListener('click',()=>{
  if(!state){error.textContent='لا يوجد فحص لتصديره.';return;}
  const keys=['SeriesID','SeriesTitle','Episode','TotalEpisodes','PaidStart','PageURL','MediaURL','Source','PlaybackStatus','SourceType','PlaybackDurationSec','PlaybackObservedAt','HLSCandidateCount','HLSDuplicateCount','HLSObservedAt','ObservedAt'];
  const rows=ordered(state);
  const csv=[keys.join(','),...rows.map(row=>keys.map(k=>csvEscape(row[k])).join(','))].join('\r\n');
  exportFile(fileName(state,'csv'),csv,'text/csv;charset=utf-8');
});
document.getElementById('txt').addEventListener('click',()=>{
  if(!state){error.textContent='لا يوجد فحص لتصديره.';return;}
  const rows=ordered(state).filter(row=>row.MediaURL);
  if(!rows.length){error.textContent='لم تُكتشف روابط فيديو صالحة.';return;}
  const txt=rows.map(row=>row.MediaURL).join('\r\n');
  exportFile(fileName(state,'txt'),txt,'text/plain;charset=utf-8');
});
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes[KEY]) refresh();});
refresh().catch(e=>{error.textContent=String(e?.message||e);});


document.getElementById('capture').addEventListener('click',async()=>{
  await runAction('DRAMA_CAPTURE_HLS');
});
document.getElementById('candidates').addEventListener('click',()=>{
  if (!state) {error.textContent='لا يوجد فهرس.';return;}
  const rows=[];
  for(let i=1; i<=state.total; i++) {
    const r=(state.rows||{})[String(i)] || {};
    for (const link of (r.hlsCandidates||[])) rows.push({Episode:i, HLSCandidateURL:link, ObservedAt:r.hlsObservedAt||''});
  }
  if (!rows.length) {error.textContent='لا توجد أي طلبات HLS مرصودة بعد. افتح الحلقة وشغّلها ثم اضغط تشخيص HLS.';return;}
  const keys=['Episode','HLSCandidateURL','ObservedAt'];
  const csv=[keys.join(','),...rows.map(row=>keys.map(k=>csvEscape(row[k])).join(','))].join('\r\n');
  exportFile(`Drama_${state.seriesId}_HLS_CANDIDATES_UNVERIFIED.csv`,csv,'text/csv;charset=utf-8');
});

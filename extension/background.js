'use strict';
importScripts('export_core.js', 'session_bridge.js', 'desktop_bridge.js');
const {csvEscape, safeName} = DramaExportCore;
function records(state) {
  const data=[];
  for (let n=1;n<=state.total;n++) {
    const r=state.rows[String(n)] || {};
    data.push({
      SeriesID:state.seriesId,SeriesTitle:state.title,Episode:n,TotalEpisodes:state.total,
      PaidStart:state.paidStart,PageURL:r.pageUrl||'',MediaURL:r.mediaUrl||'',
      Source:r.status||'not_discovered',PlaybackStatus:r.playbackStatus||'not_checked',SourceType:r.sourceType||'',PlaybackDurationSec:r.playbackDurationSec??'',PlaybackObservedAt:r.playbackObservedAt||'',HLSCandidateCount:(r.hlsCandidates||[]).length,HLSDuplicateCount:r.duplicateHlsCount||0,HLSObservedAt:r.hlsObservedAt||'',ObservedAt:r.checkedAt||''
    });
  }
  return data;
}
function csv(state) {
  const keys=['SeriesID','SeriesTitle','Episode','TotalEpisodes','PaidStart','PageURL','MediaURL','Source','PlaybackStatus','SourceType','PlaybackDurationSec','PlaybackObservedAt','HLSCandidateCount','HLSDuplicateCount','HLSObservedAt','ObservedAt'];
  return '\ufeff'+[keys.join(','),...records(state).map(r=>keys.map(k=>csvEscape(r[k])).join(','))].join('\r\n');
}
async function save(state) {
  if (!state || !/^[a-f\d]{24}$/i.test(state.seriesId) || !Number.isInteger(state.total) || state.total>150 || state.total<1 || !state.rows || typeof state.rows!=='object') throw Error('Invalid scan');
  if(state.autoMode)return ReelDesktop.publish(state);
  const pathname=`Drama_Scanner/${safeName(state.title)}_${state.seriesId}/series_index.csv`;
  const uri='data:text/csv;charset=utf-8,'+encodeURIComponent(csv(state));
  const csvId=await chrome.downloads.download({url:uri,filename:pathname,conflictAction:'overwrite',saveAs:false});
  // Separate file: network-observed URLs are candidates, never auto-treated as verified episode media.
  const candidateRows = [];
  for (let n=1; n<=state.total; n++) {
    const row=state.rows[String(n)] || {};
    for (const url of (row.hlsCandidates || [])) {
      candidateRows.push([state.seriesId, state.title,n,url,row.hlsObservedAt||'']);
    }
  }
  if (candidateRows.length) {
    const fields=['SeriesID','SeriesTitle','Episode','HLSCandidateURL','ObservedAt'];
    const text=fields.join(',')+'\r\n'+candidateRows.map(r=>r.map(csvEscape).join(',')).join('\r\n');
    const file=`Drama_Scanner/${safeName(state.title)}_${state.seriesId}/hls_candidates_UNVERIFIED.csv`;
    const data='data:text/csv;charset=utf-8,'+encodeURIComponent('\ufeff'+text);
    await chrome.downloads.download({url:data,filename:file,conflictAction:'overwrite',saveAs:false});
  }
  const video=records(state).filter(r=>r.MediaURL).map(r=>r.MediaURL);
  if (video.length) {
    const txtPath=`Drama_Scanner/${safeName(state.title)}_${state.seriesId}/media_urls.txt`;
    const txtUri='data:text/plain;charset=utf-8,'+encodeURIComponent(video.join('\r\n'));
    try { await chrome.downloads.download({url:txtUri,filename:txtPath,conflictAction:'overwrite',saveAs:false}); }
    catch (e) { /* CSV still exported, TXT can be exported from popup. */ }
  }
  return csvId;
}
chrome.runtime.onMessage.addListener((message,sender,reply) => {
  if (message?.type !== 'DRAMA_EXPORT_AUTOMATIC') return;
  if (!sender.tab || !sender.tab.url?.startsWith('https://www.reelshort.com/')) {
    reply({ok:false,error:'Invalid origin'}); return;
  }
  save(message.state).then(id=>reply({ok:true,id})).catch(err=>reply({ok:false,error:String(err)}));
  return true;
});

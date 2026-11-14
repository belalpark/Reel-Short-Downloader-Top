/* Local CSV importer for migration. Never sends uploaded files to the network. */
((root)=>{
 'use strict';
 function parseCSV(text) {
   const s=String(text||'').replace(/^\uFEFF/,'');
   if(s.length>5*1024*1024)throw Error('ملف CSV أكبر من الحد المسموح (5 ميجابايت).');
   const rows=[];let row=[],value='',quoted=false;
   for(let i=0;i<s.length;i++){
     const ch=s[i];
     if(ch==='"') {
       if(quoted && s[i+1]==='"'){value+='"';i++;}
       else quoted=!quoted;
     } else if(ch===','&&!quoted){row.push(value);value='';}
     else if((ch==='\r'||ch==='\n')&&!quoted){
       if(ch==='\r'&&s[i+1]==='\n') i++;
       row.push(value);value='';
       if(row.some(x=>x!=='')) rows.push(row);
       row=[];
     } else value+=ch;
   }
   if(quoted)throw Error('CSV يحتوي علامات اقتباس غير مغلقة.');
   row.push(value);if(row.some(x=>x!==''))rows.push(row);
   if(!rows.length)return [];
   const head=rows.shift().map(x=>x.trim());
   if(new Set(head).size!==head.length)throw Error('أسماء أعمدة CSV مكررة.');
   return rows.map(columns=>{
     if(columns.length!==head.length)throw Error('عدد أعمدة CSV غير متسق.');
     return Object.fromEntries(head.map((h,i)=>[h,columns[i]||'']));
   });
 }
 function validMedia(raw){
   try{const u=new URL(raw);return u.protocol==='https:'&&!u.username&&!u.password&&/\.(m3u8|mp4|m4v|mov|mpd|webm)$/i.test(u.pathname)&&!/(?:^|\/)(undefined|null|none|nan)(?:\/|$)/i.test(u.pathname)?u.href:'';}
   catch(_){return '';}
 }
 function loadIndex(text,tabUrl){
   const rows=parseCSV(text);
   if(!rows.length||!rows.every(r=>r.SeriesID&&r.Episode&&r.PageURL!==undefined))throw Error('ملف فهرس CSV غير صالح أو ناقص.');
   const id=String(rows[0].SeriesID||'').toLowerCase(), total=Number(rows[0].TotalEpisodes);
   if(!/^[0-9a-f]{24}$/.test(id)||!Number.isInteger(total)||total<1||total>150) throw Error('بيانات المسلسل غير صالحة.');
   if(!rows.every(r=>String(r.SeriesID).toLowerCase()===id)) throw Error('CSV يحتوي عدة مسلسلات.');
   if(!rows.every(r=>Number(r.TotalEpisodes)===total))throw Error('عدد الحلقات غير متسق داخل الفهرس.');
   const tab=new URL(tabUrl);
   if(tab.protocol!=='https:'||tab.host!=='www.reelshort.com'||!decodeURIComponent(tab.pathname).includes(id))throw Error('افتح صفحة نفس المسلسل داخل ReelShort قبل استيراد الفهرس.');
   const out={}, seen=new Set();
   for(const r of rows){
     const ep=Number(r.Episode);
     if(!Number.isInteger(ep)||ep<1||ep>total||seen.has(ep))throw Error('رقم حلقة مكرر أو خارج المسلسل: '+r.Episode);
     seen.add(ep);
     let url='';
     try{
     const u=new URL(r.PageURL);
       if(u.protocol==='https:'&&u.host==='www.reelshort.com'&&!u.username&&!u.password&&decodeURIComponent(u.pathname).includes(id) &&
          new RegExp('/episodes/episode-'+ep+'(?:-|/|$)','i').test(u.pathname)) url=u.origin+u.pathname;
     }catch(_){}
     const media=validMedia(r.MediaURL||'');
     out[String(ep)]={episode:ep,pageUrl:url,mediaUrl:media,hlsCandidates:[],
       status:media?'media_found':r.Source||'page_only',
       playbackStatus:r.PlaybackStatus||'not_checked',
       sourceType:r.SourceType||'',
       playbackDurationSec:Number(r.PlaybackDurationSec)||null,
       playbackObservedAt:r.PlaybackObservedAt||'',
       checkedAt:r.ObservedAt||'',hlsObservedAt:r.HLSObservedAt||''};
   }
   return {seriesId:id,title:rows[0].SeriesTitle||'Series',total,paidStart:Number(rows[0].PaidStart)||0,
     rows:out,current:total+1,running:false,paused:false,finished:true,includePaid:true,waitMs:10000,
     note:`تم استيراد ${seen.size} حلقة من CSV سابق. اختر «إعادة فحص الحلقات الناقصة فقط».`,sourceUrl:tabUrl};
 }
 function mergeCandidates(text, state){
   const rows=parseCSV(text);
   if(!rows.length)return 0;
   if(!rows.every(r=>r.Episode&&r.HLSCandidateURL!==undefined))throw Error('هذا ليس ملف مرشحات HLS صالحًا.');
   if(rows.some(item=>item.SeriesID&&String(item.SeriesID).toLowerCase()!==state.seriesId))throw Error('ملف مرشحات من مسلسل مختلف.');
   let n=0;
   for(const item of rows){
     if(item.SeriesID&&String(item.SeriesID).toLowerCase()!==state.seriesId)throw Error('ملف مرشحات من مسلسل مختلف.');
     const ep=Number(item.Episode),link=validMedia(item.HLSCandidateURL||'');
     if(!Number.isInteger(ep)||ep<1||ep>state.total||!link||!/\.m3u8$/i.test(new URL(link).pathname))continue;
     const row=state.rows[String(ep)]||{episode:ep,pageUrl:'',mediaUrl:'',status:'hls_candidates_unverified'};
     const previous=new Set(row.hlsCandidates||[]);
     if(!previous.has(link)){previous.add(link);n++;}
     row.hlsCandidates=[...previous].slice(0,12);row.hlsObservedAt=item.ObservedAt||row.hlsObservedAt||'';
     state.rows[String(ep)]=row;
   }
   return n;
 }
 root.DramaCSVImporter=Object.freeze({parseCSV,loadIndex,mergeCandidates});
})(globalThis);

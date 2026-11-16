/* Crawl the website's rendered catalog/shelf links in one owned tab. */
(()=>{
 'use strict';
 let busy=false,cancelled=false;
 const KEY='reelLibraryCatalogJob',sleep=ms=>new Promise(r=>setTimeout(r,ms));
 async function active(id){const value=(await chrome.storage.local.get(KEY))[KEY];return !cancelled&&value?.running&&value.id===id;}
 function collect(){return DramaLibraryCatalog.extract([...document.querySelectorAll('a[href]')].map(a=>({href:a.href,title:a.querySelector('img')?.alt||a.innerText||a.getAttribute('aria-label')||''})),location.href);}
 async function run(){
  if(busy)return {ok:true};busy=true;cancelled=false;
  try{
   const job=(await chrome.storage.local.get(KEY))[KEY];if(!job?.running)return {ok:true};
   let stable=0,previous=-1;const found=new Map();
   job.visited=job.visited||[];job.pages=job.pages||[];job.seen=job.seen||[];
   for(let pass=0;pass<80&&await active(job.id);pass++){
    const data=collect();for(const item of data.series)if(!job.seen.includes(item.id)&&found.size+job.seen.length<1000)found.set(item.id,item);
    for(const url of data.shelves)if(!job.pages.includes(url)&&!job.visited.includes(url)&&job.pages.length+job.visited.length<40)job.pages.push(url);
    const count=found.size;stable=count===previous?stable+1:0;previous=count;
    const bottom=window.scrollY+innerHeight>=document.documentElement.scrollHeight-30;
    if(bottom&&stable>=3){
     const more=[...document.querySelectorAll('button')].find(b=>!b.disabled&&/^(?:Load more|Show more|عرض المزيد|تحميل المزيد)$/i.test((b.innerText||'').trim()));
     if(more){more.click();stable=0;}else break;
    }
    window.scrollBy(0,Math.max(500,innerHeight*0.85));await sleep(250);
   }
   if(!await active(job.id))return {ok:true};
   const items=[...found.values()];
   for(let i=0;i<items.length;i+=150){
    if(!await active(job.id))return {ok:true};
    const response=await chrome.runtime.sendMessage({type:'DRAMA_LIBRARY_CATALOG',jobId:job.id,items:items.slice(i,i+150)});
    if(!response?.ok)throw Error('تعذر حفظ الفهرس؛ استخدم إعادة الربط والفحص.');
   }
   job.seen=[...new Set([...job.seen,...items.map(x=>x.id)])];
   const here=DramaLibraryCatalog.page(location.href,location.href);if(!job.visited.includes(here))job.visited.push(here);
   job.pages=job.pages.filter(p=>!job.visited.includes(p));
   if(!await active(job.id))return {ok:true};
   const heartbeat=await chrome.runtime.sendMessage({type:'DRAMA_LIBRARY_CATALOG',jobId:job.id,items:[]});
   if(!heartbeat?.ok)throw Error('تعذر متابعة الفهرس؛ استخدم إعادة الربط والفحص.');
   if(job.pages.length&&job.seen.length<1000){const next=job.pages.shift();await chrome.storage.local.set({[KEY]:job});if(await active(job.id))location.assign(next);}
   else{
    await chrome.runtime.sendMessage({type:'DRAMA_LIBRARY_CATALOG',jobId:job.id,items:[],done:true});
    job.running=false;await chrome.storage.local.set({[KEY]:job});
   }
   return {ok:true};
  }catch(error){
   const job=(await chrome.storage.local.get(KEY))[KEY];
   if(job?.running&&!cancelled){job.running=false;await chrome.storage.local.set({[KEY]:job});try{await chrome.runtime.sendMessage({type:'DRAMA_LIBRARY_CATALOG',jobId:job.id,items:[],done:true,error:String(error.message||error)});}catch(_){}}
   return {ok:false};
  }finally{busy=false;}
 }
 chrome.runtime.onMessage.addListener((message,_sender,reply)=>{
  if(message?.type==='DRAMA_LIBRARY_ENTRY'){
   const links=[...document.querySelectorAll('a[href]')].filter(a=>DramaLibraryCatalog.extract([{href:a.href}],location.href).series.some(s=>s.id===message.seriesId));
   const first=links.find(a=>/\/episodes\/episode-1(?:[-/]|$)/.test(a.href))||links.find(a=>/\/episodes\/episode-\d+/.test(a.href))||links.find(a=>/\/full-episodes\//.test(a.href));
   reply(first?{ok:true,pageUrl:DramaLibraryCatalog.page(first.href,location.href)}:{ok:false});
  }
  if(message?.type==='DRAMA_LIBRARY_DISCOVER'){run();reply({ok:true});}
  if(message?.type==='DRAMA_LIBRARY_STOP'){cancelled=true;chrome.storage.local.get(KEY).then(async value=>{const job=value[KEY];if(job){job.running=false;await chrome.storage.local.set({[KEY]:job});}reply({ok:true});});return true;}
 });
})();

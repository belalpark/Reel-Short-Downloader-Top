/* Normal page links only: no private APIs, guessed IDs or payment actions. */
((root)=>{
 'use strict';
 function page(value,base){try{const u=new URL(value,base);return u.protocol==='https:'&&u.host==='www.reelshort.com'?u.origin+u.pathname:'';}catch(_){return '';}}
 function extract(links,base){
  const series=new Map(),shelves=new Set();
  for(const link of links){
   const url=page(link.href,base);if(!url)continue;
   let path;try{path=decodeURIComponent(new URL(url).pathname);}catch(_){continue;}
   if(/\/(?:[a-z]{2}\/)?shelf\//.test(path)){shelves.add(url);continue;}
   if(!/\/(?:movie|full-episodes)\/|\/episodes\/episode-\d+(?:[-/]|$)/.test(path))continue;
   const match=path.match(/(?:^|[-/])([a-f\d]{24})(?:[-/]|$)/i);if(!match)continue;
   const id=match[1].toLowerCase(),title=String(link.title||'').trim().split('\n')[0].slice(0,250)||'Series';
   const old=series.get(id);
   if(!old||/\/episode-1-/.test(path))series.set(id,{id,title,pageUrl:url});
  }
  return {series:[...series.values()],shelves:[...shelves]};
 }
 root.DramaLibraryCatalog=Object.freeze({page,extract});
})(globalThis);

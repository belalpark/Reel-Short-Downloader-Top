/* Finite retry planning. Only explicit media URLs count as downloadable. */
((root)=>{
  'use strict';
  function missing(state) {
    const result=[];
    for(let n=1;n<=state.total;n++)if(state.patient?!state.rows?.[String(n)]?.downloaded:!state.rows?.[String(n)]?.mediaUrl)result.push(n);
    return result;
  }
  function plan(state) {
    const remaining=missing(state);
    const pass=Math.max(1,Number(state.autoPass)||1);
    const max=Math.min(3,Math.max(1,Number(state.maxPasses)||3));
    if(!state.autoMode||!remaining.length||pass>=max||state.rateLimited)return null;
    const base=Math.max(3000,Math.min(30000,Number(state.retryDelayMs)||10000));
    return {episode:remaining[0],pass:pass+1,delayMs:Math.min(60000,base*2**(pass-1)),remaining:remaining.length};
  }
  function sameEpisode(state,episode) {
    const attempts=Math.max(1,Number(state.rows?.[String(episode)]?.attempts)||1);
    const delayMs=state.rateLimited?60000:Math.min(60000,3000*2**Math.min(5,attempts-1));
    return {episode,attempts,delayMs};
  }
  root.DramaRetryPlan=Object.freeze({missing,plan,sameEpisode});
})(globalThis);

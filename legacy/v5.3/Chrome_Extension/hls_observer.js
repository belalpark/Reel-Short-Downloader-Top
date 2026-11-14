/* Read-only observation of HLS resource timing entries for THIS ReelShort tab.
   Does not read headers, cookies, keys, response bodies, or DRM internals. */
((root) => {
  'use strict';
  function isHLS(s) {
    try {
      const u = new URL(String(s || ''));
      return u.protocol === 'https:' &&
        (u.hostname === 'v-mps.crazymaplestudios.com' || u.hostname.endsWith('.crazymaplestudios.com')) &&
        /\.m3u8$/i.test(u.pathname) && !/\/(?:undefined|null|nan)\b/i.test(u.pathname);
    } catch (_) { return false; }
  }
  function fromEntries(entries, minEpoch, maxEpoch = Date.now()) {
    const seen = new Map();
    for (const e of (entries || [])) {
      if (!e || !isHLS(e.name)) continue;
      const start = Number(e.absoluteStart) || (Number(e.timeOrigin || 0) + Number(e.startTime || 0));
      // For real PerformanceResourceTiming objects timeOrigin must be injected by the caller.
      if (!(start >= minEpoch - 200 && start <= maxEpoch + 2000)) continue;
      const item = {url:String(e.name), observedAt:new Date(start).toISOString()};
      if (!seen.has(item.url)) seen.set(item.url,item);
    }
    return [...seen.values()].slice(0,25);
  }
  function recent(sinceEpoch, endEpoch = Date.now()) {
    const origin = Number(performance.timeOrigin) || (Date.now() - performance.now());
    const resources = performance.getEntriesByType('resource').map(e => ({
      name:e.name, startTime:e.startTime, timeOrigin:origin
    }));
    return fromEntries(resources,sinceEpoch,endEpoch);
  }
  root.DramaHLSObserver = Object.freeze({isHLS,fromEntries,recent});
})(globalThis);

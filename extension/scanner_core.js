/* Pure parsing utilities. Does not read passwords, cookies, tokens or hidden API traffic. */
((root) => {
  'use strict';
  function str(x) { return typeof x === 'string' ? x.trim() : ''; }
  function mediaUrl(value) {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value.trim())) return '';
    try {
      const u = new URL(value.trim());
      if (!['http:', 'https:'].includes(u.protocol)) return '';
      if (u.pathname.split('/').some(p => /^(undefined|null|nan|none)$/i.test(p))) return '';
      return /\.(?:m3u8|mp4|m4v|mov|webm|mpd)$/i.test(u.pathname) ? u.href : '';
    } catch (_) { return ''; }
  }
  function episodeNumber(url) {
    try {
      const p = new URL(url).pathname;
      const m = p.match(/\/episodes\/episode-(\d+)(?=[-\/]|$)/i);
      return m ? Number(m[1]) : 0;
    } catch (_) { return 0; }
  }
  function seriesIdFromUrl(url) {
    try {
      const p = decodeURIComponent(new URL(url).pathname);
      const m = p.match(/(?:^|[-\/])([a-f\d]{24})(?:[-\/]|$)/i);
      return m ? m[1].toLowerCase() : '';
    } catch (_) { return ''; }
  }
  function loadNext(doc) {
    const tag = doc.getElementById('__NEXT_DATA__');
    if (!tag) return {};
    try { return JSON.parse(tag.textContent || '{}'); } catch (_) { return {}; }
  }
  function findJsonLd(doc) {
    const found = [];
    for (const el of doc.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const obj = JSON.parse(el.textContent || '{}');
        const graph = Array.isArray(obj) ? obj : (Array.isArray(obj['@graph']) ? obj['@graph'] : [obj]);
        for (const item of graph) if (item && (item['@type'] === 'VideoObject' || (Array.isArray(item['@type']) && item['@type'].includes('VideoObject')))) found.push(item);
      } catch (_) { /* malformed third-party JSON-LD */ }
    }
    return found;
  }
  function extractDocument(doc, url, expectedId='', expectedEpisode=0) {
    const documentUrl = new URL(url);
    if (documentUrl.hostname !== 'www.reelshort.com' || documentUrl.protocol !== 'https:') return null;
    const next = loadNext(doc);
    const props = next?.props?.pageProps || {};
    const data = props.data || {};
    const urlId = seriesIdFromUrl(url);
    const dataId = str(data.book_id) || str(props.book_id);
    const sameBook = !urlId || !dataId || dataId.toLowerCase() === urlId;
    const episode = episodeNumber(url) || (sameBook ? Number(data.serial_number) || Number(props.serial_number) : 0) || 0;
    const seriesId = urlId || dataId;
    if (!/^[a-f\d]{24}$/i.test(seriesId)) return null;
    if (expectedId && expectedId.toLowerCase() !== seriesId.toLowerCase()) return null;
    if (urlId && urlId !== seriesId.toLowerCase()) return null;
    if (expectedEpisode && expectedEpisode !== episode) return null;
    const sameEpisode = sameBook && (Number(data.serial_number) === episode) && (episode > 0);
    let video = sameEpisode ? mediaUrl(data.video_url) : '';
    if (!video && episode > 0 && sameBook) {
      for (const item of findJsonLd(doc)) {
        const entryEp = item.name && String(item.name).match(/(?:\bEpisode|الحلقة)\s*(\d+)/i);
        // Avoid inheriting stale media URL from another episode.
        if (entryEp && Number(entryEp[1]) !== episode) continue;
        if (!sameEpisode && !entryEp) continue;
        video = mediaUrl(item.contentUrl);
        if (video) break;
      }
    }
    const title = (sameBook ? str(data.book_title) || str(props.book_title) : '') || str(doc.title).replace(/\s*\|\s*ReelShort.*$/i, '').replace(/^(?:الحلقة|Episode)\s*\d+\s*[-–:]\s*/i, '') || 'Series';
    return {
      seriesId: seriesId.toLowerCase(), title, episode,
      total: sameBook ? Number(data.chapter_count) || 0 : 0,
      paidStart: sameBook ? Number(data.paid_start) || 0 : 0,
      metadataStale: !sameBook || !sameEpisode,
      pageUrl: documentUrl.origin + documentUrl.pathname,
      mediaUrl: video,
      source: video ? 'page_metadata' : 'page_only'
    };
  }
  function readHTML(html, url, id='', episode=0) {
    const parser = new DOMParser();
    return extractDocument(parser.parseFromString(html, 'text/html'),url,id,episode);
  }
  function allButtons(doc) {
    const nums = new Set();
    for (const btn of doc.querySelectorAll('button[aria-label]')) {
      const label = (btn.getAttribute('aria-label') || '').trim();
      const m = label.match(/^(?:الحلقة|Episode|EP)\s*([1-9]\d*)$/i);
      if (m) nums.add(Number(m[1]));
    }
    for (const a of doc.querySelectorAll('a[href*="/episodes/episode-"]')) {
      const ep = episodeNumber(a.href);
      if (ep) nums.add(ep);
    }
    return Array.from(nums).sort((a,b) => a-b);
  }
  function findEpisodeButton(doc, episode) {
    for (const btn of doc.querySelectorAll('button[aria-label]')) {
      const m = (btn.getAttribute('aria-label') || '').trim().match(/^(?:الحلقة|Episode|EP)\s*([1-9]\d*)$/i);
      if (m && Number(m[1]) === episode) return btn;
    }
    return null;
  }
  function findGroupButton(doc, episode) {
    for (const btn of doc.querySelectorAll('button')) {
      const text = (btn.textContent || '').trim();
      const m = text.match(/^([1-9]\d*)\s*[-–]\s*([1-9]\d*)$/);
      if (m && Number(m[1]) <= episode && episode <= Number(m[2])) return btn;
    }
    return null;
  }
  root.DramaScannerCore = Object.freeze({ mediaUrl, episodeNumber, seriesIdFromUrl, readHTML, extractDocument, allButtons, findEpisodeButton, findGroupButton, loadNext });
})(globalThis);

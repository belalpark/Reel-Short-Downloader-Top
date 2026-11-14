/* Explicit, paired readiness only. This channel cannot start downloads or read files. */
(() => {
  'use strict';
  const KEY = 'reelShortDesktopPairV1';
  const BASE = 'http://127.0.0.1:18765';
  let inFlight = null;
  const identity = () => ({version: chrome.runtime.getManifest().version, extensionId: chrome.runtime.id});
  async function request(path, body, token = '') {
    const response = await fetch(BASE + path, {
      method: 'POST', mode: 'cors', credentials: 'omit', cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})},
      body: JSON.stringify({...identity(), ...body}), signal: AbortSignal.timeout(4000)
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'connection_failed');
    return data;
  }
  async function heartbeat() {
    if (inFlight) return inFlight;
    inFlight = (async () => {
      const stored = (await chrome.storage.local.get(KEY))[KEY];
      if (!stored?.token) return {ok: false, error: 'pairing_required'};
      return request('/heartbeat', {}, stored.token);
    })();
    try { return await inFlight; } finally { inFlight = null; }
  }
  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (!['DRAMA_DESKTOP_PAIR', 'DRAMA_DESKTOP_STATUS'].includes(message?.type)) return;
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) {
      reply({ok:false, error:'popup_required'}); return;
    }
    (async () => {
      if (message.type === 'DRAMA_DESKTOP_PAIR') {
        if (!/^\d{8}$/.test(String(message.code || ''))) throw Error('invalid_code');
        const data = await request('/pair', {code: String(message.code)});
        if (!data.token) throw Error('missing_token');
        await chrome.storage.local.set({[KEY]: {token: data.token}});
        return {ok:true, browser:data.browser};
      }
      return heartbeat();
    })().then(reply).catch(e => reply({ok:false, error:String(e?.message || e)}));
    return true;
  });
  const alarm = () => chrome.alarms.create('desktop-readiness', {periodInMinutes:1});
  chrome.runtime.onInstalled.addListener(alarm);
  chrome.runtime.onStartup.addListener(() => { alarm(); heartbeat().catch(() => {}); });
  chrome.alarms.onAlarm.addListener(item => { if (item.name === 'desktop-readiness') heartbeat().catch(() => {}); });
})();

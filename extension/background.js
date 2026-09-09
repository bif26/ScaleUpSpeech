/*
 * LanguageShadow background service worker.
 *
 * Mostly a router between the popup and content scripts. Also exposes a
 * healthcheck helper that the popup uses to know whether the manager is up.
 */

const LS_HEALTH = 'http://127.0.0.1:8765/api/health';

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.cmd === 'health') {
    fetch(LS_HEALTH, { method: 'GET' })
      .then(r => r.json())
      .then(j => sendResponse({ ok: true, ...j }))
      .catch(e => sendResponse({ ok: false, error: String(e) }));
    return true; // async response
  }
  if (msg.cmd === 'toggle') {
    chrome.storage.local.set({ 'ls.enabled': !!msg.enabled });
    // Forward to all YouTube tabs.
    chrome.tabs.query({ url: ['https://www.youtube.com/*', 'https://m.youtube.com/*'] }, (tabs) => {
      tabs.forEach(t => {
        chrome.tabs.sendMessage(t.id, { cmd: 'toggle', enabled: !!msg.enabled }, () => {
          // Tab may not have the content script loaded yet; ignore errors.
          if (chrome.runtime.lastError) {}
        });
      });
    });
    sendResponse({ ok: true, enabled: !!msg.enabled });
    return;
  }
});

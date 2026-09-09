/*
 * LanguageShadow content script - runs on every YouTube page.
 *
 * Watches the on-screen caption container (the div YouTube renders live
 * captions into) and sends each new line to the local LanguageShadow manager
 * at 127.0.0.1:8765/api/caption.
 *
 * It is intentionally tolerant of YouTube's class name changes: we observe
 * the body for mutations and probe a few candidate selectors.
 */

(function () {
  'use strict';

  const LS_ENDPOINT = 'http://127.0.0.1:8765/api/caption';
  let enabled = false;
  let lastSentText = '';
  let throttleUntil = 0;

  // YouTube renders captions into .ytp-caption-segment. Sometimes the
  // container is .captions-text. We try both, plus any descendant of the
  // player with [role="subtitle"] text content.
  const SELECTORS = [
    '.ytp-caption-segment',
    '.captions-text',
    '.caption-window .ytp-caption-segment',
    'div.ytp-transcript-caption > *',
  ];

  function tryExtractCaption(root) {
    for (const sel of SELECTORS) {
      const nodes = root.querySelectorAll(sel);
      for (const n of nodes) {
        const text = (n.innerText || n.textContent || '').trim();
        if (text && text.length > 1) return text;
      }
    }
    // Fallback: any visible element that looks like a caption container.
    const maybe = root.querySelector('.ytp-caption-window-container, .caption-window');
    if (maybe) {
      const text = (maybe.innerText || maybe.textContent || '').trim();
      if (text && text.length > 1) return text;
    }
    return null;
  }

  function sendCaption(text) {
    if (!enabled) return;
    if (text === lastSentText) return;
    if (Date.now() < throttleUntil) return;
    lastSentText = text;
    throttleUntil = Date.now() + 200; // max 5 lines/s to be gentle
    fetch(LS_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source: 'youtube' }),
    }).catch(() => {
      // Manager may not be running - silently swallow, the popup tells the user.
    });
  }

  // MutationObserver on body. Cheap enough since we filter on mutations.
  const obs = new MutationObserver(() => {
    if (!enabled) return;
    const root = document.querySelector('#movie_player') || document.body;
    if (!root) return;
    const text = tryExtractCaption(root);
    if (text) sendCaption(text);
  });

  function start() {
    if (enabled) return;
    enabled = true;
    obs.observe(document.body, { childList: true, subtree: true, characterData: true });
    console.log('[LanguageShadow] watching YouTube captions');
  }
  function stop() {
    enabled = false;
    obs.disconnect();
    console.log('[LanguageShadow] stopped watching YouTube captions');
  }

  // Listen for messages from the popup / background.
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.cmd === 'toggle') {
      msg.enabled ? start() : stop();
      sendResponse({ enabled });
      return;
    }
    if (msg.cmd === 'status') {
      sendResponse({ enabled, host: location.host });
      return;
    }
  });

  // Respect the saved state from previous runs.
  chrome.storage.local.get('ls.enabled', (data) => {
    if (data['ls.enabled']) start();
  });
})();

const toggle = document.getElementById('toggle');
const dot = document.getElementById('dot');
const status = document.getElementById('status');
const openUi = document.getElementById('open-ui');

function setDot(ok) {
  dot.className = 'dot ' + (ok ? 'green' : 'red');
}

// Restore the toggle state.
chrome.storage.local.get('ls.enabled', (data) => {
  toggle.checked = !!data['ls.enabled'];
});

toggle.addEventListener('change', () => {
  chrome.runtime.sendMessage({ cmd: 'toggle', enabled: toggle.checked }, () => {});
});

// Healthcheck the manager.
chrome.runtime.sendMessage({ cmd: 'health' }, (resp) => {
  if (chrome.runtime.lastError || !resp || !resp.ok) {
    setDot(false);
    status.textContent = 'Manager not reachable at 127.0.0.1:8765. Start it with ./start_manager.sh';
    return;
  }
  setDot(true);
  const w = resp.worker || {};
  status.textContent = `Manager OK · worker ${w.state || 'STOPPED'} · ${(resp.manager.rss_mb||0) + (w.rss_mb||0)} MB`;
});

openUi.addEventListener('click', (e) => {
  e.preventDefault();
  chrome.tabs.create({ url: 'http://127.0.0.1:8765/' });
});

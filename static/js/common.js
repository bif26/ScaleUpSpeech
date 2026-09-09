/* LanguageShadow shared client library */

// ---------------------------------------------------------------------------
// Global LS object - everything talks to port 8765 (manager)
// ---------------------------------------------------------------------------
window.LS = (function () {
  const API = `http://${location.hostname}:8765`;
  const WS_URL = `ws://${location.hostname}:8765/ws/transcribe`;

  // ---- small toast helper ----------------------------------------------
  let toastEl = null;
  function ensureToast() {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'toast';
      document.body.appendChild(toastEl);
    }
    return toastEl;
  }
  function toast(msg, kind = '') {
    const el = ensureToast();
    el.textContent = msg;
    el.className = 'show ' + kind;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = ''; }, 3000);
  }

  // ---- REST helpers -----------------------------------------------------
  async function api(path, method = 'GET', body = null) {
    const opt = { method, headers: {} };
    if (body) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(API + path, opt);
    if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
    return await r.json();
  }

  // ---- status polling ---------------------------------------------------
  let statusCb = null;
  let statusTimer = null;
  function onStatus(cb) {
    statusCb = cb;
    if (statusTimer) return;
    statusTimer = setInterval(async () => {
      try {
        const s = await api('/api/health');
        if (statusCb) statusCb(s);
      } catch (e) {
        // ignore - manager may be briefly unreachable
      }
    }, 2000);
    // Fire immediately
    api('/api/health').then(s => statusCb && statusCb(s)).catch(() => {});
  }

  // ---- audio recorder ---------------------------------------------------
  // Captures microphone at 16 kHz mono float32 and emits 250 ms PCM chunks.
  // AudioContext -> AudioWorklet (resamples to 16k) -> emit chunks.
  class Recorder {
    constructor() {
      this.ctx = null;
      this.stream = null;
      this.workletNode = null;
      this.running = false;
      this.onChunk = null;       // (Float32Array) => void
      this.onLevel = null;       // (rms 0..1) => void
      this.workletSrc = null;     // blob URL for the worklet
    }

    async start() {
      if (this.running) return;
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }
      });
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 16000,
        latencyHint: 'interactive',
      });
      // Force the source to actually be at 16k even if the mic was opened
      // at a different rate.
      const srcNode = this.ctx.createMediaStreamSource(this.stream);

      // Inline AudioWorkletProcessor (no separate .js file needed).
      const workletCode = `
        class PCMProcessor extends AudioWorkletProcessor {
          process(inputs) {
            const in0 = inputs[0];
            if (!in0 || !in0[0]) return true;
            const ch = in0[0];
            // Compute RMS for VU meter
            let sum = 0;
            for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
            const rms = Math.sqrt(sum / ch.length);
            this.port.postMessage({ kind: 'level', rms });
            this.port.postMessage({ kind: 'pcm', data: new Float32Array(ch) }, [ch.buffer]);
            return true;
          }
        }
        registerProcessor('pcm-pump', PCMProcessor);
      `;
      const blob = new Blob([workletCode], { type: 'application/javascript' });
      this.workletSrc = URL.createObjectURL(blob);
      await this.ctx.audioWorklet.addModule(this.workletSrc);
      this.workletNode = new AudioWorkletNode(this.ctx, 'pcm-pump', {
        numberOfInputs: 1, numberOfOutputs: 0,
      });
      this.workletNode.port.onmessage = (e) => {
        const m = e.data;
        if (m.kind === 'pcm' && this.onChunk) this.onChunk(m.data);
        else if (m.kind === 'level' && this.onLevel) this.onLevel(m.rms);
      };
      srcNode.connect(this.workletNode);
      this.running = true;
    }

    async stop() {
      if (!this.running) return;
      this.running = false;
      try {
        this.stream.getTracks().forEach(t => t.stop());
        if (this.ctx) await this.ctx.close();
      } catch (e) {}
      this.stream = null;
      this.ctx = null;
      this.workletNode = null;
      if (this.workletSrc) URL.revokeObjectURL(this.workletSrc);
      this.workletSrc = null;
    }
  }

  // ---- transcribe session over WS --------------------------------------
  class TranscribeSession {
    constructor({ referenceText, liveScoring, language = 'en', onMsg, onOpen, onClose }) {
      this.cfg = { reference_text: referenceText, live_scoring: liveScoring, target_language: language };
      this.onMsg = onMsg || (() => {});
      this.onOpen = onOpen || (() => {});
      this.onClose = onClose || (() => {});
      this.ws = null;
      this.closed = false;
    }

    open() {
      return new Promise((resolve, reject) => {
        this.ws = new WebSocket(WS_URL);
        this.ws.binaryType = 'arraybuffer';
        let opened = false;
        this.ws.onopen = () => {
          // Send config first
          this.ws.send(JSON.stringify(this.cfg));
        };
        this.ws.onmessage = (e) => {
          let data = e.data;
          if (typeof data === 'string') {
            try { data = JSON.parse(data); } catch {}
            if (!opened && data && data.type === 'ready') {
              opened = true;
              this.onOpen(data);
              resolve();
              return;
            }
            this.onMsg(data, 'text');
          } else {
            this.onMsg(data, 'bytes');
          }
        };
        this.ws.onerror = (e) => {
          if (!opened) reject(new Error('websocket failed to open'));
        };
        this.ws.onclose = (e) => {
          if (!opened) reject(new Error('websocket closed before ready: ' + (e.reason || e.code)));
          if (!this.closed) this.onClose(e);
        };
      });
    }

    sendPcm(float32) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // Copy to a fresh buffer because float32 may be transferred.
        const buf = float32.buffer.slice(float32.byteOffset, float32.byteOffset + float32.byteLength);
        this.ws.send(buf);
      }
    }

    end() {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send('END');
      }
      this.closed = true;
    }

    close() {
      if (this.ws) try { this.ws.close(); } catch {}
      this.closed = true;
    }
  }

  // ---- render verdicts into a words-display element ---------------------
  // verdicts: [{word, expected, status, similarity, ...}]
  // reference_words: ["the", "quick", ...]
  function renderWords(container, referenceWords, verdicts, opts = {}) {
    const showPending = opts.showPending !== false;
    // Build a map: which reference index did each verdict cover?
    // verdicts are already in alignment order, including insertions.
    container.innerHTML = '';
    let refIdx = 0;
    for (let i = 0; i < verdicts.length; i++) {
      const v = verdicts[i];
      const span = document.createElement('span');
      span.className = 'word ' + v.status;
      if (v.status === 'substitution') {
        span.textContent = v.word;
        span.title = `expected: "${v.expected}"  similarity: ${(v.similarity*100).toFixed(0)}%`;
      } else if (v.status === 'insertion') {
        span.textContent = v.word;
        span.title = 'extra word (insertion)';
      } else if (v.status === 'deletion') {
        span.textContent = v.expected;
        span.title = 'missing word';
      } else {
        span.textContent = v.word;
      }
      container.appendChild(span);
      container.appendChild(document.createTextNode(' '));
      if (v.status !== 'insertion') refIdx++;
    }
    // Add pending reference words not yet covered (for live mode).
    if (showPending) {
      while (refIdx < referenceWords.length) {
        const span = document.createElement('span');
        span.className = 'word pending';
        span.textContent = referenceWords[refIdx];
        container.appendChild(span);
        container.appendChild(document.createTextNode(' '));
        refIdx++;
      }
    }
  }

  // ---- helpers for splitting reference into a "typing-test" style grid --
  // The verdicts array already includes pending words at the end (we add
  // them above), but the caller may want a pre-built display before any
  // audio has been received.
  function renderPending(container, referenceWords) {
    container.innerHTML = '';
    referenceWords.forEach(w => {
      const span = document.createElement('span');
      span.className = 'word pending';
      span.textContent = w;
      container.appendChild(span);
      container.appendChild(document.createTextNode(' '));
    });
  }

  // ---- format helpers ---------------------------------------------------
  function fmtTime(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function scoreColor(score) {
    if (score >= 80) return 'green';
    if (score >= 50) return 'yellow';
    return 'red';
  }

  // ---- render the API.md contract subscore grid -------------------------
  function renderSubscores(container, subscores) {
    const order = ['accuracy', 'fluency', 'prosody', 'completeness'];
    container.innerHTML = order.map(k => {
      const v = Math.round(subscores[k] || 0);
      const color = scoreColor(v);
      return `
        <div class="subscore-box">
          <div class="label">${k}</div>
          <div class="value ${color}">${v}</div>
          <div class="bar"><div class="bar-fill" style="width:${v}%; background:var(--${color});"></div></div>
        </div>
      `;
    }).join('');
  }

  // ---- render the API.md contract words[] as chips -----------------------
  function renderWordChips(container, words) {
    // Per API.md: green >=80, yellow >=60, red below. Plus omitted / inserted.
    container.innerHTML = (words || []).map(w => {
      let cls = 'chip';
      const err = (w.errorType || '').toLowerCase();
      if (err === 'omitted') cls += ' omitted';
      else if (err === 'inserted') cls += ' inserted';
      else if (w.accuracy >= 80) cls += ' good';
      else if (w.accuracy >= 60) cls += ' mid';
      else cls += ' bad';
      const title = (w.errorType && w.errorType !== 'None')
        ? `errorType: ${w.errorType} · accuracy ${w.accuracy}`
        : `accuracy ${w.accuracy}`;
      return `<span class="${cls}" title="${title}">${w.word}</span>`;
    }).join('');
  }

  // ---- CEFR badge --------------------------------------------------------
  function cefrBadgeClass(level) {
    const v = (level || '').toLowerCase();
    if (v.includes('b2+')) return 'b2plus';
    if (v.startsWith('b2')) return 'b2';
    if (v.startsWith('b1')) return 'b1';
    if (v.startsWith('a2')) return 'a2';
    return 'below';
  }

  // ---- slider helper (one-liner to wire up an <input type="range">) -----
  function bindSlider(inputEl, valueEl, fmt) {
    const update = () => { if (valueEl) valueEl.textContent = (fmt || (x => x))(+inputEl.value); };
    inputEl.addEventListener('input', update);
    update();
    return inputEl;
  }

  return {
    api,
    onStatus,
    toast,
    Recorder,
    TranscribeSession,
    renderWords,
    renderPending,
    renderSubscores,
    renderWordChips,
    cefrBadgeClass,
    bindSlider,
    fmtTime,
    scoreColor,
    API_BASE: API,
    WS_URL,
  };
})();

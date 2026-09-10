/* Audio recorder using AudioWorklet to capture 16 kHz mono PCM float32 chunks */

export class Recorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private workletSrc: string | null = null;
  private running = false;

  public onChunk: ((pcm: Float32Array) => void) | null = null;
  public onLevel: ((rms: number) => void) | null = null;

  async start(): Promise<void> {
    if (this.running) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.ctx = new AudioContext({
      sampleRate: 16000,
      latencyHint: 'interactive',
    });
    // Defensive: Chrome honours the sampleRate option, but some browsers /
    // drivers silently fall back to the hardware rate. If that happens the
    // worklet below resamples to 16 kHz itself — feeding 48 kHz samples to
    // the worker "as 16 kHz" makes speech 3x slow + 3 octaves down, which
    // Silero VAD then removes 100% of ("VAD filter removed 00:03.232 of
    // audio" in languageshadow.log) and whisper returns empty transcripts.
    if (this.ctx.sampleRate !== 16000) {
      console.warn(`[recorder] AudioContext runs at ${this.ctx.sampleRate} Hz — resampling to 16 kHz`);
    }
    const srcNode = this.ctx.createMediaStreamSource(this.stream);

    // Inline AudioWorkletProcessor — no separate .js file needed.
    //
    // CRITICAL: buffer the 128-sample render quanta (8 ms) into ~250 ms
    // chunks before posting. The old version posted ONE MESSAGE PER QUANTUM
    // (~125 websocket messages/s) and the worker launched a full whisper
    // transcription per message — the socket backlog grew unboundedly,
    // partials never reached the page and the final result was never
    // computed (the 'can start record but it never shows result' bug).
    // 4 messages/s instead of ~125 also fixes the zero-copy transfer:
    // the old code called ch.slice() twice, so the transfer list referenced
    // a DIFFERENT ArrayBuffer than the one in the message.
    const TARGET_RATE = 16000;
    const CHUNK_SAMPLES = TARGET_RATE / 4; // 4000 samples = 250 ms @ 16 kHz
    const workletCode = `
      const TARGET_RATE = ${TARGET_RATE};
      const CHUNK_SAMPLES = ${CHUNK_SAMPLES};

      class PCMProcessor extends AudioWorkletProcessor {
        constructor() {
          super();
          // 'sampleRate' (global) is the ACTUAL context rate.
          this.ratio = sampleRate / TARGET_RATE; // >1 means downsample
          this.acc = new Float32Array(CHUNK_SAMPLES);
          this.accFill = 0;
          this.frac = 0;
          this.prev = 0;
          this.hasPrev = false;
        }

        emit(rms, chunk) {
          this.accFill = 0;
          // chunk owns its buffer (acc.slice()), so it can be transferred.
          this.port.postMessage({ kind: 'pcm', rms, data: chunk }, [chunk.buffer]);
        }

        push(sample, rms) {
          this.acc[this.accFill++] = sample;
          if (this.accFill === CHUNK_SAMPLES) this.emit(rms, this.acc.slice());
        }

        process(inputs) {
          const in0 = inputs[0];
          if (!in0 || !in0[0]) return true;
          const ch = in0[0];
          let sum = 0;
          for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
          const rms = Math.sqrt(sum / ch.length);

          if (this.ratio === 1) {
            // Fast path: context already runs at 16 kHz — just accumulate.
            for (let i = 0; i < ch.length; i++) this.push(ch[i], rms);
          } else {
            // Linear-interpolation resampler inRate -> 16 kHz.
            let pos = this.frac;
            let prev = this.hasPrev ? this.prev : ch[0];
            for (let i = 0; i < ch.length; i++) {
              while (pos < 1) {
                this.push(prev + (ch[i] - prev) * pos, rms);
                pos += this.ratio;
              }
              pos -= 1;
              prev = ch[i];
            }
            this.frac = pos;
            this.prev = prev;
            this.hasPrev = true;
          }
          return true;
        }
      }
      registerProcessor('pcm-pump', PCMProcessor);
    `;
    const blob = new Blob([workletCode], { type: 'application/javascript' });
    this.workletSrc = URL.createObjectURL(blob);
    await this.ctx.audioWorklet.addModule(this.workletSrc);
    this.workletNode = new AudioWorkletNode(this.ctx, 'pcm-pump', {
      numberOfInputs: 1,
      numberOfOutputs: 0,
    });
    this.workletNode.port.onmessage = (e: MessageEvent) => {
      const m = e.data;
      // One 'pcm' frame per 250 ms chunk (VU level rides along at the same
      // cadence instead of 125x/s, which starved the main thread).
      if (m.kind === 'pcm') {
        this.onLevel?.(m.rms as number);
        this.onChunk?.(m.data as Float32Array);
      }
    };
    srcNode.connect(this.workletNode);
    this.running = true;
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    this.running = false;
    try {
      this.stream?.getTracks().forEach((t) => t.stop());
      if (this.ctx) await this.ctx.close();
    } catch (e) {
      // ignore
    }
    this.stream = null;
    this.ctx = null;
    this.workletNode = null;
    if (this.workletSrc) URL.revokeObjectURL(this.workletSrc);
    this.workletSrc = null;
  }

  get isRunning() {
    return this.running;
  }
}

/* WebSocket transcribe session — opens the WS, sends the config frame, then
   forwards PCM chunks until the client sends 'END'. The server returns
   incremental and final scoring frames. */
import type { AssessResponse, PartialFrame } from './types';

export interface IncrementalFrame {
  type: 'incremental';
  session_id: string;
  progress: number;
  words_spoken: number;
  words_total: number;
  n_correct: number;
  accuracy: number;
  verdicts: Array<{ word: string; accuracy: number; errorType: string }>;
}

export type ServerFrame = IncrementalFrame | PartialFrame | (AssessResponse & { type: 'final' });

export class TranscribeSession {
  private ws: WebSocket | null = null;
  private closed = false;
  private cfg: {
    reference_text: string;
    live_scoring: boolean;
    target_language: string;
  };

  constructor(
    cfg: { referenceText: string; liveScoring: boolean; language: string },
    private handlers: {
      onOpen?: (data: any) => void;
      onMessage?: (frame: ServerFrame) => void;
      onClose?: (ev: CloseEvent) => void;
      onError?: (err: unknown) => void;
    },
  ) {
    this.cfg = {
      reference_text: cfg.referenceText,
      live_scoring: cfg.liveScoring,
      target_language: cfg.language,
    };
  }

  open(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(apiWsUrl());
      this.ws.binaryType = 'arraybuffer';
      let opened = false;

      this.ws.onopen = () => {
        this.ws!.send(JSON.stringify(this.cfg));
      };
      this.ws.onmessage = (e) => {
        let data: any = e.data;
        if (typeof data === 'string') {
          try {
            data = JSON.parse(data);
          } catch {
            /* keep raw */
          }
          if (!opened && data && data.type === 'ready') {
            opened = true;
            this.handlers.onOpen?.(data);
            resolve();
            return;
          }
          this.handlers.onMessage?.(data as ServerFrame);
        }
      };
      this.ws.onerror = (e) => {
        if (!opened) reject(e);
        this.handlers.onError?.(e);
      };
      this.ws.onclose = (e) => {
        if (!opened) reject(new Error('ws closed before ready: ' + (e.reason || e.code)));
        if (!this.closed) this.handlers.onClose?.(e);
      };
    });
  }

  sendPcm(pcm: Float32Array) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const buf = pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength);
      this.ws.send(buf);
    }
  }

  end() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send('END');
    this.closed = true;
  }

  close() {
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.closed = true;
  }
}

function apiWsUrl(): string {
  return `ws://${location.hostname}:8765/ws/transcribe`;
}

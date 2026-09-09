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
    const srcNode = this.ctx.createMediaStreamSource(this.stream);

    // Inline AudioWorkletProcessor — no separate .js file needed.
    const workletCode = `
      class PCMProcessor extends AudioWorkletProcessor {
        process(inputs) {
          const in0 = inputs[0];
          if (!in0 || !in0[0]) return true;
          const ch = in0[0];
          let sum = 0;
          for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
          const rms = Math.sqrt(sum / ch.length);
          this.port.postMessage({ kind: 'level', rms });
          // Send a copy of the channel; the buffer stays owned by the worklet
          // because we transfer a fresh Float32Array built from ch.slice().
          this.port.postMessage(
            { kind: 'pcm', data: ch.slice() },
            // ch.slice() returns a new buffer; we can transfer it.
            [ch.slice().buffer]
          );
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
      if (m.kind === 'pcm' && this.onChunk) this.onChunk(m.data as Float32Array);
      else if (m.kind === 'level' && this.onLevel) this.onLevel(m.rms as number);
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

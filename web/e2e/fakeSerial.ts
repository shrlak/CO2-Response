/**
 * A fake Arduino behind the Web Serial API, injected before the app loads, so the real
 * SerialSource code (port picker, streams, INFO/START, unplug and replug) runs in CI
 * without hardware. It speaks the firmware's protocol: docs/protocol.md.
 */
export const fakeSerialScript = () => {
  const enc = new TextEncoder();
  const xor = (s: string) => {
    let x = 0;
    for (let i = 0; i < s.length; i++) x ^= s.charCodeAt(i);
    return x.toString(16).toUpperCase().padStart(2, '0');
  };
  const INFO = 'I,FW,1.0.0,ADC,ADS1115,CH,3,RATE,100,BAUD,115200,ADDR,0x48,FSR,6.144\n';

  class FakePort extends EventTarget {
    readable: ReadableStream<Uint8Array> | null = null;
    writable: WritableStream<Uint8Array> | null = null;
    commands: string[] = [];
    present = true;
    opens = 0;
    private ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
    private timer: ReturnType<typeof setInterval> | null = null;
    private n = 0;
    private t0 = 0;

    getInfo() {
      return { usbVendorId: 0x2341, usbProductId: 0x0043 };
    }

    async open() {
      if (!this.present) throw new DOMException('The device has been lost.', 'NetworkError');
      this.opens++;
      this.n = 0;
      this.t0 = performance.now();
      this.readable = new ReadableStream<Uint8Array>({
        start: (ctl) => {
          this.ctl = ctl;
          ctl.enqueue(enc.encode(INFO));
          this.timer = setInterval(() => this.pump(), 20);
        },
        cancel: () => this.stopTimer(),
      });
      this.writable = new WritableStream<Uint8Array>({
        write: (chunk) => {
          const cmd = new TextDecoder().decode(chunk).trim();
          this.commands.push(cmd);
          if (/^INFO$/i.test(cmd)) this.ctl?.enqueue(enc.encode(INFO));
          if (/^START$/i.test(cmd)) this.ctl?.enqueue(enc.encode('A,START\n'));
        },
      });
    }

    private stopTimer() {
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
    }

    private pump() {
      const due = Math.floor((performance.now() - this.t0) / 10);
      let out = '';
      while (this.n < due) {
        const t = this.n / 100;
        const x = t % 4; // 15 breaths/min
        let flow: number;
        let co2: number;
        if (x < 4 / 3) {
          flow = 35 * Math.sin((Math.PI * x) / (4 / 3));
          co2 = 0.2;
        } else {
          const e = x - 4 / 3;
          flow = -60 * Math.exp(-e / 0.53) * (1 - Math.exp(-e / 0.06));
          co2 = e < 0.3 ? (38 * e) / 0.3 : 38;
        }
        const v = [((co2 / 760) * 100) / 2, (flow + 100) / 40, (flow / 0.2 + 500) / 200].map((y) => y.toFixed(4));
        const payload = `,${this.n & 0xffff},${this.n * 10},${v.join(',')}`;
        out += `D${payload}*${xor(payload)}\n`;
        this.n++;
      }
      if (out) {
        try {
          this.ctl?.enqueue(enc.encode(out));
        } catch {
          /* stream already closed */
        }
      }
    }

    unplug() {
      this.stopTimer();
      this.present = false;
      try {
        this.ctl?.error(new DOMException('The device has been lost.', 'NetworkError'));
      } catch {
        /* already errored */
      }
      fire('disconnect');
    }

    replug() {
      this.present = true;
      fire('connect');
    }

    async close() {
      this.stopTimer();
    }
  }

  const port = new FakePort();
  const serial = new EventTarget() as EventTarget & { requestPort(): Promise<FakePort>; getPorts(): Promise<FakePort[]>; requests: number };
  let granted = false;
  serial.requests = 0;
  serial.requestPort = async () => {
    serial.requests++;
    granted = true;
    return port;
  };
  serial.getPorts = async () => (granted && port.present ? [port] : []);
  // events bubble from the port to navigator.serial with the port as target
  const fire = (type: string) => {
    const ev = new Event(type);
    Object.defineProperty(ev, 'target', { value: port });
    serial.dispatchEvent(ev);
  };
  Object.defineProperty(navigator, 'serial', { value: serial, configurable: true });
  (window as unknown as { __fake: { port: FakePort; serial: typeof serial } }).__fake = { port, serial };
};

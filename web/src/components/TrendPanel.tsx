import { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { monitor, type Snapshot } from '../engine/monitor';
import { mmHgToPct } from '../processing/co2';
import { linearFit } from '../processing/metrics';
import { fmt } from './format';

type Tab = 'trend' | 'response';

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const FONT = '12px "Barlow Semi Condensed", "Arial Narrow", sans-serif';

interface View {
  unit: 'mmHg' | '%';
  pb: number;
  wv: boolean;
  high: number;
  low: number | null;
  minutes: number;
  scaleMax: number;
}

export function TrendPanel({ snap, theme }: { snap: Snapshot; theme: string }) {
  const [tab, setTab] = useState<Tab>('trend');
  const [meta, setMeta] = useState('');
  const el = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const s = snap.settings;
  const view = useRef<View>(null!);
  view.current = {
    unit: s.co2Unit,
    pb: s.processing.barometricMmHg,
    wv: s.processing.waterVapor,
    high: s.alarms.highMmHg,
    low: s.alarms.lowMmHg,
    minutes: s.trendMinutes,
    scaleMax: s.co2ScaleMaxMmHg,
  };
  const disp = (mm: number) => (view.current.unit === '%' ? mmHgToPct(mm, view.current.pb, view.current.wv) : mm);

  // Build the chart when the tab, theme or units change.
  useEffect(() => {
    const div = el.current!;
    const C = { muted: css('--muted'), grid: css('--grid'), co2: css('--co2'), pneumo: css('--pneumo'), text: css('--text'), high: css('--alarm-high'), med: css('--alarm-med') };
    const axis = { stroke: C.muted, font: FONT, grid: { stroke: C.grid, width: 1 }, ticks: { stroke: C.grid, width: 1, size: 4 } };
    const w = Math.max(100, div.clientWidth);
    const h = Math.max(60, div.clientHeight);

    const box = (u: uPlot) => u.bbox;
    const drawUnder = (u: uPlot) => {
      if (tab !== 'trend') return;
      const ctx = u.ctx;
      const b = box(u);
      const v = view.current;
      const yHigh = u.valToPos(disp(v.high), 'y', true);
      ctx.save();
      ctx.beginPath();
      ctx.rect(b.left, b.top, b.width, b.height);
      ctx.clip();
      ctx.fillStyle = C.high;
      ctx.globalAlpha = 0.1;
      ctx.fillRect(b.left, b.top, b.width, Math.max(0, yHigh - b.top));
      if (v.low !== null) {
        const yLow = u.valToPos(disp(v.low), 'y', true);
        ctx.fillStyle = C.med;
        ctx.fillRect(b.left, yLow, b.width, Math.max(0, b.top + b.height - yLow));
      }
      ctx.globalAlpha = 1;
      // continuous CO2 behind the breath points
      const x0 = u.scales.x.min ?? 0;
      ctx.strokeStyle = C.co2;
      ctx.globalAlpha = 0.25;
      ctx.lineWidth = 1;
      ctx.beginPath();
      const T = monitor.traceT;
      const V = monitor.traceV;
      let started = false;
      let lastT = 0;
      for (let i = 0; i < T.length; i++) {
        if (T[i] < x0) continue;
        const x = u.valToPos(T[i], 'x', true);
        const y = u.valToPos(disp(V[i]), 'y', true);
        if (!started || T[i] - lastT > 5) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        started = true;
        lastT = T[i];
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.setLineDash([8, 6]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = C.high;
      ctx.beginPath();
      ctx.moveTo(b.left, yHigh);
      ctx.lineTo(b.left + b.width, yHigh);
      ctx.stroke();
      if (v.low !== null) {
        const yLow = u.valToPos(disp(v.low), 'y', true);
        ctx.strokeStyle = C.med;
        ctx.beginPath();
        ctx.moveTo(b.left, yLow);
        ctx.lineTo(b.left + b.width, yLow);
        ctx.stroke();
      }
      ctx.restore();
    };

    const drawOver = (u: uPlot) => {
      if (tab !== 'trend') return;
      const ctx = u.ctx;
      const b = box(u);
      const x0 = u.scales.x.min ?? 0;
      const dpr = devicePixelRatio || 1;
      ctx.save();
      ctx.font = `${12 * dpr}px "Barlow Semi Condensed", sans-serif`;
      ctx.textBaseline = 'top';
      for (const m of monitor.markers) {
        const t = m.t / 1000;
        if (t < x0) continue;
        const x = Math.round(u.valToPos(t, 'x', true)) + 0.5;
        ctx.strokeStyle = C.text;
        ctx.globalAlpha = 0.55;
        ctx.setLineDash([3 * dpr, 3 * dpr]);
        ctx.beginPath();
        ctx.moveTo(x, b.top);
        ctx.lineTo(x, b.top + b.height);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.setLineDash([]);
        ctx.fillStyle = C.text;
        ctx.fillText(`▼ ${m.label}`, x + 3 * dpr, b.top + 2 * dpr);
      }
      for (const a of monitor.alarmStarts) {
        const t = a.t / 1000;
        if (t < x0) continue;
        const x = u.valToPos(t, 'x', true);
        ctx.fillStyle = a.priority === 'high' ? C.high : C.med;
        ctx.beginPath();
        ctx.moveTo(x, b.top + b.height - 1 * dpr);
        ctx.lineTo(x - 6 * dpr, b.top + b.height - 12 * dpr);
        ctx.lineTo(x + 6 * dpr, b.top + b.height - 12 * dpr);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    };

    const opts: uPlot.Options =
      tab === 'trend'
        ? {
            width: w,
            height: h,
            legend: { show: false },
            cursor: { drag: { x: false, y: false }, points: { show: false } },
            scales: { x: { time: true }, y: { auto: false, range: () => [0, disp(view.current.scaleMax)] } },
            axes: [
              {
                ...axis,
                size: 26,
                values: (_u, vals) =>
                  vals.map((v) => {
                    const d = new Date(v * 1000);
                    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
                  }),
              },
              { ...axis, size: 42, values: (_u, vals) => vals.map((v) => (view.current.unit === '%' ? v.toFixed(1) : v.toFixed(0))) },
            ],
            series: [{}, { label: 'EtCO2', stroke: C.co2, width: 0, paths: () => null, points: { show: true, size: 5, space: 0, width: 1, stroke: C.co2, fill: C.co2 } }],
            hooks: { drawAxes: [drawUnder], draw: [drawOver] },
          }
        : {
            width: w,
            height: h,
            legend: { show: false },
            cursor: { drag: { x: false, y: false }, points: { show: false } },
            scales: {
              x: { time: false, auto: true, range: (_u, mn, mx) => [Math.floor((mn ?? 30) - 2), Math.ceil((mx ?? 50) + 2)] },
              y: { auto: true, range: (_u, _mn, mx) => [0, Math.max(10, Math.ceil((mx ?? 10) * 1.2))] },
            },
            axes: [
              { ...axis, size: 26, values: (_u, vals) => vals.map((v) => `${v}`) },
              { ...axis, size: 42 },
            ],
            series: [
              {},
              { label: 'MV', stroke: C.pneumo, width: 0, paths: () => null, points: { show: true, size: 5, space: 0, width: 1, stroke: C.pneumo, fill: C.pneumo } },
              { label: 'Fit', stroke: C.text, width: 1.5, points: { show: false } },
            ],
          };

    const u = new uPlot(opts, tab === 'trend' ? [[], []] : [[], [], []], div);
    plot.current = u;
    const ro = new ResizeObserver(() => u.setSize({ width: Math.max(100, div.clientWidth), height: Math.max(60, div.clientHeight) }));
    ro.observe(div);
    return () => {
      ro.disconnect();
      u.destroy();
      plot.current = null;
    };
    // disp reads view.current, which is refreshed every render
  }, [tab, theme]);

  // Refresh the data once a second and whenever a breath or marker arrives.
  const secondTick = Math.floor(snap.now / 1000);
  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    const nowS = Date.now() / 1000;
    const minutes = view.current.minutes;
    const t0 = minutes > 0 ? nowS - minutes * 60 : (monitor.trend[0]?.t ?? nowS - 60);
    const pts = monitor.trend.filter((p) => p.t >= t0);
    if (tab === 'trend') {
      u.setData([pts.map((p) => p.t), pts.map((p) => (Number.isFinite(p.etco2) ? disp(p.etco2) : null))], false);
      u.setScale('x', { min: t0, max: nowS });
      setMeta(`${minutes > 0 ? `${minutes} min` : 'whole session'} · one point per breath · ${pts.length} breaths`);
    } else {
      const xy = pts.filter((p) => Number.isFinite(p.etco2) && Number.isFinite(p.mv)).sort((a, b) => a.etco2 - b.etco2);
      const xs = xy.map((p) => p.etco2);
      const ys = xy.map((p) => p.mv);
      const fit = linearFit(xs, ys);
      u.setData([xs, ys, fit ? xs.map((x) => fit.slope * x + fit.intercept) : xs.map(() => null)]);
      setMeta(fit ? `MV (L/min) vs EtCO2 (mmHg) · slope ${fmt(fit.slope, 2)} L/min/mmHg · r² ${fmt(fit.r2, 2)} · n ${xs.length}` : 'MV vs EtCO2 · needs at least 3 breaths');
    }
  }, [secondTick, snap.trendVersion, tab, theme, s.trendMinutes, s.co2Unit, s.alarms.highMmHg, s.alarms.lowMmHg, s.co2ScaleMaxMmHg]);

  return (
    <section className="trend">
      <div className="trend-head" role="tablist">
        <button className="tab" role="tab" aria-selected={tab === 'trend'} onClick={() => setTab('trend')}>
          EtCO2 trend
        </button>
        <button className="tab" role="tab" aria-selected={tab === 'response'} onClick={() => setTab('response')}>
          Ventilatory response
        </button>
        <span className="trend-meta">
          <span>{meta}</span>
          <label>
            Window{' '}
            <select value={s.trendMinutes} onChange={(e) => monitor.updateSettings({ trendMinutes: Number(e.target.value) })} aria-label="Trend window">
              <option value={15}>15 min</option>
              <option value={30}>30 min</option>
              <option value={120}>2 h</option>
              <option value={0}>All</option>
            </select>
          </label>
        </span>
      </div>
      <div className="trend-plot" ref={el} />
    </section>
  );
}

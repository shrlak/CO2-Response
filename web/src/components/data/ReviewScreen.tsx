import { useEffect, useMemo, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { monitor } from '../../engine/monitor';
import { breathsCsv, eventsCsv, eventType, exportSessionParts, summarize, type ParsedSession, type Range } from '../../storage/csv';
import { downloadText, saveBlob } from '../../storage/download';
import type { SessionRecord } from '../../storage/library';
import { duration, fmt } from '../format';
import { exportSession } from '../settings/DataPanel';
import { showToast } from '../toast';

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const FONT = '12px "Barlow Semi Condensed", "Arial Narrow", sans-serif';

/** elapsed seconds → h:mm:ss */
const el = (s: number) => {
  const t = Math.max(0, Math.round(s));
  return `${Math.floor(t / 3600)}:${String(Math.floor(t / 60) % 60).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
};

function lowerBound(a: Float64Array, x: number): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (a[m] < x) lo = m + 1;
    else hi = m;
  }
  return lo;
}

/** Waveforms for a time range, reduced to min/max pairs when there are more points than pixels. */
function waveData(s: ParsedSession, r: Range, maxPts = 3000): uPlot.AlignedData {
  const i0 = lowerBound(s.timeS, r.t0);
  const i1 = lowerBound(s.timeS, r.t1);
  const n = i1 - i0;
  const cols = [s.co2MmHg, s.flowLpm, s.pneumoLpm];
  if (n <= maxPts) {
    return [Array.from(s.timeS.subarray(i0, i1)), ...cols.map((c) => Array.from(c.subarray(i0, i1), (v) => (Number.isFinite(v) ? v : null)))];
  }
  const buckets = Math.floor(maxPts / 2);
  const per = n / buckets;
  const x: number[] = [];
  const ys: (number | null)[][] = cols.map(() => []);
  for (let b = 0; b < buckets; b++) {
    const a = i0 + Math.floor(b * per);
    const z = Math.min(i1, i0 + Math.floor((b + 1) * per));
    x.push(s.timeS[a], s.timeS[Math.max(a, z - 1)]);
    cols.forEach((c, k) => {
      let mn = Infinity;
      let mx = -Infinity;
      for (let i = a; i < z; i++) {
        const v = c[i];
        if (v < mn) mn = v;
        if (v > mx) mx = v;
      }
      ys[k].push(Number.isFinite(mn) ? mn : null, Number.isFinite(mx) ? mx : null);
    });
  }
  return [x, ...ys];
}

interface ChartProps {
  data: uPlot.AlignedData;
  series: uPlot.Series[];
  scales: uPlot.Scales;
  axes: uPlot.Axis[];
  height: number;
  range: Range;
  full: Range;
  setRange: (r: Range | null) => void;
  events: ParsedSession['events'];
  theme: string;
  label: string;
}

function Chart({ data, series, scales, axes, height, range, full, setRange, events, theme, label }: ChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const evRef = useRef(events);
  evRef.current = events;
  const setRef = useRef(setRange);
  setRef.current = setRange;

  useEffect(() => {
    const div = el.current!;
    const C = { text: css('--text'), high: css('--alarm-high') };
    const opts: uPlot.Options = {
      width: Math.max(200, div.clientWidth),
      height,
      legend: { show: true, live: true },
      cursor: { drag: { x: true, y: false, setScale: false }, sync: { key: 'review' } },
      scales: { ...scales, x: { time: false, auto: false, range: () => [rangeRef.current.t0, rangeRef.current.t1] } },
      axes,
      series,
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 4) {
              const a = u.posToVal(u.select.left, 'x');
              const b = u.posToVal(u.select.left + u.select.width, 'x');
              setRef.current({ t0: Math.max(full.t0, a), t1: Math.min(full.t1, b) });
            }
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
          },
        ],
        draw: [
          (u) => {
            const ctx = u.ctx;
            const b = u.bbox;
            const dpr = devicePixelRatio || 1;
            ctx.save();
            for (const e of evRef.current) {
              const type = eventType(e.text);
              const alarm = type === 'ALARM_START';
              if (!alarm && type !== 'MARK') continue;
              if (e.timeS < rangeRef.current.t0 || e.timeS > rangeRef.current.t1) continue;
              const x = Math.round(u.valToPos(e.timeS, 'x', true)) + 0.5;
              ctx.strokeStyle = alarm ? C.high : C.text;
              ctx.globalAlpha = 0.6;
              ctx.setLineDash([3 * dpr, 3 * dpr]);
              ctx.beginPath();
              ctx.moveTo(x, b.top);
              ctx.lineTo(x, b.top + b.height);
              ctx.stroke();
            }
            ctx.restore();
          },
        ],
      },
    };
    const u = new uPlot(opts, data, div);
    plot.current = u;
    const ro = new ResizeObserver(() => u.setSize({ width: Math.max(200, div.clientWidth), height }));
    ro.observe(div);
    return () => {
      ro.disconnect();
      u.destroy();
      plot.current = null;
    };
    // rebuilt only when the look changes; data and range are pushed below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, height]);

  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    u.setData(data, false);
    u.setScale('x', { min: range.t0, max: range.t1 });
  }, [data, range]);

  return <div className="review-chart" ref={el} aria-label={label} />;
}

export function ReviewScreen({ id, onBack, theme, onReplay }: { id: string; onBack: () => void; theme: string; onReplay: (s: ParsedSession, name: string) => void }) {
  const [rec, setRec] = useState<SessionRecord | null>(null);
  const [parsed, setParsed] = useState<ParsedSession | null>(null);
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState<Range | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await monitor.library.get(id);
        if (!r) throw new Error('This session is no longer in the library.');
        const p = await monitor.library.parse(id);
        if (live) {
          setRec(r);
          setParsed(p);
        }
      } catch (e) {
        if (live) setError((e as Error).message);
      }
    })();
    return () => {
      live = false;
    };
  }, [id]);

  const sum = useMemo(() => (parsed ? summarize(parsed) : null), [parsed]);
  const full: Range = useMemo(() => ({ t0: 0, t1: Math.max(1, parsed?.durationS ?? 1) }), [parsed]);
  const range = zoom ?? full;
  const C = useMemo(() => ({ co2: css('--co2'), flow: css('--flow'), pneumo: css('--pneumo'), text: css('--text'), muted: css('--muted'), grid: css('--grid') }), [theme]); // eslint-disable-line react-hooks/exhaustive-deps

  const breathData = useMemo<uPlot.AlignedData>(() => {
    const b = parsed?.breaths ?? [];
    const nn = (v: number) => (Number.isFinite(v) ? v : null);
    return [b.map((x) => x.timeS), b.map((x) => nn(x.etco2)), b.map((x) => nn(x.rr)), b.map((x) => nn(x.vt)), b.map((x) => nn(x.mv))];
  }, [parsed]);
  const trendData = useMemo<uPlot.AlignedData>(() => [breathData[0], breathData[1], breathData[2]], [breathData]);
  const ventData = useMemo<uPlot.AlignedData>(() => [breathData[0], breathData[3], breathData[4]], [breathData]);
  const waves = useMemo(() => (parsed ? waveData(parsed, range) : ([[], [], [], []] as uPlot.AlignedData)), [parsed, range]);

  if (error)
    return (
      <div className="settings-body">
        <button className="btn primary" onClick={onBack}>
          ← Back to data
        </button>
        <p className="err">Could not open this session: {error}</p>
      </div>
    );
  if (!parsed || !rec || !sum)
    return (
      <div className="settings-body">
        <p className="note">Reading session…</p>
      </div>
    );

  const axis = (scale?: string, side?: number, label?: string): uPlot.Axis => ({ scale, side, label, stroke: C.muted, font: FONT, labelFont: FONT, grid: { stroke: C.grid, width: 1, show: side !== 1 }, ticks: { stroke: C.grid, width: 1, size: 4 }, size: 48 });
  const xAxis: uPlot.Axis = { ...axis(), size: 28, values: (_u, vals) => vals.map((v) => el(v)) };
  const pts = (color: string): uPlot.Series['points'] => ({ show: true, size: 4, space: 0, stroke: color, fill: color });
  const fmtVal = (d: number, unit: string) => (_u: uPlot, v: number | null) => (v === null || v === undefined ? '--' : `${v.toFixed(d)} ${unit}`);
  const shared = { range, full, setRange: (r: Range | null) => setZoom(r), events: parsed.events, theme };
  const base = rec.fileName.replace(/\.csv$/i, '');
  const rangeTag = zoom ? `_${Math.round(zoom.t0)}-${Math.round(zoom.t1)}s` : '';

  const stat = (k: string, v: string) => (
    <div key={k}>
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );

  return (
    <div className="settings-body review">
      <div className="review-head">
        <button className="btn primary" onClick={onBack}>
          ← Back to data
        </button>
        <div style={{ minWidth: 0 }}>
          <h2 className="mono" style={{ fontSize: 18 }}>
            {rec.fileName}
          </h2>
          <p className="note">
            Study {parsed.studyCode || '—'} · started {new Date(parsed.startedMs).toLocaleString()} · {parsed.meta.source ?? 'unknown source'} · firmware {parsed.meta.firmware ?? '?'} · file format v{parsed.version}
            {parsed.version < 2 ? ' (exports are converted to v2)' : ''}
          </p>
        </div>
      </div>

      <div className="stats">
        {stat('Length', duration(sum.durationS * 1000))}
        {stat('Breaths', String(sum.breaths))}
        {stat('EtCO2 mean (min–max)', `${fmt(sum.etco2.mean, 1)} (${fmt(sum.etco2.min, 0)}–${fmt(sum.etco2.max, 0)}) mmHg`)}
        {stat('RR mean', `${fmt(sum.rr, 1)} /min`)}
        {stat('VT mean', `${fmt(sum.vt, 0)} mL`)}
        {stat('MV mean', `${fmt(sum.mv, 2)} L/min`)}
        {stat('Alarms', String(sum.alarms))}
        {stat('Markers', String(sum.markers))}
        {stat('Samples', `${sum.samples.toLocaleString()} at ${sum.rateHz} Hz`)}
        {stat('Dropped samples', String(sum.dropped))}
      </div>

      <div className="row-actions" style={{ margin: '4px 0 10px' }}>
        <span className="note">Drag across a chart to zoom. {zoom ? `Showing ${el(zoom.t0)}–${el(zoom.t1)}.` : 'Showing the whole session.'}</span>
        <button className="btn small" disabled={!zoom} onClick={() => setZoom(null)}>
          Show whole session
        </button>
      </div>

      <h3>EtCO2 and RR per breath</h3>
      <Chart
        {...shared}
        label="EtCO2 and respiratory rate per breath"
        height={190}
        data={trendData}
        scales={{ co2: { auto: true }, rr: { auto: true } }}
        axes={[xAxis, axis('co2', 3, 'EtCO2 mmHg'), axis('rr', 1, 'RR /min')]}
        series={[
          { label: 'Elapsed', value: (_u, v) => (v === null ? '--' : el(v)) },
          { label: 'EtCO2', scale: 'co2', stroke: C.co2, width: 1, points: pts(C.co2), value: fmtVal(1, 'mmHg') },
          { label: 'RR', scale: 'rr', stroke: C.text, width: 1, value: fmtVal(1, '/min') },
        ]}
      />
      <h3>Tidal volume and minute ventilation</h3>
      <Chart
        {...shared}
        label="Tidal volume and minute ventilation per breath"
        height={170}
        data={ventData}
        scales={{ vt: { auto: true }, mv: { auto: true } }}
        axes={[xAxis, axis('vt', 3, 'VT mL'), axis('mv', 1, 'MV L/min')]}
        series={[
          { label: 'Elapsed', value: (_u, v) => (v === null ? '--' : el(v)) },
          { label: 'VT', scale: 'vt', stroke: C.pneumo, width: 1, points: pts(C.pneumo), value: fmtVal(0, 'mL') },
          { label: 'MV', scale: 'mv', stroke: C.flow, width: 1.5, value: fmtVal(2, 'L/min') },
        ]}
      />
      <h3>Waveforms {zoom ? '' : '(zoom in for full detail)'}</h3>
      <Chart
        {...shared}
        label="CO2, flow and pneumotach waveforms"
        height={220}
        data={waves}
        scales={{ co2: { auto: true }, flow: { auto: true } }}
        axes={[xAxis, axis('co2', 3, 'CO2 mmHg'), axis('flow', 1, 'Flow L/min')]}
        series={[
          { label: 'Elapsed', value: (_u, v) => (v === null ? '--' : el(v)) },
          { label: 'CO2', scale: 'co2', stroke: C.co2, width: 1, value: fmtVal(1, 'mmHg') },
          { label: 'Flow', scale: 'flow', stroke: C.flow, width: 1, value: fmtVal(1, 'L/min') },
          { label: 'Pneumotach', scale: 'flow', stroke: C.pneumo, width: 1, value: fmtVal(1, 'L/min') },
        ]}
      />

      <h3 style={{ marginTop: 14 }}>Export</h3>
      <div className="row-actions" style={{ margin: '6px 0 14px' }}>
        <button className="btn primary" onClick={() => void exportSession(rec, 'session')}>
          Whole session (one .csv)
        </button>
        <button className="btn" onClick={() => void exportSession(rec, 'breaths')}>
          Breaths (.csv)
        </button>
        <button className="btn" onClick={() => void exportSession(rec, 'events')}>
          Alarms & events (.csv)
        </button>
        <button
          className="btn"
          disabled={!zoom}
          onClick={async () => {
            const name = `${base}${rangeTag}.csv`;
            const r = await saveBlob(new Blob(exportSessionParts(parsed, zoom, __APP_VERSION__), { type: 'text/csv' }), name);
            if (r !== 'declined') showToast(`Saved ${name}.`);
          }}
        >
          Zoomed range, all data (.csv)
        </button>
        <button className="btn" disabled={!zoom} onClick={() => void downloadText(breathsCsv(parsed, zoom), `${base}${rangeTag}_breaths.csv`, 'text/csv')}>
          Zoomed range, breaths (.csv)
        </button>
        <button className="btn" disabled={!zoom} onClick={() => void downloadText(eventsCsv(parsed, zoom), `${base}${rangeTag}_events.csv`, 'text/csv')}>
          Zoomed range, events (.csv)
        </button>
        <button className="btn" disabled={!parsed.n} onClick={() => onReplay(parsed, rec.fileName)}>
          Replay on the monitor
        </button>
      </div>

      <h3>Alarms and events ({parsed.events.length})</h3>
      <div className="loglist" style={{ maxHeight: 280, marginTop: 6 }}>
        {parsed.events.length ? (
          parsed.events.map((e, i) => (
            <div key={i} className={eventType(e.text).startsWith('ALARM') ? 'alarm' : ''}>
              <button className="linkish" onClick={() => setZoom({ t0: Math.max(0, e.timeS - 30), t1: Math.min(full.t1, e.timeS + 30) })} title="Zoom to 30 s either side">
                {el(e.timeS)}
              </button>{' '}
              {e.text}
            </div>
          ))
        ) : (
          <div>No events.</div>
        )}
      </div>
    </div>
  );
}

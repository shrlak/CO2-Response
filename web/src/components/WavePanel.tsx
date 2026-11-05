import { useEffect, useRef } from 'react';
import type { ChannelId } from '../config/channels';
import { monitor } from '../engine/monitor';
import { SweepRenderer } from '../render/sweep';

interface Props {
  id: ChannelId;
  label: string;
  unit: string;
  colorVar: string;
  min: number;
  max: number;
  ticks: number[];
  signed?: boolean;
  limit?: number | null;
  sweepSec: number;
  rateHz: number;
  /** changes when the theme changes so colors are re-read */
  theme: string;
  tag?: string | null;
}

export function WavePanel({ id, label, unit, colorVar, min, max, ticks, signed, limit, sweepSec, rateHz, theme, tag }: Props) {
  const fg = useRef<HTMLCanvasElement>(null);
  const bg = useRef<HTMLCanvasElement>(null);
  const r = useRef<SweepRenderer | null>(null);
  const tickKey = ticks.join(',');

  useEffect(() => {
    const rend = new SweepRenderer(fg.current!, bg.current!, { min: 0, max: 1, ticks: [], colorVar, seconds: 12, rateHz: 100 });
    r.current = rend;
    const off = monitor.registerRenderer(id, rend);
    return () => {
      off();
      rend.dispose();
      r.current = null;
    };
  }, [id, colorVar]);

  useEffect(() => {
    r.current?.setOptions({ min, max, ticks: tickKey.split(',').map(Number), signed, limit, seconds: sweepSec, rateHz });
  }, [min, max, tickKey, signed, limit, sweepSec, rateHz, theme, colorVar]);

  return (
    <div className="wave">
      <div className="wlabel">
        {label}
        <span>{unit}</span>
      </div>
      {tag ? <span className="wtag">{tag}</span> : null}
      <canvas ref={bg} aria-hidden="true" />
      <canvas ref={fg} role="img" aria-label={`${label} waveform`} />
    </div>
  );
}

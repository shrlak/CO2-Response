import type { ReactNode } from 'react';

interface Props {
  label: string;
  unit: string;
  value: string;
  big?: boolean;
  stale?: boolean;
  alarm?: 'high' | 'medium' | null;
  flash?: boolean;
  limits?: { hi: string; lo: string } | null;
  qualifier?: ReactNode;
  raw?: string | null;
  id?: string;
}

export function NumericTile({ label, unit, value, big, stale, alarm, flash, limits, qualifier, raw, id }: Props) {
  return (
    <div className={`tile ${big ? 'big' : ''} ${stale ? 'stale' : ''} ${alarm ? `alarm-${alarm}` : ''}`} data-testid={id}>
      <div className="tlabel">
        <span>
          <b>{label}</b>
          <span className="unit">{unit}</span>
        </span>
        {limits ? (
          <span className="limits" aria-label="alarm limits">
            <span className="hi">{limits.hi}</span>
            <span>{limits.lo}</span>
          </span>
        ) : null}
      </div>
      <div className={`val ${flash ? 'flash' : ''}`} aria-live="off">
        {value}
      </div>
      {qualifier || raw ? (
        <div className="qual">
          <span>{qualifier}</span>
          {raw ? <span className="raw">{raw}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

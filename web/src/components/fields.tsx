import { useEffect, useState, type ReactNode } from 'react';

interface NumProps {
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  disabled?: boolean;
  id?: string;
  ariaLabel?: string;
  width?: number;
}

/** Number input that only commits on blur or Enter, so half-typed values never reach the engine. */
export function NumInput({ value, onChange, step = 1, min, max, disabled, id, ariaLabel, width }: NumProps) {
  const [draft, setDraft] = useState(Number.isFinite(value) ? String(value) : '');
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setDraft(Number.isFinite(value) ? String(+value.toPrecision(8)) : '');
  }, [value, focused]);
  const commit = () => {
    const n = Number(draft);
    if (draft.trim() === '' || !Number.isFinite(n)) {
      setDraft(Number.isFinite(value) ? String(value) : '');
      return;
    }
    const c = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n));
    onChange(c);
  };
  return (
    <input
      id={id}
      type="number"
      aria-label={ariaLabel}
      step={step}
      min={min}
      max={max}
      disabled={disabled}
      value={draft}
      style={width ? { width } : undefined}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false);
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

export function Field({ label, htmlFor, hint, children }: { label: ReactNode; htmlFor?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span className="flabel">{label}</span>}
      <div className="ctl">{children}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

export function NumberField(props: NumProps & { label: ReactNode; unit?: string; hint?: ReactNode; id: string }) {
  const { label, unit, hint, ...rest } = props;
  return (
    <Field label={label} htmlFor={props.id} hint={hint}>
      <NumInput {...rest} />
      {unit !== undefined ? <span className="u">{unit}</span> : null}
    </Field>
  );
}

export function Seg<T extends string | number>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SegField<T extends string | number>(props: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; hint?: ReactNode }) {
  return (
    <Field label={props.label} hint={props.hint}>
      <Seg value={props.value} options={props.options} onChange={props.onChange} label={props.label} />
    </Field>
  );
}

export function ToggleField({ id, label, checked, onChange, hint }: { id: string; label: ReactNode; checked: boolean; onChange: (v: boolean) => void; hint?: ReactNode }) {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </Field>
  );
}

export function SelectField<T extends string | number>({ id, label, value, options, onChange, hint }: { id: string; label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; hint?: ReactNode }) {
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <select
        id={id}
        className="wide"
        value={String(value)}
        onChange={(e) => {
          const o = options.find((x) => String(x.value) === e.target.value);
          if (o) onChange(o.value);
        }}
      >
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

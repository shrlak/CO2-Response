import { useEffect, useState } from 'react';
import { onToast } from './toast';

export function Toast() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => onToast(setMsg), []);
  if (!msg) return null;
  return (
    <div className="toast" role="status">
      {msg}
    </div>
  );
}

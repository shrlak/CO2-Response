import { useSyncExternalStore } from 'react';
import { monitor, type Snapshot } from './monitor';

export function useMonitor(): Snapshot {
  return useSyncExternalStore(monitor.subscribe, monitor.getSnapshot);
}

export { monitor };

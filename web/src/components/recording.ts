import { monitor } from '../engine/monitor';
import { showToast } from './toast';

/** Start or stop recording; on stop the session is saved as one CSV file. */
export async function toggleRecording(): Promise<void> {
  if (monitor.recorder.active) {
    const { fileName, result } = await monitor.stopRecording();
    const where = 'It is also kept in Settings > Data & recordings.';
    if (result === 'downloaded' || result === 'saved') showToast(`Saved ${fileName}. ${where}`, 5000);
    else if (result === 'declined') showToast(`${fileName} was not saved to a file. ${where}`, 6000);
    else showToast(`Could not save ${fileName}. Export it from Settings > Data & recordings.`, 7000);
  } else {
    await monitor.startRecording();
    showToast(`Recording ${monitor.recorder.fileName}. Saved to this browser every second.`);
  }
}

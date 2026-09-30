import * as BackgroundFetch from 'expo-background-fetch';
import * as TaskManager from 'expo-task-manager';
import { flushQueue } from './sync';

export const SYNC_TASK = 'findback-background-sync';

TaskManager.defineTask(SYNC_TASK, async () => {
  try {
    await flushQueue();
    return BackgroundFetch.BackgroundFetchResult.NewData;
  } catch {
    return BackgroundFetch.BackgroundFetchResult.Failed;
  }
});

export async function registerBackgroundSync() {
  const registered = await TaskManager.isTaskRegisteredAsync(SYNC_TASK);
  if (!registered) {
    await BackgroundFetch.registerTaskAsync(SYNC_TASK, { minimumInterval: 15 * 60, stopOnTerminate: false, startOnBoot: true });
  }
}

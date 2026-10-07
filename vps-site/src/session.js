import { launchStep, progressReporter } from './launch-progress.js';

// Socket completion confirms delivery, not payload startup.
export async function loadRequiredPayloads(runtime, options) {
  const { send, report, markSent } = options;
  const emit = progressReporter(options.onProgress);
  const wait = options.wait || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  report("Loading Kstuff…");
  await launchStep(emit, 'kstuff', async () => {
    options.beforePayload?.('kstuff.elf');
    await send(runtime, "kstuff.elf");
    markSent("kstuff.elf");
  }, { detail: 'Delivery only; startup is not confirmed.' });
  report("Kstuff sent. Allowing 10 seconds for startup; watch for its welcome notification.");
  await launchStep(emit, 'kstuff-wait', () => wait(10000), { detail: 'Existing 10-second allowance, not a startup confirmation. Watch for the welcome notification.' });
  await launchStep(emit, 'ppr', async () => {
    options.beforePayload?.('a53_ppr_install.elf');
    if (typeof options.confirmPpr !== 'function') throw Error('A53 PPR requires confirmation before mounting games.');
    report("Loading A53 PPR patch…");
    await send(runtime, "a53_ppr_install.elf");
    markSent("a53_ppr_install.elf");
  }, { enabled: options.ppr === true, detail: 'Delivery only; wait for the success notification.' });
  await launchStep(emit, 'ppr-confirm', async () => {
    await options.confirmPpr();
    options.confirmedPpr?.();
  }, { enabled: options.ppr === true, waiting: true, detail: 'Confirm the A53 PPR success notification with CONTINUE. On failure, restart your PS5.', completedDetail: 'Manually confirmed with CONTINUE; no automatic startup check.' });
  report("Loading ShadowMountPlus…");
  await launchStep(emit, 'shadowmount', async () => {
    options.beforePayload?.('shadowmountplus.elf');
    await send(runtime, "shadowmountplus.elf");
    markSent("shadowmountplus.elf");
  }, { detail: 'Delivery only; app discovery is not confirmed.' });
}

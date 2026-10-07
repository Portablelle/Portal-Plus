// Socket completion confirms delivery, not payload startup.
export async function loadRequiredPayloads(runtime, options) {
  const { send, report, markSent } = options;
  const wait = options.wait || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  report("Loading Kstuff…");
  options.beforePayload?.('kstuff.elf');
  await send(runtime, "kstuff.elf");
  markSent("kstuff.elf");
  report("Kstuff sent. Allowing 10 seconds for startup; watch for its welcome notification.");
  await wait(10000);
  if (options.ppr) {
    options.beforePayload?.('a53_ppr_install.elf');
    if (typeof options.confirmPpr !== 'function') throw Error('A53 PPR requires confirmation before mounting games.');
    report("Loading A53 PPR patch…");
    await send(runtime, "a53_ppr_install.elf");
    markSent("a53_ppr_install.elf");
    await options.confirmPpr();
    options.confirmedPpr?.();
  }
  report("Loading ShadowMountPlus…");
  options.beforePayload?.('shadowmountplus.elf');
  await send(runtime, "shadowmountplus.elf");
  markSent("shadowmountplus.elf");
}

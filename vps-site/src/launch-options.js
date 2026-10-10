const STORAGE_KEY = 'botty.launch-services.v1';
const SERVICES = ['ftp', 'rtorrent', 'cheatrunner', 'ppr', 'codex', 'botty'];

export function supportsPpr(firmware) {
  return /^\d+\.\d{2}$/.test(firmware || '') && Number(firmware) <= 11.40;
}

export function normalizeLaunchServices(value) {
  return Object.fromEntries(SERVICES.map(name => [name, ['ppr', 'codex'].includes(name) ? value?.[name] === true : value?.[name] !== false]));
}

export function bindLaunchOptions(document, browser) {
  const fieldset = document.getElementById('launch-services');
  const inputs = SERVICES.map(name => fieldset.querySelector('input[name="' + name + '"]'));
  const storageStatus = document.getElementById('launch-options-storage');
  let services;
  try {
    services = normalizeLaunchServices(JSON.parse(browser.localStorage.getItem(STORAGE_KEY)));
  } catch {
    services = normalizeLaunchServices();
    storageStatus.hidden = false;
    storageStatus.textContent = 'Choices apply to this launch. Browser storage is unavailable or saved choices could not be read.';
  }
  const ppr = inputs.find(input => input.name === 'ppr');
  const rtorrent = inputs.find(input => input.name === 'rtorrent');
  const botty = inputs.find(input => input.name === 'botty');
  ppr.disabled = !supportsPpr(browser.fw_str);
  const pprAvailability = document.getElementById('ppr-availability');
  pprAvailability.hidden = !ppr.disabled;
  pprAvailability.textContent = ppr.disabled
    ? 'A53 PPR is unavailable: it supports PS5 firmware up to 11.40 only. This browser firmware is incompatible or could not be identified.'
    : '';
  const read = () => ({ ...services, rtorrent: services.rtorrent || services.botty, ppr: services.ppr && !ppr.disabled });
  const summarize = () => {
    rtorrent.disabled = botty.checked;
    document.getElementById('rtorrent-option').hidden = botty.checked;
    document.getElementById('rtorrent-included').hidden = !botty.checked;
    const selected = read();
    // Restored patch choices must expose their prerequisites before launch.
    if (selected.ppr) document.getElementById('launch-advanced').open = true;
    const names = [];
    if (selected.botty) names.push('Botty+ (includes rTorrent)');
    if (selected.ftp) names.push('FTP');
    if (selected.rtorrent && !selected.botty) names.push('rTorrent');
    if (selected.cheatrunner) names.push('CheatRunner');
    if (selected.ppr) names.push('A53 PPR');
    if (selected.codex) names.push('Codex PS5');
    document.getElementById('launch-options-summary').textContent = names.join(', ') || 'Jailbreak only';
  };
  for (const input of inputs) {
    input.checked = services[input.name] && !input.disabled;
    input.addEventListener('change', () => {
      services[input.name] = input.checked;
      summarize();
      try {
        browser.localStorage.setItem(STORAGE_KEY, JSON.stringify(services));
        storageStatus.hidden = true;
        storageStatus.textContent = '';
      } catch {
        storageStatus.hidden = false;
        storageStatus.textContent = 'Choices apply to this launch only. Browser storage is unavailable.';
      }
    });
  }
  summarize();
  return { lock() {
    const selected = read();
    fieldset.disabled = true;
    document.getElementById('launch-options').open = false;
    return selected;
  } };
}

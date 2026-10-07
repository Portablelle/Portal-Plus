export function postLaunchInstructions(summary) {
  const components = summary.components;
  const instructions = [];
  const unavailable = component => component?.state === 'deferred'
    ? 'Not started this session. Let active work finish, then try in a later idle session. Your current work continues.'
    : component?.state === 'update_pending'
    ? 'An update is pending. Follow the component’s update guidance in the session result before opening the updated app.'
    : 'Not available from this launch. Review the session log before trying again; do not interrupt active work.';
  const pending = 'The update applies next console session. The current service is preserved and active work continues.';

  if (components.native?.state !== 'not_requested' && components.native) {
    const ready = components.native.state === 'ready' && components.manager?.state === 'ready';
    instructions.push({ name: 'Botty+', text: ready
      ? 'Press PS to return to the home screen, then open Botty+. Allow time for the home screen to refresh; app visibility is not confirmed.'
      : unavailable(components.native.state === 'ready' ? components.manager : components.native) });
    if (components.manager?.state === 'update_pending') instructions[instructions.length - 1].text += ' ' + pending;
  }
  for (const id of ['codex', 'cheatrunner', 'ftp']) {
    const component = components[id];
    if (!component || component.state === 'not_requested') continue;
    let text = unavailable(component);
    if (component.state === 'ready') {
      if (id === 'codex') text = 'Press PS to return to the home screen. Find Codex PS5 in the game library and open it there; app visibility is not confirmed.';
      if (id === 'cheatrunner') text = 'The service is confirmed ready. Use Open CheatRunner below to access it in this browser; its home-screen tile may not be visible yet.';
      if (id === 'ftp') text = 'FTP is confirmed on port 2121. Use your PS5’s network settings to find its address for your FTP client. The portal server address is not your console address.';
    }
    if (component.state === 'update_pending') text += ' ' + pending + (component.detail ? ' ' + component.detail : '');
    instructions.push({ name: component.name, text });
  }
  return instructions;
}

export function renderPostLaunch(document, summary) {
  const container = document.getElementById('post-launch');
  const list = document.getElementById('post-launch-instructions');
  list.replaceChildren();
  for (const instruction of postLaunchInstructions(summary)) {
    const row = document.createElement('li');
    const title = document.createElement('h3');
    title.textContent = instruction.name;
    const text = document.createElement('p');
    text.textContent = instruction.text;
    row.appendChild(title);
    row.appendChild(text);
    list.appendChild(row);
  }
  container.hidden = false;
}

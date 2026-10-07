export function diagnosticError(code, message, action = '') {
  return Object.assign(new Error(message), { code, action });
}

export async function atStage(stage, operation) {
  try { return await operation(); }
  catch (error) {
    if (error?.stage) throw error;
    throw Object.assign(new Error(error?.message || String(error)), {
      cause: error, stage, code: error?.code || 'STEP_FAILED', action: error?.action || '', logMessage: error?.logMessage,
    });
  }
}

export function failureStatus(error) {
  const stage = error?.stage || 'Session setup';
  const action = error?.action || (['WebKit exploit', 'Kernel exploit', 'Jailbreak'].includes(stage)
    ? 'Console state is uncertain. Restart your PS5 before another launch.'
    : 'The cause and console state are not fully known. Check the session log; do not launch again in this session.');
  return `${stage} failed [${error?.code || 'STEP_FAILED'}]. ${action}`;
}

export function safeLog(message) {
  return String(message)
    .replace(/(https?:\/\/)[^\s/]*@/gi, '$1[redacted]@')
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9+/_.=-]+/gi, '$1 [redacted]')
    .replace(/(["']?(?:password|previousPassword|rpc-password|token|api[_-]?key|secret|authorization|cookie)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&}]+)/gi, '$1[redacted]');
}

export function optionalFailure(component, error) {
  return {
    ready: false, code: error?.code || 'OPTIONAL_SETUP_FAILED',
    reason: safeLog(error?.message || String(error)),
    diagnostic: `${component} is unavailable [${error?.code || 'OPTIONAL_SETUP_FAILED'}]. Your session remains usable. ${error?.action || 'The component state is unconfirmed. Check the session log; do not launch again in this session.'}`,
  };
}

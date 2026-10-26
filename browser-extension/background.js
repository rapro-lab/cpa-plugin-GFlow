// background.js - GFlow Login Assistant background service
importScripts('common.js');

const ALARM = 'gflow-cookie-sync';
const FLOW_URL = 'https://flow.google.com/projects';
let activeSync = null;

// Logging system
const Logger = {
  async log(level, message, details = null) {
    const timestamp = new Date().toISOString();
    const safeDetails = sanitizeLogDetails(details);
    const logEntry = { timestamp, level, message, details: safeDetails };
    console.log(`[${level}] ${message}`, safeDetails || '');
    try {
      const { logs = [] } = await chrome.storage.local.get(['logs']);
      const updatedLogs = [logEntry, ...logs].slice(0, 100);
      await chrome.storage.local.set({ logs: updatedLogs });
    } catch (_) {}
  },
  info(msg, d) { return this.log('INFO', msg, d); },
  error(msg, d) { return this.log('ERROR', msg, d); },
  success(msg, d) { return this.log('SUCCESS', msg, d); },
  async getLogs() {
    const { logs = [] } = await chrome.storage.local.get(['logs']);
    return logs;
  },
  async clearLogs() {
    await chrome.storage.local.set({ logs: [] });
  }
};

function sanitizeLogDetails(value, key = '') {
  if (value == null) return value;
  if (/^(authorization|connectionToken|google_cookies|session_token|password|secret)$/i.test(key)) {
    return '[REDACTED]';
  }
  if (Array.isArray(value)) return value.map(item => sanitizeLogDetails(item));
  if (typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, sanitizeLogDetails(v, k)])
    );
  }
  if (typeof value === 'string') {
    return value
      .replace(/Bearer\s+[^\s]+/gi, 'Bearer [REDACTED]')
      .replace(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi, email => email[0] + '***@' + email.split('@')[1])
      .replace(/((?:^|[;\s])(?:__Secure-)?(?:OSID|SID|HSID|SSID|APISID|SAPISID)=)[^;\s]+/gi, '$1[REDACTED]');
  }
  return value;
}

function safeServerDiagnostic(raw) {
  const result = {};
  if (['bootstrap', 'rpc'].includes(raw?.phase)) result.phase = raw.phase;
  if (['nzlxg', 'jHPbke'].includes(raw?.rpc)) result.rpc = raw.rpc;
  if (['redirect', 'http_auth', 'missing_metadata'].includes(raw?.reason)) result.reason = raw.reason;
  for (const key of ['http_status', 'rpc_code', 'browser_http_status', 'browser_rpc_code']) {
    if (Number.isInteger(raw?.[key]) && raw[key] >= 0 && raw[key] < 600) result[key] = raw[key];
  }
  for (const key of ['bootstrap_identity', 'xsrf_present', 'account_matches', 'browser_credit_ok', 'observed_cookie_header']) {
    if (typeof raw?.[key] === 'boolean') result[key] = raw[key];
  }
  for (const key of ['rpc_cookie_count', 'duplicate_cookie_names']) {
    if (Number.isInteger(raw?.[key]) && raw[key] >= 0 && raw[key] <= 1000) result[key] = raw[key];
  }
  return result;
}

async function publicState() {
  const state = await chrome.storage.local.get(['config', 'lastSync']);
  const config = state.config || {};
  return {
    configured: Boolean(config.connectionToken),
    loginAccount: config.loginAccount || '',
    apiUrl: config.apiUrl || '',
    autoSync: config.autoSync !== false,
    lastSync: state.lastSync || null
  };
}

async function restoreAlarm() {
  const { config } = await chrome.storage.local.get('config');
  await chrome.alarms.clear(ALARM);
  if (config?.connectionToken && config.autoSync) {
    await chrome.alarms.create(ALARM, { periodInMinutes: 60 });
  }
}

async function saveConfiguration(input) {
  const state = await chrome.storage.local.get('config');
  const connection = validateConnection(input.connection || state.config);
  const config = {
    ...connection,
    loginAccount: validateEmail(input.loginAccount),
    autoSync: Boolean(input.autoSync)
  };
  if (!await chrome.permissions.contains({ origins: [connectionPermission(config.apiUrl)] })) {
    throw new Error('The extension needs permission to connect to the local GFlow');
  }
  await chrome.storage.local.set({ config });
  await restoreAlarm();
  await Logger.info('GFlow connection config saved', { apiUrl: config.apiUrl, loginAccount: config.loginAccount });
  return publicState();
}

async function waitForTabReady(tabId, maxWaitMs = 15000) {
  const startTime = Date.now();
  while (Date.now() - startTime < maxWaitMs) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab && tab.status === 'complete') return tab;
    } catch (_) {
      break;
    }
    await new Promise(r => setTimeout(r, 500));
  }
}

async function closeTemporaryTab(tab) {
  if (tab && typeof tab.id === 'number') {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
  }
}

// The listener only covers nzlxg requests from this temporary tab; Cookies exist in memory only.
async function readObservedFlowCookies(tabId, email) {
  if (!chrome.webRequest?.onBeforeSendHeaders) throw new Error('Reload the GFlow Login Assistant to enable request reading permission');
  let cookieHeader='';
  const listener = details => {
    const url=new URL(details.url);
    if (details.tabId !== tabId || details.method !== 'POST' || url.origin !== 'https://flow.google.com'
        || url.pathname !== '/_/AiSandboxAngularFrontend/data/batchexecute' || url.searchParams.get('rpcids') !== 'nzlxg') return;
    cookieHeader=(details.requestHeaders || []).find(header=>header.name.toLowerCase()==='cookie')?.value || '';
  };
  chrome.webRequest.onBeforeSendHeaders.addListener(listener,
    {urls:['https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute*'],tabId}, ['requestHeaders','extraHeaders']);
  try {
    const diagnostic = await chrome.tabs.sendMessage(tabId, {action:'gflow-credit-check',email});
    // Keep the browser's real order and duplicate Cookie names; reject empty values and control characters.
    if (!cookieHeader || cookieHeader.length > 262144 || /[\x00-\x1f\x7f]/.test(cookieHeader)) {
      throw new Error('Chrome did not provide the Cookie for this Flow request. Make sure the extension management page allowed the new permission, then retry.');
    }
    const safe = {success:diagnostic?.success === true};
    if (['identity','network','rpc'].includes(diagnostic?.phase)) safe.phase=diagnostic.phase;
    for (const key of ['http_status','rpc_code']) if (Number.isInteger(diagnostic?.[key]) && diagnostic[key]>=0 && diagnostic[key]<600) safe[key]=diagnostic[key];
    return {cookieHeader,diagnostic:safe};
  } finally {
    chrome.webRequest.onBeforeSendHeaders.removeListener(listener);
  }
}

async function executeSync() {
  const { config } = await chrome.storage.local.get('config');
  if (!config) throw new Error('Copy the config from GFlow and save it first');
  const connection = validateConnection(config);
  const loginAccount = validateEmail(config.loginAccount);

  await Logger.info('Starting GFlow session sync...', { account: loginAccount });

  // Pin this sync to one window and cookie store, so the background tab and the read come from the same window.
  const sourceTabs = chrome.tabs?.query ? await chrome.tabs.query({ active: true, currentWindow: true }) : [];
  const sourceTab = sourceTabs[0];
  let storeId;
  if (sourceTab && chrome.cookies?.getAllCookieStores) {
    const stores = await chrome.cookies.getAllCookieStores();
    storeId = stores.find(item => item.tabIds.includes(sourceTab.id))?.id;
    if (!storeId) throw new Error('Cannot determine the session for the current window. Open the assistant in the window where Flow is running.');
  }
  let tempTab = null;
  let rawCookies, frontend, observed;
  let pageUrl = FLOW_URL;
  try {
    const current = new URL(sourceTab?.url || '');
    if (current.origin === 'https://flow.google.com' && /^\/project\/[a-f0-9-]{36}$/.test(current.pathname)) pageUrl = current.origin + current.pathname;
  } catch (_) {}
  try {
    if (chrome.tabs?.create && chrome.tabs?.get) {
      await Logger.info('Refreshing the Flow session in the background…');
      tempTab = await chrome.tabs.create({ url: pageUrl, active: false,
        ...(Number.isInteger(sourceTab?.windowId) ? { windowId: sourceTab.windowId } : {}) });
      const loadedTab = await waitForTabReady(tempTab.id);
      if (!loadedTab) throw new Error('Timed out loading the Flow page. Make sure the site opens normally, then retry.');
      if (new URL(loadedTab.url || '').hostname !== 'flow.google.com') {
        throw new Error('The Flow page requires signing in again. Sign in in this browser first.');
      }
      await new Promise(resolve => setTimeout(resolve, 5000));
      if (chrome.tabs.sendMessage) {
        let session;
        try { session = await chrome.tabs.sendMessage(tempTab.id, {action:'gflow-page-session',email:loginAccount}); }
        catch (_) { throw new Error('Cannot read the Flow page session. Make sure the assistant was reloaded, then retry.'); }
        if (!session?.signed_in) throw new Error('The Flow page did not return a signed-in account. Sign in to Flow in this browser, then sync.');
        if (session.account_matches !== true) throw new Error('The current Flow account does not match the email entered in the assistant. Check the email or use a separate browser profile.');
        if (!session.frontend) throw new Error('The Flow page authentication fields did not load fully. Refresh Flow and retry.');
        frontend = session.frontend;
        observed = await readObservedFlowCookies(tempTab.id, loginAccount);
        await Logger.info('Flow browser credit check completed', observed.diagnostic);
      }
      // Determine the cookie store from the actual warm-up tab; keep it consistent for scheduled syncs with no active tab.
      if (chrome.cookies?.getAllCookieStores) {
        const stores = await chrome.cookies.getAllCookieStores();
        const warmedStore = stores.find(item => item.tabIds.includes(tempTab.id));
        if (!warmedStore || (storeId && storeId !== warmedStore.id)) {
          throw new Error('The Flow page and the current window have different sessions. Sync from the window where Flow is running.');
        }
        storeId = warmedStore.id;
      }
    }
    if (!observed) {
      // Compatibility with test/older browsers that lack the page message API; current Chrome uses the real request headers.
      const scopes = [FLOW_URL, 'https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute'];
      const groups = [];
      for (const url of scopes) {
        groups.push(await chrome.cookies.getAll({ url, ...(storeId ? { storeId } : {}) }));
      }
      rawCookies = groups.flat();
    }
    const googleCookies = observed?.cookieHeader || serializeFlowCookies(rawCookies);
    await Logger.info('Cookie extracted. Submitting to the local GFlow core...');

    const payload = {
      login_account: loginAccount,
      google_cookies: googleCookies,
      protocol_mode: 'protocol',
      user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      ...(frontend ? {frontend} : {}),
      ...(observed ? {browser_check:observed.diagnostic,cookie_source:'browser_request'} : {})
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120000);
    let response;
    try {
      response = await fetch(connection.apiUrl, {
        method: 'POST',
        redirect: 'error',
        cache: 'no-store',
        credentials: 'omit',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + connection.connectionToken
        },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
    } catch (error) {
      const errText = error.name === 'AbortError' ? 'GFlow sync timed out. Check the proxy settings and retry.' : 'Cannot reach the local GFlow. Make sure the EasyCPA core is running.';
      await Logger.error(errText);
      throw new Error(errText);
    } finally {
      clearTimeout(timer);
    }

    let result = null;
    try { result = await response.json(); } catch (_) {}
    if (!response.ok || result?.success !== true) {
      if (response.status === 401 || response.status === 403) {
        await Logger.error('Connection credential expired');
        throw new Error('The connection credential expired. Go back to GFlow and copy the extension config again.');
      }
      if (response.status === 404) {
        await Logger.error('Endpoint not found');
        throw new Error('This GFlow version does not support extension sync. Update the plugin.');
      }
      const detail = result?.error;
      const safeMessage = typeof detail?.message === 'string'
        && detail.message.length <= 500
        && !/(cookie\s*[:=]|bearer\s|connectionToken|access_token|https?:\/\/|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i.test(detail.message)
        ? detail.message : null;
      const code = /^gflow_[a-z_]+$/.test(detail?.code || '') ? detail.code : null;
      const errorMsg = safeMessage
        ? safeMessage + (code ? ' [' + code + ']' : '')
        : 'GFlow sync failed (HTTP ' + response.status + '). Check the log for a detailed diagnosis.';
      await Logger.error('Sync failed', { error: errorMsg, diagnostic: safeServerDiagnostic(detail?.diagnostic) });
      throw new Error(errorMsg);
    }

    const actionMsg = result.action === 'updated' ? 'GFlow account session updated' : 'GFlow account added';
    await Logger.success(`✅ ${actionMsg}`, { action: result.action });
    return { success: true, action: result.action, message: actionMsg };
  } finally {
    await closeTemporaryTab(tempTab);
  }
}

function syncNow() {
  if (activeSync) return activeSync;
  activeSync = (async () => {
    let result;
    try {
      result = await executeSync();
    } catch (error) {
      result = { success: false, message: error.message };
    }
    await chrome.storage.local.set({
      lastSync: { success: result.success, message: result.message, timestamp: Date.now() }
    });
    if (chrome.action?.setBadgeText) {
      await chrome.action.setBadgeText({ text: result.success ? 'OK' : '!' });
      await chrome.action.setBadgeBackgroundColor({ color: result.success ? '#0d9488' : '#dc2626' });
    }
    return result;
  })().finally(() => { activeSync = null; });
  return activeSync;
}

chrome.runtime.onMessage.addListener((request, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return false;
  const action = request.action;
  if (action === 'state' || action === 'getConfig') {
    publicState().then(result => respond({ ok: true, success: true, result, config: result }), () => respond({ ok: false, success: false }));
    return true;
  }
  if (action === 'save' || action === 'saveConfig') {
    saveConfiguration(request.config).then(result => respond({ ok: true, success: true, result, config: result }), e => respond({ ok: false, success: false, message: e.message, error: e.message }));
    return true;
  }
  if (action === 'sync' || action === 'testNow') {
    syncNow().then(result => respond({ ok: true, success: result.success, result, ...result }), e => respond({ ok: false, success: false, message: e.message, error: e.message }));
    return true;
  }
  if (action === 'forget') {
    chrome.storage.local.remove(['config', 'lastSync'])
      .then(restoreAlarm)
      .then(publicState)
      .then(result => respond({ ok: true, result }), () => respond({ ok: false }));
    return true;
  }
  if (action === 'getLogs') {
    Logger.getLogs().then(logs => respond({ ok: true, success: true, logs }), () => respond({ ok: false, logs: [] }));
    return true;
  }
  if (action === 'clearLogs') {
    Logger.clearLogs().then(() => respond({ ok: true, success: true }), () => respond({ ok: false }));
    return true;
  }
  return false;
});

if (chrome.alarms?.onAlarm) {
  chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) void syncNow(); });
}
if (chrome.runtime?.onStartup) {
  chrome.runtime.onStartup.addListener(() => { void restoreAlarm(); });
}
if (chrome.runtime?.onInstalled) {
  chrome.runtime.onInstalled.addListener(() => { void restoreAlarm(); });
}

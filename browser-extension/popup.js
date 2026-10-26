// popup.js - GFlow Login Assistant config UI
let paired = false;
let savedApiUrl = '';

function field(id) { return document.getElementById(id); }

function showStatus(text, type = 'info') {
  const el = field('status');
  el.textContent = text;
  el.className = 'status-banner ' + type;
}

function busy(val) {
  field('saveAndSyncBtn').disabled = val;
  field('saveOnlyBtn').disabled = val;
  field('quickFillBtn').disabled = val;
  field('forgetBtn').disabled = val;
}

async function sendMsg(action, config) {
  const resp = await chrome.runtime.sendMessage({ action, config });
  if (resp?.ok !== true) {
    throw new Error(resp?.message || resp?.error || 'Cannot reach the GFlow extension background');
  }
  const result = resp.result ?? resp;
  // ok means the message was delivered; only success === true means the sync itself succeeded.
  if (action === 'sync' && result?.success !== true) {
    throw new Error(result?.message || 'GFlow sync failed. Check the sync log.');
  }
  return result;
}

function parseAndFillConfig(rawText) {
  const text = String(rawText || '').trim();
  if (!text) return false;
  try {
    const data = JSON.parse(text);
    const connection = validateConnection(data);
    const email = data.loginAccount ? validateEmail(data.loginAccount) : '';
    field('apiUrl').value = connection.apiUrl;
    field('connectionToken').value = connection.connectionToken;
    if (email) field('email').value = email;
    return true;
  } catch (_) {
    return false;
  }
}

function renderState(state) {
  paired = state.configured === true;
  savedApiUrl = state.apiUrl || '';
  field('connectionToken').required = !paired;
  field('connectionToken').placeholder = paired ? 'Saved; leave blank to keep the current one' : 'Filled in automatically after pasting the config';
  const isPaired = paired;
  if (state.apiUrl) field('apiUrl').value = state.apiUrl;
  if (state.loginAccount) field('email').value = state.loginAccount;
  if (state.autoSync !== undefined) field('autoSync').checked = Boolean(state.autoSync);
  
  if (state.lastSync) {
    const d = new Date(state.lastSync.timestamp);
    const succeeded = state.lastSync.success === true;
    const timeStr = Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString();
    showStatus(`${succeeded ? 'Sync succeeded' : 'Sync failed'} (${timeStr})\n${state.lastSync.message || ''}`, succeeded ? 'success' : 'error');
  } else if (isPaired) {
    showStatus('Local connection configured. Sign in to Flow, then click "Save and sync now".', 'info');
  } else {
    showStatus('On the GFlow page click "Copy extension config" and paste it here.', 'info');
  }
}

async function saveFromForm() {
  const apiUrl = field('apiUrl').value.trim();
  const connectionToken = field('connectionToken').value.trim();
  const loginAccount = validateEmail(field('email').value);
  const autoSync = field('autoSync').checked;
  let connection;
  if (connectionToken) {
    connection = validateConnection({ product: 'GFlow', apiUrl, connectionToken });
    const granted = await chrome.permissions.request({ origins: [connectionPermission(connection.apiUrl)] });
    if (!granted) throw new Error('The extension needs permission to connect to the local GFlow');
  } else if (!paired || apiUrl !== savedApiUrl) {
    throw new Error('Copy the extension config from GFlow and paste it into the quick import box');
  }
  const state = await sendMsg('save', { connection, loginAccount, autoSync });
  renderState(state);
  field('connectionToken').value = '';
}

document.addEventListener('DOMContentLoaded', async () => {
  try {
    const state = await sendMsg('state');
    renderState(state);
  } catch (err) {
    showStatus(err.message, 'error');
  }

  // Quick import
  field('quickFillBtn').addEventListener('click', () => {
    const val = field('quickPaste').value.trim();
    if (!val) return showStatus('Paste the config text first', 'error');
    if (parseAndFillConfig(val)) {
      field('quickPaste').value = '';
      showStatus('Config filled in. Check the email, then click "Save and sync now".', 'success');
    } else {
      showStatus('Could not parse the config JSON. Make sure it came from "Copy extension config" on the GFlow page.', 'error');
    }
  });

  // Auto-fill while typing in the quick box
  field('quickPaste').addEventListener('input', () => {
    const val = field('quickPaste').value.trim();
    if (val.startsWith('{') && val.endsWith('}')) {
      if (parseAndFillConfig(val)) {
        field('quickPaste').value = '';
        showStatus('Config detected and filled in.', 'success');
      }
    }
  });

  // Save and sync
  field('setup').addEventListener('submit', async (e) => {
    e.preventDefault();
    busy(true);
    showStatus('Connecting to the GFlow backend and syncing the session...', 'info');
    try {
      await saveFromForm();

      showStatus('Syncing the session…', 'info');
      const syncResult = await sendMsg('sync');
      showStatus(`Sync succeeded: ${syncResult.message || ''}`, 'success');
    } catch (err) {
      showStatus(err.message, 'error');
    } finally {
      busy(false);
    }
  });

  // Save only
  field('saveOnlyBtn').addEventListener('click', async () => {
    busy(true);
    try {
      await saveFromForm();
      showStatus('Config saved.', 'success');
    } catch (err) {
      showStatus(err.message, 'error');
    } finally {
      busy(false);
    }
  });

  // Log page
  field('logsBtn').addEventListener('click', () => {
    window.location.href = 'logs.html';
  });

  // Clear config
  field('forgetBtn').addEventListener('click', async () => {
    if (!confirm('Clear the GFlow extension connection config on this machine?')) return;
    try {
      const state = await sendMsg('forget');
      renderState(state);
      field('connectionToken').value = '';
      field('apiUrl').value = '';
      field('email').value = '';
      showStatus('Config cleared.', 'info');
    } catch (err) {
      showStatus(err.message, 'error');
    }
  });
});

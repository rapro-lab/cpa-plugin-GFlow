import json
from pathlib import Path
import shutil
import subprocess
import unittest

PAGE = Path(__file__).resolve().parents[1] / "static/manage.html"
HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const elements = new Map();
function element() {
  return { value: '', textContent: '', innerHTML: '', disabled: false, checked: false,
    style: {}, children: [], append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; }, focus() {}, select() { this.selected = true; } };
}
const events = new Map(), requests = [], intervals = new Map();
let mode = 'ok', copiedConfig = '', clipboardDenied = false;
const location = { origin: 'http://127.0.0.1:8317', host: '127.0.0.1:8317', hash: '', pathname: '/v0/resource/plugins/gflow/manage', search: '' };
const login = JSON.stringify({state:{managementKey:'test-management-secret',apiBase:location.origin}});
let savedLogin = login;
const storage = {
  getItem(name) { assert.equal(name, 'cli-proxy-auth'); return savedLogin; },
  setItem() { throw new Error('Plugin must not persist management credentials'); }
};
const context = vm.createContext({
  document: {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element
  },
  window: { location, localStorage: storage, addEventListener(name, fn) { events.set(name, fn); } },
  navigator: { userAgent: 'synthetic-agent', clipboard: { async writeText(value) {
    if (clipboardDenied) throw new Error('clipboard denied'); copiedConfig = value;
  } } },
  console, AbortController, URL, TextEncoder, TextDecoder,
  setTimeout(callback, milliseconds) { return setTimeout(callback, milliseconds === 120000 ? 25 : milliseconds); },
  clearTimeout,
  setInterval(callback) { const id = intervals.size + 1; intervals.set(id, callback); return id; },
  clearInterval(id) { intervals.delete(id); },
  atob: value => Buffer.from(value, 'base64').toString('binary'),
  fetch: async (path, opts) => {
    requests.push({ path, opts });
    assert.ok(path.startsWith('/v0/management/plugins/gflow/api/'));
    assert.equal(opts.headers.Authorization, 'Bearer test-management-secret');
    let status = mode === 'wrong' ? 401 : 200;
    if (mode === 'config-failure' && path.endsWith('/config')) status = 500;
    if (mode === 'timeout' && path.endsWith('/status')) {
      return new Promise((resolve, reject) => opts.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name:'AbortError'}))));
    }
    if (mode === 'slow-body' && path.endsWith('/status')) {
      return {status:200,ok:true,json:() => new Promise((resolve,reject) => opts.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name:'AbortError'}))))};
    }
    const data = path.endsWith('/status')
      ? { total_tokens: 0, active_tokens: 0, total_credits: 0, version: '3.12.0', python: '/plugin/gflow-data/runtime/gflow-worker' }
      : path.endsWith('/tokens') ? { tokens: [], total: 0 }
      : path.endsWith('/models') ? { models: [] }
      : status === 500 ? { error: { message: 'config temporarily unavailable' } } : {};
    return { status, ok: status === 200, json: async () => data };
  }
});
vm.runInContext(SCRIPT, context);
const el = id => context.document.getElementById(id);
(async () => {
  await events.get('DOMContentLoaded')();
  assert.equal(el('stat-python-ver').textContent, 'Python 3.12.0');
  assert.equal(el('save-settings-button').disabled, false);
  assert.equal(el('connection-setup').hidden, true);
  await context.copyExtensionConfig();
  const pairing = JSON.parse(copiedConfig);
  assert.equal(pairing.connectionToken, 'test-management-secret');
  assert.equal(pairing.apiUrl, location.origin + '/v0/management/plugins/gflow/api/extension-sync');
  assert.ok(requests.every(request => !request.path.includes('test-management-secret')));
  console.log('PASS same-origin host login authenticates management calls without a public bootstrap');

  const beforeLink = requests.length;
  await context.copyExtensionLink(false);
  assert.equal(copiedConfig, location.origin + '/v0/resource/plugins/gflow/extension.zip');
  clipboardDenied = true;
  await context.copyExtensionLink(false);
  assert.equal(el('extension-public-url').selected, true);
  assert.equal(requests.length, beforeLink);
  clipboardDenied = false;
  console.log('PASS extension link remains a static public resource');

  const mask = Buffer.from('cli-proxy-api-webui::secure-storage|' + location.host + '|synthetic-agent');
  const encoded = Buffer.from(login);
  for (let index = 0; index < encoded.length; index++) encoded[index] ^= mask[index % mask.length];
  savedLogin = 'enc::v1::' + encoded.toString('base64');
  await context.initializeManagement();
  assert.equal(el('stat-python-ver').textContent, 'Python 3.12.0');
  assert.ok(savedLogin.startsWith('enc::v1::'));
  console.log('PASS host obfuscated login is read without rewriting storage');

  mode = 'wrong';
  await context.loadAll();
  assert.match(el('connection-status').textContent, /401/);
  assert.equal(el('save-settings-button').disabled, true);
  assert.equal(el('connection-setup').hidden, false);
  console.log('PASS expired host credentials fail closed');

  mode = 'ok'; savedLogin = login;
  await context.initializeManagement();
  assert.equal(el('save-settings-button').disabled, false);
  console.log('PASS corrected host login reconnects');

  mode = 'config-failure';
  await context.loadAll();
  const beforeSave = requests.length;
  await context.saveSettings();
  assert.equal(requests.length, beforeSave);
  console.log('PASS failed configuration loading cannot overwrite settings');

  mode = 'timeout';
  await context.loadAll();
  assert.equal(el('refresh-button').disabled, false);
  assert.equal(el('plugin-version').textContent, 'Connection failed');
  assert.equal(el('tokens-tbody').children[0].children[0].children[0].textContent, 'Backend not responding');
  mode = 'ok';
  await context.refreshManagement();
  assert.equal(el('stat-python-ver').textContent, 'Python 3.12.0');
  console.log('PASS timeouts leave a usable retry button and an honest account state');

  mode = 'slow-body';
  await context.loadAll();
  assert.match(el('connection-status').textContent, /timed out/);
  mode = 'ok';
  await context.refreshManagement();
  console.log('PASS the timeout also bounds reading the response body');

  el('cfg-yescaptcha-key').value = 'unsaved-local-edit';
  const beforeAutomatic = requests.length;
  await context.loadAll(false);
  assert.equal(el('cfg-yescaptcha-key').value, 'unsaved-local-edit');
  assert.ok(!requests.slice(beforeAutomatic).some(request => request.path.endsWith('/config') || request.opts.method !== 'GET'));
  console.log('PASS automatic status polling preserves unsaved settings and only reads state');

  savedLogin = '';
  const beforeMissing = requests.length;
  await context.initializeManagement();
  assert.equal(requests.length, beforeMissing);
  assert.equal(el('add-token-button').disabled, true);
  assert.equal(el('connection-setup').hidden, false);
  console.log('PASS missing login makes no privileged request');

  savedLogin = JSON.stringify({state:{managementKey:'remote-secret',apiBase:'https://other.example'}});
  await context.initializeManagement();
  assert.equal(requests.length, beforeMissing);
  console.log('PASS credentials belonging to a different origin are not forwarded');

  savedLogin = ''; mode = 'ok';
  el('connection-key').value = 'test-management-secret';
  await context.connectManagement();
  assert.equal(el('connection-key').value, '');
  assert.equal(savedLogin, '');
  assert.equal(el('save-settings-button').disabled, false);
  await context.refreshManagement();
  assert.equal(el('stat-python-ver').textContent, 'Python 3.12.0');
  console.log('PASS manual fallback keeps credentials only in memory');

  await assert.rejects(context.apiRequest('https://other.example/api'));
  events.get('pagehide')();
  const beforeClose = requests.length;
  await assert.rejects(context.apiRequest('/v0/management/plugins/gflow/api/status'));
  assert.equal(requests.length, beforeClose);
  console.log('PASS closing the page clears in-memory credentials');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


class ManagementUITest(unittest.TestCase):
    def test_management_connection(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the management UI regression")
        html = PAGE.read_text(encoding="utf-8")
        self.assertNotIn("/v0/resource/plugins/gflow/bootstrap", html)
        self.assertIn("cli-proxy-auth", html)
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(
            [node, "-"], input="const SCRIPT = " + json.dumps(script) + ";\n" + HARNESS,
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("PASS "), 13, result.stdout)
        print(result.stdout, end="")


if __name__ == "__main__":
    unittest.main()

"""执行实际弹窗提交处理，覆盖后台已送达但同步失败的消息。"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const fields = new Map(), events = new Map(), calls = [];
let mode = 'rejected', permission = true;
function field(id) {
  if (!fields.has(id)) fields.set(id, {value:'', checked:true, disabled:false, required:true,
    className:'', textContent:'', addEventListener(name, fn) { this[name] = fn; }});
  return fields.get(id);
}
const state = {configured:true,apiUrl:'http://127.0.0.1:8317/v0/management/plugins/gflow/api/extension-sync', loginAccount:'example@example.test',autoSync:true};
const context = vm.createContext({URL, console, confirm:()=>true,
  document:{getElementById:field,addEventListener(name, fn){events.set(name,fn);}},
  window:{location:{}},
  chrome:{permissions:{async request(){return permission;}}, runtime:{async sendMessage(req){
    calls.push(req);
    if (req.action === 'state' || req.action === 'save') return {ok:true,result:state};
    if (req.action === 'sync') {
      if (mode === 'transport') return {ok:false,message:'The extension background cannot connect'};
      const result = mode === 'success' ? {success:true,message:'GFlow account added'}
        : mode === 'truthy' ? {success:'false',message:'Invalid server response'}
        : {success:false,message:'Google did not accept the current session [gflow_google_auth_rejected]'};
      return {ok:true,success:result.success,result};
    }
  }}}
});
vm.runInContext(COMMON, context);
vm.runInContext(POPUP, context);
async function submit() { await field('setup').submit({preventDefault(){}}); }
(async () => {
  await events.get('DOMContentLoaded')();
  assert.equal(field('connectionToken').required,false);
  assert.equal(field('connectionToken').value,'');
  await submit();
  assert.equal(field('status').className,'status-banner error');
  assert.match(field('status').textContent,/gflow_google_auth_rejected/);
  assert.ok(!field('status').textContent.includes('Sync successful'));
  assert.equal(calls.find(call => call.action === 'save').config.connection,undefined);
  assert.equal(field('saveAndSyncBtn').disabled,false);
  assert.equal(field('forgetBtn').disabled,false);
  mode='truthy'; await submit();
  assert.equal(field('status').className,'status-banner error');
  mode='transport'; await submit();
  assert.match(field('status').textContent,/background cannot connect/);
  mode='success'; await submit();
  assert.equal(field('status').className,'status-banner success');
  assert.match(field('status').textContent,/account added/);
  assert.equal(context.parseAndFillConfig('{"apiUrl":"https://evil.test","connectionToken":"fake"}'),false);
  context.renderState({...state,lastSync:{success:false,timestamp:1,message:'Sync failed'}});
  assert.equal(field('status').className,'status-banner error');
  field('connectionToken').value='synthetic-key'; permission=false;
  const syncCalls=calls.filter(call=>call.action==='sync').length;
  await submit();
  assert.match(field('status').textContent,/needs permission/);
  assert.equal(calls.filter(call=>call.action==='sync').length,syncCalls);
  console.log('PASS popup failure, true success, saved pairing, denied permission and reload state');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""

class ExtensionPopupTest(unittest.TestCase):
    def test_real_submit_handler(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        ext = ROOT / "browser-extension"
        script = "const COMMON=" + json.dumps((ext / "common.js").read_text(encoding="utf-8")) + ";const POPUP=" + json.dumps((ext / "popup.js").read_text(encoding="utf-8")) + ";\n" + HARNESS
        result = subprocess.run([node, "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

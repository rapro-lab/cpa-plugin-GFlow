"""预热标签须与来源窗口同属一个 Cookie 存储区，失败也必须关闭。"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
const vm=require('node:vm'), assert=require('node:assert/strict');
const saved={config:{product:'GFlow',apiUrl:'http://127.0.0.1:8317/v0/management/plugins/gflow/api/extension-sync',connectionToken:'fake',loginAccount:'example@example.test'}};
let mode='success', removed=0, uploads=0, created=0, headerListener, listening=0;
const event={addListener(){}};
const context=vm.createContext({URL,AbortController,console,clearTimeout,
  setTimeout:(fn,ms)=>setTimeout(fn,ms===5000?0:ms),importScripts(){},
  chrome:{
    storage:{local:{async get(){return saved;},async set(data){Object.assign(saved,data);}}},
    action:{async setBadgeText(){},async setBadgeBackgroundColor(){}},
    runtime:{onMessage:event,onInstalled:event,onStartup:event},alarms:{onAlarm:event},
    webRequest:{onBeforeSendHeaders:{addListener(fn,filter,extra){assert.equal(filter.tabId,99);assert.ok(extra.includes('extraHeaders'));headerListener=fn;listening++;},removeListener(fn){assert.equal(fn,headerListener);listening--;}}},
    tabs:{async query(){return [{id:42,windowId:7}];},async create(opts){created++;assert.equal(opts.active,false);assert.equal(opts.windowId,7);return{id:99};},
      async get(){return {id:99,status:'complete',url:mode==='redirect'?'https://accounts.google.com/':'https://flow.google.com/projects'};},
      async sendMessage(id,req){assert.equal(req.email,'example@example.test');if(req.action==='gflow-credit-check'){assert.equal(listening,1);if(mode==='read-failure')throw new Error('synthetic message failure');
headerListener({tabId:42,method:'POST',url:'https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=nzlxg',requestHeaders:[{name:'Cookie',value:'other-tab=NEVER_UPLOAD'}]});headerListener({tabId:99,method:'POST',url:'https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=nzlxg',requestHeaders:[{name:'Cookie',value:'SID=observed; OSID=actual-flow; rpc-cookie=actual-scope'}]});return{success:true,http_status:200};}return{signed_in:true,account_matches:true,frontend:{at:'fake',sid:'123',bl:'boq_labs-ai-sandbox-frontend_test',source_path:'/projects'}};},
      async remove(id){assert.equal(id,99);removed++;}},
    cookies:{async getAllCookieStores(){return [{id:'bound-profile',tabIds:[42,99]}];},
      async getAll(query){assert.equal(query.storeId,'bound-profile');if(mode==='read-failure')throw new Error('synthetic read failure');return [
        {name:'SID',value:'fake-google',domain:'.google.com',path:'/'},
        {name:'OSID',value:'fake-flow',domain:'flow.google.com',hostOnly:true,path:'/'},
        ...(query.url.includes('/_/')?[{name:'rpc-cookie',value:'fake-scoped',domain:'flow.google.com',hostOnly:true,path:'/_/'}]:[])
      ];}}
  },fetch:async(url,opts)=>{uploads++;const payload=JSON.parse(opts.body);assert.equal(payload.frontend.at,'fake');assert.equal(listening,0);assert.equal(removed,created-1);assert.equal(payload.google_cookies,'SID=observed; OSID=actual-flow; rpc-cookie=actual-scope');assert.equal(payload.browser_check.success,true);assert.equal(payload.cookie_source,'browser_request');return mode==='backend-failure'?{status:422,ok:false,json:async()=>({success:false,error:{code:'gflow_google_auth_rejected',message:'simulated login validation failure'}})}:{status:200,ok:true,json:async()=>({success:true,action:'added'})};}
});
vm.runInContext(COMMON,context);vm.runInContext(BACKGROUND,context);
(async()=>{
  assert.equal((await context.syncNow()).success,true);assert.equal(removed,1);assert.equal(uploads,1);
  mode='read-failure';assert.equal((await context.syncNow()).success,false);assert.equal(removed,2);assert.equal(uploads,1);
  mode='redirect';assert.equal((await context.syncNow()).success,false);assert.equal(removed,3);assert.equal(uploads,1);
  mode='backend-failure';assert.equal((await context.syncNow()).success,false);assert.equal(removed,4);assert.equal(uploads,2);assert.equal(listening,0);
  console.log('PASS scoped RPC cookies, correct window and unconditional temporary-tab cleanup');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""

class WarmupTest(unittest.TestCase):
    def test_bound_window_and_cleanup(self):
        ext = ROOT / "browser-extension"
        script = "const COMMON=" + json.dumps((ext / "common.js").read_text(encoding="utf-8")) + ";const BACKGROUND=" + json.dumps((ext / "background.js").read_text(encoding="utf-8")) + ";\n" + HARNESS
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

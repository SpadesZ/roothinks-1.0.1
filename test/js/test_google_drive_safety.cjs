// 目的：選用 Google SDK 不可用時不崩潰；跨專案／章節回應不套進新研究脈絡。
// 驗證：node test/js/test_google_drive_safety.cjs。全程合成值與 API 替身，非真 OAuth 驗收。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const notices=[];
const context=vm.createContext({console,window:{},document:{getElementById:()=>null},alert:message=>notices.push(message),setTimeout,clearTimeout,Date,URL,AbortController});
vm.runInContext(fs.readFileSync('app/static/js/google_driveapi.js','utf8'),context);
const adapter=context.window.driveAdapter;
adapter.API_KEY='synthetic-browser-key';adapter.CLIENT_ID='synthetic-client';adapter.APP_ID='synthetic-project';
assert.doesNotThrow(()=>adapter.openPicker(()=>{}),'SDK 尚未就緒必須明示失敗，不能 TypeError');
assert.ok(notices.length);
vm.runInContext(fs.readFileSync('app/static/js/manuscript_wsui.js','utf8'),context);
const UI=vm.runInContext('ManuUI',context);
let callback;
context.window.driveAdapter={openPicker:fn=>{callback=fn;}};
const ui=Object.create(UI.prototype);
ui.showNotice=message=>notices.push(message);
ui.app={pid:'SYNTHETIC-A',drafterTargetSection:{value:'introduction'},currentAttachment:null,
    filePreviewArea:{classList:{remove(){}}},fileNameDisplay:{innerText:''}};
ui.triggerGoogleDrive();ui.app.pid='SYNTHETIC-B';callback({name:'control.txt',mime:'text/plain',content:'other-project'});
assert.equal(ui.app.currentAttachment,null,'舊專案回應不能套進新專案');
ui.app.pid='SYNTHETIC-A';ui.triggerGoogleDrive();ui.app.drafterTargetSection.value='results';callback({name:'control.txt',mime:'text/plain',content:'other-section'});
assert.equal(ui.app.currentAttachment,null,'舊章節回應不能套進新章節');
ui.triggerGoogleDrive();const older=callback;ui.triggerGoogleDrive();older({name:'old.txt',content:'old'});
assert.equal(ui.app.currentAttachment,null,'後發請求不能被舊請求覆寫');
callback({name:'latest.txt',mime:'text/plain',content:'latest'});
assert.equal(ui.app.currentAttachment.content,'latest');
assert.equal(adapter.SCOPES,'https://www.googleapis.com/auth/drive.file','只請求明確選取檔案');
let authRequests=0,pickers=0;
adapter.pickerInited=true;adapter.gisInited=true;
adapter.tokenClient={requestAccessToken:()=>authRequests++};adapter.createPicker=()=>pickers++;
adapter.accessToken='synthetic-expired';adapter.tokenExpiresAt=Date.now()-1;
adapter.openPicker(()=>{});assert.equal(authRequests,1);assert.equal(pickers,0);
assert.doesNotThrow(()=>adapter.tokenClient.callback({error:'access_denied'}));assert.equal(adapter.accessToken,null);
assert.doesNotThrow(()=>adapter.tokenClient.callback(null));
adapter.tokenClient.callback({access_token:'synthetic-fresh',expires_in:3600});assert.equal(pickers,1);
assert.ok(adapter.tokenExpiresAt>Date.now());
(async()=>{
    for(const status of [401,403]){
        let requested='';adapter.accessToken='synthetic-revoked';
        context.fetch=async url=>{requested=url;return {ok:false,status};};
        await assert.rejects(adapter.downloadFile('../other?x=1','text/plain','synthetic-revoked'),/Google/);
        assert.ok(requested.includes('%2F')&&requested.includes('%3F'),'file ID 不得改動 URL 結構');
        assert.equal(adapter.accessToken,null,'撤權後清除 token');
    }
    context.fetch=async()=>({ok:true,blob:async()=>({type:'text/plain',text:async()=>'<script>control</script>'})});
    assert.equal(await adapter.downloadFile('control','text/plain','synthetic-token'),'<script>control</script>');
    console.log('Google SDK/config, scope, expiry/revocation, URL, and stale context controls passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

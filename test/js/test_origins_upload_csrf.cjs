// 真正執行舊 XHR 上傳 handler，確保原件和專案身分保留且 CSRF 不漏送。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let sent;
class XHR {constructor(){this.upload={};}open(method,url){assert.equal(method,'POST');assert.equal(url,'/api/literature/upload');}send(body){sent=body;}}
const context={FormData,XMLHttpRequest:XHR,window:{},document:{getElementById:()=>null,querySelector:()=>({content:'synthetic-csrf'}),addEventListener:()=>{}}};
vm.runInNewContext(fs.readFileSync('app/static/js/literature_origins.js','utf8')+'\nglobalThis.Origins=LiteratureOriginsHandler;',context);
const handler=Object.create(context.Origins.prototype);handler.app={currentPid:'SYNTHETIC-p',log:()=>{}};
const file=new File(['%PDF-1.4 Synthetic original'],'original.pdf',{type:'application/pdf'});
handler.handleUpload({target:{files:[file]}});
assert.equal(sent.get('csrf_token'),'synthetic-csrf');assert.equal(sent.get('pid'),'SYNTHETIC-p');assert.equal(sent.get('file').name,'original.pdf');
console.log('Origins XHR upload: CSRF, original, project preserved');

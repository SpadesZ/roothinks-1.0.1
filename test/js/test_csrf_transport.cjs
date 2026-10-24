// 目的：同源 CSRF 傳輸保留 Request／正文／取消訊號，外站永不收到 token。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const calls=[],signal=new AbortController().signal;
const context={URL,Headers,location:{href:'https://research.invalid/manuscript',origin:'https://research.invalid'},document:{querySelector:()=>({content:'synthetic-csrf'})},window:{fetch:(...a)=>{calls.push(a);return Promise.resolve({ok:true});}}};
vm.runInNewContext(fs.readFileSync('app/static/js/csrf.js','utf8'),context);
const fetch=context.window.fetch;
fetch('/api/write',{method:'POST',body:'exact author words',credentials:'same-origin',signal});
assert.equal(calls[0][1].headers.get('X-CSRFToken'),'synthetic-csrf');
assert.equal(calls[0][1].body,'exact author words');assert.equal(calls[0][1].signal,signal);
fetch('https://external.invalid/api',{method:'POST',body:'reference'});
assert.equal(calls[1][1].headers,undefined);
const request=new Request('https://research.invalid/api',{method:'PATCH',body:'request body',headers:{'X-Original':'keep'}});
fetch(request);
assert.equal(calls[2][0],request);assert.equal(calls[2][1].headers.get('X-Original'),'keep');
assert.equal(calls[2][1].headers.get('X-CSRFToken'),'synthetic-csrf');
fetch('/api/read');assert.equal(calls[3][1],undefined);
fetch('/api/write',{method:'DELETE',headers:{'X-CSRFToken':'explicit-token'}});
assert.equal(calls[4][1].headers.get('X-CSRFToken'),'explicit-token');
console.log('CSRF transport: 5 scenarios passed');

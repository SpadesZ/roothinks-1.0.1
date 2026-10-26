// 真瀏覽器資產導流（I12 起沿用）：只服務樣板引用的 CDN 資產，來源為本機 npm 發佈真品；其餘外部請求一律擋下並記錄。
// 相對 I11 的改動：記錄「每一個」請求與失敗，避免資產無聲失敗卻讓測試通過。
// plotly 由 I12_SERVE_PLOTLY 控制；關閉時不攔 cdn.plot.ly，讓它照原樣失敗。
// 不修改產品碼，也不修改測試腳本的既有 assertion。
const fs=require('node:fs'),path=require('node:path');
const VENDOR=process.env.I12_VENDOR_DIR,LOG=process.env.I12_ROUTE_LOG;
const SERVE_PLOTLY=process.env.I12_SERVE_PLOTLY==='1';
if(!VENDOR)throw new Error('I12_VENDOR_DIR 未設');

const MAP=[
    ['https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/','ex/bootstrap-5.3.0/'],
    ['https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/','ex/bootstrap-icons-1.11.1/'],
    ['https://cdn.jsdelivr.net/npm/katex@0.16.9/','ex/katex-0.16.9/'],
    ['https://cdn.jsdelivr.net/npm/mermaid@10/','ex/mermaid-10.9.8/'],
];
const EXACT={
    'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js':'ex/socket.io-client-4.7.2/dist/socket.io.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/split.js/1.6.0/split.min.js':'ex/split.js-1.6.0/dist/split.min.js',
};
const PLOTLY={'https://cdn.plot.ly/plotly-2.27.0.min.js':'ex/plotly_js-dist-min-2.27.0/plotly.min.js'};
const TYPES={'.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8',
    '.css':'text/css; charset=utf-8','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf',
    '.svg':'image/svg+xml','.json':'application/json; charset=utf-8','.map':'application/json; charset=utf-8'};
const served=[],missing=[],aborted=[],requests=[],failures=[],consoleErrors=[];

function resolve(url){
    const clean=url.split('?')[0].split('#')[0];
    if(EXACT[clean])return EXACT[clean];
    if(SERVE_PLOTLY&&PLOTLY[clean])return PLOTLY[clean];
    for(const [prefix,dir] of MAP)if(clean.startsWith(prefix))return dir+clean.slice(prefix.length);
    return null;
}
async function handle(route,request){
    const url=request.url(),rel=resolve(url);
    if(rel===null){aborted.push(url);return route.abort('blockedbyclient');}
    const file=path.join(VENDOR,rel);
    if(!fs.existsSync(file)){missing.push({url,file});return route.abort('blockedbyclient');}
    const body=fs.readFileSync(file);
    served.push({url,file:rel,bytes:body.length});
    return route.fulfill({status:200,body,headers:{'content-type':TYPES[path.extname(file)]||'application/octet-stream',
        'access-control-allow-origin':'*','cache-control':'no-store'}});
}
const PATTERNS=['https://cdn.jsdelivr.net/**','https://cdnjs.cloudflare.com/**','https://apis.google.com/**'];
if(SERVE_PLOTLY)PATTERNS.push('https://cdn.plot.ly/**');
async function install(target){
    // 每一個請求都記錄，外部主機的失敗才不會像 I11 那樣在證據裡消失。
    target.on('request',r=>{const u=r.url();if(!u.startsWith('http://127.0.0.1'))requests.push({url:u,type:r.resourceType()});});
    target.on('requestfailed',r=>{const u=r.url();if(!u.startsWith('http://127.0.0.1'))
        failures.push({url:u,type:r.resourceType(),error:(r.failure()||{}).errorText});});
    if(target.on&&target.constructor.name!=='BrowserContext')
        target.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text().slice(0,300));});
    for(const pattern of PATTERNS)await target.route(pattern,handle);
}

const playwright=require('playwright');
const original=playwright.chromium.launch.bind(playwright.chromium);
playwright.chromium.launch=async function(...args){
    const browser=await original(...args);
    const newPage=browser.newPage.bind(browser);
    browser.newPage=async function(...a){const page=await newPage(...a);await install(page);return page;};
    const newContext=browser.newContext.bind(browser);
    browser.newContext=async function(...a){const ctx=await newContext(...a);await install(ctx);return ctx;};
    return browser;
};
process.on('exit',()=>{
    if(!LOG)return;
    const uniq=a=>[...new Set(a)].sort();
    try{fs.writeFileSync(LOG,JSON.stringify({serve_plotly:SERVE_PLOTLY,
        served_count:served.length,distinct_served:uniq(served.map(s=>s.file)),
        external_requests_distinct:uniq(requests.map(r=>r.url.split('?')[0])),
        request_failures:failures,request_failures_distinct:uniq(failures.map(f=>f.url.split('?')[0])),
        aborted_distinct:uniq(aborted),missing,console_errors:uniq(consoleErrors)},null,2));}catch(e){}
});

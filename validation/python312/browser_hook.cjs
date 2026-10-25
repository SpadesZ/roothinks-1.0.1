// 本機 QA：以經 npm integrity／樣板 SRI 核對的原始資產導流，記錄所有請求與失敗。
// 使用 NODE_OPTIONS=--require=<本檔>；APP、既有測試斷言及 CDN 權限均不改。
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const manifest=JSON.parse(fs.readFileSync(process.env.PY312_ASSET_MANIFEST,'utf8'));
const rows=[],pages=new WeakSet();
async function attach(page){
    if(pages.has(page))return page;pages.add(page);
    page.on('request',r=>{const u=new URL(r.url());rows.push({event:'request',url:u.origin+u.pathname});});
    page.on('requestfailed',r=>{const u=new URL(r.url());rows.push({event:'failed',url:u.origin+u.pathname,error:r.failure()?.errorText});});
    await page.route('**/*',async route=>{
        const url=route.request().url(),record=manifest.files[url.split('?')[0]];
        if(record){
            const ext=path.extname(record.path),type={'.js':'application/javascript','.css':'text/css','.woff2':'font/woff2','.woff':'font/woff'}[ext]||'application/octet-stream';
            rows.push({event:'served_verified_bytes',url,sha256:record.sha256});
            await route.fulfill({path:record.path,contentType:type,headers:{'Access-Control-Allow-Origin':'*'}});
        }else if(new URL(url).hostname==='cdn.plot.ly'){
            rows.push({event:'blocked_unmapped_plotly',url});await route.abort('blockedbyclient');
        }else await route.continue();
    });return page;
}
const launch=chromium.launch.bind(chromium);
chromium.launch=async options=>{
    const browser=await launch(options),newPage=browser.newPage.bind(browser),newContext=browser.newContext.bind(browser);
    browser.newPage=async options=>attach(await newPage(options));
    browser.newContext=async options=>{
        const context=await newContext(options),create=context.newPage.bind(context);
        context.newPage=async()=>attach(await create());return context;
    };return browser;
};
process.on('exit',()=>fs.writeFileSync(process.env.PY312_BROWSER_REQUEST_LOG,JSON.stringify(rows,null,2),{flag:'wx'}));

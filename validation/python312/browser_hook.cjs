// 本機 QA：以經 npm integrity／樣板 SRI 核對的原始資產導流，記錄所有請求與失敗。
// 使用 NODE_OPTIONS=--require=<本檔>；APP、既有測試斷言及 CDN 權限均不改。
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
// SQLite 寫入留在同一 Linux 容器，避免 Windows/Linux 共用 bind mount 的鎖語意混用。
const child=require('node:child_process'),nativeExec=child.execFileSync;
child.execFileSync=(file,args,options)=>{
    if(!process.env.PY312_BROWSER_CONTAINER || file!==process.env.SAI_TEST_PYTHON)return nativeExec(file,args,options);
    const container=process.env.PY312_BROWSER_CONTAINER,host=process.env.SAI_SYNTHETIC_ROOT,remote=process.env.PY312_CONTAINER_SYNTHETIC_ROOT;
    if(!container.startsWith('roothinks-py312-local-')||!host||!remote?.startsWith('/qa-tmp/roothinks-sai-browser-'))throw Error('Only this synthetic fixture may receive DB writes');
    const mapped=args.map(v=>typeof v==='string'&&v.startsWith(host+path.sep)?remote+v.slice(host.length).replaceAll('\\','/'):v);
    return nativeExec('docker',['exec',container,'python',...mapped],options);
};
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
        }else if(process.env.PY312_BLOCK_GOOGLE_SDK==='1' && (new URL(url).hostname==='apis.google.com'||url.startsWith('https://accounts.google.com/gsi/'))){
            rows.push({event:'blocked_optional_google_sdk',url,profile:'explicit local-account/offline-SDK control'});await route.abort('blockedbyclient');
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

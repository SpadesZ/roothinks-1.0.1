// 目的：hotfix H7 真瀏覽器驗證。所有請求網址都不得含 token；圖片在兩種認證模式都能載入。
// 執行：先啟動 hotfix_token_fixture.py（HOTFIX_MODE=token|session），再 HOTFIX_MODE=… node test/integration_e2e/hotfix_token_browser.cjs <out.json>
const {chromium}=require('playwright');
const fs=require('fs');
(async()=>{
    const mode=process.env.HOTFIX_MODE||'token', base=`http://127.0.0.1:${process.env.HOTFIX_PORT||'8899'}`;
    const TOKEN='synthetic-hotfix-token-0123456789', pid='HOTFIX-p';
    const browser=await chromium.launch();
    const page=await (await browser.newContext()).newPage();
    const urls=[],checks=[];
    page.on('request',r=>urls.push(r.url()));
    const check=(name,ok,detail)=>checks.push({name,ok:!!ok,detail});
    if(mode==='token'){
        await page.goto(`${base}/manuscript/?pid=${pid}`);
        await page.evaluate(t=>localStorage.setItem('roothinks_api_token',t),TOKEN);
    }else{
        await page.goto(`${base}/auth/login`);
        await page.evaluate(async()=>{await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:'member@example.invalid',password:'Synthetic123!'})});});
    }
    await page.goto(`${base}/manuscript/?pid=${pid}`);
    await page.waitForFunction(()=>window.wsApp);
    await page.evaluate(()=>window.rtImageTicket||null);  // 舊程式沒有票證流程（負向控制時為 null）
    // 模擬已存稿件中的圖片（舊稿件含 ?access_token= 的情況由伺服器讀取時去除，見單元測試）
    await page.evaluate(p=>{const img=document.createElement('img');img.id='probe';img.src=`/manuscript/image/${p}/fig.png`;document.body.appendChild(img);},pid);
    await page.waitForFunction(()=>{const i=document.getElementById('probe');return i&&i.complete&&i.naturalWidth>0;},null,{timeout:10000}).catch(()=>{});
    const loaded=await page.evaluate(()=>document.getElementById('probe').naturalWidth);
    check(`${mode}: 圖片載入（網址不帶 token）`,loaded>0,{loaded});
    const cookies=await page.context().cookies();
    if(mode==='token'){
        check('token: 取得限定專案路徑的 HttpOnly 圖片票證',cookies.some(c=>c.name.startsWith('rt_img_')&&c.httpOnly&&c.path===`/manuscript/image/${pid}/`&&!c.value.includes(TOKEN)),cookies.map(c=>({name:c.name,path:c.path,httpOnly:c.httpOnly})));
    }
    const leaking=urls.filter(u=>u.includes(TOKEN)||/[?&](access_token|token)=/.test(u));
    check(`${mode}: 沒有任何請求網址帶 token`,leaking.length===0,leaking);
    check(`${mode}: 確實發出過圖片請求`,urls.some(u=>u.includes(`/manuscript/image/${pid}/fig.png`)),urls.length);
    await browser.close();
    fs.writeFileSync(process.argv[2],JSON.stringify({mode,checks,requests:urls.length},null,1));
    console.log(JSON.stringify({mode,failed:checks.filter(c=>!c.ok)}));
    process.exit(checks.every(c=>c.ok)?0:1);
})();

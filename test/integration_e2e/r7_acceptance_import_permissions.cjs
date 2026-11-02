// Roothinks source maintenance contract
// 目的：R7 端到端驗收的「匯入文獻＋權限負向控制」段（其他段落：study_coresearch_browser.cjs、reading_ui_r7_browser.cjs、
//       study_matrix_r7_browser.cjs；runner：r7_acceptance.sh）。
// 內容：研究者在 Literature 文獻庫用介面匯入兩筆文獻（含中文標題與 DOI）；格式錯誤的匯入被擋下且不改資料；
//       外人讀不到文獻庫、論文清單、全文與比較矩陣；教授審閱身分不能匯入、看不到比較矩陣（PA-01，提議中）；
//       不確定與缺資料不擋寫作：研究者照常匯出 DOCX。
// 執行：先啟動 study_coresearch_fixture.py（STUDY_FIXTURE_PORT），再
//       NODE_PATH=<playwright> node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/r7_acceptance_import_permissions.cjs <out.json>
// 邊界：只連 127.0.0.1 合成站；不接正式資料或模型。
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const port=process.env.STUDY_FIXTURE_PORT||'8894';
const base=`http://127.0.0.1:${port}`,pid='STUDYCO-p',paper='synthetic_calibration',password='Synthetic123!';
const outFile=process.argv[2]||path.resolve('output/playwright','r7-acceptance-import-permissions.json');
const shots=process.env.R7_SHOTS_DIR||path.dirname(outFile);
const evidence={checks:[],errors:[],dialogs:[]};
function check(name,ok,detail){evidence.checks.push({name,ok:!!ok,detail:detail===undefined?null:detail});}

async function pageFor(browser){
    const ctx=await browser.newContext({viewport:{width:1440,height:960}});
    const page=await ctx.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror',e=>evidence.errors.push(e.message));
    page.on('dialog',async d=>{evidence.dialogs.push(d.message());await d.accept();});
    return page;
}
async function login(page,who){
    await page.goto(base+'/auth/login');
    await page.getByRole('textbox',{name:'Email',exact:true}).fill(`${who}@example.invalid`);
    await page.getByRole('textbox',{name:'密碼',exact:true}).fill(password);
    await page.getByRole('button',{name:'登入'}).click();
    await page.waitForURL(base+'/');
}
// 用頁面自己的 session 與 CSRF 送 API（與前端相同的路徑）
const api=(page,url,body,method)=>page.evaluate(async({url,body,method})=>{
    const token=document.querySelector('meta[name="csrf-token"]')?.content||'';
    const r=await fetch(url,body===undefined&&!method?{}:{method:method||'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token},
        body:body===undefined?undefined:JSON.stringify(body)});
    let data=null;try{data=await r.json();}catch(e){}
    return {status:r.status,data};
},{url,body,method});
const libraryCount=async page=>((await api(page,`/api/literature/library?pid=${pid}`)).data?.entries||[]).length;

(async()=>{
    const browser=await chromium.launch();
    try{
        // ---------- 1. 研究者在文獻庫匯入兩筆文獻 ----------
        const page=await pageFor(browser);
        await login(page,'researcher');
        await page.goto(`${base}/literature?pid=${pid}`);
        await page.locator('#libImportText').waitFor({state:'attached'});
        const before=await libraryCount(page);
        const items=[{title:'界面熱阻在少層石墨烯中的量測',DOI:'10.0000/r7.import.1',issued:{'date-parts':[[2024]]}},
                     {title:'Strain effects on phonon transport in 2D materials',DOI:'10.0000/r7.import.2'}];
        await page.locator('#libImportText').evaluate((el,v)=>{el.value=v;},JSON.stringify(items));
        await page.evaluate(()=>window.literatureLibrary.importItems());
        await page.waitForFunction(n=>document.querySelectorAll('#libImportText').length&&!document.querySelector('#libImportText').value,null,{timeout:10000}).catch(()=>{});
        const after=await libraryCount(page);
        const lib=(await api(page,`/api/literature/library?pid=${pid}`)).data?.entries||[];
        const titles=lib.map(e=>e.title||'');
        check('R7 研究者用文獻庫介面匯入兩筆文獻（含中文標題與 DOI）',after===before+2&&titles.includes(items[0].title)&&titles.includes(items[1].title),
            {before,after,dialogs:evidence.dialogs.slice(-1)});
        await page.screenshot({path:path.join(shots,'r7-acceptance-01-library-import.png')});
        // ---------- 2. 格式錯誤的匯入被擋下，不改資料 ----------
        await page.locator('#libImportText').evaluate(el=>{el.value='{這不是 JSON';});
        await page.evaluate(()=>window.literatureLibrary.importItems());
        await page.waitForTimeout(300);
        check('R7 負向控制：格式錯誤的匯入被擋下並說明原因，文獻數不變',
            evidence.dialogs.some(m=>m.includes('JSON 解析失敗'))&&await libraryCount(page)===after,evidence.dialogs.slice(-1));
        // ---------- 3. 從文獻庫直接進入雙語閱讀 ----------
        await page.goto(`${base}/study/project/${pid}?pid=${pid}&read=${paper}`);
        await page.locator('#fulltext-content .original-content').first().waitFor({timeout:15000});
        const mtLabel=await page.locator('#fulltext-content').innerText();
        check('R7 匯入後可直接開啟雙語閱讀，譯文標示機器翻譯',mtLabel.includes('機器翻譯'));
        await page.screenshot({path:path.join(shots,'r7-acceptance-02-reader.png')});
        // ---------- 4. 不確定與缺資料不擋寫作：研究者照常匯出 DOCX ----------
        const docx=await page.evaluate(async p=>{
            const token=document.querySelector('meta[name="csrf-token"]')?.content||'';
            const r=await fetch(`/manuscript/api/export/docx/${p}`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token},
                body:JSON.stringify({title:'R7 驗收稿',html:'<p>矩陣中有「未報告」與「不確定」的格子，研究者仍可寫作與匯出。</p>'})});
            const b=new Uint8Array(await r.arrayBuffer());return {status:r.status,zip:b[0]===0x50&&b[1]===0x4b};},pid);
        check('R7 研究者自主：有不確定或缺資料時仍可匯出 DOCX',docx.status===200&&docx.zip,docx);
        await page.context().close();

        // ---------- 5. 外人：所有研究資料都讀不到 ----------
        const outsider=await pageFor(browser);
        await login(outsider,'outsider');
        const probes={library:`/api/literature/library?pid=${pid}`,papers:`/api/study/paper_list/${pid}`,
                      matrix_history:`/api/study/${pid}/matrix/history`,matrix_latest:`/api/study/${pid}/matrix/latest`,
                      provenance:`/manuscript/api/provenance?pid=${pid}&section=introduction`};
        const out={};
        for(const [k,u] of Object.entries(probes)) out[k]=(await api(outsider,u)).status;
        out.import=(await api(outsider,'/api/literature/library/import',{pid,items:[{title:'外人插入'}]})).status;
        check('R7 負向控制：外人讀不到文獻庫、論文、比較矩陣與段落出處，也不能匯入',
            Object.values(out).every(s=>s===403||s===404),out);
        await outsider.context().close();

        // ---------- 6. 教授審閱身分：不能匯入；看不到研究矩陣（PA-01，提議中） ----------
        const prof=await pageFor(browser);
        await login(prof,'professor');
        const profImport=await api(prof,'/api/literature/library/import',{pid,items:[{title:'教授插入'}]});
        const profMatrix=await api(prof,`/api/study/${pid}/matrix/history`);
        const countAfter=await (async()=>{const p=await pageFor(browser);await login(p,'researcher');const n=await libraryCount(p);await p.context().close();return n;})();
        check('R7 負向控制：教授審閱身分不能匯入文獻（資料不變）',profImport.status>=400&&countAfter===after,{status:profImport.status,countAfter});
        check('R7 教授審閱身分看不到研究矩陣（PA-01 提議中的範圍）',profMatrix.status===403,{status:profMatrix.status});
        await prof.context().close();
    }catch(e){
        evidence.errors.push(String(e&&e.stack||e));
    }finally{
        await browser.close();
        const failed=evidence.checks.filter(c=>!c.ok).length;
        evidence.summary={checks:evidence.checks.length,failed,errors:evidence.errors.length};
        await fs.mkdir(path.dirname(outFile),{recursive:true});
        await fs.writeFile(outFile,JSON.stringify(evidence,null,1));
        console.log(JSON.stringify(evidence.summary));
        process.exit(failed||evidence.errors.length?1:0);
    }
})();

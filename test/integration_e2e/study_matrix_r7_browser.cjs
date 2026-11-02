// 目的：R7 多篇比較矩陣的真瀏覽器檢查；對 study_coresearch_fixture.py 合成站的 STUDYMX-p（matrix_fixture_papers.py）實走。
// 階段：MATRIX_PHASE=before 只記錄改版前現況（不做 assertion）；after 逐項驗收並留下截圖與量測 JSON。
// 執行：STUDY_FIXTURE_PORT=8911 STUDY_FIXTURE_ROOT=<合成站 root> MATRIX_OUT=<輸出資料夾> \
//       NODE_PATH=<playwright 所在 node_modules> node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/study_matrix_r7_browser.cjs
// 邊界：只連 127.0.0.1 合成站；比較器是合成替身，不呼叫任何模型；不接正式資料。
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const port=process.env.STUDY_FIXTURE_PORT||'8911';
const base=`http://127.0.0.1:${port}`,pid='STUDYMX-p',password='Synthetic123!';
const phase=process.env.MATRIX_PHASE||'after';
const output=path.resolve(process.env.MATRIX_OUT||path.join('output/playwright',`matrix-r7-${phase}-`+new Date().toISOString().replace(/[:.]/g,'-')));
const fixtureRoot=process.env.STUDY_FIXTURE_ROOT||'';
// 研究者勾選順序（第一篇是基準）；資料夾排序是 a、b、c、d。
const ORDER=['mx_c_calibration','mx_b_annealing','mx_a_lowtemp','mx_d_position'];
const FAKE='The improvement was replicated across five independent sample batches.';
const FAKE_ONLY='The authors acknowledge that no experiments were performed in this paper.';
const RANKING=/優於|較優|較嚴謹|較少，證據較弱|明顯較小/;
const evidence={phase,checks:[],metrics:{},errors:[],screenshots:[]};
let browser;

function check(name,ok,detail){
    evidence.checks.push({name,ok:!!ok,detail:detail===undefined?null:detail});
    if(phase==='after')assert.ok(ok,`${name}: ${JSON.stringify(detail)}`);
}
async function shot(page,name,opts={}){
    const file=path.join(output,name+'.png');
    await page.screenshot({path:file,fullPage:false,...opts});
    evidence.screenshots.push(file);
}
async function pageFor(viewport={width:1440,height:960}){
    const ctx=await browser.newContext({viewport});
    const page=await ctx.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror',e=>evidence.errors.push(e.message));
    page.on('dialog',d=>d.accept());
    return page;
}
async function login(page){
    await page.goto(base+'/auth/login');
    await page.getByRole('textbox',{name:'Email',exact:true}).fill('researcher@example.invalid');
    await page.getByRole('textbox',{name:'密碼',exact:true}).fill(password);
    await page.getByRole('button',{name:'登入'}).click();
    await page.waitForURL(base+'/');
}
async function openStudy(page){
    await page.goto(`${base}/study/project/${pid}?pid=${pid}`);
    await page.waitForFunction(()=>window.studyView&&window.studyCore);
}
async function openPapers(page){
    await page.evaluate(()=>window.studyView.toggleSidebar('tab-papers'));
    await page.locator('.paper-checkbox').first().waitFor();
}
async function tick(page,ids){
    for(const id of ids)await page.locator(`.paper-checkbox[value="${id}"]`).check();
}
async function stubCalls(){
    if(!fixtureRoot)return null;
    try{return JSON.parse(await fs.readFile(path.join(fixtureRoot,'matrix_stub_calls.json'),'utf8'));}catch(e){return [];}
}

async function before(){
    const page=await pageFor();
    await login(page);
    await openStudy(page);
    await page.waitForTimeout(800);
    evidence.metrics.legacy_latest_rendered=await page.locator('#matrixContainer table').count();
    await shot(page,'before-00-legacy-latest');
    await openPapers(page);
    await tick(page,ORDER);
    const [req,resp]=await Promise.all([
        page.waitForRequest(r=>r.url().endsWith('/api/study/compare')),
        page.waitForResponse(r=>r.url().endsWith('/api/study/compare')),
        page.getByRole('button',{name:/生成比較矩陣/}).click(),
    ]);
    const body=await resp.json();
    evidence.metrics.request_payload=JSON.parse(req.postData()||'{}');
    evidence.metrics.response_comparisons_order=(body.data?.comparisons||[]).map(c=>c.target_paper_id);
    evidence.metrics.response_baseline=body.data?.baseline?.id;
    await page.evaluate(()=>window.studyView.toggleSidebar());
    await page.waitForTimeout(500);
    const ui=await page.evaluate(()=>{
        const t=document.querySelector('#matrixContainer table');
        return {headers:t?[...t.querySelectorAll('thead th')].map(th=>th.innerText.replace(/\s+/g,' ').trim()):[],
            rows:t?t.querySelectorAll('tbody tr').length:0,title:document.getElementById('matrix-title')?.innerText||'',
            text:document.getElementById('matrixContainer').innerText};
    });
    evidence.metrics.ui_headers=ui.headers;
    evidence.metrics.ui_rows=ui.rows;
    evidence.metrics.ui_title=ui.title;
    evidence.metrics.papers_selected=ORDER.length;
    evidence.metrics.papers_visible_as_columns=ORDER.filter(id=>ui.headers.some(h=>h.includes(id))).length;
    evidence.metrics.shows_not_reported_label=/未報告/.test(ui.text);
    evidence.metrics.ranking_text_visible=RANKING.test(ui.text);
    evidence.metrics.na_cells=(ui.text.match(/N\/A/g)||[]).length;
    evidence.metrics.stub_calls=await stubCalls();
    await shot(page,'before-01-matrix-4-papers');
    await shot(page,'before-02-matrix-4-papers-full',{fullPage:true});
    await page.context().close();
}

const CUSTOM='樣本溫度';
// 讀目前矩陣：欄順序、每格狀態／文字／證據／被排除引文。
async function readMatrix(page){
    return page.evaluate(()=>{
        const t=document.querySelector('#matrixContainer table.rt-matrix-table');
        if(!t)return null;
        const cols=[...t.querySelectorAll('thead th[data-paper-id]')].map(th=>({id:th.dataset.paperId,
            coverage:(th.querySelector('[data-coverage]')||th.querySelector('.rt-coverage'))?.innerText||''}));
        const rows=[...t.querySelectorAll('tbody tr')].map(tr=>({dim:tr.querySelector('th').innerText.trim(),
            cells:[...tr.querySelectorAll('td')].map(td=>({paper:td.dataset.paperId,status:td.dataset.status,
                badge:td.querySelector('[data-status-badge]')?.innerText||'',text:td.innerText,
                evidence:[...td.querySelectorAll('[data-evidence-anchor]')].map(b=>({ref:b.dataset.evidenceAnchor,text:b.innerText})),
                rejected:[...td.querySelectorAll('[data-rejected-quote]')].map(li=>li.textContent),
                relation:td.querySelector('.rt-relation')?.innerText||'',withheld:td.querySelector('[data-relation-withheld]')?.dataset.relationWithheld||''}))}));
        return {cols,rows,columns:Number(t.dataset.matrixColumns),caption:t.querySelector('caption')?.innerText||'',
            text:document.getElementById('matrixContainer').innerText,legacy:!!document.querySelector('[data-matrix-legacy]'),
            notice:document.querySelector('[data-matrix-notice]')?.innerText||'',check:document.querySelector('[data-system-check]')?.innerText||''};
    });
}
const cellOf=(m,paper,dimPart)=>{const row=m.rows.find(r=>r.dim.includes(dimPart));return row&&row.cells.find(c=>c.paper===paper);};

async function after(){
    const page=await pageFor();
    await login(page);
    // ---------- 1. 舊記錄（R6 單一比較）仍可載入，畫成 2 欄 ----------
    await openStudy(page);
    await page.locator('#matrixContainer table.rt-matrix-table').waitFor();
    const legacy=await readMatrix(page);
    check('舊記錄（R6 單一比較）載入後畫成 2 欄矩陣',legacy.columns===2&&legacy.cols.map(c=>c.id).join()==='mx_c_calibration,mx_b_annealing',legacy.cols);
    check('舊記錄標明未核對原文',legacy.legacy&&/舊版記錄/.test(legacy.text));
    const legacyResult=cellOf(legacy,'mx_b_annealing','結果');
    check('舊記錄的 N/A 顯示為未報告，當時的評析不顯示',legacyResult&&legacyResult.status==='not_reported'&&legacyResult.withheld==='missing'
        &&!legacyResult.text.includes('資料不足，待補充比較證據'),legacyResult);
    await shot(page,'after-00-legacy-record');

    // ---------- 2. 側欄：勾選順序、基準、維度 ----------
    await openPapers(page);
    await tick(page,ORDER);
    const order=await page.locator('[data-order-list] li').evaluateAll(lis=>lis.map(li=>li.dataset.orderId));
    check('側欄順序＝勾選順序（不是清單排序），第一篇標基準',JSON.stringify(order)===JSON.stringify(ORDER)&&
        (await page.locator('[data-order-list] li').first().innerText()).includes('基準'),order);
    const custom=page.locator('#rt-matrix-setup [data-dim-custom]');
    await custom.fill(CUSTOM);
    await custom.press('Enter');
    const dimsPicked=await page.locator('#rt-matrix-setup [data-dim-option]:checked').evaluateAll(xs=>xs.map(x=>x.value));
    check('可自填維度並勾選',dimsPicked.includes(CUSTOM)&&dimsPicked.length===6,dimsPicked);
    await page.locator('#rt-matrix-setup').scrollIntoViewIfNeeded();
    await shot(page,'after-01-sidebar-setup');

    // ---------- 3. 產生 4 篇矩陣 ----------
    const callsBefore=(await stubCalls()||[]).length;
    const [req,resp]=await Promise.all([
        page.waitForRequest(r=>r.url().endsWith('/api/study/compare')),
        page.waitForResponse(r=>r.url().endsWith('/api/study/compare')),
        page.getByRole('button',{name:/生成比較矩陣/}).click(),
    ]);
    const payload=JSON.parse(req.postData()||'{}');
    const body=await resp.json();
    evidence.metrics.request_payload=payload;
    evidence.metrics.matrix_id=body.matrix_id;
    check('送出勾選順序、明示基準與所選維度',JSON.stringify(payload.paper_ids)===JSON.stringify(ORDER)&&payload.baseline_id===ORDER[0]
        &&payload.dimensions.includes(CUSTOM),payload);
    check('伺服器保存成功',body.saved===true&&resp.status()===200&&body.data.status==='completed',{status:resp.status(),saved:body.saved});
    await page.evaluate(()=>window.studyView.toggleSidebar());
    await page.locator('#matrixContainer table.rt-matrix-table[data-matrix-columns="4"]').waitFor();
    const m=await readMatrix(page);
    evidence.metrics.columns=m.cols.map(c=>c.id);
    evidence.metrics.rows=m.rows.map(r=>r.dim);
    check('4 篇論文全部成為欄位，順序＝勾選順序',JSON.stringify(m.cols.map(c=>c.id))===JSON.stringify(ORDER),m.cols.map(c=>c.id));
    check('列＝所選 6 個維度（含自填）',m.rows.length===6&&m.rows.some(r=>r.dim===CUSTOM),m.rows.map(r=>r.dim));
    check('每篇都有比較依據（頁碼範圍與段數）',m.cols.every(c=>/比較依據：p\.\d/.test(c.coverage)&&/共 \d+ 段/.test(c.coverage)),m.cols.map(c=>c.coverage));
    check('標明 AI 協助、研究者判斷，引文不等於支持',/判斷由你決定/.test(m.notice)&&/不代表它支持/.test(m.notice),m.notice);
    check('表格有 caption 說明欄列',/列是比較維度，欄是論文/.test(m.caption));
    const all=m.rows.flatMap(r=>r.cells);
    const evTexts=all.flatMap(c=>c.evidence.map(e=>e.text));
    check('捏造的引文 0 次成為證據',!evTexts.some(t=>t.includes(FAKE)||t.includes(FAKE_ONLY)),evTexts.filter(t=>/replicated|acknowledge/.test(t)));
    const rejectedAll=all.flatMap(c=>c.rejected);
    check('捏造的引文只出現在「已排除」清單',rejectedAll.some(t=>t.includes(FAKE))&&rejectedAll.some(t=>t.includes(FAKE_ONLY)),rejectedAll);
    const annealResult=cellOf(m,'mx_b_annealing','結果');
    check('同一格真引文保留、可點回原文',annealResult.status==='reported'&&annealResult.evidence.length===1&&annealResult.evidence[0].ref==='p3-b2',annealResult);
    const posLimit=cellOf(m,'mx_d_position','限制');
    check('只有捏造引文的「有報告」降為不確定',posLimit.status==='uncertain'&&posLimit.text.includes('原文中找得到的引文'),posLimit);
    const missing=[cellOf(m,'mx_d_position','結果'),cellOf(m,'mx_d_position','資料'),cellOf(m,'mx_a_lowtemp','限制')];
    check('缺資料顯示「未報告」',missing.every(c=>c.status==='not_reported'&&c.badge==='未報告'),missing.map(c=>[c.paper,c.status,c.badge]));
    const notReported=all.filter(c=>c.status==='not_reported');
    check('未報告的格子沒有任何差異描述或排名',notReported.length>=4&&notReported.every(c=>!c.relation&&!RANKING.test(c.text)&&c.evidence.length===0),
        notReported.map(c=>[c.paper,c.relation,c.withheld]));
    check('整個矩陣沒有任何由缺資料或不可比較推出的優劣排名',!RANKING.test(m.text),(m.text.match(RANKING)||[])[0]);
    const lowResult=cellOf(m,'mx_a_lowtemp','結果');
    check('條件不同標「不可比較」並附理由',lowResult.status==='not_comparable'&&lowResult.badge==='不可比較'&&/77 K/.test(lowResult.text)&&lowResult.withheld==='not_comparable',lowResult);
    const baseLimit=cellOf(m,'mx_c_calibration','限制');
    check('AI 標錯段落的真引文改到實際段落並註明',baseLimit.evidence.some(e=>e.ref==='p3-b3')&&/已改到實際所在段落/.test(baseLimit.text),baseLimit.evidence);
    const lowMethods=cellOf(m,'mx_a_lowtemp','方法');
    check('太短的引文被排除',lowMethods.rejected.some(t=>t.includes('太短')),lowMethods.rejected);
    check('系統檢查列出找到與排除的引文數',/原文中找到的引文 \d+ 段/.test(m.check)&&/已排除 [1-9]\d* 段/.test(m.check),m.check);
    const customCells=m.rows.find(r=>r.dim===CUSTOM).cells;
    check('自填維度沒有資料時標不確定，不補值',customCells.every(c=>['uncertain','not_reported'].includes(c.status)),customCells.map(c=>c.status));
    // 展開：伺服器保存全文，畫面折疊
    const expand=page.locator('td[data-paper-id="mx_b_annealing"] [data-expand]').first();
    const annealMethods=cellOf(m,'mx_b_annealing','方法');
    check('長文字沒有被截斷（全文保存）',annealMethods.text.includes('這段文字刻意超過畫面折疊長度'),annealMethods.text.slice(-40));
    await expand.click();
    check('長文字可「展開」',await expand.getAttribute('aria-expanded')==='true'&&(await expand.innerText())==='收合');
    // 模型呼叫次數與輸入涵蓋
    const calls=(await stubCalls()||[]).slice(callsBefore).filter(c=>c.format==='v2');
    evidence.metrics.model_calls_this_compare=calls.length;
    evidence.metrics.annealing_input_markers=(calls.find(c=>c.target==='mx_b_annealing')||{}).markers||null;
    check('模型呼叫次數＝對照論文數（每篇一次）',calls.length===ORDER.length-1,calls.length);
    check('2,500 字之後的方法／結果／限制段落有送給比較器',Object.values(evidence.metrics.annealing_input_markers||{}).every(Boolean),evidence.metrics.annealing_input_markers);
    await page.locator('#matrixContainer').evaluate(el=>el.scrollTo(0,0));
    await shot(page,'after-02-matrix-4-papers');
    await shot(page,'after-02b-matrix-4-papers-full',{fullPage:true});
    await fs.writeFile(path.join(output,'after-02-matrix.yml'),await page.locator('#matrixContainer').ariaSnapshot());

    // ---------- 4. 鍵盤：Tab 到證據按鈕、Enter 開閱讀器到該段 ----------
    const chip=page.locator('td[data-paper-id="mx_b_annealing"] [data-evidence-anchor="p3-b2"]');
    await chip.focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('#fulltext-content [data-evidence-current][data-ref="p3-b2"]'));
    const revealed=await page.evaluate(()=>({paper:window.studyView.currentFulltextPaperId,
        mode:document.querySelector('#fulltext-content [data-evidence-current][data-ref="p3-b2"]').getAttribute('data-evidence-current')}));
    check('鍵盤按證據開閱讀器並定位到同一段',revealed.paper==='mx_b_annealing'&&revealed.mode==='quote',revealed);
    await page.waitForTimeout(600);
    await shot(page,'after-03-evidence-in-reader');

    // ---------- 5. 重新整理：順序與維度不變 ----------
    await page.reload();
    await page.waitForFunction(()=>window.studyView&&window.studyCore);
    await page.locator('#matrixContainer table.rt-matrix-table[data-matrix-columns="4"]').waitFor();
    const again=await readMatrix(page);
    check('重新整理後欄位順序不變',JSON.stringify(again.cols.map(c=>c.id))===JSON.stringify(ORDER),again.cols.map(c=>c.id));
    check('重新整理後維度不變',JSON.stringify(again.rows.map(r=>r.dim))===JSON.stringify(m.rows.map(r=>r.dim)),again.rows.map(r=>r.dim));
    await shot(page,'after-04-reloaded');

    // ---------- 6. 更舊的 papers_comp 記錄（v1.6，只有整體文字）也能畫 ----------
    await page.evaluate(()=>window.studyCore.loadMatrixRecord('legacy::v16_pair'));
    await page.locator('#matrixContainer table.rt-matrix-table[data-matrix-columns="2"]').waitFor();
    const oldest=await readMatrix(page);
    check('papers_comp 舊檔畫成 2 欄並標舊版',oldest.legacy&&oldest.cols.map(c=>c.id).join()==='mx_c_calibration,mx_a_lowtemp'&&/舊版純文字比較/.test(oldest.text),oldest.cols);
    await page.context().close();

    // ---------- 7. 手機寬度：水平捲動、第一欄固定 ----------
    const phone=await pageFor({width:390,height:844});
    await login(phone);
    await openStudy(phone);
    await phone.locator('#matrixContainer table.rt-matrix-table[data-matrix-columns="4"]').waitFor();
    const sticky=await phone.evaluate(()=>{
        const box=document.querySelector('.rt-matrix-scroll');
        const before=box.querySelector('tbody th').getBoundingClientRect().left;
        box.scrollLeft=400;
        const afterLeft=box.querySelector('tbody th').getBoundingClientRect().left;
        return {scrollable:box.scrollWidth>box.clientWidth,scrolled:box.scrollLeft,before,after:afterLeft,
            pageOverflow:document.documentElement.scrollWidth>window.innerWidth+1};
    });
    check('窄螢幕可水平捲動且維度欄固定',sticky.scrollable&&sticky.scrolled>0&&Math.abs(sticky.before-sticky.after)<2,sticky);
    await shot(phone,'after-05-phone');
    await phone.context().close();
}

(async()=>{
    await fs.mkdir(output,{recursive:true});
    browser=await chromium.launch(process.env.SAI_BROWSER_EXECUTABLE?{executablePath:process.env.SAI_BROWSER_EXECUTABLE}:{});
    try{
        if(phase==='before')await before();
        else await after();
    }catch(err){
        evidence.errors.push(String(err&&err.stack||err));
        process.exitCode=1;
    }finally{
        await fs.writeFile(path.join(output,`matrix-r7-${phase}.json`),JSON.stringify(evidence,null,2));
        await browser.close();
        console.log(JSON.stringify({phase,output,checks:evidence.checks.length,
            failed:evidence.checks.filter(c=>!c.ok).length,errors:evidence.errors.length}));
    }
})();

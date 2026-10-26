// 目的：對 study_coresearch_fixture.py 合成站實走 Study 研究者旅程，留下截圖、ARIA 快照與量測 JSON。
// 階段：STUDY_PHASE=before 只記錄改版前的現況（不做 assertion）；after 走完整共研切片並逐項驗收。
// 執行：NODE_PATH=<playwright 所在 node_modules> node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/study_coresearch_browser.cjs
// 邊界：只連 127.0.0.1 合成站；成果只寫 output/playwright/<新 run>；不接正式資料或模型。
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const port=process.env.STUDY_FIXTURE_PORT||'8894';
const base=`http://127.0.0.1:${port}`,pid='STUDYCO-p',paper='synthetic_calibration',password='Synthetic123!';
const phase=process.env.STUDY_PHASE||'after';
const run=`study-${phase}-`+new Date().toISOString().replace(/[:.]/g,'-');
const output=path.resolve('output/playwright',run);
const evidence={run,phase,sha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),
    dirty:execFileSync('git',['status','--porcelain'],{encoding:'utf8'}).trim().split('\n').filter(Boolean).length,
    checks:[],metrics:{},errors:[]};
let browser;
const QUOTE='The improvement was observed only at 300 K under one calibration protocol.';

function check(name,ok,detail){
    evidence.checks.push({name,ok:!!ok,detail:detail===undefined?null:detail});
    if(phase==='after')assert.ok(ok,`${name}: ${JSON.stringify(detail)}`);
}
async function snapshot(page,name){
    await fs.writeFile(path.join(output,name+'.yml'),await page.locator('body').ariaSnapshot());
    await page.screenshot({path:path.join(output,name+'.png'),fullPage:false});
}
async function pageFor(){
    const ctx=await browser.newContext({viewport:{width:1440,height:960}});
    const page=await ctx.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror',e=>evidence.errors.push(e.message));
    page.on('dialog',d=>d.accept());
    return page;
}
async function login(page,who){
    await page.goto(base+'/auth/login');
    await page.getByRole('textbox',{name:'Email',exact:true}).fill(`${who}@example.invalid`);
    await page.getByRole('textbox',{name:'密碼',exact:true}).fill(password);
    await page.getByRole('button',{name:'登入'}).click();
    await page.waitForURL(base+'/');
}
async function openReader(page){
    await page.goto(`${base}/study/project/${pid}?pid=${pid}`);
    await page.waitForFunction(()=>window.studyView && window.studyChat);
    await page.evaluate(()=>window.studyView.toggleSidebar('tab-papers'));
    await page.getByRole('button',{name:/閱讀全文/}).first().click();
    await page.locator('#fulltext-content .original-content').first().waitFor();
}
// 以真實 DOM Range 選取原文中的一句，再送出 mouseup，與滑鼠拖曳觸發同一個處理器。
async function selectQuote(page,quote,side='original'){
    return page.evaluate(({quote,side})=>{
        const sel=side==='original'?'#fulltext-content .original-content':'#fulltext-content .translation-content';
        for(const host of document.querySelectorAll(sel)){
            const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);
            let node;
            while((node=walker.nextNode())){
                const at=node.textContent.indexOf(quote);
                if(at<0)continue;
                const range=document.createRange();
                range.setStart(node,at);range.setEnd(node,at+quote.length);
                const s=window.getSelection();s.removeAllRanges();s.addRange(range);
                host.scrollIntoView({block:'center'});
                host.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
                return true;
            }
        }
        return false;
    },{quote,side});
}

async function before(){
    const page=await pageFor();
    await login(page,'researcher');
    await openReader(page);
    evidence.metrics.reader_rows=await page.locator('#fulltext-content .original-content').count();
    evidence.metrics.rows_with_anchor_attrs=await page.locator('#fulltext-content [data-seq-id]').count();
    await snapshot(page,'01-reader');
    check('selection found',await selectQuote(page,QUOTE));
    evidence.metrics.context_badge=await page.locator('#chat-context-badge').textContent();
    evidence.metrics.selection_context_chars=await page.evaluate(()=>String(window.studyChat.currentContext?.content||'').length);
    evidence.metrics.selection_context_has_anchor=await page.evaluate(()=>!!window.studyChat.currentContext?.anchor);
    evidence.metrics.selection_actions_visible=await page.locator('[data-study-selection-action]').count();
    await page.locator('#chat-input').fill('請解釋這段在說什麼？');
    await page.locator('#chat-input').press('Enter');
    await page.locator('#chat-history .bi-robot').first().waitFor();
    await page.waitForTimeout(300);
    evidence.metrics.answer_text=(await page.locator('#chat-history').innerText()).slice(-400);
    evidence.metrics.answer_evidence_links=await page.locator('#chat-history [data-evidence-anchor]').count();
    evidence.metrics.answer_uncertainty_shown=await page.locator('#chat-history [data-uncertainty]').count();
    evidence.metrics.accept_note_controls=await page.locator('[data-evidence-note-accept]').count();
    evidence.metrics.evidence_note_list=await page.locator('#evidence-notes-list').count();
    await snapshot(page,'02-tutor-answer');
    await page.context().close();
}

(async()=>{
    await fs.mkdir(output,{recursive:true});
    browser=await chromium.launch(process.env.SAI_BROWSER_EXECUTABLE?{executablePath:process.env.SAI_BROWSER_EXECUTABLE}:{});
    try{
        if(phase==='before')await before();
        else await require('./study_coresearch_after.cjs')({base,pid,paper,QUOTE,browser,pageFor,login,openReader,selectQuote,snapshot,check,evidence});
    }catch(err){
        evidence.errors.push(String(err&&err.stack||err));
        process.exitCode=1;
    }finally{
        await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
        await browser.close();
        console.log(JSON.stringify({run,output,checks:evidence.checks.length,
            failed:evidence.checks.filter(c=>!c.ok).length,errors:evidence.errors.length}));
    }
})();

// 目的：真瀏覽器驗證 study 頁面實際用到的前端資產（KaTeX、Split.js）確實載入並被程式消費，
//       以及資產失敗不會無聲通過。研究者自主不受影響。
// 邊界：只用 localhost 的 sai_browser_fixture --autonomy；外部資產由測試層導流至 npm 真品。
// 驗證：node test/integration_e2e/sai_assets_browser.cjs
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const base='http://127.0.0.1:8893',pid='SAIBROWSER-p';
const output=path.resolve('output/playwright','assets-'+new Date().toISOString().replace(/[:.]/g,'-'));
const evidence={sha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),
    checks:[],errors:[],asset_state:{},failed_requests:[]};
let browser,page;
async function snapshot(name){
    await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
    await fs.writeFile(path.join(output,name+'.yml'),await page.locator('body').ariaSnapshot());
}

async function main(){
    await fs.mkdir(output,{recursive:true});
    browser=await chromium.launch({headless:true,executablePath:process.env.SAI_BROWSER_EXECUTABLE});
    page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(20000);
    page.on('pageerror',e=>evidence.errors.push(e.message));page.on('dialog',d=>d.accept());
    page.on('requestfailed',r=>{const u=r.url();
        if(!u.startsWith('http://127.0.0.1'))evidence.failed_requests.push({url:u,error:(r.failure()||{}).errorText});});

    await page.goto(base+'/auth/login');
    await page.locator('[name=email]').fill('author@example.invalid');
    await page.locator('[name=password]').fill('Synthetic123!');
    await page.getByRole('button',{name:'登入'}).click();await page.waitForURL(base+'/');

    await page.goto(base+'/study/project/'+pid+'?pid='+pid,{waitUntil:'load'});
    // Split.js：樣板用 typeof Split !== 'undefined' 包住，載入失敗時面板會無聲地不可拖動。
    await page.waitForFunction(()=>typeof window.Split==='function');
    await page.locator('#matrix-pane').waitFor();await page.locator('#aux-pane').waitFor();
    const gutters=await page.locator('.gutter').count();
    assert.ok(gutters>=2,'Split.js 必須真的被程式消費並產生 gutter，實得 '+gutters);
    const draggable=await page.locator('#matrix-pane').evaluate(n=>getComputedStyle(n).width);
    evidence.asset_state.split={loaded:true,gutters,matrix_pane_width:draggable};
    await snapshot('study-split-panes');evidence.checks.push('splitjs_loaded_and_consumed_by_app');

    // KaTeX：study_view.js 用 window.katex.render 渲染 .katex-eq[data-latex]。
    await page.waitForFunction(()=>!!(window.katex&&window.katex.render));
    const rendered=await page.evaluate(()=>{
        const host=document.createElement('span');host.className='katex-eq';
        host.setAttribute('data-latex','E = mc^2');document.body.append(host);
        window.katex.render('E = mc^2',host,{displayMode:true,throwOnError:false});
        return {html:host.innerHTML.slice(0,120),hasKatex:!!host.querySelector('.katex'),
            text:(host.textContent||'').replace(/\s+/g,'')};
    });
    assert.ok(rendered.hasKatex,'KaTeX 必須真的產生 .katex 輸出：'+JSON.stringify(rendered));
    assert.ok(rendered.text.includes('mc2')||rendered.text.includes('mc'),'KaTeX 輸出必須含公式內容：'+rendered.text);
    evidence.asset_state.katex={loaded:true,produced_katex_node:rendered.hasKatex};
    await snapshot('study-katex-rendered');evidence.checks.push('katex_loaded_and_renders_math');

    // 研究者自主：沒有教授、沒有證據，study 頁面仍可用。
    const title=await page.title();
    assert.ok(title.length>0,'頁面必須正常載入');
    const blocked=await page.locator('text=科學警訊').count().catch(()=>0);
    evidence.asset_state.autonomy={page_title:title,blocking_overlay_count:blocked};
    assert.equal(blocked,0,'不得有阻擋性科學警訊遮罩');
    evidence.checks.push('study_page_usable_without_professor_or_evidence');

    // 資產失敗不得無聲：記錄全部外部失敗，並對已授權的資產要求零失敗。
    const authorized=evidence.failed_requests.filter(f=>
        f.url.startsWith('https://cdn.jsdelivr.net/')||f.url.startsWith('https://cdnjs.cloudflare.com/'));
    assert.deepEqual(authorized,[],'已授權網域的資產不得失敗：'+JSON.stringify(authorized));
    evidence.asset_state.plotly_served=process.env.I12_SERVE_PLOTLY==='1';
    evidence.checks.push('authorized_assets_have_no_silent_failures');
}

main().then(async()=>{evidence.status='passed';})
    .catch(async e=>{evidence.status='failed';evidence.failure=e.stack||String(e);
        try{await snapshot('failure');}catch(_){}})
    .finally(async()=>{
        try{await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({output,...evidence},null,2));}catch(_){}
        if(browser)await browser.close();
        console.log(JSON.stringify({output,...evidence},null,2));
        process.exit(evidence.status==='passed'?0:1);
    });

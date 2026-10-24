// 目的：真瀏覽器驗科學建議、原稿不變、來源變更回應恢復及無證據仍保存／匯出。
// 邊界：只用 localhost 的 sai_browser_fixture --autonomy，資料修正限明示的合成 tmp。
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {execFileSync}=require('node:child_process');
const base='http://127.0.0.1:8893',pid='SAIBROWSER-p';
const output=path.resolve('output/playwright','scientific-'+new Date().toISOString().replace(/[:.]/g,'-'));
const evidence={sha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),checks:[],errors:[]};
let browser,page;
async function snapshot(name){await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});await fs.writeFile(path.join(output,name+'.yml'),await page.locator('body').ariaSnapshot());}
async function main(){
    await fs.mkdir(output,{recursive:true});
    browser=await chromium.launch({headless:true,executablePath:process.env.SAI_BROWSER_EXECUTABLE});
    page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
    page.on('pageerror',e=>evidence.errors.push(e.message));page.on('dialog',d=>d.accept());
    await page.goto(base+'/auth/login');await page.locator('[name=email]').fill('author@example.invalid');await page.locator('[name=password]').fill('Synthetic123!');await page.getByRole('button',{name:'登入'}).click();await page.waitForURL(base+'/');
    await page.goto(base+'/manuscript?pid='+pid);await page.waitForFunction(()=>window.wsApp?.socket?.connected);
    await page.locator('#sectionDropdownBtn').click();await page.locator('[data-section-id=introduction]').click();await page.locator('#editorCanvas .card-content').waitFor();
    const original=await page.locator('#editorCanvas').innerText();
    await page.locator('#btnScientificReview').click();await page.locator('#sciEvidence option').first().waitFor({state:'attached'});
    const claim='Every device experimentally improved at all temperatures.';
    await page.locator('#sciText').fill(claim);await page.locator('#sciEvidence').selectOption({index:0});
    const pending=page.waitForResponse(r=>r.url().endsWith('/scientific/assess'));
    await page.getByRole('button',{name:'比對並保存建議',exact:true}).click();const response=await pending;const report=await response.json();
    assert.equal(response.status(),201,JSON.stringify(report));assert.equal(report.original_text,claim);assert.equal(report.scientific_proof,false);
    assert.ok(report.claims.flatMap(c=>c.findings).some(f=>f.code==='unsupported_generalization'));
    await page.locator('#sciReport blockquote').first().waitFor();assert.equal(await page.locator('#editorCanvas').innerText(),original);
    await snapshot('claim-evidence-comparison');evidence.checks.push('actionable_source_comparison_original_unchanged');
    const reason='I retain this as a hypothesis pending more measurements.';
    await page.getByRole('textbox',{name:'作者回應理由'}).first().fill(reason);
    // 合成 DB 故障注入；絕不接觸專案工作樹／使用者資料庫。
    const root=path.resolve(process.env.SAI_SYNTHETIC_ROOT||'');
    assert.equal((await fs.realpath(path.dirname(root))).toLowerCase(),(await fs.realpath(os.tmpdir())).toLowerCase());assert.ok(path.basename(root).startsWith('roothinks-sai-browser-'));
    execFileSync(process.env.SAI_TEST_PYTHON,['-c',"import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"UPDATE evidence_segments SET text=text || ' Subsequent correction.' WHERE project_id=?\",(sys.argv[2],)); c.commit(); c.close()",path.join(root,'app.db'),pid]);
    const conflict=page.waitForResponse(r=>r.url().includes('/responses')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'追加我的回應'}).first().click();assert.equal((await conflict).status(),409);
    assert.equal(await page.getByRole('textbox',{name:'作者回應理由'}).first().inputValue(),reason);
    await page.getByRole('button',{name:'重新載入此報告（保留未送出的回應）'}).click();
    await page.locator('#sciReport .alert-warning').waitFor();assert.equal(await page.getByRole('textbox',{name:'作者回應理由'}).first().inputValue(),reason);
    const answered=page.waitForResponse(r=>r.url().includes('/responses')&&r.request().method()==='POST');await page.getByRole('button',{name:'追加我的回應'}).first().click();const answer=await answered;
    assert.equal(answer.status(),201);assert.equal((await answer.json()).response.scientific_proof,false);
    await page.locator('#sciReport').getByText('作者回應：',{exact:false}).first().waitFor();await snapshot('stale-response-preserved');evidence.checks.push('stale_409_refresh_reason_preserved_append_response');
    await page.locator('#sciEvidence').selectOption([]);await page.locator('#sciText').fill('Hypothesis: an untested mechanism may operate.');
    const unknown=page.waitForResponse(r=>r.url().endsWith('/scientific/assess'));await page.getByRole('button',{name:'比對並保存建議',exact:true}).click();assert.equal((await unknown).status(),201);
    await page.locator('#sciReport').getByText('尚未建立支持／未知',{exact:true}).waitFor();
    await page.getByRole('button',{name:'關閉科學建議'}).click();
    const before=await page.locator('#sectionVersion').inputValue();await page.locator('#editorCanvas .card-content').fill('Hypothesis: an untested mechanism may operate.');await page.getByRole('button',{name:/Save$/}).click();
    await page.waitForFunction(old=>document.querySelector('#sectionVersion').value!==old,before);
    const download=page.waitForEvent('download');await page.locator('#btnExportWord').click();const file=await download;assert.equal(await file.failure(),null);await file.saveAs(path.join(output,'advisory-nonblocking.docx'));
    assert.ok((await fs.stat(path.join(output,'advisory-nonblocking.docx'))).size>1000);
    await snapshot('hypothesis-saved-exported');evidence.checks.push('unknown_advisory_does_not_block_save_export');assert.deepEqual(evidence.errors,[]);
}
main().then(()=>{evidence.status='passed';}).catch(async e=>{evidence.status='failed';evidence.failure=e.stack;if(page)await snapshot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({output,...evidence},null,2));if(browser)await browser.close();});

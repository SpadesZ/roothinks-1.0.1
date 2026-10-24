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
    await page.waitForFunction(()=>document.querySelector('#scientificPanel').classList.contains('show')&&!document.querySelector('#scientificPanel').classList.contains('showing'));
    assert.equal(await page.locator('#scientificPanel .offcanvas-body > p').first().evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),true,'自主性說明不能被工作列遮住');
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
    for(const [source,text,expectedReason] of [
        ['No evidence shows that protein X causes disease Y.','Protein X causes disease Y.','source_does_not_establish_claim'],
        ['Treatment X decreases survival at 77 K.','Treatment X increases survival at 300 K.','different_quantities']
    ]){
        execFileSync(process.env.SAI_TEST_PYTHON,['-c',"import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE evidence_segments SET text=? WHERE project_id=?',(sys.argv[3],sys.argv[2])); c.commit(); c.close()",path.join(root,'app.db'),pid,source]);
        await page.locator('#sciText').fill(text);
        const pendingScope=page.waitForResponse(r=>r.url().endsWith('/scientific/assess'));
        await page.getByRole('button',{name:'比對並保存建議',exact:true}).click();const scoped=await pendingScope,body=await scoped.json();
        assert.equal(scoped.status(),201);assert.equal(body.claims[0].relation,'unknown');
        assert.equal(body.claims[0].comparisons[0].reason,expectedReason);assert.equal(body.original_text,text);
        await page.locator('#sciReport').getByText('尚未建立支持／未知',{exact:true}).waitFor();
        await page.locator('#sciReport').getByText(expectedReason==='different_quantities'?'數值或條件不同，請先確認是否同一樣本與指標。':'來源明示尚無證據、未知或未測試；不能當成支持或已證實矛盾。',{exact:true}).waitFor();
        assert.equal(await page.locator('#editorCanvas').innerText(),original);
    }
    await snapshot('scoped-abstention-explained');evidence.checks.push('negative_evidence_and_different_conditions_explained_without_rewriting');
    // I11 新增：保守判讀收斂成 unknown 時，未解析的方向衝突必須在畫面上指出要開哪一句與為什麼，且不得變成判定。
    {
        const leadSource='Energy expenditure was increased upon treatment X.',leadClaim='Treatment X reduces energy expenditure.';
        execFileSync(process.env.SAI_TEST_PYTHON,['-c',"import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE evidence_segments SET text=? WHERE project_id=?',(sys.argv[3],sys.argv[2])); c.commit(); c.close()",path.join(root,'app.db'),pid,leadSource]);
        await page.locator('#sciText').fill(leadClaim);
        const pendingLead=page.waitForResponse(r=>r.url().endsWith('/scientific/assess'));
        await page.getByRole('button',{name:'比對並保存建議',exact:true}).click();
        const leadResponse=await pendingLead,leadBody=await leadResponse.json();
        assert.equal(leadResponse.status(),201);assert.equal(leadBody.original_text,leadClaim);
        assert.equal(leadBody.claims[0].relation,'unknown','線索不得升格為矛盾判定');
        assert.equal(leadBody.scientific_proof,false);assert.equal(leadBody.blocks_writing,false);
        const lead=leadBody.claims[0].findings.find(f=>f.code==='counter_evidence_lead');
        assert.ok(lead,JSON.stringify(leadBody.claims[0].findings.map(f=>f.code)));
        assert.ok(lead.leads[0].signals.includes('direction_differs_arguments_unresolved'),JSON.stringify(lead.leads));
        assert.ok(leadBody.claims[0].findings.some(f=>f.code==='support_not_established'),'棄權說明不得被線索取代');
        await page.locator('#sciReport').getByText('尚未建立支持／未知',{exact:true}).waitFor();
        await page.locator('#sciReport').getByText(/請核對：來源 .+ 第 1 句/).first().waitFor();
        await page.locator('#sciReport').getByText(/方向相反，但主客體未能對應/).first().waitFor();
        assert.equal(await page.locator('#editorCanvas').innerText(),original,'線索不得改動作者原文');
        await snapshot('counter-evidence-lead');evidence.checks.push('unresolved_counter_evidence_lead_visible_without_verdict');
    }
    await page.locator('#sciEvidence').selectOption([]);await page.locator('#sciText').fill('Hypothesis: an untested mechanism may operate.');
    const unknown=page.waitForResponse(r=>r.url().endsWith('/scientific/assess'));await page.getByRole('button',{name:'比對並保存建議',exact:true}).click();assert.equal((await unknown).status(),201);
    await page.locator('#sciReport').getByText('尚未建立支持／未知',{exact:true}).waitFor();
    await page.getByRole('button',{name:'關閉科學建議'}).click();
    const before=await page.locator('#sectionVersion').inputValue();await page.locator('#editorCanvas .card-content').fill('Hypothesis: an untested mechanism may operate.');await page.getByRole('button',{name:/Save$/}).click();
    await page.waitForFunction(old=>document.querySelector('#sectionVersion').value!==old,before);
    const download=page.waitForEvent('download');await page.locator('#btnExportWord').click();const file=await download;assert.equal(await file.failure(),null);await file.saveAs(path.join(output,'advisory-nonblocking.docx'));
    assert.ok((await fs.stat(path.join(output,'advisory-nonblocking.docx'))).size>1000);
    await snapshot('hypothesis-saved-exported');evidence.checks.push('unknown_advisory_does_not_block_save_export');
    await page.setViewportSize({width:390,height:844});await page.locator('#btnScientificReview').click();await page.locator('#sciText').waitFor();
    await page.waitForFunction(()=>document.querySelector('#scientificPanel').classList.contains('show')&&!document.querySelector('#scientificPanel').classList.contains('showing'));
    assert.equal(await page.locator('#scientificPanel').evaluate(n=>{const r=n.getBoundingClientRect();return r.width<=window.innerWidth+1&&r.left>=-1&&r.right<=window.innerWidth+1;}),true);
    await snapshot('mobile-advisory');evidence.checks.push('mobile_panel_fits_viewport');
    await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'關閉科學建議'}).click();
    await page.evaluate(pid=>window.wsApp.socket.emit('chat_message',{pid,section:'introduction',title:'Synthetic',msg:'Draft an explicit hypothesis.'}),pid);
    await page.locator('#scientificDraftStatus button').waitFor();
    await page.locator('#sectionDropdownBtn').click();await page.locator('[data-section-id=results]').click();
    await page.locator('#scientificDraftStatus button').click();await page.locator('#sciMessage').filter({hasText:'此報告屬於 introduction'}).waitFor();
    assert.equal(await page.locator('#sciReport article').count(),0);await snapshot('generated-report-chapter-boundary');evidence.checks.push('generated_report_not_mislabeled_in_other_chapter');
    await page.goto(base+'/literature?pid='+pid);
    await page.waitForFunction(()=>window.literatureOrigins?.pid);
    const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/literature/upload')&&r.request().method()==='POST');
    await page.locator('#pdfUploadInput').setInputFiles({name:'synthetic-browser-csrf.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-synthetic-XHR-preservation-control')});
    const upload=await uploaded,result=await upload.json();assert.equal(upload.status(),200,JSON.stringify(result));assert.equal(result.status,'success');
    const originalFile=path.join(root,pid,'literature','papers',result.paper_id,'00_origins','source.pdf');
    assert.equal(await fs.readFile(originalFile,'utf8'),'%PDF-synthetic-XHR-preservation-control');
    await snapshot('csrf-xhr-upload-registered');evidence.checks.push('csrf_xhr_upload_original_registered_no_parsing_claim');assert.deepEqual(evidence.errors,[]);
}
main().then(()=>{evidence.status='passed';}).catch(async e=>{evidence.status='failed';evidence.failure=e.stack;if(page)await snapshot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({output,...evidence},null,2));if(browser)await browser.close();});

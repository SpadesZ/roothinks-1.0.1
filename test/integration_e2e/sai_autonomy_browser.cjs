// 目的：對 --autonomy 合成站實走獨立、教授選用、限定團隊三流程；不接正式資料或模型。
// 驗證：NODE_PATH 指向已安裝 playwright，SAI_BROWSER_EXECUTABLE 可指定現有 Chromium。
// 執行：node test/integration_e2e/sai_autonomy_browser.cjs；成果只寫 output/playwright/<新 run>。
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const base='http://127.0.0.1:8893',pid='SAIBROWSER-p',password='Synthetic123!';
const run='autonomy-'+new Date().toISOString().replace(/[:.]/g,'-');
const output=path.resolve('output/playwright',run);
const evidence={run,sha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),checks:[],errors:[]};
let browser;
async function snapshot(page,name){
    await fs.writeFile(path.join(output,name+'.yml'),await page.locator('body').ariaSnapshot());
    await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
}
async function login(page,email){
    await page.goto(base+'/auth/login');
    await page.getByRole('textbox',{name:'Email',exact:true}).fill(email);
    await page.getByRole('textbox',{name:'密碼',exact:true}).fill(password);
    await page.getByRole('button',{name:'登入'}).click();
    await page.waitForURL(base+'/');
}
async function register(page,email){
    await page.goto(base+'/auth/register');
    await snapshot(page,'register-'+email.split('@')[0]);
    await page.locator('[name=email]').fill(email);
    await page.locator('[name=password]').fill(password);
    await page.locator('[name=confirm_password]').fill(password);
    await page.getByRole('button',{name:/註冊/}).click();
    await page.waitForURL(base+'/');
}
async function pageFor(){
    const ctx=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await ctx.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror',e=>evidence.errors.push(e.message));
    page.on('dialog',d=>d.accept());
    return page;
}
async function api(page,url,body){
    return page.evaluate(async({url,body})=>{
        const token=document.querySelector('input[name=csrf_token],#collaboration-csrf,#impact-csrf')?.value;
        const r=await fetch(url,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(token?{'X-CSRFToken':token}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
        return {status:r.status,data:await r.json()};
    },{url,body});
}
async function workbench(page,target){
    await page.goto(base+'/manuscript?pid='+target);
    await page.waitForFunction(()=>window.wsApp?.socket?.connected);
    await page.locator('#editorCanvas .card-content').waitFor();
}
async function saveSection(page,text){
    const before=await page.locator('#sectionVersion').inputValue();
    await page.locator('#editorCanvas .card-content').fill(text);
    await page.getByRole('button',{name:/Save$/}).click();
    await page.waitForFunction(old=>document.querySelector('#sectionVersion').value && document.querySelector('#sectionVersion').value!==old,before);
    return page.locator('#sectionVersion').inputValue();
}
async function savePaper(page,text){
    const before=await page.locator('#globalVersion').inputValue();
    await page.locator('#fusionCanvas').fill(text);
    await page.locator('#btnSaveGlobal').click();
    await page.waitForFunction(old=>document.querySelector('#globalVersion').value && document.querySelector('#globalVersion').value!==old,before);
    return page.locator('#globalVersion').inputValue();
}
async function exportWord(page,name){
    const pending=page.waitForEvent('download');
    await page.locator('#btnExportWord').click();
    const file=await pending;
    assert.equal(await file.failure(),null);
    const destination=path.join(output,name+'.docx');
    await file.saveAs(destination);
    assert.ok((await fs.stat(destination)).size>1000);
    return destination;
}
async function main(){
    await fs.mkdir(output,{recursive:true});
    browser=await chromium.launch({headless:true,...(process.env.SAI_BROWSER_EXECUTABLE?{executablePath:process.env.SAI_BROWSER_EXECUTABLE}:{})});
    const author=await pageFor();
    await login(author,'author@example.invalid');
    await snapshot(author,'independent-dashboard');
    await author.getByRole('tab',{name:/正式研究/}).click();
    await author.getByRole('button',{name:/新增正式研究/}).click();
    await author.getByRole('textbox',{name:'專案名稱 (Project Name) *',exact:true}).fill('獨立研究 '+run);
    await author.getByRole('textbox',{name:'研究背景與動機 (Context & Background)',exact:true}).fill('樣本 A；300 K；量測，不延伸到其他樣本。');
    const response=author.waitForResponse(r=>r.url().includes('/api/project/create') && r.request().method()==='POST');
    await author.getByRole('button',{name:'建立正式專案 (Create Formal)'}).click();
    const result=await response,created=await result.json();
    assert.equal(result.status(),201); assert.deepEqual(created.project.members,[]);
    await workbench(author,created.pid);
    const original='Sample A improved at 300 K. No other sample was measured.';
    const first=await saveSection(author,original);
    const second=await saveSection(author,original+' Calibration was held fixed.');
    assert.notEqual(first,second);
    await savePaper(author,original+' Calibration was held fixed.');
    await exportWord(author,'independent');
    await snapshot(author,'independent-saved-exported');
    evidence.checks.push({name:'independent_no_professor',pid:created.pid,sectionVersions:[first,second],passed:true});
    // 其餘兩條流程於同一初始獨立研究加入選用角色。
    await guidedAndTeam(author);
    assert.deepEqual(evidence.errors,[]);
}
async function guidedAndTeam(author){
    await workbench(author,pid);
    await author.locator('#sectionDropdownBtn').click();
    await author.locator('[data-section-id=introduction]').click();
    await author.locator('#editorCanvas .card-content').filter({hasText:'The measured improvement'}).waitFor();
    const paragraph=await author.locator('#editorCanvas .card-content').innerText();
    await author.getByRole('button',{name:'主張與審閱',exact:true}).click();
    await author.locator('#prEvidence option').first().waitFor({state:'attached'});
    for(const [id,text] of Object.entries({prParagraph:paragraph,prPurpose:'說明本樣本量測',prClaim:'Only sample A improved',prConditions:'300 K; same calibration',prForbidden:'No other samples'}))await author.locator('#'+id).fill(text);
    await author.locator('#prConfirmed').check();
    const confirmed=author.waitForResponse(r=>r.url().endsWith('/paragraph/confirm'));
    await author.getByRole('button',{name:'確認並保存新決策'}).click();
    const confirmedResponse=await confirmed,confirmedData=await confirmedResponse.json();
    assert.equal(confirmedResponse.status(),201,JSON.stringify(confirmedData));
    const decision=confirmedData.decision;
    await author.getByRole('button',{name:'關閉',exact:true}).click();
    await savePaper(author,paragraph+' Calibration used the same reference plane. No claim about other samples is made.');
    await author.goto(base+'/collaboration?pid='+pid);
    await snapshot(author,'collaboration-owner');
    const professorEmail='professor-'+run+'@example.invalid',teamEmail='team-'+run+'@example.invalid';
    async function invite(email,kind,role='viewer'){
        await author.locator('#collaboration-pid').selectOption(pid);
        await author.locator('#invite-email').fill(email);
        await author.locator('#invite-kind').selectOption(kind);
        if(kind==='team')await author.locator('#invite-role').selectOption(role);
        if(role==='coauthor')await author.locator('#invite-sections').fill('introduction');
        const pending=author.waitForResponse(r=>r.request().method()==='POST' && r.url().endsWith('/invitations'));
        await author.getByRole('button',{name:'建立邀請',exact:true}).click();
        const response=await pending,result=await response.json();
        assert.equal(response.status(),201,JSON.stringify(result));
        return result;
    }
    const invitation=await invite(professorEmail,'professor');
    const professor=await pageFor();
    await register(professor,professorEmail);
    await professor.goto(base+'/collaboration');
    await professor.locator('#academic-role').selectOption('professor');
    await professor.locator('#academic-affiliation').fill('合成大學');
    await professor.getByRole('button',{name:'保存學術背景'}).click();
    await professor.getByRole('status').filter({hasText:'學術背景已保存'}).waitFor();
    assert.equal((await api(professor,'/manuscript/api/bootstrap/'+pid)).status,403);
    const wrong=await api(professor,'/api/collaboration/invitations/'+invitation.invitation.id+'/accept',{token:'wrong'});
    assert.ok([400,403].includes(wrong.status));
    await professor.getByRole('textbox',{name:'一次性邀請碼'}).fill(invitation.token);
    const accepted=professor.waitForResponse(r=>r.url().endsWith('/accept'));
    await professor.getByRole('button',{name:'接受邀請',exact:true}).click();
    assert.equal((await accepted).status(),200);
    await professor.goto(base+'/mentor/');
    await professor.locator('#reviewFeedbackBody').waitFor();
    await snapshot(professor,'professor-workbench');
    await professor.locator('#reviewFeedbackBody').fill('請明確保留樣本 A 與 300 K，不外推其他樣本。');
    await professor.locator('#reviewDecisionSelect').selectOption(decision.decision_id);
    const feedback=professor.waitForResponse(r=>r.url().endsWith('/items') && r.request().method()==='POST');
    await professor.getByRole('button',{name:'送出',exact:true}).click();
    const feedbackResponse=await feedback,feedbackData=await feedbackResponse.json();
    assert.equal(feedbackResponse.status(),201,JSON.stringify(feedbackData));
    const comment=feedbackData.item.comment;
    await workbench(author,pid);
    const revisedVersion=await savePaper(author,paragraph+' Calibration used the same reference plane. No claim about other samples is made.');
    await author.getByRole('button',{name:'主張與審閱',exact:true}).click();
    await author.locator('#prReviewVersion').selectOption(feedbackData.item.paper_version);
    const responseForm=author.locator('#prReviews form[data-thread="'+comment.id+'"]');
    await responseForm.waitFor();
    await responseForm.locator('[name=body]').fill('已保留條件，校正說明位於 V2。');
    await responseForm.locator('[name=version]').selectOption(revisedVersion);
    await responseForm.locator('[name=location]').fill('Calibration used the same reference plane. No claim about other samples is made.');
    const reply=author.waitForResponse(r=>r.url().includes('/comments/'+comment.id+'/events'));
    await responseForm.getByRole('button',{name:'追加作者回覆'}).click();
    assert.equal((await reply).status(),201);
    await professor.reload();
    await professor.locator('#reviewReason'+comment.id).fill('已核對該版本的量測界線。');
    const confirm=professor.waitForResponse(r=>r.url().includes('/comments/'+comment.id+'/events'));
    await professor.getByRole('button',{name:'確認已處理',exact:true}).click();
    assert.equal((await confirm).status(),201);
    await snapshot(professor,'professor-confirmed');
    await impactCycle(author,professor,decision,comment,revisedVersion,paragraph);
    await author.goto(base+'/collaboration?pid='+pid);
    await author.locator('#collaboration-pid').selectOption(pid);
    await author.getByRole('button',{name:'載入專案',exact:true}).click();
    const professorCard=author.locator('#project-invitations > div').filter({hasText:professorEmail});
    await professorCard.getByRole('textbox',{name:'撤銷理由'}).fill('完成本次審閱；移除存取但保留歷史。');
    const revoked=author.waitForResponse(r=>r.url().endsWith('/revoke'));
    await professorCard.getByRole('button',{name:'撤銷邀請／移除存取'}).click();
    assert.equal((await revoked).status(),200);
    assert.equal((await api(professor,'/api/mentor/mentees/1/reviews/'+pid)).status,403);
    await workbench(author,pid);
    await author.getByRole('button',{name:'主張與審閱',exact:true}).click();
    await author.locator('#prReviewVersion').selectOption(feedbackData.item.paper_version);
    await author.locator('#prReviews').getByText('已保留條件，校正說明位於 V2。',{exact:false}).waitFor();
    await snapshot(author,'professor-removed-history');
    await author.locator('#paragraphReviewPanel button').first().click();
    await saveSection(author,'Independent work continues after removing the optional professor.');
    await savePaper(author,'Independent work continues after removing the optional professor.');
    await exportWord(author,'after-professor-removal');
    evidence.checks.push({name:'optional_professor_invite_review_response_remove',reviewId:comment.id,passed:true});
    await author.goto(base+'/collaboration?pid='+pid);
    const teamInvitation=await invite(teamEmail,'team','coauthor');
    const team=await pageFor();
    await register(team,teamEmail);
    await team.goto(base+'/collaboration');
    await team.getByRole('textbox',{name:'一次性邀請碼'}).fill(teamInvitation.token);
    const teamAccepted=team.waitForResponse(r=>r.url().endsWith('/accept'));
    await team.getByRole('button',{name:'接受邀請',exact:true}).click();
    assert.equal((await teamAccepted).status(),200);
    await workbench(team,pid);
    await saveSection(team,'Team contribution within the assigned introduction only.');
    assert.equal((await api(team,'/manuscript/api/paragraph/context?pid='+pid+'&section=results')).status,403);
    assert.equal((await api(team,'/manuscript/api/export/docx/'+pid,{html:'<p>guess</p>'})).status,403);
    await snapshot(team,'team-scoped-writing');
    await team.goto(base+'/collaboration');
    await team.getByRole('textbox',{name:'退出理由'}).fill('本輪協作完成。');
    const left=team.waitForResponse(r=>r.url().endsWith('/revoke'));
    await team.getByRole('button',{name:'退出此專案'}).click();
    assert.equal((await left).status(),200);
    assert.equal((await api(team,'/manuscript/api/bootstrap/'+pid)).status,403);
    const revisions=await api(author,'/manuscript/api/revisions/'+pid);
    assert.equal(revisions.status,200);
    evidence.checks.push({name:'team_scoped_save_export_denial_exit_history',passed:true});
}
async function impactCycle(author,professor,decision,comment,version,paragraph){
    await author.getByRole('button',{name:'關閉',exact:true}).click();
    const entryId=decision.paragraph_context.evidence.entry_id;
    async function changeReason(note){
        const library=await api(author,'/api/literature/library?pid='+pid);
        assert.equal(library.status,200);
        const entry=library.data.entries.find(e=>e.entry_id===entryId);
        const updated=await api(author,'/api/literature/library/update',{pid,entry_id:entryId,base_rev:entry._rev,patch:{screening_note:note,screening_status:'included'}});
        assert.equal(updated.status,200,JSON.stringify(updated));
    }
    await changeReason('重新核對：僅 sample A、300 K；'+run);
    await author.goto(base+'/manuscript/change-impact?pid='+pid);
    await author.locator('#impact-claims').getByText('需要重新核對',{exact:false}).waitFor();
    await author.getByRole('button',{name:'檢視來源並重新確認本段'}).last().click();
    await author.locator('#impact-reason').fill('已讀原證據，保留作者測量界線。');
    await author.locator('#impact-conditions').fill('300 K; same calibration; AUTHOR_INPUT_KEEP');
    await author.locator('#impact-contradictions').fill('尚未解決的溫度矛盾');
    await author.locator('#impact-confirmed').check();
    let pending=author.waitForResponse(r=>r.url().includes('/paragraph/') && r.url().endsWith('/reconfirm'));
    await author.getByRole('button',{name:'追加新的作者確認'}).click();
    assert.equal((await pending).status(),409);
    assert.equal(await author.locator('#impact-conditions').inputValue(),'300 K; same calibration; AUTHOR_INPUT_KEEP');
    await author.locator('#impact-contradictions').fill('');
    await changeReason('表單開啟後再核對：sample A、300 K；'+run);
    pending=author.waitForResponse(r=>r.url().includes('/paragraph/') && r.url().endsWith('/reconfirm'));
    await author.getByRole('button',{name:'追加新的作者確認'}).click();
    assert.equal((await pending).status(),409);
    await snapshot(author,'impact-stale-retains-input');
    await author.getByRole('button',{name:'重新檢查變更'}).click();
    await author.getByRole('button',{name:'重新載入證據，保留我填的判斷'}).click();
    await author.locator('#impact-message').filter({hasText:'證據已重新載入'}).waitFor();
    assert.equal(await author.locator('#impact-conditions').inputValue(),'300 K; same calibration; AUTHOR_INPUT_KEEP');
    assert.equal(await author.locator('#impact-confirmed').isChecked(),false);
    await author.locator('#impact-confirmed').check();
    pending=author.waitForResponse(r=>r.url().includes('/paragraph/') && r.url().endsWith('/reconfirm'));
    await author.getByRole('button',{name:'追加新的作者確認'}).click();
    const saved=await pending,result=await saved.json();
    assert.equal(saved.status(),201,JSON.stringify(result));
    const did=result.decision.decision_id;
    const paper=author.locator('#impact-papers article').filter({has:author.getByRole('heading',{name:new RegExp('^稿件 '+version+' ·')})});
    await paper.locator('select').selectOption([did]);
    await paper.locator('textarea').fill('已核對本版本實際段落及新採用理由。');
    await paper.locator('input[type=checkbox]').check();
    pending=author.waitForResponse(r=>r.url().endsWith('/change-impact/reconfirm'));
    await paper.getByRole('button',{name:'追加稿件重新確認'}).click();
    assert.equal((await pending).status(),201);
    await snapshot(author,'impact-author-paper-reconfirmed');
    await professor.goto(base+'/manuscript/change-impact?pid='+pid);
    const review=professor.locator('#impact-reviews article').last();
    await review.locator('select').selectOption([did]);
    await review.locator('textarea').fill('原審閱者重新核對採用理由及條件。');
    await review.locator('input[type=checkbox]').check();
    pending=professor.waitForResponse(r=>r.url().endsWith('/change-impact/reconfirm'));
    await review.getByRole('button',{name:'原審閱者追加重新確認'}).click();
    assert.equal((await pending).status(),201);
    await snapshot(professor,'impact-professor-reconfirmed');
    const report=await api(author,'/manuscript/api/change-impact?pid='+pid);
    assert.equal(report.data.claims.find(c=>c.decision_id===decision.decision_id).status,'superseded');
    assert.equal(report.data.claims.find(c=>c.decision_id===did).status,'current');
    evidence.checks.push({name:'impact_explicit_author_paper_original_reviewer_reconfirmation',passed:true,review:comment.id});
}
main().then(()=>{evidence.status='passed';}).catch(async e=>{
    evidence.status='failed';evidence.failure=e.stack;process.exitCode=1;
    if(browser)for(const [i,page] of browser.contexts().flatMap(c=>c.pages()).entries())await snapshot(page,'failure-'+i).catch(()=>{});
}).finally(async()=>{
    if(browser)await browser.close();
    await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify(evidence,null,2));
    console.log(JSON.stringify({output,...evidence},null,2));
});

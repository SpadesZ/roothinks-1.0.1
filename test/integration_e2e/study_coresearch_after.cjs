// 目的：STUDY_PHASE=after 的完整共研旅程；由 study_coresearch_browser.cjs 載入，逐項對應設計文件的 AC1–AC11。
// 角色：研究者（owner）、隊友（editor）、指導教授（viewer＋MentorLink）、外人。全部是合成帳號與合成論文。
module.exports=async function after(ctx){
    const {base,pid,QUOTE,pageFor,login,openReader,selectQuote,snapshot,check,evidence}=ctx;
    const FAKE='replicated in three independent laboratories';
    const api=(page,url,body)=>page.evaluate(async({url,body})=>{
        const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const text=await r.text();
        let data=null;try{data=JSON.parse(text);}catch(e){data=text;}
        return {status:r.status,data};
    },{url,body});
    const notes=async page=>(await api(page,`/api/study/evidence_notes/${pid}`)).data.notes||[];

    // ---------- 研究者：閱讀器 ----------
    const page=await pageFor();
    const matrixCalls=[];
    page.on('request',r=>{if(r.url().includes('/matrix/latest'))matrixCalls.push(r.url());});
    await login(page,'researcher');
    await openReader(page);
    // R3：介面用繁中研究用語；譯文明示是機器翻譯；沒有矩陣時不重打 404。
    const ui=await page.evaluate(()=>({text:document.body.innerText,trans:[...document.querySelectorAll('#fulltext-content .section-header')].map(e=>e.textContent)}));
    check('R3 介面沒有殘留英文狀態字',!/Original Text|Context: None|Single View|Matrix View|Fulltext \(Block\)|\bIdle\b/.test(ui.text));
    check('R3 譯文欄標明機器翻譯',ui.trans.some(t=>t.includes('機器翻譯')),ui.trans.slice(0,4));
    check('R3 沒有矩陣時只請求一次',matrixCalls.length<=1,matrixCalls.length);
    const numberWarnings=await page.evaluate(()=>[...document.querySelectorAll('#fulltext-content [data-translation-check="numbers"]')].map(e=>e.textContent));
    check('R4 機器翻譯數字不一致只在錯的那段提示（其他段 0 誤報）',numberWarnings.length===1&&numberWarnings[0].includes('0.5'),numberWarnings);
    evidence.metrics.matrix_latest_requests=matrixCalls.length;
    const reader=await page.evaluate(()=>{
        const rows=[...document.querySelectorAll('#fulltext-content .original-content')];
        let current=null;const order=[];
        for(const el of document.querySelectorAll('#fulltext-content .page-marker, #fulltext-content .study-anchor-row')){
            if(el.classList.contains('page-marker'))current=Number(el.textContent.replace(/\D+/g,''));
            else order.push({ref:el.dataset.ref,page:Number(el.dataset.page),marker:current});
        }
        return {rows:rows.length,withAttrs:rows.filter(r=>r.dataset.ref&&r.dataset.page&&r.dataset.side==='original').length,order};
    });
    evidence.metrics.reader_rows=reader.rows;
    evidence.metrics.rows_with_anchor_attrs=reader.withAttrs;
    check('AC1 每個正文列都帶錨點屬性',reader.rows>0&&reader.rows===reader.withAttrs,reader);
    check('AC1 每列都在自己頁碼標記之後',reader.order.every(r=>r.marker===r.page),reader.order);
    await snapshot(page,'01-reader');

    // ---------- 選字 → 動作列 ----------
    check('選到原文',await selectQuote(page,QUOTE));
    await page.locator('#study-selection-toolbar').waitFor({state:'visible'});
    evidence.metrics.selection_actions_visible=await page.locator('[data-study-selection-action]:visible').count();
    evidence.metrics.selection_context_chars=await page.evaluate(()=>String(window.studyChat.currentContext?.content||'').length);
    evidence.metrics.selection_context_has_anchor=await page.evaluate(()=>!!window.studyChat.currentContext?.selection?.ref);
    check('AC2 選字後出現解釋／批判動作',evidence.metrics.selection_actions_visible===3);
    await snapshot(page,'02-selection-toolbar');

    // ---------- 解釋 → 結構化回答 ----------
    const [chat]=await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/study/chat')&&r.request().method()==='POST'),
        page.locator('[data-study-selection-action="explain"]').click(),
    ]);
    const chatBody=await chat.json();
    check('AC2 伺服器驗證選段為 exact',chatBody.structured?.selection?.status==='exact',chatBody.structured?.selection);
    const card=page.locator('[data-structured-answer]').last();
    await card.waitFor();
    const answer=await card.evaluate((el,fake)=>({
        pointEvidence:el.querySelectorAll('.study-points [data-evidence-anchor]').length,
        rejected:el.querySelectorAll('[data-rejected-quote]').length,
        fakeShownAsEvidence:[...el.querySelectorAll('[data-evidence-anchor]')].some(a=>a.textContent.includes(fake)),
        supports:[...el.querySelectorAll('[data-quote-match]')].map(s=>s.dataset.quoteMatch),
        verifiedWord:/驗證/.test(el.innerText),
        uncertainty:el.querySelector('[data-uncertainty]')?.textContent||'',
        systemCheck:el.querySelector('[data-system-check]')?.textContent||'',
    }),FAKE);
    evidence.metrics.answer_evidence_links=answer.pointEvidence;
    evidence.metrics.answer_rejected_quotes=answer.rejected;
    evidence.metrics.answer_uncertainty_shown=answer.uncertainty?1:0;
    check('AC3 假引文 0 個成為證據且明示被排除',answer.pointEvidence===1&&answer.rejected===1&&!answer.fakeShownAsEvidence,answer);
    check('AC3 沒有原文引文的論點標出來',JSON.stringify(answer.supports)==='["found","none"]',answer.supports);
    check('AC5 同時顯示 AI 自評與系統檢查',answer.uncertainty.includes('AI 自評')&&answer.systemCheck.includes('原文中找到的引文 1'),answer);
    check('B9 回答卡不用「驗證」描述引文比對，並說明不判斷是否支持',!answer.verifiedWord&&answer.systemCheck.includes('不判斷是否支持論點'),answer.systemCheck);
    await snapshot(page,'03-structured-answer');

    // ---------- 點證據回原文 ----------
    const chip=card.locator('.study-points [data-evidence-anchor]').first();
    const ref=await chip.getAttribute('data-evidence-anchor');
    await chip.click();
    await page.waitForFunction(r=>document.querySelector(`#fulltext-content [data-evidence-current][data-ref="${r}"]`),ref);
    const revealed=await page.evaluate(r=>{
        const el=document.querySelector(`#fulltext-content [data-evidence-current][data-ref="${r}"]`);
        const box=el.getBoundingClientRect();
        return {mode:el.getAttribute('data-evidence-current'),side:el.dataset.side,inView:box.bottom>0&&box.top<window.innerHeight,
            highlight:!!(window.CSS&&CSS.highlights&&CSS.highlights.get('study-evidence'))};
    },ref);
    check('AC4 點證據定位到同一列並標出引文',revealed.inView&&revealed.side==='original'&&revealed.mode==='quote',{ref,...revealed});
    await page.waitForTimeout(700);
    await snapshot(page,'04-evidence-revealed');

    // ---------- 草稿：未接受前不存檔；重新整理可復原 ----------
    check('AC6 接受前伺服器沒有證據筆記',(await notes(page)).length===0);
    await card.locator('[data-evidence-note-draft]').click();
    const draft=page.locator('[data-evidence-draft]');
    await draft.waitFor();
    check('AC3 引文全被排除的 AI 論點不預設進草稿',await draft.locator('[data-draft-skipped="1"]').count()===1&&
        !(await draft.innerText()).includes('多個實驗室重現'));
    await draft.locator('textarea[aria-label="我的假說"]').last().fill('退火時間可能是混淆因素，需另外驗證。');
    await snapshot(page,'05-draft');
    check('AC6 草稿編輯中伺服器仍沒有筆記',(await notes(page)).length===0);
    await page.reload();
    await page.waitForFunction(()=>window.studyEvidence);
    await page.locator('[data-notes-tab="evidence"]').click();
    const restored=await page.locator('[data-evidence-draft]').innerText();
    check('復原：重新整理後草稿仍在本機',restored.includes('從本機復原')&&
        (await page.locator('[data-evidence-draft] textarea[aria-label="我的假說"]').last().inputValue()).includes('退火時間'));
    await page.locator('[data-evidence-note-accept]').click();
    await page.locator('[data-evidence-note]').first().waitFor();
    const saved=await notes(page);
    const kinds=saved[0]?.statements.map(s=>s.kind)||[];
    check('AC6 接受後才寫入，三種類別正確',saved.length===1&&kinds[0]==='source_fact'&&kinds.includes('ai_interpretation')&&kinds.includes('human_hypothesis'),kinds);
    check('AC6 原文事實是伺服器核對的原文',saved[0]?.statements[0].text===QUOTE&&saved[0]?.anchors[0].status==='exact');
    check('AC6 接受者有紀錄',saved[0]?.accepted_by?.username==='researcher');
    check('AC3 假出處的說法沒有存進筆記',!JSON.stringify(saved[0]?.statements||[]).includes('重現'));
    check('無證據的陳述在清單上標示',await page.locator('[data-evidence-note] [data-statement-kind="human_hypothesis"]').first().innerText().then(t=>t.includes('無證據')));
    await snapshot(page,'06-evidence-note');

    // ---------- 譯文選段：來源事實改用原文 ----------
    await openReader(page);
    check('選到譯文',await selectQuote(page,'其他溫度沒有測試','translation'));
    await page.locator('#study-selection-toolbar').waitFor({state:'visible'});
    await page.locator('[data-study-selection-action="critique"]').click();
    const critique=page.locator('[data-structured-answer="critique"]').last();
    await critique.waitFor();
    const critiqueCheck=await critique.locator('[data-system-check]').innerText();
    check('譯文選段標明來自機器譯文',critiqueCheck.includes('選段來自機器譯文'),critiqueCheck);
    check('譯文選段附整段原文作來源並標明是推定',await critique.getByText('來源事實改用由譯文推定的整段原文（非逐句對應）：').count()===1);
    await snapshot(page,'07-translation-critique');

    // ---------- 舊流程：純文字 Tutor 照常 ----------
    await page.locator('#chat-input').fill('一般提問：這篇的樣本是什麼？');
    const [plain]=await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/study/chat')&&r.request().method()==='POST'),
        page.locator('#chat-input').press('Enter'),
    ]);
    const plainBody=await plain.json();
    check('AC10 不帶 mode 的 Tutor 回答不變',plainBody.ok===true&&!plainBody.structured&&plainBody.answer.includes('合成模型替身'),plainBody);

    // ---------- 請求審閱、匯出 ----------
    await page.locator('[data-notes-tab="evidence"]').click();
    await page.locator('[data-evidence-review="request"]').first().click();
    await page.locator('[data-review-state="requested"]').first().waitFor();
    check('S4 研究者請求審閱',(await notes(page))[0].review.state==='requested');
    const exported=await api(page,`/api/study/evidence_notes/${pid}/export`);
    check('AC11 匯出 Markdown 帶類別標籤',exported.status===200&&String(exported.data).includes('**我的假說**')&&String(exported.data).includes('**原文事實**'));

    // ---------- 隊友留言 ----------
    const mate=await pageFor();
    await login(mate,'teammate');
    await openReader(mate);
    await mate.locator('[data-notes-tab="evidence"]').click();
    const mateCard=mate.locator('[data-evidence-note]').first();
    await mateCard.waitFor();
    await mateCard.locator('input[aria-label="留言內容"]').fill('樣本數是否足夠？');
    await mateCard.locator('[data-evidence-comment]').click();
    await mate.locator('[data-comment-role="member"]').first().waitFor();
    check('AC9 隊友（editor）可留言',(await notes(mate))[0].comments.some(c=>c.author_role==='member'&&c.author.username==='teammate'));
    await snapshot(mate,'08-teammate-comment');
    await mate.context().close();

    // ---------- 指導教授：導師工作台 ----------
    const prof=await pageFor();
    await login(prof,'professor');
    const viewerWrite=await (async()=>{await prof.goto(`${base}/study/project/${pid}?pid=${pid}`);
        return api(prof,'/api/study/evidence_notes/comment',{pid,note_id:saved[0].note_id,body:'viewer 想直接寫'});})();
    check('AC9 viewer 不能用 Study API 寫',viewerWrite.status===403,viewerWrite.status);
    await prof.goto(`${base}/mentor/`);
    await prof.locator('[data-mentee-id]').first().click();
    const review=prof.locator('[data-study-review-note]').first();
    await review.waitFor();
    await review.locator('input').fill('請補其他溫度的量測，再談外推。');
    await review.locator('[data-study-review-comment]').click();
    await prof.waitForFunction(()=>document.querySelector('[data-study-review-note]')?.textContent.includes('指導教授'));
    check('AC9 教授在工作台看到已請求審閱的筆記並留言',(await notes(page)).length===1&&
        (await api(page,`/api/study/evidence_notes/${pid}`)).data.notes[0].comments.some(c=>c.author_role==='mentor'));
    await snapshot(prof,'09-mentor-workbench');
    await prof.context().close();

    // ---------- 外人 ----------
    const out=await pageFor();
    await login(out,'outsider');
    await out.goto(base+'/');
    const outsider=await api(out,`/api/study/evidence_notes/${pid}`);
    check('AC9 外人讀不到',outsider.status===403,outsider.status);
    await out.context().close();

    // ---------- 研究者看到回饋；陳述沒被改 ----------
    await page.reload();
    await page.waitForFunction(()=>window.studyEvidence);
    await page.locator('[data-notes-tab="evidence"]').click();
    await page.locator('[data-comment-role="mentor"]').first().waitFor();
    const final=(await notes(page))[0];
    check('人類回饋不改陳述與類別',JSON.stringify(final.statements)===JSON.stringify(saved[0].statements));
    await snapshot(page,'10-researcher-sees-feedback');

    // ---------- R2-C：非作者提建議，作者明確接受 ----------
    const mate2=await pageFor();
    await login(mate2,'teammate');
    await openReader(mate2);
    await mate2.locator('[data-notes-tab="evidence"]').click();
    const mcard=mate2.locator('[data-evidence-note]').first();
    await mcard.waitFor();
    check('B11 非作者看不到改寫與請求審閱，只能提建議',
        await mcard.locator('[data-evidence-edit],[data-evidence-review]').count()===0&&await mcard.locator('[data-evidence-suggest]').count()===1);
    await mcard.locator('[data-evidence-suggest]').click();
    const sEditor=mcard.locator('[data-evidence-editor="suggest"]');
    await sEditor.locator('textarea').last().fill('只限樣本 A、300 K；退火時間可能是混淆因素。');
    await sEditor.locator('input[aria-label="建議理由"]').fill('把範圍寫清楚');
    await sEditor.locator('[data-evidence-editor-submit]').click();
    await mate2.waitForFunction(()=>document.querySelector('#evidence-notes-status')?.textContent.includes('已送出建議'));
    const suggested=(await notes(mate2))[0];
    check('B11 建議不改作者內容',JSON.stringify(suggested.statements)===JSON.stringify(final.statements)&&suggested.suggestions.length===1);
    await snapshot(mate2,'11-teammate-suggestion');
    await mate2.context().close();

    await page.reload();
    await page.waitForFunction(()=>window.studyEvidence);
    await page.locator('[data-notes-tab="evidence"]').click();
    await page.locator('[data-suggestion-accept]').first().click();
    await page.waitForFunction(()=>document.querySelector('#evidence-notes-status')?.textContent.includes('已接受建議'));
    const merged=(await notes(page))[0];
    check('B11 作者接受後內容改變且可追溯',merged.statements.at(-1).text.includes('混淆因素')&&merged.statements.at(-1).added_by.username==='teammate'&&
        merged.revisions[0].action==='accepted_suggestion'&&merged.revisions[0].previous_statements.at(-1).text.includes('退火時間可能是混淆因素，需另外驗證'));
    check('B11 協作紀錄看得到',await page.locator('[data-evidence-history]').count()===1);

    // ---------- R2-B：支持判斷只由研究者設定 ----------
    await page.locator('[data-evidence-edit]').first().click();
    const aEditor=page.locator('[data-evidence-editor="edit"]');
    await aEditor.locator('select[aria-label^="引文是否支持"]').first().selectOption('partial');
    await aEditor.locator('[data-evidence-editor-submit]').click();
    await page.waitForFunction(()=>document.querySelector('#evidence-notes-status')?.textContent.includes('已改寫'));
    const judged=(await notes(page))[0].statements.find(st=>st.claim_support==='partial');
    check('B10 研究者的支持判斷有紀錄',judged&&judged.claim_support_by.username==='researcher');
    check('B10 清單標示研究者判斷',await page.locator('[data-evidence-note] [data-claim-support="partial"]').count()>=1);

    // ---------- R3：不確定性、修訂前內容、寫進研究筆記 ----------
    check('R3 已存筆記仍顯示 AI 自評不確定性',await page.locator('[data-evidence-note] [data-ai-uncertainty]').count()>=1);
    await page.locator('[data-evidence-history] > summary').first().click();
    check('R3 作者看得到每次修改前的內容',await page.locator('[data-revision-previous]').count()>=1);
    await page.locator('[data-evidence-insert]').first().click();
    const inserted=await page.locator('#study-notes').inputValue();
    check('R3 插入研究筆記帶出處與判斷狀態',inserted.includes('【證據筆記】')&&inserted.includes('證據 1')&&inserted.includes('不代表支持')&&inserted.includes('研究者判斷：部分支持'),inserted.slice(0,300));
    await page.waitForFunction(()=>document.querySelector('#study-notes-status')?.textContent==='已儲存',null,{timeout:15000});
    const savedFree=await api(page,`/api/study/notes/load/${pid}`);
    check('R3 插入的內容經既有存檔路徑寫入伺服器',JSON.stringify(savedFree.data).includes('【證據筆記】'),savedFree.status);
    await snapshot(page,'12b-evidence-into-notes');
    await snapshot(page,'12-author-accepts-and-judges');

    // ---------- R4：Manuscript 段落出處（寫作 → 證據筆記 → 修訂 → 匯出）----------
    const PARA1='本研究只在 300 K 觀察到樣本 A 的改善';
    await page.goto(`${base}/manuscript/?pid=${pid}`);
    await page.waitForFunction(()=>window.wsApp&&window.wsApp.collab&&window.paragraphProvenance);
    await page.locator('#sectionDropdownBtn').click();
    await page.locator('#sectionDropdownMenu [data-section-id="introduction"]').first().click();
    await page.waitForFunction(()=>window.wsApp.collab.currentSVer()&&document.querySelector('.editor-card[data-section="introduction"]')?.textContent.includes('300 K'),null,{timeout:15000});
    const selectText=async needle=>page.evaluate(n=>{
        const card=document.querySelector('.editor-card[data-section="introduction"]');
        const walker=document.createTreeWalker(card,NodeFilter.SHOW_TEXT);
        let node;while((node=walker.nextNode())){const i=node.textContent.indexOf(n);if(i>=0){
            const r=document.createRange();r.setStart(node,0);r.setEnd(node,node.textContent.length);
            const sel=window.getSelection();sel.removeAllRanges();sel.addRange(r);return node.textContent;}}
        return null;
    },needle);
    const selected=await selectText(PARA1);
    await page.locator('#btn-paragraph-provenance').click();
    await page.locator('#paragraphProvenancePanel.show').waitFor();
    await page.waitForFunction(()=>document.querySelector('#ppNote')?.options.length>0,null,{timeout:10000});
    check('R4 段落出處面板帶入選取的段落與證據筆記',(await page.locator('#ppParagraph').inputValue()).includes(PARA1)&&
        await page.locator('#ppNote option').count()>=1,{selected});
    await page.locator('[data-provenance-link]').click();
    await page.locator('[data-provenance-kind="evidence_note"]').first().waitFor({timeout:10000});
    const linkedCard=page.locator('[data-provenance-kind="evidence_note"]').first();
    const linkedText=await linkedCard.innerText();
    check('R4 段落連到證據筆記，分開顯示原文引文、AI 解讀、研究者判斷',linkedText.includes('不代表支持')&&linkedText.includes('AI 解讀')&&
        linkedText.includes('研究者判斷')&&await linkedCard.locator('[data-provenance-issues]').count()===0,linkedText.slice(0,300));
    await page.locator('#ppParagraph').fill('我們推測退火時間可能影響結果，這是研究者自己的推論。');
    await page.locator('#ppReason').fill('研究者推論，尚無文獻');
    await page.locator('[data-provenance-free]').click();
    await page.locator('[data-provenance-kind="free_writing"]').first().waitFor({timeout:10000});
    check('R4 沒有證據的段落可以標為自由寫作',await page.locator('[data-provenance-kind="free_writing"]').count()===1);
    await snapshot(page,'14-manuscript-provenance-linked');
    // 改寫已連結的段落並保存：保存成功（不被出處擋住），出處標示需要重新核對
    await page.keyboard.press('Escape');
    await page.locator('#paragraphProvenancePanel').evaluate(el=>bootstrap.Offcanvas.getOrCreateInstance(el).hide());
    await page.waitForTimeout(400);
    const beforeVer=await page.evaluate(()=>window.wsApp.collab.currentSVer());
    await page.evaluate(n=>{
        const card=document.querySelector('.editor-card[data-section="introduction"]');
        const walker=document.createTreeWalker(card,NodeFilter.SHOW_TEXT);let node;
        while((node=walker.nextNode())){if(node.textContent.includes(n)){node.textContent=node.textContent.replace('300 K','300 K 與 310 K');break;}}
        card.querySelector('[contenteditable="true"]')?.dispatchEvent(new Event('input',{bubbles:true}));
    },PARA1);
    await page.locator('button:has-text("Save")').first().click();
    await page.waitForFunction(v=>window.wsApp.collab.currentSVer()&&window.wsApp.collab.currentSVer()!==v,beforeVer,{timeout:15000});
    const afterVer=await page.evaluate(()=>window.wsApp.collab.currentSVer());
    check('R4 有出處的段落仍可改寫並保存成新版本',afterVer!==beforeVer,{beforeVer,afterVer});
    await page.locator('#btn-paragraph-provenance').click();
    await page.waitForFunction(()=>document.querySelector('[data-provenance-kind="evidence_note"] [data-provenance-issues]'),null,{timeout:10000});
    const issues=await page.locator('[data-provenance-kind="evidence_note"] [data-provenance-issues]').first().getAttribute('data-provenance-issues');
    check('R4 段落改寫後出處標示需要重新核對',issues.split(',').includes('paragraph_changed'),issues);
    await page.locator('#paragraphProvenancePanel.show').waitFor();
    await page.waitForTimeout(500);  // offcanvas 滑入動畫
    await snapshot(page,'15-manuscript-provenance-recheck');
    const provExport=await page.evaluate(async p=>{const r=await fetch(`/manuscript/api/provenance/export?pid=${p}`);return {status:r.status,text:await r.text()};},pid);
    check('R4 出處紀錄可匯出，標明需要重新核對與自由寫作',provExport.status===200&&provExport.text.includes('需要重新核對')&&
        provExport.text.includes('自由寫作')&&provExport.text.includes('不代表支持'),provExport.status);
    evidence.metrics.provenance_export_chars=provExport.text.length;
    // Study 端看得到反向連結
    await page.goto(`${base}/study/project/${pid}?pid=${pid}`);
    await page.waitForFunction(()=>window.studyEvidence);
    await page.locator('[data-notes-tab="evidence"]').click();
    await page.locator('[data-evidence-used-in]').first().waitFor({timeout:10000});
    check('R4 Study 證據筆記顯示被用在稿件哪一段',(await page.locator('[data-evidence-used-in]').first().innerText()).includes('introduction'));
    await page.context().close();

    // ---------- R2-A：教授審閱模式 ----------
    const prof2=await pageFor();
    const asked=[];
    prof2.on('request',r=>{if(r.url().includes('/api/study/'))asked.push(r.url().replace(base,''));});
    await login(prof2,'professor');
    await prof2.goto(`${base}/study/project/${pid}?pid=${pid}`);
    await prof2.waitForFunction(()=>window.studyEvidence&&window.STUDY_REVIEW_SCOPE===true);
    await prof2.locator('[data-evidence-note]').first().waitFor();
    const reviewNotes=await prof2.locator('[data-evidence-note]').count();
    const banner=await prof2.locator('#evidence-notes-list').innerText();
    check('B2 教授只看到請求審閱的筆記',reviewNotes===1&&banner.includes('審閱模式'),{reviewNotes});
    check('B1 教授頁面不呼叫私人資料 API',!asked.some(u=>/notes\/load|conversations|conversation\/|matrix/.test(u)),asked);
    const privateRead=await api(prof2,`/api/study/notes/load/${pid}`);
    check('B1 教授直接讀自由筆記被拒',privateRead.status===403&&privateRead.data.review_scope===true,privateRead.status);
    const provProf=await api(prof2,`/manuscript/api/provenance?pid=${pid}&section=introduction`);
    const profNote=(provProf.data.links||[]).find(l=>l.kind==='evidence_note')?.note||{};
    const shared=(await api(prof2,`/api/study/evidence_notes/${pid}`)).data.notes?.length>0;
    check('R4 教授看得到段落出處，但只看得到已請求審閱的筆記內容',provProf.status===200&&(provProf.data.notes||[]).length===0&&
        (shared?!profNote.hidden:profNote.hidden===true),{status:provProf.status,shared,profNote:Object.keys(profNote)});
    await prof2.goto(`${base}/mentor/`);
    await prof2.locator('[data-mentee-id]').first().click();
    await prof2.locator('[data-study-review-used-in]').first().waitFor({timeout:10000});
    check('R4 教授工作台看得到請求審閱的筆記用在稿件哪一段',(await prof2.locator('[data-study-review-used-in]').first().innerText()).includes('introduction'));
    await snapshot(prof2,'16-mentor-sees-manuscript-use');
    await prof2.goto(`${base}/study/project/${pid}?pid=${pid}`);
    await prof2.waitForFunction(()=>window.studyEvidence&&window.STUDY_REVIEW_SCOPE===true);
    await prof2.locator('[data-evidence-note]').first().waitFor();
    check('R3 教授看不到修改前的內容',await prof2.locator('[data-revision-previous]').count()===0&&await prof2.locator('[data-evidence-insert]').count()===0);
    check('B3 教授仍能讀全文',(await api(prof2,`/api/study/get_fulltext/${pid}/${ctx.paper}`)).status===200);
    await snapshot(prof2,'13-professor-review-mode');
    await prof2.context().close();
};

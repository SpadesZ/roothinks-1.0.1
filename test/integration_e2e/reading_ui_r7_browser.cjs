// 目的：R7 雙語閱讀器的真瀏覽器檢查。開啟 study_coresearch_fixture.py 合成站裡的豐富合成論文（reading_ui_fixture_paper.py），
//       在桌機／筆電／平板／手機四種寬度量測：水平溢出、原文↔譯文同列同 data-ref、公式 KaTeX、圖表捲動、機器翻譯標示、
//       缺譯明示、頁碼、選字動作列位置、鍵盤可捲動區、繁中用語，並留下截圖與 JSON。
// 階段：R7_PHASE=before 只記錄（不 assert，供改版前存證）；after（預設）逐項驗收，任一失敗 exit 1。
// 執行：STUDY_FIXTURE_PORT=8921 I12_VENDOR_DIR=<vendor> NODE_PATH=<playwright 所在 node_modules> \
//       node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/reading_ui_r7_browser.cjs
//       R7_OUT=<結果目錄>（預設 output/playwright/reading-r7-<phase>-<時間>）、R7_SHOTS=<截圖目錄>（預設同 R7_OUT）。
// 邊界：只連 127.0.0.1 合成站；不寫資料、不呼叫模型；不改產品碼。
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const port=process.env.STUDY_FIXTURE_PORT||'8921';
const base=`http://127.0.0.1:${port}`,pid='STUDYCO-p',paper='synthetic_r7_reading',password='Synthetic123!';
const phase=process.env.R7_PHASE||'after';
const stamp=new Date().toISOString().replace(/[:.]/g,'-');
const out=path.resolve(process.env.R7_OUT||path.join('output/playwright',`reading-r7-${phase}-${stamp}`));
const shots=path.resolve(process.env.R7_SHOTS||out);
const VIEWPORTS=[['desktop',1440,900],['laptop',1280,800],['tablet',820,1180],['phone',390,844]];
// 只列「簡體專用字」與中國大陸慣用詞；繁體也用的字（如 件、制、查）不列，避免誤報。
const SIMPLIFIED=/[这们为说时对发数据译显码页图无关闭选择简获载错误请点击记录题读写设输认证计网络软统应该过还进现个来没种样实验测论献结讨参态状资务专项师导处开启动体变节检宽长温]/g;
const MAINLAND_TERMS=/信息|默認|屏幕|視頻|文檔|鼠標|用戶|軟件|數據庫|點擊這裡/g;
const UI_ENGLISH=/\bPage \d+\b|Failed to OCR|Equation Render Error|\breason:|Original Text|Translation\b|Loading\b/;
const result={phase,base,paper,started:new Date().toISOString(),viewports:{},checks:[],errors:[]};

function check(name,ok,detail){result.checks.push({name,ok:!!ok,detail:detail===undefined?null:detail});}

async function login(browser){
    const ctx=await browser.newContext();
    const page=await ctx.newPage();
    await page.goto(base+'/auth/login');
    await page.getByRole('textbox',{name:'Email',exact:true}).fill('researcher@example.invalid');
    await page.getByRole('textbox',{name:'密碼',exact:true}).fill(password);
    await page.getByRole('button',{name:'登入'}).click();
    await page.waitForURL(base+'/');
    const state=await ctx.storageState();
    await ctx.close();
    return state;
}

async function openReader(page){
    await page.goto(`${base}/study/project/${pid}?pid=${pid}&read=${paper}`);
    await page.waitForFunction(p=>window.studyView&&window.studyView.currentFulltextPaperId===p,paper);
    await page.locator('#fulltext-content .original-content').first().waitFor();
    await page.waitForFunction(()=>!document.querySelector('#fulltext-content .katex-eq:empty'));
    await page.evaluate(()=>document.fonts.ready);
    // 圖片是 loading="lazy"：像讀者一樣逐張捲到眼前讓它載入，再回到頂端。
    for(const img of await page.locator('#fulltext-content img').all()){
        await img.scrollIntoViewIfNeeded();
        await img.evaluate(i=>i.complete?null:new Promise(ok=>{i.addEventListener('load',ok,{once:true});i.addEventListener('error',ok,{once:true});}));
    }
    await page.evaluate(()=>document.querySelector('#fulltext-content').parentElement.scrollTo(0,0));
    await page.waitForTimeout(150);
}

// 讓目標元素捲到閱讀窗格頂端（閱讀器在內嵌捲動容器裡，不是整頁捲動）。
async function scrollTo(page,selector){
    return page.evaluate(sel=>{
        const el=document.querySelector(sel);
        if(!el)return false;
        el.scrollIntoView({block:'start'});
        return true;
    },selector);
}

function measure(){
    const content=document.getElementById('fulltext-content');
    const scroller=content.parentElement;
    const se=document.scrollingElement;
    const r=x=>Math.round(x);
    const box=el=>el.getBoundingClientRect();
    const res={viewport:[innerWidth,innerHeight]};
    res.doc={scrollWidth:se.scrollWidth,clientWidth:se.clientWidth,overflow:se.scrollWidth>se.clientWidth};
    res.reader={clientWidth:scroller.clientWidth,scrollWidth:scroller.scrollWidth,overflow:scroller.scrollWidth>scroller.clientWidth+1};
    // 內容溢出：元素自己的內容比框寬（長網址、長檔名），且最近的裁切祖先不是刻意的捲動區。
    const clipper=el=>{
        for(let a=el.parentElement;a&&a!==content;a=a.parentElement){
            const ox=getComputedStyle(a).overflowX;
            if(ox!=='visible')return {el:a,mode:ox};
        }
        return null;
    };
    const overflowing=[];
    for(const el of content.querySelectorAll('p,div,span,h4,h5,img,table')){
        if(el.closest('.katex'))continue;
        const cs=getComputedStyle(el);
        if(cs.display==='inline'||!el.clientWidth)continue;
        const own=el.scrollWidth-el.clientWidth;
        if(own>1&&cs.overflowX==='visible'){
            const c=clipper(el);
            // 刻意的捲動區（寬公式／表格）裡的溢出是設計，另由公式／表格檢查負責。
            if(c&&c.el.matches('[data-scroll-region]')&&/auto|scroll/.test(c.mode))continue;
            overflowing.push({cls:String(el.className||el.tagName).slice(0,50),excess:own,width:el.clientWidth,
                clippedBy:c?String(c.el.className||c.el.tagName).slice(0,40)+':'+c.mode:'none',text:(el.textContent||'').trim().slice(0,70)});
        }
    }
    // 只留最內層（祖先會一起被算到）。
    res.overflowing=overflowing.filter(o=>!overflowing.some(x=>x!==o&&x.text.includes(o.text)&&x.width<o.width)).slice(0,12);
    res.overflowCount=overflowing.length;

    // 原文↔譯文對齊。
    const rows=[...content.querySelectorAll('.study-anchor-row')];
    res.rows=rows.map(row=>{
        const ref=row.dataset.ref;
        const o=row.querySelector('[data-side="original"]');
        const t=row.querySelector('[data-side="translation"]');
        const [c0,c1]=[row.children[0],row.children[1]];
        const b0=box(c0),b1=c1?box(c1):null;
        const stacked=b1?b1.top>=b0.bottom-1:false;
        return {ref,page:row.dataset.page,origRef:o?.dataset.ref||null,transRef:t?.dataset.ref||null,stacked,
            topDiff:b1&&!stacked?r(Math.abs(b0.top-b1.top)):null,heightDiff:b1&&!stacked?r(Math.abs(b0.height-b1.height)):null,
            missingShown:!t&&!!row.querySelector('[data-translation-missing]'),
            missingText:!t&&c1?c1.innerText.trim().slice(0,60):null,
            mtLabelInRow:!!row.querySelector('.section-header')||(t?/機器翻譯/.test(getComputedStyle(t,'::before').content||''):false)};
    });
    const payload=window.studyView.currentFulltextPayload||{};
    const blocks=[];
    (payload.blocks||[]).forEach((p,pi)=>{
        const pg=Number(p.page||pi+1);
        (p.blocks||[]).forEach((b,bi)=>{
            const type=String(b.type||'body').toLowerCase();
            blocks.push({ref:`p${pg}-b${bi+1}`,page:pg,type,noise:type==='header'||!!b.is_page_noise,
                text:String(b.content||'').trim(),zh:String(b.translation||b.content_zh||'').trim()});
        });
    });
    const q=(side,ref)=>content.querySelector(`[data-side="${side}"][data-ref="${CSS.escape(ref)}"]`);
    const textBlocks=blocks.filter(b=>!b.noise&&!['figure','table','equation'].includes(b.type)&&b.text);
    const flat=content.textContent.replace(/\s+/g,' ');
    res.blocks={total:blocks.length,text:textBlocks.length,
        originalAnchored:textBlocks.filter(b=>q('original',b.ref)).length,
        withTranslation:textBlocks.filter(b=>b.zh).length,
        translationAnchored:textBlocks.filter(b=>b.zh&&q('translation',b.ref)).length,
        withoutTranslation:textBlocks.filter(b=>!b.zh).map(b=>b.ref),
        noOriginalAnchor:textBlocks.filter(b=>!q('original',b.ref)).map(b=>`${b.ref}:${b.type}:${b.text.slice(0,24)}`),
        translationNotShown:textBlocks.filter(b=>b.zh&&!flat.includes(b.zh.replace(/\s+/g,' ').slice(0,14))).map(b=>`${b.ref}:${b.type}:${b.zh.slice(0,14)}`),
        // 原文必須逐字保留（不得 NFKC：10⁻³、µm、∂ω₂D 等）。
        originalAltered:textBlocks.filter(b=>{const h=q('original',b.ref);return h&&!h.textContent.replace(/\s+/g,' ').includes(b.text.replace(/\s+/g,' ').slice(0,60));}).map(b=>b.ref),
        specialWithRef:[...content.querySelectorAll('.equation-block[data-ref],.figure-block[data-ref],.table-block[data-ref]')].length,
        special:blocks.filter(b=>['figure','table','equation'].includes(b.type)).length};

    // 公式。
    const eqBlocks=[...content.querySelectorAll('.equation-block')];
    const katexErrors=[...content.querySelectorAll('.katex-error, .katex [style]')].filter(e=>e.classList.contains('katex-error')||e.style.color==='rgb(204, 0, 0)');
    res.equations={blocks:eqBlocks.length,rendered:content.querySelectorAll('.katex-eq .katex').length,
        // throwOnError:false 時，不認得的指令（如 \\SI）會畫成紅字（errorColor #cc0000），不一定產生 .katex-error。
        katexErrorNodes:katexErrors.length,
        katexErrorText:katexErrors.map(e=>e.textContent.slice(0,60)),
        fallbackShown:content.querySelectorAll('[data-equation-fallback]').length,
        items:eqBlocks.map(e=>({w:e.clientWidth,sw:e.scrollWidth,scrolls:e.scrollWidth>e.clientWidth+1,
            scrollContainer:(()=>{const s=e.matches('[data-scroll-region]')?e:e.querySelector('[data-scroll-region]');return s?{sw:s.scrollWidth,cw:s.clientWidth,tab:s.tabIndex,role:s.getAttribute('role')}:null;})(),
            outer:r(box(e).width),text:e.innerText.replace(/\s+/g,' ').slice(0,50)}))};

    // 圖表。
    res.media=[...content.querySelectorAll('.figure-block,.table-block')].map(b=>{
        const img=b.querySelector('img');
        const s=b.querySelector('[data-scroll-region]');
        return {kind:b.classList.contains('table-block')?'table':'figure',imgLoaded:img?img.complete&&img.naturalWidth>0:null,
            natural:img?img.naturalWidth:0,shown:img?r(box(img).width):0,scale:img&&img.naturalWidth?+(box(img).width/img.naturalWidth).toFixed(2):null,
            blockWidth:b.clientWidth,outer:r(box(b).width),blockOverflow:b.scrollWidth>b.clientWidth+1,
            scrollRegion:s?{sw:s.scrollWidth,cw:s.clientWidth,scrolls:s.scrollWidth>s.clientWidth+1,tab:s.tabIndex,role:s.getAttribute('role'),label:s.getAttribute('aria-label')}:null,
            caption:(b.querySelector('p')?.textContent||'').slice(0,40),captionZh:/[一-鿿]/.test(b.textContent),
            mtLabel:/機器翻譯/.test(b.innerText)};
    });
    // 引文／網址／DOI 所在段落：不得撐出欄位。
    res.links=[...content.querySelectorAll('[data-side]')].filter(h=>/https?:\/\/|doi:/.test(h.textContent)).map(h=>{
        const col=h.parentElement;
        return {ref:h.dataset.ref,side:h.dataset.side,own:h.scrollWidth-h.clientWidth,colOverflow:col.scrollWidth-col.clientWidth,
            rightOver:r(box(h).right-box(col).right)};
    });
    // 頁碼。
    res.pages={markers:[...content.querySelectorAll('.page-marker')].map(m=>m.textContent.trim()),
        nav:!!document.querySelector('[data-page-nav]'),navItems:[...document.querySelectorAll('[data-page-nav] [data-page-jump]')].map(b=>b.dataset.pageJump)};
    let current=null;const order=[];
    for(const el of content.querySelectorAll('.page-marker, .study-anchor-row, [data-ref][data-page]:not(.study-anchor-row):not([data-side])')){
        if(el.classList.contains('page-marker'))current=Number(el.textContent.replace(/\D+/g,''));
        else order.push({ref:el.dataset.ref,page:Number(el.dataset.page),marker:current});
    }
    res.pages.outOfOrder=order.filter(o=>o.page!==o.marker);
    // 機器翻譯標示。
    const sections=[...content.querySelectorAll('.bilingual-section')];
    const labelled=el=>/機器翻譯/.test(getComputedStyle(el,'::before').content||'')||[...el.querySelectorAll('.section-header,.study-mt-badge')].some(h=>h.textContent.includes('機器翻譯')&&h.offsetParent);
    const hosts=[...content.querySelectorAll('[data-side="translation"]')];
    res.mt={sections:sections.length,sectionsWithTranslation:sections.filter(s=>s.querySelector('[data-side="translation"]')).length,
        sectionsLabelled:sections.filter(s=>s.querySelector('[data-side="translation"]')&&[...s.querySelectorAll('.section-header')].some(h=>h.textContent.includes('機器翻譯'))).length,
        translationHosts:hosts.length,
        unlabelled:hosts.filter(t=>{
            const heading=t.closest('.study-heading-trans');
            if(heading)return !labelled(heading);
            const row=t.closest('.study-anchor-row');
            if(!row)return true;
            const col=row.children[1];
            const stacked=col&&box(col).top>=box(row.children[0]).bottom-1;
            const own=labelled(col)||[...col.querySelectorAll('[data-side="translation"]')].some(labelled);
            if(stacked)return !own;
            const sec=row.closest('.bilingual-section');
            return !(own||(sec&&[...sec.querySelectorAll('.section-header')].some(h=>h.textContent.includes('機器翻譯'))));
        }).map(t=>t.dataset.ref)};
    // 版面：手機寬度時閱讀窗格與 AI 家教是否上下排列、閱讀窗格寬度。
    const mp=document.getElementById('matrix-pane'),ap=document.getElementById('aux-pane');
    res.layout={matrixPane:[r(box(mp).width),r(box(mp).height)],auxPane:[r(box(ap).width),r(box(ap).height)],
        splitStacked:box(ap).top>=box(mp).bottom-1,readerWidth:scroller.clientWidth};
    // 介面文字（閱讀器框架＋內容；論文本文是英文原文，所以只掃中文字與 UI 標籤）。
    const panel=document.getElementById('inline-fulltext-panel');
    const ui=panel.innerText;
    res.l10n={simplified:[...new Set(ui.match(/[这们为说时对发数据译显码页图无关闭选择简获载错误请点击记录题读写设输认证计网络软统应该过还进现个来没种样实验测论献结讨参态状资务专项师导处开启动体变节检宽长温]/g)||[])],
        mainland:[...new Set(ui.match(/信息|默認|屏幕|視頻|文檔|鼠標|用戶|軟件|數據庫|點擊這裡/g)||[])],
        uiEnglish:[...new Set((ui.match(/\bPage \d+\b|Failed to OCR Equation|Equation Render Error|\breason: [a-z_]+/g)||[]))],
        headerLabels:[...content.querySelectorAll('.section-header')].map(h=>h.textContent.trim()).filter((v,i,a)=>a.indexOf(v)===i)};
    // 鍵盤：會水平捲動的區域必須可聚焦（否則鍵盤使用者捲不到公式／表格右半邊）。
    const scrollRegions=[...content.querySelectorAll('*')].filter(el=>{const ox=getComputedStyle(el).overflowX;return (ox==='auto'||ox==='scroll')&&el.scrollWidth>el.clientWidth+1;});
    res.keyboard={scrollRegions:scrollRegions.length,focusable:scrollRegions.filter(el=>el.tabIndex>=0).length,
        unfocusable:scrollRegions.filter(el=>el.tabIndex<0).map(el=>String(el.className).slice(0,40))};
    return res;
}

async function selectionProbe(page){
    // 選譯文欄靠右的一段（最容易被擠出視窗），檢查動作列在視窗內且不蓋住選取。
    return page.evaluate(()=>{
        const host=document.querySelector('#fulltext-content [data-side="translation"][data-ref="p1-b5"]')
            ||document.querySelector('#fulltext-content [data-side="translation"]');
        host.scrollIntoView({block:'center'});
        const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);
        const node=walker.nextNode();
        const text=node.textContent;
        const at=Math.max(0,text.indexOf('降幅'));
        const range=document.createRange();
        range.setStart(node,at);range.setEnd(node,Math.min(text.length,at+16));
        const s=window.getSelection();s.removeAllRanges();s.addRange(range);
        host.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
        const bar=document.getElementById('study-selection-toolbar');
        const b=bar.getBoundingClientRect(),sel=range.getBoundingClientRect();
        const overlap=!(b.right<=sel.left||b.left>=sel.right||b.bottom<=sel.top||b.top>=sel.bottom);
        return {visible:getComputedStyle(bar).display!=='none',left:Math.round(b.left),right:Math.round(b.right),top:Math.round(b.top),
            bottom:Math.round(b.bottom),width:Math.round(b.width),height:Math.round(b.height),vw:innerWidth,vh:innerHeight,
            inViewport:b.left>=0&&b.right<=innerWidth&&b.top>=0&&b.bottom<=innerHeight,overlapsSelection:overlap,
            buttons:bar.querySelectorAll('[data-study-selection-action]').length,
            buttonsVisible:[...bar.querySelectorAll('[data-study-selection-action]')].filter(x=>{const r=x.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth;}).length,
            note:bar.querySelector('[data-selection-note]')?.textContent||''};
    });
}

async function keyboardProbe(page){
    // 從第一個可捲動的公式區用 Tab 走一步，確認焦點會落在下一個可捲動區並有可見外框。
    return page.evaluate(()=>{
        const regions=[...document.querySelectorAll('#fulltext-content [data-scroll-region]')];
        if(!regions.length)return {regions:0};
        const r=regions[0];
        r.focus();
        const focused=document.activeElement===r;
        const cs=getComputedStyle(r);
        return {regions:regions.length,focused,outline:cs.outlineStyle+' '+cs.outlineWidth,boxShadow:cs.boxShadow.slice(0,40),
            labelled:regions.every(x=>x.getAttribute('aria-label')),roles:[...new Set(regions.map(x=>x.getAttribute('role')))]};
    });
}

async function runViewport(browser,state,[name,width,height]){
    const ctx=await browser.newContext({viewport:{width,height},storageState:state,deviceScaleFactor:1});
    const page=await ctx.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror',e=>result.errors.push(`${name}: ${e.message}`));
    await openReader(page);
    const shot=async(label)=>page.screenshot({path:path.join(shots,`${name}-${label}.png`)});
    await shot('01-top');
    const m=await page.evaluate(measure);
    await scrollTo(page,'#fulltext-content .study-anchor-row[data-ref="p1-b5"]');await shot('02-abstract');
    await scrollTo(page,'#fulltext-content .study-anchor-row[data-ref="p2-b4"]');await shot('03-methods-url');
    await scrollTo(page,'#fulltext-content .equation-block');await shot('04-equations');
    await page.evaluate(()=>{const e=document.querySelectorAll('#fulltext-content .equation-block');e[e.length-1]?.scrollIntoView({block:'start'});});await shot('05-equations-fallback');
    await scrollTo(page,'#fulltext-content .figure-block');await shot('06-figure');
    await scrollTo(page,'#fulltext-content .table-block');await shot('07-table');
    await scrollTo(page,'#fulltext-content .study-anchor-row[data-ref="p4-b5"]');await shot('08-references');
    m.selection=await selectionProbe(page);
    await page.waitForTimeout(100);
    await shot('09-selection');
    m.keyboardFocus=await keyboardProbe(page);
    result.viewports[name]=m;
    await ctx.close();
    return m;
}

function evaluate(){
    for(const [name] of VIEWPORTS){
        const m=result.viewports[name];
        if(!m)continue;
        check(`${name} 整頁沒有水平溢出`,!m.doc.overflow,m.doc);
        check(`${name} 閱讀窗格沒有水平溢出`,!m.reader.overflow,m.reader);
        check(`${name} 段落內容沒有撐出欄位（長網址／DOI／檔名）`,m.overflowCount===0,m.overflowing);
        check(`${name} 每列原文與譯文同一 data-ref`,m.rows.length>0&&m.rows.every(r=>r.origRef===r.ref&&(r.transRef===null||r.transRef===r.ref)),m.rows.filter(r=>r.origRef!==r.ref||(r.transRef&&r.transRef!==r.ref)));
        check(`${name} 並排時原文與譯文同列頂端對齊、等高`,m.rows.every(r=>r.stacked||(r.topDiff<=1&&r.heightDiff<=1)),m.rows.filter(r=>!r.stacked&&(r.topDiff>1||r.heightDiff>1)));
        check(`${name} 每個正文區塊都有原文錨點`,m.blocks.originalAnchored===m.blocks.text,m.blocks.noOriginalAnchor);
        check(`${name} 每段有譯文的區塊都顯示譯文且帶同一錨點`,m.blocks.translationAnchored===m.blocks.withTranslation&&m.blocks.translationNotShown.length===0,
            {anchored:m.blocks.translationAnchored,expected:m.blocks.withTranslation,notShown:m.blocks.translationNotShown});
        check(`${name} 原文逐字保留（不正規化符號與數字）`,m.blocks.originalAltered.length===0,m.blocks.originalAltered);
        const missing=m.rows.filter(r=>r.transRef===null);
        check(`${name} 缺譯段落明確標示`,missing.length>=m.blocks.withoutTranslation.length&&missing.every(r=>r.missingShown),missing);
        check(`${name} 公式全部排版或改顯示原始 LaTeX，沒有 KaTeX 錯誤節點`,m.equations.katexErrorNodes===0&&m.equations.fallbackShown>=1,m.equations);
        check(`${name} 公式寬時在自己的區塊內捲動`,m.equations.items.every(e=>e.outer<=m.reader.clientWidth),m.equations.items);
        check(`${name} 圖表圖片都載入`,m.media.length>=2&&m.media.every(x=>x.imgLoaded),m.media);
        check(`${name} 表格在自己的容器內捲動且可讀（縮放 ≥ 0.3）`,m.media.filter(x=>x.kind==='table').every(x=>x.scrollRegion&&!x.blockOverflow&&x.scale>=0.3&&x.outer<=m.reader.clientWidth),m.media.filter(x=>x.kind==='table'));
        check(`${name} 圖說附機器翻譯並標示`,m.media.filter(x=>x.kind==='figure').every(x=>x.captionZh&&x.mtLabel),m.media);
        check(`${name} 引文／網址段落不撐出欄位`,m.links.every(l=>l.own<=1&&l.rightOver<=1),m.links);
        check(`${name} 每段譯文都看得到「機器翻譯」標示（並排看欄頭，上下排列每段自帶）`,m.mt.unlabelled.length===0&&m.mt.sectionsLabelled===m.mt.sectionsWithTranslation,m.mt);
        check(`${name} 閱讀窗格寬度可讀（≥ 300px）`,m.layout.readerWidth>=300,m.layout);
        check(`${name} 頁碼標記 4 頁、順序正確、可跳頁`,m.pages.markers.length===4&&m.pages.outOfOrder.length===0&&m.pages.nav&&m.pages.navItems.length===4,m.pages);
        check(`${name} 介面沒有簡體字或大陸用語`,m.l10n.simplified.length===0&&m.l10n.mainland.length===0,m.l10n);
        check(`${name} 閱讀器沒有殘留英文介面字`,m.l10n.uiEnglish.length===0,m.l10n.uiEnglish);
        check(`${name} 選字動作列在視窗內、三個按鈕可見、不蓋住選取`,m.selection.visible&&m.selection.inViewport&&m.selection.buttonsVisible===3&&!m.selection.overlapsSelection,m.selection);
        check(`${name} 可捲動的公式／表格區可用鍵盤聚焦`,m.keyboard.unfocusable.length===0&&m.keyboardFocus.regions>0&&m.keyboardFocus.focused&&m.keyboardFocus.labelled,{...m.keyboard,...m.keyboardFocus});
    }
    check('沒有頁面腳本錯誤',result.errors.length===0,result.errors);
}

(async()=>{
    await fs.mkdir(out,{recursive:true});
    await fs.mkdir(shots,{recursive:true});
    const browser=await chromium.launch(process.env.SAI_BROWSER_EXECUTABLE?{executablePath:process.env.SAI_BROWSER_EXECUTABLE}:{});
    try{
        const state=await login(browser);
        for(const vp of VIEWPORTS)await runViewport(browser,state,vp);
        evaluate();
    }catch(err){
        result.errors.push(String(err&&err.stack||err));
    }finally{
        await browser.close();
        result.finished=new Date().toISOString();
        const failed=result.checks.filter(c=>!c.ok);
        result.summary={checks:result.checks.length,passed:result.checks.length-failed.length,failed:failed.length,errors:result.errors.length};
        await fs.writeFile(path.join(out,'reading_ui_r7_result.json'),JSON.stringify(result,null,2));
        console.log(JSON.stringify({phase,out,shots,...result.summary,failedChecks:failed.map(c=>c.name)},null,1));
        if(phase!=='before'&&(failed.length||result.errors.length))process.exitCode=1;
    }
})();

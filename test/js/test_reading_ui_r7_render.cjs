// 目的：R7 雙語閱讀器的渲染契約（不開瀏覽器）：錨點、譯文、機器翻譯標示、公式退回原始 LaTeX、頁碼、數字檢查不誤報 DOI。
// 執行：node test/js/test_reading_ui_r7_render.cjs（在 repo 根目錄）。版面量測見 test/integration_e2e/reading_ui_r7_browser.cjs。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({console,window:{},document:{},CSS:{escape:s=>String(s)}});
vm.runInContext(fs.readFileSync('app/static/js/study_view.js','utf8'),context);
const StudyView=vm.runInContext('StudyView',context);
const view=Object.create(StudyView.prototype);

// 1. 數字檢查：DOI／網址裡的數字不算量測值；真的錯的數字仍要提示（R4）。
const doi='doi:10.5555/zenodo.1234567890123';
const warn=view._translationCheckHtml(`Drift stayed below 0.5 percent (${doi}).`,`漂移低於 0.3%（${doi}）。`);
assert.match(warn,/0\.5/,'錯的數字仍要提示');
assert.doesNotMatch(warn,/1234567890123/,'DOI 尾碼前是「.」不可誤報成遺失數字');
assert.equal(view._translationCheckHtml(`See https://data.example.org/v2.3.1/file_20260501.csv`,`見 https://data.example.org/v2.3.1/file_20260501.csv`),'');

// 2. 頁界標記：中文、只含頁碼這一組數字（旅程測試以 textContent 去掉非數字取頁碼）、是跳頁目標。
const marker=view._renderPageMarker(12);
assert.match(marker,/data-page-marker="12"/);
assert.doesNotMatch(marker,/Page/);
assert.equal(marker.replace(/<[^>]+>/g,'').replace(/\D+/g,''),'12');
assert.equal(view._renderPageNav([1]),'','只有一頁不顯示跳頁列');
assert.equal((view._renderPageNav([1,2,2,3]).match(/data-page-jump=/g)||[]).length,3);

// 3. 段落對照：每個正文區塊都有原文錨點；有譯文就顯示同一錨點的譯文；缺譯明示；符號原樣保留。
const pages=[{page:1,blocks:[
    {type:'Body',content:'1. Introduction',content_zh:'1. 緒論'},
    {type:'Body',content:'Power density stayed below 10⁻³ W µm⁻² for every 2–5 µm ribbon [12].',content_zh:'每條 2–5 µm 微帶的功率密度都低於 10⁻³ W µm⁻² [12]。'},
    {type:'Body',content:'(n = 12)',content_zh:'（n = 12）'},
    {type:'SubSubtitle',content:'2.1 Fabrication',content_zh:'2.1 製作'},
    {type:'Body',content:'The absorbed fraction was measured in situ with a photodiode.'},
    {type:'Equation',content:'(2)',latex:'\\kappa = \\frac{L}{2A}',latex_confidence:0.95},
    {type:'Figure',content:'Figure 1. κ versus ε.',content_zh:'圖 1．κ 對 ε。',pid:'P-p',paper_id:'x1',seq_id:'p1_fig_7'},
    {type:'Table',content:'Table 1. Values.',pid:'P-p',paper_id:'x1',seq_id:'p1_table_8'},
]}];
const html=view._renderBlockSections(pages,{translationReady:true});
const host=(side,ref)=>new RegExp(`data-side="${side}" data-ref="${ref}"`);
assert.match(html,host('original','p1-b1'),'章節標題有原文錨點');
assert.match(html,host('translation','p1-b1'),'章節標題的譯文要顯示');
assert.match(html,host('original','p1-b3'),'黏到上一列的短碎片仍有自己的原文錨點');
assert.match(html,host('translation','p1-b3'),'短碎片的譯文不可消失');
assert.ok(html.includes('（n = 12）'));
assert.match(html,host('original','p1-b4'),'小標題有原文錨點');
assert.match(html,host('translation','p1-b4'),'小標題的譯文要顯示');
assert.ok(html.includes('10⁻³ W µm⁻²')&&html.includes('2–5 µm')&&html.includes('[12]'),'原文符號、單位、引文原樣保留（不做 NFKC）');
assert.match(html,/data-translation-missing[^>]*>此段落沒有機器翻譯/,'缺譯段落明示');
assert.ok((html.match(/study-mt-badge/g)||[]).length>=3,'標題與圖說的譯文都標「機器翻譯」');
assert.match(html,/class="equation-block[^"]*" data-ref="p1-b6" data-page="1"[^>]*data-scroll-region tabindex="0" role="region"/,'公式區塊帶頁碼、可鍵盤聚焦捲動');
assert.match(html,/class="figure-block[^"]*" data-ref="p1-b7"/);
assert.match(html,/study-caption-trans[^>]*><span class="study-mt-badge"[^>]*>機器翻譯<\/span>圖 1．κ 對 ε。/,'圖說附機器翻譯');
assert.match(html,/class="study-media-scroll" data-scroll-region tabindex="0" role="region"/,'表格圖在可聚焦的捲動區內');
assert.doesNotMatch(html,/data-side="(original|translation)"[^>]*class="(equation|figure|table)-block/,'圖表公式不是選字錨點');
assert.equal((html.match(/study-anchor-row study-row-labelled/g)||[]).length,(html.match(/class="section-header[^"]*text-secondary"/g)||[]).length,'有欄頭的列才標 study-row-labelled');

// 4. 公式排不出來：原始 LaTeX 與 OCR 文字原樣（escape 後）顯示，不讓 4.2e6 變成 4.2·e·6。
const fallback=view._equationFallbackHtml('R = \\SI{4.2e6}{\\kelvin}<img src=x onerror=1>','Rth = 4.2×10⁶ K W⁻¹ (3)');
assert.match(fallback,/data-equation-fallback/);
assert.ok(fallback.includes('\\SI{4.2e6}{\\kelvin}&lt;img src=x onerror=1&gt;'),'LaTeX 原樣、HTML 跳脫');
assert.ok(fallback.includes('4.2×10⁶ K W⁻¹'));
assert.doesNotMatch(view._equationFallbackHtml('x','<Failed to OCR Equation>'),/OCR 文字/,'哨兵字不當成 OCR 文字');

// 5. OCR 失敗公式：中文說明、原哨兵字留在 title。
const failed=view._renderBlockCard({type:'Equation',content:'<Failed to OCR Equation>',equation_failed:true,equation_failure_reason:'latex_low_confidence'},{});
assert.match(failed,/公式辨識失敗/);
assert.match(failed,/原因代碼：latex_low_confidence/);
assert.doesNotMatch(failed.replace(/title="[^"]*"/g,''),/Failed to OCR|reason:/);
console.log('R7 reading UI render contract passed');

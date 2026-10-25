// 本機有效 PDF 控制：真瀏覽器 XHR、CSRF、原件、解析與同 ID 重複恢復。
// 只用既有 localhost 合成 fixture；不觸發模型或變更產品程式。
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto'),{execFileSync}=require('node:child_process');
const output=process.env.PY312_PDF_EVIDENCE,host=process.env.SAI_SYNTHETIC_ROOT;
const container=process.env.PY312_BROWSER_CONTAINER,remote=process.env.PY312_CONTAINER_SYNTHETIC_ROOT;
assert.ok(output && host && container?.startsWith('roothinks-py312-local-') && remote?.startsWith('/qa-tmp/roothinks-sai-browser-'));
fs.mkdirSync(output,{recursive:false});
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
// ponytail: 固定一頁 PDF 即足以驗三種解析路徑；不建立通用 PDF generator。
const stream='BT /F1 32 Tf 72 700 Td (Local PDF 300 K) Tj ET';
const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`];
let pdf='%PDF-1.4\n',offsets=[0];
for(let i=0;i<objects.length;i++){offsets.push(Buffer.byteLength(pdf));pdf+=`${i+1} 0 obj\n${objects[i]}\nendobj\n`;}
const xref=Buffer.byteLength(pdf);pdf+=`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
const bytes=Buffer.from(pdf);fs.writeFileSync(path.join(output,'synthetic.pdf'),bytes,{flag:'wx'});
const evidence={sha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),checks:[],errors:[]};
let browser;
(async()=>{
    browser=await chromium.launch({headless:true,executablePath:process.env.SAI_BROWSER_EXECUTABLE});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
    page.on('pageerror',e=>evidence.errors.push(e.message));page.on('dialog',d=>d.accept());
    const base='http://127.0.0.1:8893',pid='SAIBROWSER-p';
    await page.goto(base+'/auth/login');await page.locator('[name=email]').fill('author@example.invalid');
    await page.locator('[name=password]').fill('Synthetic123!');await page.getByRole('button',{name:'登入'}).click();await page.waitForURL(base+'/');
    await page.goto(base+'/literature?pid='+pid);await page.waitForFunction(()=>window.literatureOrigins?.pid);
    const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/literature/upload')&&r.request().method()==='POST');
    await page.locator('#pdfUploadInput').setInputFiles({name:'local-valid-control.pdf',mimeType:'application/pdf',buffer:bytes});
    const response=await uploaded,result=await response.json();assert.equal(response.status(),200,JSON.stringify(result));assert.equal(result.status,'success');
    const suffix=`/${pid}/literature/papers/${result.paper_id}/00_origins/source.pdf`;
    const original=path.join(host,...suffix.split('/').filter(Boolean));assert.equal(hash(fs.readFileSync(original)),hash(bytes));
    evidence.upload=result;evidence.sha256=hash(bytes);evidence.checks.push('browser_xhr_csrf_and_original_bytes');
    const csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');assert.ok(csrf);
    evidence.recovery=[];
    for(let i=0;i<2;i++){
        const recovery=await page.request.post(base+'/api/literature/recover_upload',{headers:{'X-CSRFToken':csrf},data:{pid,paper_id:result.paper_id}});
        const body=await recovery.json();assert.equal(recovery.status(),200,JSON.stringify(body));
        assert.equal(body.paper_id,result.paper_id);assert.equal(body.linked_entry_id,result.linked_entry_id);evidence.recovery.push(body);
        assert.equal(hash(fs.readFileSync(original)),hash(bytes));
    }
    evidence.checks.push('idempotent_recovery_preserves_identity_and_original');
    evidence.parsing=JSON.parse(execFileSync('docker',['exec','-i',container,'python','-',remote+suffix],
        {input:fs.readFileSync(path.join(__dirname,'pdf_check.py')),encoding:'utf8'}).trim());
    assert.equal(evidence.parsing.sha256,evidence.sha256);evidence.checks.push('poppler_text_pdf2image_tesseract_real_one_page');
    await page.screenshot({path:path.join(output,'uploaded.png'),fullPage:true});
    assert.deepEqual(evidence.errors,[]);evidence.status='passed';
})().catch(e=>{evidence.status='failed';evidence.failure=e.stack;process.exitCode=1;}).finally(async()=>{
    fs.writeFileSync(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2),{flag:'wx'});
    console.log(JSON.stringify(evidence,null,2));if(browser)await browser.close();
});

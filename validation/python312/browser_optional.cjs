const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const qa=process.env.PY312_EXTRA_EVIDENCE,errors=[],dialogs=[],consoleErrors=[];let browser,page;
(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.SAI_BROWSER_EXECUTABLE});
 page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
 page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text());});
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.accept();});
 await page.goto('http://127.0.0.1:8893/auth/login');await page.locator('[name=email]').fill('author@example.invalid');await page.locator('[name=password]').fill('Synthetic123!');
 await page.getByRole('button',{name:'登入'}).click();await page.waitForURL('http://127.0.0.1:8893/');
 await page.goto('http://127.0.0.1:8893/manuscript?pid=SAIBROWSER-p');await page.waitForFunction(()=>window.wsApp?.socket?.connected);
 await page.locator('#sectionDropdownBtn').click();await page.locator('[data-section-id=introduction]').click();
 await page.locator('#editorCanvas .card-content').waitFor();
 assert.equal(await page.evaluate(()=>typeof window.mermaid),'undefined','不能預載未用 Mermaid');
 const original=await page.locator('#editorCanvas').innerText();await page.evaluate(()=>window.wsApp.triggerGoogleDrive());
 assert.ok(dialogs.some(message=>message.includes('尚未由管理者配置')));assert.equal(await page.locator('#editorCanvas').innerText(),original);
 await page.evaluate(()=>window.wsApp.imgEngine.renderMermaidToChat('graph TD\nA[Source] --> B[Claim]'));
 assert.ok(await page.locator('.message-bubble svg').count(),JSON.stringify({consoleErrors,notices:await page.locator('.message-bubble').allInnerTexts()}));
 await page.evaluate(()=>{window._injected=false;return window.wsApp.imgEngine.renderMermaidToChat('graph TD\n???<img src=x onerror=window._injected=true>');});
 assert.equal(await page.evaluate(()=>window._injected),false);assert.equal(await page.locator('img[src=x]').count(),0);
 assert.ok((await page.locator('.message-bubble .alert-danger').innerText()).includes('繪圖解析失敗'));
 assert.deepEqual(errors,[]);await page.screenshot({path:path.join(qa,'diagram-and-optional-google.png'),fullPage:true});
 fs.writeFileSync(path.join(qa,'diagram-and-google.json'),JSON.stringify({status:'passed',lazy_real_mermaid_svg:true,literal_parse_error:true,
  unconfigured_google_nonblocking:true,live_oauth_verified:false,editor_unchanged:true,errors},null,2),{flag:'wx'});
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();});

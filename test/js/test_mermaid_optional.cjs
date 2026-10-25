// 目的：圖表僅在使用時載入；失敗文字不可成為 HTML。模型與科學規則不在測試範圍。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=[],scripts=[];
function element(){const value={children:[],style:{},appendChild(child){this.children.push(child);},replaceChildren(...children){this.children=children;},remove(){}};nodes.push(value);return value;}
let initializations=0;
const context=vm.createContext({console,setTimeout,clearTimeout,window:{},document:{createElement:element,head:{appendChild(script){scripts.push(script);}}}});
vm.runInContext(fs.readFileSync('app/static/js/manuscript_image.js','utf8'),context);
const Image=vm.runInContext('ManuImage',context),app={socket:{on(){}},chatContainer:element(),soed:{scrollToBottom(){}}};
const engine=new Image(app);
assert.equal(scripts.length,0,'工作區啟動不能下載未使用圖表 bundle');
(async()=>{
    const hostile='<img src=x onerror=alert(1)>';
    context.mermaid=context.window.mermaid={initialize:()=>initializations++,render:async()=>{throw new Error(hostile);}};
    await engine.renderMermaidToChat('invalid');
    const notices=nodes.filter(n=>String(n.textContent||'').includes(hostile));
    assert.equal(notices.length,1,'錯誤必須當作純文字顯示');
    assert.equal(notices[0].innerHTML,undefined,'不能把 untrusted error 插入 HTML');
    assert.equal(initializations,1);
    await engine.renderMermaidToChat('invalid again');assert.equal(initializations,1);
    console.log('Mermaid lazy load and literal error controls passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

// 目的：限定作者首次進站必須定位可見章節；不以未授權的 abstract 作預設。
// 驗證：node test/js/test_manuscript_visible_section.cjs；真 DOM 另由三流程瀏覽器驗證。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({console,document:{createElement:()=>({})}});
vm.runInContext(fs.readFileSync('app/static/js/manuscript_wsui.js','utf8'),context);
const UI=vm.runInContext('ManuUI',context);
for(const ids of [['introduction'],['abstract','introduction'],[]]){
    let options=[],value='abstract';
    const select={appendChild:o=>options.push(o),get value(){return value;},
        set value(v){value=options.some(o=>o.value===v)?v:'';},set innerHTML(_){options=[];value='';}};
    const app={sections:ids.map(id=>({id,label:id})),selectedSections:new Set(['abstract']),drafterTargetSection:select};
    const ui=Object.create(UI.prototype);ui.app=app;ui.renderSectionDropdown();
    const expected=ids.includes('abstract')?'abstract':ids[0] || '';
    assert.equal(select.value,expected,'2A 章節必須存在於可見選單');
    assert.equal([...app.selectedSections][0] || '',expected,'2B 必須與 2A 定位一致');
}
console.log('visible section initialization: 3 scenarios passed');

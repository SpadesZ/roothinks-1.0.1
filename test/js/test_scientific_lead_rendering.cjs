// 目的：保守判讀收斂成 unknown 時，未解析的反證線索必須讓作者看到哪一句、為什麼、下一步。
// 邊界：合成 DOM，不是真瀏覽器驗收；不證明科學正確，只證明線索不會在畫面上消失。
// 驗證：node test/js/test_scientific_lead_rendering.cjs
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

function element(tag){
    const n={tag,children:[],textContent:'',className:'',value:'',attrs:{},
        append(...k){n.children.push(...k);return n;},
        appendChild(k){n.children.push(k);return k;},
        replaceChildren(...k){n.children.length=0;n.children.push(...k);return n;},
        setAttribute(k,v){n.attrs[k]=v;},add(o){n.children.push(o);},addEventListener(){}};
    return n;
}
function collect(node,out=[]){
    if(node.textContent)out.push(node.textContent);
    for(const child of node.children||[])collect(child,out);
    return out;
}
function setup(){
    const container=element('div');
    const context=vm.createContext({console,fetch(){return Promise.resolve({ok:true,json:async()=>({})});},
        Option:function(label,value){return {label,value,textContent:label};},
        window:{},document:{addEventListener(){},createElement:element,
            getElementById:id=>id==='sciReport'?container:element('div'),body:element('body')}});
    const source=fs.readFileSync('app/static/js/scientific_review.js','utf8')
        .replace("    document.addEventListener('DOMContentLoaded'","    globalThis.check={render};\n    document.addEventListener('DOMContentLoaded'");
    vm.runInContext(source,context);
    return {context,container};
}
function reportWith(findings){
    return {report_id:'r1',stale:false,uncertainty:'規則未涵蓋的語意仍須人工核對。',responses:[],view_snapshot:'snap',
        claims:[{id:'c1',text:'Treatment X reduces energy expenditure.',relation:'unknown',findings,
            comparisons:[{source_id:'synthetic',sentence_index:0,locator:{page:3},
                quote:'Energy expenditure was increased upon treatment X.',relation:'unknown',
                reason:'different_arguments_or_conditions',applicable_conditions:[],other_context_conditions:[],source_conditions:[]}]}]};
}
const LEAD={code:'counter_evidence_lead',message:'候選原句有未解析的方向／否定／立場差異。',comparison:0,
    leads:[{comparison:0,source_id:'synthetic',sentence_index:0,signals:['direction_differs_arguments_unresolved']}]};

{   // 1. 有線索時，作者看到要開哪一句與原因。
    const {context,container}=setup();
    context.check.render(reportWith([LEAD,{code:'support_not_established',message:'這些規則未建立本主張的支持關係。'}]));
    const shown=collect(container).join('\n');
    assert.match(shown,/請核對：來源 synthetic 第 1 句/,'線索必須指出來源與句號');
    assert.match(shown,/方向相反，但主客體未能對應/,'線索必須說明原因');
    assert.match(shown,/這些規則未建立本主張的支持關係/,'線索不得取代棄權說明');
    assert.match(shown,/Treatment X reduces energy expenditure\./,'作者原句必須原樣顯示');
    assert.match(shown,/Energy expenditure was increased upon treatment X\./,'來源原句必須原樣顯示');
}
{   // 2. 沒有 leads 欄位時不得崩潰，也不得憑空出現核對提示。
    const {context,container}=setup();
    context.check.render(reportWith([{code:'missing_condition',message:'本句未明示部分條件。',missing:['300 K']}]));
    const shown=collect(container).join('\n');
    assert.doesNotMatch(shown,/請核對：來源/,'沒有線索就不得顯示核對提示');
    assert.match(shown,/本句未明示：300 K/,'既有條件渲染不得被破壞');
}
{   // 3. 未知訊號代碼要原樣露出，不可變成空白讓作者以為沒事。
    const {context,container}=setup();
    const unknown={...LEAD,leads:[{comparison:0,source_id:'synthetic',sentence_index:2,signals:['future_signal_code']}]};
    context.check.render(reportWith([unknown]));
    const shown=collect(container).join('\n');
    assert.match(shown,/請核對：來源 synthetic 第 3 句 — future_signal_code/,'未知代碼必須原樣顯示');
}
{   // 4. 多個訊號全部列出，不只顯示第一個。
    const {context,container}=setup();
    const many={...LEAD,leads:[{comparison:0,source_id:'synthetic',sentence_index:0,
        signals:['direction_differs_arguments_unresolved','negation_scope_differs']}]};
    context.check.render(reportWith([many]));
    const shown=collect(container).join('\n');
    assert.match(shown,/方向相反，但主客體未能對應/);
    assert.match(shown,/本句與候選的否定範圍不同/,'第二個訊號不得被吞掉');
}
console.log('scientific lead rendering: 4 scenarios passed');

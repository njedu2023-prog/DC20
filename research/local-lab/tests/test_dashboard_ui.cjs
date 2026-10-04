const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes={};const document={getElementById:id=>nodes[id]??=( {value:'',innerHTML:''})};
const ctx=vm.createContext({document,console});
const source=fs.readFileSync(__dirname+'/../web/app.js','utf8').replace(';load();',';');
vm.runInContext(source,ctx);
vm.runInContext(`
const row=(id,day,p,eligible,provenance='PROSPECTIVE_LOCAL')=>({id,signal_date:day,code:'A',name:'甲',p,model:'v'+id,provenance,payload:{independent_rank_eligible:eligible},outcomes:[]});
S={predictions:[row(1,'D1',.5,false),row(2,'D1',.6,true),row(3,'D2',.4,true)]};
`,ctx);
assert.equal(vm.runInContext('rated(S.predictions[0])',ctx),false);
assert.equal(vm.runInContext('historyRows().length',ctx),2);
assert.equal(vm.runInContext('historyRows()[0].id',ctx),1); // never pick a better later prediction
vm.runInContext(`S.predictions[0].provenance='RETROSPECTIVE'`,ctx);
assert.equal(vm.runInContext('historyRows()[0].id',ctx),2);
vm.runInContext(`S.official={D1:{rows:[],promotion:[{ts_code:'A',promotion_rank:1}]}};$('day').value='D1';$('predictionVersion').value='v1';renderRows();`,ctx);
assert(nodes.rows.innerHTML.includes('无信息 / 未评级'));
assert(nodes.rows.innerHTML.includes('晋1'));
console.log('6 dashboard regression checks passed');
vm.runInContext(`
S={learning:{quote_learning:{summary:{original_predictions:31,verified_quote_rows:15,complete_feature_rows:13,eligible_rows:6,eligible_days:1},training:{state:'BLOCKED_INSUFFICIENT_DATA'},days:[{day:'20260928',original_predictions:6,expected_count:6,verified_quote_rows:6,complete_feature_rows:6,eligible_rows:6,reasons:[]}]},active_research:{tracking:{engineering_completed:1,scheduled_linked_completed:0,runs:[{phase:'COMPLETED_AGENT',origin:'ENGINEERING_ACCEPTANCE',run_id:'engineering-only',attempt:1,at:'2026-10-04T00:00:00Z',disposition_ids:[1,2,3]}]}}}};
renderLearningProgress();
`,ctx);
assert(nodes.learningProgress.innerHTML.includes('6 条 / 1 日'));
assert(nodes.learningProgress.innerHTML.includes('定时关联完成 0 次'));
assert(nodes.learningProgress.innerHTML.includes('人工工程验收'));
assert(nodes.learningProgress.innerHTML.includes('不计入真实结算'));
console.log('4 learning readiness and trigger separation checks passed');

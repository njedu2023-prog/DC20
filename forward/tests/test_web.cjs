const assert=require('node:assert/strict');
const {pct,date,ranked,GROUPS}=require('../web/app.js');
assert.equal(pct(null),'—');assert.equal(pct(NaN),'—');assert.equal(pct(.0123),'1.23%');
assert.equal(date('20260909'),'2026-09-09');assert.equal(date('bad'),'—');
const rows=[{promotion_rank:1,profit_rank:2},{promotion_rank:2,profit_rank:1}];
assert.equal(ranked(rows,'profit_rank')[0].promotion_rank,2);
assert.equal(rows[0].promotion_rank,1);
assert.equal(Object.keys(GROUPS).length,7);
console.log('forward frontend format/order contracts: PASS');

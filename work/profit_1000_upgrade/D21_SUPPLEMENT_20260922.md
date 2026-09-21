# D 20260921 supplemental eligible-stock ranking

User approved on 2026-09-22: retain short-history names in the promotion list,
mark them unscorable for profit, and publish Top1/Top2 among scoreable names.
This is a supplementary display, NOT original prebuy publication evidence.
Do not insert it into the original natural shadow journal or historical wins.

- Original D bundle: 7d5c1338bd4223f93d2a4578b000adf7b046fc9107d84efae354ab47a1185a99.
- Runtime SHA256: 7795f558b7ba8379478a3358e592cfa081a41a29db14ff35abdd7244b81c8c00.
- Fixed model canonical SHA256: 999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589.
- Model evaluation SHA256: 779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872.
- Original receipt at dfe997422ad578c6473c8ed3c87c142885fb67d7 binds 20 earlier daily files plus D daily.
- Historical prior SHA256: 7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46.
- Used unchanged candidate_live_feature_math.compute_daily_features,
  compute_stock_prior and candidate_forward_predict.predict_forward.
- No T or later market data, model fitting, replacement promotion ranks or zero-filled returns.
- 601123.SH 马矿股份 listed 20260901; only15observed bars at D. Excluded from supplementary
  profit eligibility, not from the original ten-stock promotion list. Earlier August
  archived daily files contained no additional observations for this code.
- Other9stocks each have21observed bars. Recompute their daily features, retain
  source-adapter five-year/pool fields, recompute fixed prior; keep historically
  absent promotion_probability/path_change/path_label/existing_profit_rank as None.

|Supplement rank|Code|Name|Promotion rank|Fixed model score|
|---|---|---|---|---|
|1|000910.SZ|大亚圣象|4|-0.00938387292441548|
|2|002589.SZ|瑞康医药|6|-0.010534959973042811|
|3|000532.SZ|华金资本|8|-0.01254410506449177|
|4|000504.SZ|南华生物|1|-0.013999036902490013|
|5|002453.SZ|华软科技|3|-0.016054169665348356|
|6|600630.SH|龙头股份|2|-0.01673597195847716|
|7|600606.SH|绿地控股|7|-0.016856456046316744|
|8|001376.SZ|百通能源|5|-0.018739218262683953|
|9|600448.SH|华纺股份|9|-0.020714013233237103|

Scores are not probabilities or promises. Original formal index, frozen membership,
models and shadow statistics are unchanged. Exact date/model/bundle/feature/member
and complete promotion-rank bindings gate the display. If original formal D21
evidence becomes available, the normal validated loader takes priority.
This one-day repair does not implement a generalized daily short-history policy.

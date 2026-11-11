"""Read-only, explicit original-generation/redeployment provenance.

This entry never changes a failed run into a successful one. Original CAS and
later public deployment retain different identities. It grants no authority.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SCHEMA = "dc20_candidate_p0_original_generation_redeployment_v1"
IMPORT_SCHEMA = "dc20_candidate_p0_continuation_import_v1"
PREFIX = "work/profit_1000_upgrade/candidate_natural_forward/"
MAX_READS = 160
ORIGINAL_P0_CODE_PROFILE = {'.github/workflows/run_primary_d_daily.yml': '47537ce63b6abfbdd5d2db189b282a00a9eba59b', 'models/decision_model_freeze.json': '88026728c8b4266d5d9e958f206f24ed717393d3', 'models/decision_three_engines/promotion.joblib': '41ab1b8771cc3f601da4afc5521eea31d15b6385', 'models/decision_three_engines/validation_latest.json': '805eb6df569787b7f4fdc757cc464e57535cc0bf', 'requirements-dev.lock': '892183b9e5c4ed2e1c1899589b8793f556b32136', 'requirements.lock': '93ed4f921c4b9abe94c02e74a97b932a304d27d5', 'scripts/publish_primary_three_rank.py': 'a9e4a67d2458309d0faed75f04dafc913a8b68ea', 'scripts/sync_market_raw.py': 'ede7b0e29dc67c5f7552f5b8c71a2788f144de83', 'scripts/sync_pred_source.py': 'a5517d8271f0dc67c8034e16a1534a838f22fb06', 'src/top10decision/auction_v3/__init__.py': '8608ad5871ac45206bfa471ebea90e799113fe82', 'src/top10decision/auction_v3/calibration.py': 'abb71530b1b62889d924a06bfd490d82c0c4a2db', 'src/top10decision/auction_v3/config.py': '714a8c922b458db06f0ff4b93cebf19b3c2bcbb8', 'src/top10decision/auction_v3/engine.py': '191c42d51485c06406eebc4046994184b542b152', 'src/top10decision/auction_v3/promotion_model.py': 'c4ea817f173e6058004b19522a1862d486eb755f', 'src/top10decision/data/tushare_minute.py': 'dc27bff77ac11b9ea871220b8189aaf6725a9f81', 'src/top10decision/decision/canonical_fingerprint.py': 'c313f51c1c0630565bcaacca3783680f6e03d785', 'src/top10decision/decision/contracts.py': '34862a4dcdb3160754189e8301b49ab24c9dbf2e', 'src/top10decision/decision/d_close_features.py': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7', 'src/top10decision/decision/eligibility.py': '34ae803f7ea73cee861865ba49507dd81c0655c1', 'src/top10decision/decision/exit_policy.py': 'a495782254ddf3f58aa52ee668fe441f2aef315e', 'src/top10decision/decision/model_freeze.py': '38b190572b9afae3eaca2662e936e1e750e1d034', 'src/top10decision/decision/observation.py': 'd3f848e30519938b8530e6da7890a761f6722260', 'src/top10decision/decision/three_engine_models.py': '7a74a2fde21465a632db3a11c81954031ad550db', 'src/top10decision/decision/three_rank.py': 'cf18b86656e71eff5a38e0e57d35494db172528f', 'src/top10decision/decision/trade_selector.py': 'c9c8cac9e97a42b6d888df9b42cde11b532aef63', 'src/top10decision/probability_calibration.py': 'a005f68b26465bee49de70c57824c7a56c32d61f', 'src/top10decision/writers/io_contract.py': '28ded13148bcdd334da10767b1aed83e44c32e7c'}
ORIGINAL_RUNTIME_CODE_BINDINGS = [{'path': 'scripts/publish_primary_three_rank.py', 'sha256': 'f691fc579b1cd9b44168cd90393b492ffa833fe3a4576a2322bcd307dadbc9d1', 'git_blob_sha1': 'a9e4a67d2458309d0faed75f04dafc913a8b68ea', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/__init__.py', 'sha256': '1989253ea8106f0350ca08445ca41f623374c08c73d6621e23d34cf2860aded8', 'git_blob_sha1': '8608ad5871ac45206bfa471ebea90e799113fe82', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/calibration.py', 'sha256': '06890dee4d9c7a6cd007ebdda823b4907774ba372ebe37f3072432335fce79a9', 'git_blob_sha1': 'abb71530b1b62889d924a06bfd490d82c0c4a2db', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/config.py', 'sha256': '4c28a0ae2265435074a0689f4ad904ebfe465bd59fce3e8f558764d5014b94b2', 'git_blob_sha1': '714a8c922b458db06f0ff4b93cebf19b3c2bcbb8', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/engine.py', 'sha256': '68b39867e22761809c5636c1274013040e71555149193c0856ad44a51ec0d5d8', 'git_blob_sha1': '191c42d51485c06406eebc4046994184b542b152', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/promotion_model.py', 'sha256': 'af9941589e7eec766a3a855d9d8e384316852ea7f74c23122968961cc4cb2170', 'git_blob_sha1': 'c4ea817f173e6058004b19522a1862d486eb755f', 'git_mode': '100644'}, {'path': 'src/top10decision/data/tushare_minute.py', 'sha256': '30aa95e69040d3d61bd3f7ec9e9afe7bde909606e72c3fe5e687d0ef967af785', 'git_blob_sha1': 'dc27bff77ac11b9ea871220b8189aaf6725a9f81', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/canonical_fingerprint.py', 'sha256': '61024fb51f54eb1a90af59baf6330eee16dbe9678167e96fcf5940b51a4cc58b', 'git_blob_sha1': 'c313f51c1c0630565bcaacca3783680f6e03d785', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/contracts.py', 'sha256': '50e2cba257ee02804db8937a4a072572a56372afba941dfec9bfa719b812c917', 'git_blob_sha1': '34862a4dcdb3160754189e8301b49ab24c9dbf2e', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/d_close_features.py', 'sha256': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e', 'git_blob_sha1': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/eligibility.py', 'sha256': '31692b832fb0a6d4e83a2880600e78d36e38086f593ee4641174dd07f808acb7', 'git_blob_sha1': '34ae803f7ea73cee861865ba49507dd81c0655c1', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/exit_policy.py', 'sha256': '4c68819862b69541d55624b1f8be6dce69c902d20d4e9420212415596dd4597f', 'git_blob_sha1': 'a495782254ddf3f58aa52ee668fe441f2aef315e', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/observation.py', 'sha256': '577fbb21166fd39456ff17a06d0a6fde0a860e531dbe38b66ea117c0dd12d713', 'git_blob_sha1': 'd3f848e30519938b8530e6da7890a761f6722260', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/three_engine_models.py', 'sha256': '9a4a2405e3b95af9f1c05100aa8b97dc8b3ee62d63b4dda12e13f7f0fcd1de4c', 'git_blob_sha1': '7a74a2fde21465a632db3a11c81954031ad550db', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/three_rank.py', 'sha256': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a', 'git_blob_sha1': 'cf18b86656e71eff5a38e0e57d35494db172528f', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/trade_selector.py', 'sha256': '7934daec17dd50b5e702878fbe7e9ac35f894eff20daef0bedbe29712b182858', 'git_blob_sha1': 'c9c8cac9e97a42b6d888df9b42cde11b532aef63', 'git_mode': '100644'}, {'path': 'src/top10decision/probability_calibration.py', 'sha256': '058437b22042db5eed8572ff2b6e67fc5d2b6415bc7f89f846a5c230f983717c', 'git_blob_sha1': 'a005f68b26465bee49de70c57824c7a56c32d61f', 'git_mode': '100644'}, {'path': 'src/top10decision/writers/io_contract.py', 'sha256': '80bff133b8cd1519f0ef701338b0000587e22a6202212de80f0de5e6d91e0b97', 'git_blob_sha1': '28ded13148bcdd334da10767b1aed83e44c32e7c', 'git_mode': '100644'}]
ORIGINAL_PROMOTION_MODEL = {'version': 'decision_three_engine_models_v2:promotion:20260709:hgb:platt', 'as_of_date': '20260709', 'artifact_sha256': 'b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c'}
# Reviewed suspension-boundary implementation already active before D20261106.
CONTROLLED_P0_CODE_PROFILE = {**ORIGINAL_P0_CODE_PROFILE,
    'models/decision_model_freeze.json': '03572f2897d22961e96d88440f6aecbe5134bfa6',
    'scripts/publish_primary_three_rank.py': '54cc956de2996663b170ed6e09ce9fef812e2221'}
CONTROLLED_RUNTIME_CODE_BINDINGS = [{'path': 'scripts/publish_primary_three_rank.py', 'sha256': 'bb90b81c93787fd5e2ababdd76adaff33f3dec2bf1fb4553c57723b7a8003ba8', 'git_blob_sha1': '54cc956de2996663b170ed6e09ce9fef812e2221', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/__init__.py', 'sha256': '1989253ea8106f0350ca08445ca41f623374c08c73d6621e23d34cf2860aded8', 'git_blob_sha1': '8608ad5871ac45206bfa471ebea90e799113fe82', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/calibration.py', 'sha256': '06890dee4d9c7a6cd007ebdda823b4907774ba372ebe37f3072432335fce79a9', 'git_blob_sha1': 'abb71530b1b62889d924a06bfd490d82c0c4a2db', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/config.py', 'sha256': '4c28a0ae2265435074a0689f4ad904ebfe465bd59fce3e8f558764d5014b94b2', 'git_blob_sha1': '714a8c922b458db06f0ff4b93cebf19b3c2bcbb8', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/engine.py', 'sha256': '68b39867e22761809c5636c1274013040e71555149193c0856ad44a51ec0d5d8', 'git_blob_sha1': '191c42d51485c06406eebc4046994184b542b152', 'git_mode': '100644'}, {'path': 'src/top10decision/auction_v3/promotion_model.py', 'sha256': 'af9941589e7eec766a3a855d9d8e384316852ea7f74c23122968961cc4cb2170', 'git_blob_sha1': 'c4ea817f173e6058004b19522a1862d486eb755f', 'git_mode': '100644'}, {'path': 'src/top10decision/data/tushare_minute.py', 'sha256': '30aa95e69040d3d61bd3f7ec9e9afe7bde909606e72c3fe5e687d0ef967af785', 'git_blob_sha1': 'dc27bff77ac11b9ea871220b8189aaf6725a9f81', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/canonical_fingerprint.py', 'sha256': '61024fb51f54eb1a90af59baf6330eee16dbe9678167e96fcf5940b51a4cc58b', 'git_blob_sha1': 'c313f51c1c0630565bcaacca3783680f6e03d785', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/contracts.py', 'sha256': '50e2cba257ee02804db8937a4a072572a56372afba941dfec9bfa719b812c917', 'git_blob_sha1': '34862a4dcdb3160754189e8301b49ab24c9dbf2e', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/d_close_features.py', 'sha256': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e', 'git_blob_sha1': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/eligibility.py', 'sha256': '31692b832fb0a6d4e83a2880600e78d36e38086f593ee4641174dd07f808acb7', 'git_blob_sha1': '34ae803f7ea73cee861865ba49507dd81c0655c1', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/exit_policy.py', 'sha256': '4c68819862b69541d55624b1f8be6dce69c902d20d4e9420212415596dd4597f', 'git_blob_sha1': 'a495782254ddf3f58aa52ee668fe441f2aef315e', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/observation.py', 'sha256': '577fbb21166fd39456ff17a06d0a6fde0a860e531dbe38b66ea117c0dd12d713', 'git_blob_sha1': 'd3f848e30519938b8530e6da7890a761f6722260', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/three_engine_models.py', 'sha256': '9a4a2405e3b95af9f1c05100aa8b97dc8b3ee62d63b4dda12e13f7f0fcd1de4c', 'git_blob_sha1': '7a74a2fde21465a632db3a11c81954031ad550db', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/three_rank.py', 'sha256': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a', 'git_blob_sha1': 'cf18b86656e71eff5a38e0e57d35494db172528f', 'git_mode': '100644'}, {'path': 'src/top10decision/decision/trade_selector.py', 'sha256': '7934daec17dd50b5e702878fbe7e9ac35f894eff20daef0bedbe29712b182858', 'git_blob_sha1': 'c9c8cac9e97a42b6d888df9b42cde11b532aef63', 'git_mode': '100644'}, {'path': 'src/top10decision/probability_calibration.py', 'sha256': '058437b22042db5eed8572ff2b6e67fc5d2b6415bc7f89f846a5c230f983717c', 'git_blob_sha1': 'a005f68b26465bee49de70c57824c7a56c32d61f', 'git_mode': '100644'}, {'path': 'src/top10decision/writers/io_contract.py', 'sha256': '80bff133b8cd1519f0ef701338b0000587e22a6202212de80f0de5e6d91e0b97', 'git_blob_sha1': '28ded13148bcdd334da10767b1aed83e44c32e7c', 'git_mode': '100644'}]
DELIVERY_HARDENED_P0_CODE_PROFILE = {'.github/workflows/run_primary_d_daily.yml': '47537ce63b6abfbdd5d2db189b282a00a9eba59b',
 'models/decision_model_freeze.json': '861df3b61d0f6d0a87dc1798f19b1f4434fe9d27',
 'models/decision_three_engines/promotion.joblib': '41ab1b8771cc3f601da4afc5521eea31d15b6385',
 'models/decision_three_engines/validation_latest.json': '805eb6df569787b7f4fdc757cc464e57535cc0bf',
 'requirements-dev.lock': '892183b9e5c4ed2e1c1899589b8793f556b32136',
 'requirements.lock': '93ed4f921c4b9abe94c02e74a97b932a304d27d5',
 'scripts/publish_primary_three_rank.py': '54cc956de2996663b170ed6e09ce9fef812e2221',
 'scripts/sync_market_raw.py': 'ede7b0e29dc67c5f7552f5b8c71a2788f144de83',
 'scripts/sync_pred_source.py': 'a5517d8271f0dc67c8034e16a1534a838f22fb06',
 'src/top10decision/auction_v3/__init__.py': '8608ad5871ac45206bfa471ebea90e799113fe82',
 'src/top10decision/auction_v3/calibration.py': 'abb71530b1b62889d924a06bfd490d82c0c4a2db',
 'src/top10decision/auction_v3/config.py': '714a8c922b458db06f0ff4b93cebf19b3c2bcbb8',
 'src/top10decision/auction_v3/engine.py': '191c42d51485c06406eebc4046994184b542b152',
 'src/top10decision/auction_v3/promotion_model.py': 'c4ea817f173e6058004b19522a1862d486eb755f',
 'src/top10decision/data/tushare_minute.py': 'dc27bff77ac11b9ea871220b8189aaf6725a9f81',
 'src/top10decision/decision/canonical_fingerprint.py': 'c313f51c1c0630565bcaacca3783680f6e03d785',
 'src/top10decision/decision/contracts.py': '34862a4dcdb3160754189e8301b49ab24c9dbf2e',
 'src/top10decision/decision/d_close_features.py': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7',
 'src/top10decision/decision/eligibility.py': '34ae803f7ea73cee861865ba49507dd81c0655c1',
 'src/top10decision/decision/exit_policy.py': 'a495782254ddf3f58aa52ee668fe441f2aef315e',
 'src/top10decision/decision/model_freeze.py': '38b190572b9afae3eaca2662e936e1e750e1d034',
 'src/top10decision/decision/observation.py': 'd3f848e30519938b8530e6da7890a761f6722260',
 'src/top10decision/decision/three_engine_models.py': '7a74a2fde21465a632db3a11c81954031ad550db',
 'src/top10decision/decision/three_rank.py': 'cf18b86656e71eff5a38e0e57d35494db172528f',
 'src/top10decision/decision/trade_selector.py': 'c9c8cac9e97a42b6d888df9b42cde11b532aef63',
 'src/top10decision/probability_calibration.py': 'a005f68b26465bee49de70c57824c7a56c32d61f',
 'src/top10decision/writers/io_contract.py': '28ded13148bcdd334da10767b1aed83e44c32e7c'}
# Exact successor after the independently reviewed, display-only pin rotation.
# The source review itself is bound in all three trees; earlier profiles remain
# valid only with their own unchanged complete file sets.
NEXT_DAY_STATISTICS_P0_CODE_PROFILE = {**DELIVERY_HARDENED_P0_CODE_PROFILE,
    'models/decision_model_freeze.json': 'f3354cd51c58e2ad737b24e0362aedc97e3e2adb',
    'models/decision_source_surface_review_20261108_next_day_statistics.json': '8d0a7ef4ed9fd179419c7f49442a9499237e904f'}
# Exact reviewed disclosure/pin successor. Model and inference code are unchanged.
SUSPENSION_EVIDENCE_PATH = 'data/market/confirmed_suspensions.json'
MANIFEST_PATH = 'models/decision_model_freeze.json'
SUSPENSION_P0_CODE_PROFILE = {**NEXT_DAY_STATISTICS_P0_CODE_PROFILE,
    MANIFEST_PATH: 'e4e73b5d0754483b7c493fd2e4135402f4a47d94',
    SUSPENSION_EVIDENCE_PATH: '28b5b76b258ace91e6e44aacaf297ec13971adf1'}
SUSPENSION_TRANSITION_IDENTITY = {
    'signal_date': '20261110',
    'original_run_id': '42811412247',
    'original_head': '0702ff2caa6411cfe9a0477c70fffc1621061a8c',
    'original_cas': 'd96c73c14ed3f04e2238182a3acc3e369eea6899',
    'reuse_run_id': '42824431708',
    'deployed_head': '983bb8c2bb8572339ac0f659866f2a3ee6b0c208',
}
PRE_SUSPENSION_IMPORTER_SHA = 'b780901b9a0de408d8e99a850c2aaa6372d0043ca7d32b221117e18281786710'
PRE_SUSPENSION_CONTINUATION_PINS = {
    'work/profit_1000_upgrade/candidate_natural_p0_continuation.py': '1f63566c995e5d95a06dc9e07036c6ec7949cb228566e6faae1c013c50ab9a9b',
    'work/profit_1000_upgrade/candidate_p0_generation_artifact.py': '8bac12a1d68fe5a840a83bda50412b34a05cfaa57556230280307d4239f83bb0',
}
PRE_NEXT_DAY_IMPORTER_SHA = 'f416e399522f092402338b536bf58b583f9d420f44d194601e41da942353e154'
PRE_NEXT_DAY_CONTINUATION_PINS = {
    'work/profit_1000_upgrade/candidate_natural_p0_continuation.py': 'fdfd921f5b2fd8de09a4c76810c0d4042533c122a55c8b3830999176d6524b96',
    'work/profit_1000_upgrade/candidate_p0_generation_artifact.py': '8bac12a1d68fe5a840a83bda50412b34a05cfaa57556230280307d4239f83bb0',
}
PRE_RELIABILITY_IMPORT_PROFILE = {'importer_sha256': '514b001ce944cf1529757dc06f972eb313c8515f0fea02a55818433b414d8e46',
 'source_adapter_dependency_sha256': {'scripts/publish_primary_three_rank.py': 'bb90b81c93787fd5e2ababdd76adaff33f3dec2bf1fb4553c57723b7a8003ba8',
                                      'src/top10decision/decision/d_close_features.py': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e',
                                      'src/top10decision/decision/three_rank.py': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a',
                                      'work/profit_1000_upgrade/candidate_d_feature_projection.py': 'aabe73a52482626ab71b042cf4a7c4a1548e6131b7b231707d2d30d29348188f',
                                      'work/profit_1000_upgrade/candidate_live_feature_math.py': '922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68'},
 'source_adapter_sha256': 'eb95ce0f8fd1c7ceb354108aa70bc2c3806ddabc33122b9569c8d8b3bffd041b'}
PRE_RELIABILITY_CONTINUATION_PINS = {'work/profit_1000_upgrade/candidate_natural_p0_continuation.py': '6718bb26b269444fd0313b3854ce51f02d81b5fdf21ffd1523b00e2f3e0e12b2',
 'work/profit_1000_upgrade/candidate_p0_generation_artifact.py': '8bac12a1d68fe5a840a83bda50412b34a05cfaa57556230280307d4239f83bb0'}
RELIABILITY_P0_CODE_PROFILE = {'.github/workflows/run_primary_d_daily.yml': 'bbe106513b75e6ab9bb92dc273ad3efa2495a4a7',
 'models/decision_model_freeze.json': '7f952c9590147e6ee4b447a373e0b69849a2a668',
 'models/decision_three_engines/promotion.joblib': '41ab1b8771cc3f601da4afc5521eea31d15b6385',
 'models/decision_three_engines/validation_latest.json': '805eb6df569787b7f4fdc757cc464e57535cc0bf',
 'requirements-dev.lock': '892183b9e5c4ed2e1c1899589b8793f556b32136',
 'requirements.lock': '93ed4f921c4b9abe94c02e74a97b932a304d27d5',
 'scripts/candidate_runtime_preflight.py': 'dfa65d534b65a3b9f00d7dfff2f6c5c0fee281d7',
 'scripts/primary_failure_diagnostics.py': '8d69e8c3ae0f3bb20cfcd9386ed0821ef33b028d',
 'scripts/publish_primary_three_rank.py': '2a730786322ffa4d697804c9d17f5c55d3c5a0a8',
 'scripts/sync_market_raw.py': 'ede7b0e29dc67c5f7552f5b8c71a2788f144de83',
 'scripts/sync_pred_source.py': 'a5517d8271f0dc67c8034e16a1534a838f22fb06',
 'src/top10decision/auction_v3/__init__.py': '8608ad5871ac45206bfa471ebea90e799113fe82',
 'src/top10decision/auction_v3/calibration.py': 'abb71530b1b62889d924a06bfd490d82c0c4a2db',
 'src/top10decision/auction_v3/config.py': '714a8c922b458db06f0ff4b93cebf19b3c2bcbb8',
 'src/top10decision/auction_v3/engine.py': '191c42d51485c06406eebc4046994184b542b152',
 'src/top10decision/auction_v3/promotion_model.py': 'c4ea817f173e6058004b19522a1862d486eb755f',
 'src/top10decision/data/tushare_minute.py': 'dc27bff77ac11b9ea871220b8189aaf6725a9f81',
 'src/top10decision/decision/canonical_fingerprint.py': 'c313f51c1c0630565bcaacca3783680f6e03d785',
 'src/top10decision/decision/contracts.py': '34862a4dcdb3160754189e8301b49ab24c9dbf2e',
 'src/top10decision/decision/d_close_features.py': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7',
 'src/top10decision/decision/eligibility.py': '34ae803f7ea73cee861865ba49507dd81c0655c1',
 'src/top10decision/decision/exit_policy.py': 'a495782254ddf3f58aa52ee668fe441f2aef315e',
 'src/top10decision/decision/model_freeze.py': '38b190572b9afae3eaca2662e936e1e750e1d034',
 'src/top10decision/decision/observation.py': 'd3f848e30519938b8530e6da7890a761f6722260',
 'src/top10decision/decision/suspension_evidence_registry.py': 'ff88c1442dee7586adb14e6d00047408aeac53f8',
 'src/top10decision/decision/three_engine_models.py': '7a74a2fde21465a632db3a11c81954031ad550db',
 'src/top10decision/decision/three_rank.py': 'cf18b86656e71eff5a38e0e57d35494db172528f',
 'src/top10decision/decision/trade_selector.py': 'c9c8cac9e97a42b6d888df9b42cde11b532aef63',
 'src/top10decision/probability_calibration.py': 'a005f68b26465bee49de70c57824c7a56c32d61f',
 'src/top10decision/writers/io_contract.py': '28ded13148bcdd334da10767b1aed83e44c32e7c'}
# The published Top1 paper-account template and Pages workflow rotate the
# model manifest. All P0 execution, feature and model blobs keep their bindings.
TOP1_PAPER_P0_CODE_PROFILE = {**RELIABILITY_P0_CODE_PROFILE,
    MANIFEST_PATH: '3e9d5c0576977fdf85feada3ccca23b4bc92c524'}
TOP1_COUNT_P0_CODE_PROFILE = {**TOP1_PAPER_P0_CODE_PROFILE,
    MANIFEST_PATH: '8b53a922b9c7fad06eb2d38155f26a0cbf9b17a1'}
TOP1_RATE_P0_CODE_PROFILE = {**TOP1_COUNT_P0_CODE_PROFILE,
    MANIFEST_PATH: 'ad58a5c7197e700dcc406bed6ed93fc93b3adf54'}
RELIABILITY_PROFILES = (RELIABILITY_P0_CODE_PROFILE, TOP1_PAPER_P0_CODE_PROFILE, TOP1_COUNT_P0_CODE_PROFILE, TOP1_RATE_P0_CODE_PROFILE)
RELIABILITY_RUNTIME_CODE_BINDINGS = [{'path': 'scripts/primary_failure_diagnostics.py',
  'sha256': '4d89fe7325bbf52ba3296f390e2987ecba5b3e160514d0b55000a0a3e27d2ac8',
  'git_blob_sha1': '8d69e8c3ae0f3bb20cfcd9386ed0821ef33b028d',
  'git_mode': '100644'},
 {'path': 'scripts/publish_primary_three_rank.py',
  'sha256': '1ce33bbb5546f503c6bae28daf52000cb264237c7aa00237939b87330131cba0',
  'git_blob_sha1': '2a730786322ffa4d697804c9d17f5c55d3c5a0a8',
  'git_mode': '100644'},
 {'path': 'src/top10decision/auction_v3/__init__.py',
  'sha256': '1989253ea8106f0350ca08445ca41f623374c08c73d6621e23d34cf2860aded8',
  'git_blob_sha1': '8608ad5871ac45206bfa471ebea90e799113fe82',
  'git_mode': '100644'},
 {'path': 'src/top10decision/auction_v3/calibration.py',
  'sha256': '06890dee4d9c7a6cd007ebdda823b4907774ba372ebe37f3072432335fce79a9',
  'git_blob_sha1': 'abb71530b1b62889d924a06bfd490d82c0c4a2db',
  'git_mode': '100644'},
 {'path': 'src/top10decision/auction_v3/config.py',
  'sha256': '4c28a0ae2265435074a0689f4ad904ebfe465bd59fce3e8f558764d5014b94b2',
  'git_blob_sha1': '714a8c922b458db06f0ff4b93cebf19b3c2bcbb8',
  'git_mode': '100644'},
 {'path': 'src/top10decision/auction_v3/engine.py',
  'sha256': '68b39867e22761809c5636c1274013040e71555149193c0856ad44a51ec0d5d8',
  'git_blob_sha1': '191c42d51485c06406eebc4046994184b542b152',
  'git_mode': '100644'},
 {'path': 'src/top10decision/auction_v3/promotion_model.py',
  'sha256': 'af9941589e7eec766a3a855d9d8e384316852ea7f74c23122968961cc4cb2170',
  'git_blob_sha1': 'c4ea817f173e6058004b19522a1862d486eb755f',
  'git_mode': '100644'},
 {'path': 'src/top10decision/data/tushare_minute.py',
  'sha256': '30aa95e69040d3d61bd3f7ec9e9afe7bde909606e72c3fe5e687d0ef967af785',
  'git_blob_sha1': 'dc27bff77ac11b9ea871220b8189aaf6725a9f81',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/canonical_fingerprint.py',
  'sha256': '61024fb51f54eb1a90af59baf6330eee16dbe9678167e96fcf5940b51a4cc58b',
  'git_blob_sha1': 'c313f51c1c0630565bcaacca3783680f6e03d785',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/contracts.py',
  'sha256': '50e2cba257ee02804db8937a4a072572a56372afba941dfec9bfa719b812c917',
  'git_blob_sha1': '34862a4dcdb3160754189e8301b49ab24c9dbf2e',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/d_close_features.py',
  'sha256': 'c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e',
  'git_blob_sha1': 'caf8bb44beb93f2d78f9e24af706bbf0b1157ee7',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/eligibility.py',
  'sha256': '31692b832fb0a6d4e83a2880600e78d36e38086f593ee4641174dd07f808acb7',
  'git_blob_sha1': '34ae803f7ea73cee861865ba49507dd81c0655c1',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/exit_policy.py',
  'sha256': '4c68819862b69541d55624b1f8be6dce69c902d20d4e9420212415596dd4597f',
  'git_blob_sha1': 'a495782254ddf3f58aa52ee668fe441f2aef315e',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/observation.py',
  'sha256': '577fbb21166fd39456ff17a06d0a6fde0a860e531dbe38b66ea117c0dd12d713',
  'git_blob_sha1': 'd3f848e30519938b8530e6da7890a761f6722260',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/suspension_evidence_registry.py',
  'sha256': 'efeb7d53abf2cf7138c1853a45e1a8a5b82ded8ec28bda4f1700fcad1c7dffa9',
  'git_blob_sha1': 'ff88c1442dee7586adb14e6d00047408aeac53f8',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/three_engine_models.py',
  'sha256': '9a4a2405e3b95af9f1c05100aa8b97dc8b3ee62d63b4dda12e13f7f0fcd1de4c',
  'git_blob_sha1': '7a74a2fde21465a632db3a11c81954031ad550db',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/three_rank.py',
  'sha256': 'f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a',
  'git_blob_sha1': 'cf18b86656e71eff5a38e0e57d35494db172528f',
  'git_mode': '100644'},
 {'path': 'src/top10decision/decision/trade_selector.py',
  'sha256': '7934daec17dd50b5e702878fbe7e9ac35f894eff20daef0bedbe29712b182858',
  'git_blob_sha1': 'c9c8cac9e97a42b6d888df9b42cde11b532aef63',
  'git_mode': '100644'},
 {'path': 'src/top10decision/probability_calibration.py',
  'sha256': '058437b22042db5eed8572ff2b6e67fc5d2b6415bc7f89f846a5c230f983717c',
  'git_blob_sha1': 'a005f68b26465bee49de70c57824c7a56c32d61f',
  'git_mode': '100644'},
 {'path': 'src/top10decision/writers/io_contract.py',
  'sha256': '80bff133b8cd1519f0ef701338b0000587e22a6202212de80f0de5e6d91e0b97',
  'git_blob_sha1': '28ded13148bcdd334da10767b1aed83e44c32e7c',
  'git_mode': '100644'}]
MAX_ANCESTRY = 24
SKIPPED_STEPS = (
    "Resolve immutable upstream commits", "Snapshot protected secondary state",
    "Sync exact-D independent inputs", "Publish primary promotion list in the isolated scope",
    "Build exact P0 candidate patch", "Upload immutable P0 candidate",
)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def validate_code_profiles(gh, reads, base_tree, cas_tree, deployed_tree, identity):
    """Admit complete fixed profiles, plus one exact original/deployed pin repair."""
    manifest_blob = base_tree.get(MANIFEST_PATH, {}).get('sha')
    for profile in (*RELIABILITY_PROFILES, SUSPENSION_P0_CODE_PROFILE,
                    NEXT_DAY_STATISTICS_P0_CODE_PROFILE, DELIVERY_HARDENED_P0_CODE_PROFILE):
        if manifest_blob == profile[MANIFEST_PATH]:
            break
    else:
        publisher = 'scripts/publish_primary_three_rank.py'
        profile = (CONTROLLED_P0_CODE_PROFILE if base_tree.get(publisher, {}).get('sha')
                   == CONTROLLED_P0_CODE_PROFILE[publisher] else ORIGINAL_P0_CODE_PROFILE)
    if profile is TOP1_RATE_P0_CODE_PROFILE:
        require(gh.date(identity.get("signal_date")) >= "20261112", "RATE_PROFILE_ACTIVATION_DATE_REQUIRED")
    if profile in RELIABILITY_PROFILES:
        require(gh.date(identity.get("signal_date")) >= "20261111", "RELIABILITY_PROFILE_ACTIVATION_DATE_REQUIRED")
    deployed_profile = profile
    transition = None
    if (profile is NEXT_DAY_STATISTICS_P0_CODE_PROFILE
            and deployed_tree.get(MANIFEST_PATH, {}).get('sha') == SUSPENSION_P0_CODE_PROFILE[MANIFEST_PATH]):
        require(identity == SUSPENSION_TRANSITION_IDENTITY, 'SUSPENSION_PIN_TRANSITION_EXACT_IDENTITY_REQUIRED')
        old_pin = b'ad5499eaf517ce7ad6f2939baca9c8b6ee03ee42d623debdf85ce9095d679b68'
        new_pin = b'4f9a6341823d23283d25a02b62957671cabfa2e1aa77f6ea25b25d46cfa12158'
        old_raw = reads.blob(base_tree, MANIFEST_PATH)
        new_raw = reads.blob(deployed_tree, MANIFEST_PATH)
        require(gh.sha256(old_raw) == '94e4a4b3c71e1e66b369c44e4b6126ff6b0fb51142733b506c7ff187115f3435'
            and gh.sha256(new_raw) == '31ad138b076f8ebb2bb1bf508813670ebe4c84c61619501272129f369f32bea0'
            and old_raw.count(old_pin) == 1 and old_raw.replace(old_pin, new_pin) == new_raw
            and reads.blob(cas_tree, MANIFEST_PATH) == old_raw,
            'SUSPENSION_PIN_TRANSITION_ONLY_EXACT_REPLACEMENT_REQUIRED')
        for tree in (base_tree, cas_tree, deployed_tree):
            item = tree.get(SUSPENSION_EVIDENCE_PATH, {})
            require(item.get('type') == 'blob' and item.get('mode') == '100644'
                and item.get('sha') == SUSPENSION_P0_CODE_PROFILE[SUSPENSION_EVIDENCE_PATH]
                and gh.sha256(reads.blob(tree, SUSPENSION_EVIDENCE_PATH)) == new_pin.decode(),
                'SUSPENSION_PIN_TRANSITION_ORIGINAL_EVIDENCE_REQUIRED')
        deployed_profile = SUSPENSION_P0_CODE_PROFILE
        transition = {**identity, 'original_manifest_sha256': gh.sha256(old_raw),
            'deployed_manifest_sha256': gh.sha256(new_raw), 'evidence_sha256': new_pin.decode()}
    for tree, expected in ((base_tree, profile), (cas_tree, profile), (deployed_tree, deployed_profile)):
        for logical, expected_blob in expected.items():
            require(tree.get(logical, {}).get('type') == 'blob' and tree[logical].get('mode') == '100644'
                and tree[logical].get('sha') == expected_blob,
                'ORIGINAL_AND_REUSED_P0_REVIEWED_CODE_PROFILE_REQUIRED')
    return profile, transition


def _stamp(value):
    require(type(value) is str, "CONTINUATION_TIMESTAMP_REQUIRED")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "CONTINUATION_AWARE_TIMESTAMP_REQUIRED")
    return result.astimezone(timezone.utc)


def validate_original_run(gh, run, run_id, day):
    gh.exact_id(run_id); gh.date(day)
    require(type(run) is dict and type(run.get("id")) is int and run["id"] == int(run_id)
        and type(run.get("workflow_id")) is int and run["workflow_id"] == gh.WORKFLOW_ID
        and run.get("path") == gh.WORKFLOW_PATH and run.get("head_branch") == "main"
        and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("status") == "completed" and run.get("conclusion") == "failure"
        and run.get("event") in ("workflow_dispatch", "schedule")
        and run.get("repository", {}).get("full_name") == gh.REPOSITORY
        and run.get("head_repository", {}).get("full_name") == gh.REPOSITORY
        and run.get("name") == run.get("display_title") == (
            gh.WORKFLOW_NAME if run.get("event") == "schedule" else f"DC20 controlled daily NATURAL | D={day}"),
        "EXACT_FAILED_CONTROLLED_GENERATING_P0_REQUIRED")
    gh.exact_sha(run.get("head_sha"), 40)
    created, started, updated = [gh.utc(run.get(k)) for k in ("created_at", "run_started_at", "updated_at")]
    local = created.astimezone(ZoneInfo("Asia/Shanghai"))
    require(local.strftime("%Y%m%d") == day and (15, 0) <= (local.hour, local.minute) < (23, 30)
        and created <= started <= updated, "ORIGINAL_CONTROLLED_WINDOW_REQUIRED")
    return {k: run[k] for k in ("id", "workflow_id", "path", "head_branch", "head_sha", "run_attempt",
        "status", "conclusion", "event", "name", "display_title", "created_at", "run_started_at", "updated_at")}


def _jobs(gh, document, run, run_id, *, original):
    jobs = document.get("jobs")
    require(type(jobs) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(jobs) <= 100, "CONTINUATION_COMPLETE_JOBS_REQUIRED")
    found, ids = {}, set()
    for job in jobs:
        require(type(job) is dict and type(job.get("id")) is int and job["id"] > 0 and job["id"] not in ids
            and type(job.get("run_id")) is int and job["run_id"] == int(run_id)
            and type(job.get("name")) is str and job["name"] not in found, "CONTINUATION_JOB_IDENTITY_CHANGED")
        ids.add(job["id"]); found[job["name"]] = job
    require(set(gh.CRITICAL_JOBS) <= set(found), "CONTINUATION_CRITICAL_JOB_MISSING")
    compute, cas, deploy = [found[name] for name in gh.CRITICAL_JOBS]
    wanted = ("success", "success", "failure") if original else ("success", "skipped", "success")
    require(all(j.get("status") == "completed" and j.get("conclusion") == conclusion
        for j, conclusion in zip((compute, cas, deploy), wanted)), "CONTINUATION_EXACT_JOB_OUTCOMES_REQUIRED")
    require(all(j.get("status") == "completed" and j.get("conclusion") in ("success", "skipped")
        for name, j in found.items() if name != gh.CRITICAL_JOBS[2]), "CONTINUATION_ONLY_ORIGINAL_DEPLOY_MAY_FAIL")
    ordered = (compute, cas, deploy) if original else (compute, deploy)
    require(all(gh.utc(run["run_started_at"]) <= gh.utc(j.get("started_at")) <= gh.utc(j.get("completed_at"))
        <= gh.utc(run["updated_at"]) for j in ordered), "CONTINUATION_JOB_TIME_INVALID")
    require(all(gh.utc(ordered[i]["completed_at"]) <= gh.utc(ordered[i+1]["started_at"])
        for i in range(len(ordered)-1)), "CONTINUATION_JOB_SEQUENCE_INVALID")
    states, numbers = {}, set()
    require(type(compute.get("steps")) is list, "CONTINUATION_COMPLETE_STEPS_REQUIRED")
    for step in compute["steps"]:
        require(type(step) is dict and type(step.get("number")) is int and step["number"] > 0
            and step["number"] not in numbers and type(step.get("name")) is str and step["name"] not in states
            and step.get("status") == "completed", "CONTINUATION_STEP_IDENTITY_CHANGED")
        numbers.add(step["number"]); states[step["name"]] = step.get("conclusion")
    require(states.get("Resolve exact D and reject a completed duplicate") == "success"
        and all(states.get(name) == ("success" if original else "skipped") for name in SKIPPED_STEPS),
        "CONTINUATION_GENERATION_OR_DUPLICATE_STEPS_INVALID")
    return [{k: j.get(k) for k in ("id", "run_id", "name", "status", "conclusion", "started_at", "completed_at")}
        for j in (compute, cas, deploy)]


def select_generation_artifact(gh, document, run, run_id):
    entries = document.get("artifacts")
    require(type(entries) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(entries) <= 100 and all(type(x) is dict for x in entries),
        "CONTINUATION_COMPLETE_ARTIFACT_LIST_REQUIRED")
    selected = [x for x in entries if x.get("name") == f"primary-d-candidate-{run_id}-1"]
    require(len(selected) == 1, "UNIQUE_ORIGINAL_GENERATION_ARTIFACT_REQUIRED")
    item = selected[0]; origin = item.get("workflow_run", {})
    require(type(item.get("id")) is int and item["id"] > 0 and item.get("expired") is False
        and type(item.get("size_in_bytes")) is int and 0 < item["size_in_bytes"] <= gh.MAX_ZIP_BYTES
        and type(origin.get("id")) is int and origin["id"] == int(run_id)
        and origin.get("head_branch") == "main" and origin.get("head_sha") == run["head_sha"],
        "ORIGINAL_GENERATION_ARTIFACT_ORIGIN_CHANGED")
    require(type(item.get("digest")) is str and item["digest"].startswith("sha256:"), "GENERATION_EXTERNAL_DIGEST_REQUIRED")
    gh.exact_sha(item["digest"][7:])
    require(gh.utc(run["run_started_at"]) <= gh.utc(item.get("created_at")) <= gh.utc(item.get("updated_at"))
        <= gh.utc(run["updated_at"]), "GENERATION_ARTIFACT_TIME_INVALID")
    return {k: item[k] for k in ("id", "name", "size_in_bytes", "digest", "expired", "created_at", "updated_at", "workflow_run")}


class Reads:
    def __init__(self, gh, client):
        self.gh, self.client, self.cost = gh, client, 0
        self.registry_allowance = 0
        self.bound, self.trees, self.blobs = {}, {}, {}
        if type(client) is gh.GitHubReadClient:
            client.enable_continuation_reads()

    def json(self, suffix, *, refresh=False):
        gh = self.gh
        self.cost += 1; require(self.cost <= MAX_READS + self.registry_allowance, "CONTINUATION_READ_BUDGET_EXCEEDED")
        value = self.client.get_json(gh.API_PREFIX + suffix)
        require(type(value) is dict, "CONTINUATION_JSON_REQUIRED")
        if not refresh:
            raw = gh.json_bytes(value)
            require(suffix not in self.bound or self.bound[suffix] == raw, "CONTINUATION_API_EVIDENCE_CHANGED")
            self.bound[suffix] = raw
        return value

    def tree(self, commit_sha):
        gh = self.gh; gh.exact_sha(commit_sha, 40)
        if commit_sha not in self.trees:
            commit = self.json("/git/commits/" + commit_sha)
            require(commit.get("sha") == commit_sha and type(commit.get("tree")) is dict,
                "CONTINUATION_EXACT_COMMIT_REQUIRED")
            tree_sha = gh.exact_sha(commit["tree"].get("sha"), 40)
            self.trees[commit_sha] = commit, gh.validate_tree(self.json("/git/trees/" + tree_sha + "?recursive=1"), tree_sha)
        return self.trees[commit_sha]

    def blob(self, tree, path):
        gh = self.gh; item = tree.get(path, {})
        require(item.get("type") == "blob" and item.get("mode") == "100644", "CONTINUATION_EXACT_REGULAR_BLOB_REQUIRED")
        key = gh.exact_sha(item.get("sha"), 40)
        if key not in self.blobs:
            outer = self
            class Client:
                def get_json(self, route): return outer.json(route.removeprefix(gh.API_PREFIX))
            self.blobs[key] = gh.load_blob(Client(), tree, path)
        return self.blobs[key]

    def archive(self, artifact):
        self.cost += 2; require(self.cost <= MAX_READS + self.registry_allowance, "CONTINUATION_READ_BUDGET_EXCEEDED")
        raw = self.client.get_artifact_zip(artifact["id"])
        require(type(raw) is bytes and len(raw) == artifact["size_in_bytes"]
            and self.gh.sha256(raw) == artifact["digest"][7:], "CONTINUATION_ARTIFACT_DIGEST_CHANGED")
        return raw

    def ancestry(self, ancestor, descendant):
        current, chain = descendant, []
        for _ in range(MAX_ANCESTRY):
            if current == ancestor: return chain + [ancestor]
            commit, _ = self.tree(current)
            parents = commit.get("parents")
            require(type(parents) is list and len(parents) == 1, "CONTINUATION_SINGLE_PARENT_ANCESTRY_REQUIRED")
            chain.append(current); current = self.gh.exact_sha(parents[0].get("sha"), 40)
            require(current not in chain, "CONTINUATION_ANCESTRY_CYCLE")
        require(False, "CONTINUATION_ANCESTRY_BOUND_EXCEEDED")


def validate_reuse_event(run, day):
    """Accept natural events or the exact reviewed controlled daily entry only.

    The pinned P0 workflow emits this title only after all daily confirmations.
    Its successful duplicate-resolution step compares the immutable upstream
    commits to the existing receipt; _jobs separately requires generation/CAS
    to be skipped. Ordinary manual runs and recovery never qualify.
    """
    if run.get("event") in ("schedule", "workflow_run"):
        return
    title = f"DC20 controlled daily NATURAL | D={day}"
    require(run.get("event") == "workflow_dispatch"
        and run.get("name") == run.get("display_title") == title
        and run.get("run_attempt") == 1,
        "CONTINUATION_EXACT_CONTROLLED_REUSE_REQUIRED")
    created = _stamp(run.get("created_at")).astimezone(ZoneInfo("Asia/Shanghai"))
    require(created.strftime("%Y%m%d") == day
        and (15, 0) <= (created.hour, created.minute) < (23, 30),
        "CONTINUATION_CONTROLLED_REUSE_WINDOW_REQUIRED")


def verify_pair(gh, client, *, original_run_id, original_cas_commit, reuse_run_id,
                original_sources=None, source_main_sha=None, require_absent=False,
                require_same_main=False, now=None, cached_reuse_revision=None):
    """Verify two real runs and one immutable generating commit, without writing."""
    original_run_id, reuse_run_id = gh.exact_id(original_run_id), gh.exact_id(reuse_run_id)
    require(original_run_id != reuse_run_id, "TWO_DISTINCT_P0_RUNS_REQUIRED")
    gh.exact_sha(original_cas_commit, 40)
    reads = Reads(gh, client)
    original_path, reuse_path = ("/actions/runs/" + x for x in (original_run_id, reuse_run_id))
    r0, r1 = reads.json(original_path), reads.json(reuse_path)
    redeployed = gh.validate_run(r1, reuse_run_id)
    r1_jobs = _jobs(gh, reads.json(reuse_path + "/attempts/1/jobs?per_page=100"), r1, reuse_run_id, original=False)
    pages = gh.select_artifact(reads.json(reuse_path + "/artifacts?per_page=100"), r1, reuse_run_id)
    require(gh.utc(r1_jobs[2]["started_at"]) <= gh.utc(pages["created_at"]) <= gh.utc(pages["updated_at"])
        <= gh.utc(r1_jobs[2]["completed_at"]), "REUSE_PAGES_ARTIFACT_MUST_BELONG_TO_DEPLOY_JOB")
    if cached_reuse_revision is None:
        revision, revision_sha = gh.revision_from_zip(reads.archive(pages))
    else:
        require(type(client) is not gh.GitHubReadClient and getattr(client, "continuation_replay", False) is True and type(cached_reuse_revision) is tuple
            and len(cached_reuse_revision) == 2, "CACHED_REVISION_ONLY_FOR_EXPLICIT_REPLAY")
        revision, revision_sha = cached_reuse_revision
        gh.exact_sha(revision_sha)
    day, trade, exit_day, deployed_sha = gh.validate_revision(revision, r1, reuse_run_id)
    validate_reuse_event(r1, day)
    require(deployed_sha == r1["head_sha"], "REUSE_PAGES_MUST_PUBLISH_ITS_EXACT_HEAD")
    generated = validate_original_run(gh, r0, original_run_id, day)
    r0_jobs = _jobs(gh, reads.json(original_path + "/attempts/1/jobs?per_page=100"), r0, original_run_id, original=True)
    original_close = datetime.strptime(day + "233000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    require(gh.utc(r0_jobs[1]["completed_at"]) < original_close, "ORIGINAL_CAS_MUST_COMPLETE_BEFORE_D2330")
    require(gh.utc(r0_jobs[1]["completed_at"]) < gh.utc(r1["created_at"]), "REDEPLOY_MUST_POSTDATE_ORIGINAL_CAS")
    artifact = select_generation_artifact(gh, reads.json(original_path + "/artifacts?per_page=100"), r0, original_run_id)
    require(gh.utc(r0_jobs[0]["started_at"]) <= gh.utc(artifact["created_at"]) <= gh.utc(artifact["updated_at"])
        <= gh.utc(r0_jobs[0]["completed_at"]) <= gh.utc(r0_jobs[1]["started_at"]), "GENERATION_ARTIFACT_MUST_PRECEDE_CAS")
    candidate_zip = reads.archive(artifact)
    cas, cas_tree = reads.tree(original_cas_commit)
    base, base_tree = reads.tree(r0["head_sha"])
    require(gh.utc(r0_jobs[1]["started_at"]) <= gh.utc(cas.get("committer", {}).get("date"))
        <= gh.utc(r0_jobs[1]["completed_at"]), "ORIGINAL_CAS_COMMIT_MUST_BELONG_TO_SUCCESSFUL_CAS_JOB")
    paths = gh.adapter._p0_paths(day)
    sources = ({p: reads.blob(cas_tree, p) for p in paths.values()} if original_sources is None else dict(original_sources))
    require(all(p in sources for p in paths.values()), "ORIGINAL_FOUR_FILES_REQUIRED")
    receipt, contract = (gh.parse_json(sources[paths[k]]) for k in ("receipt", "three_rank_json"))
    require(receipt.get("generation_mode") == "NATURAL" and receipt.get("primary_status") == "READY"
        and receipt.get("prospective") is True and receipt.get("forward_eligible") is True
        and all(receipt.get(k) is False for k in ("not_forward_generated", "future_market_data_consumed", "latest_fallback_used",
            "action_authorized", "action_input_consumed")) and type(receipt.get("formal_trade_count")) is int
        and receipt["formal_trade_count"] == 0, "ORIGINAL_NATURAL_RECEIPT_BOUNDARY_REQUIRED")
    require([receipt.get(k) for k in ("signal_date", "exec_date", "exit_date")] == [day, trade, exit_day]
        == [contract.get(k) for k in ("signal_date", "exec_date", "exit_date")]
        and contract.get("bundle_sha256") == revision["primary_d_bundle_sha256"]
        and contract.get("top10_count") == revision["primary_d_top10_count"], "CONTINUATION_ORIGINAL_D_BUNDLE_CHANGED")
    require(receipt["inputs"]["git_head"] == r0["head_sha"]
        and [p.get("sha") for p in cas.get("parents", [])] == [r0["head_sha"]], "ORIGINAL_CAS_SINGLE_PARENT_CHANGED")
    registered, daily, _, _ = gh.adapter._registration(receipt, contract, day)
    registry_bindings = [binding for item in receipt["inputs"]["runtime_consumed_market_files"]
        if item.get("table") == "confirmed_suspensions" and "registry" in item
        for binding in gh.adapter.suspension_registry.validate_declaration(item["registry"]).values()]
    reads.registry_allowance = len(registry_bindings)
    if type(client) is gh.GitHubReadClient:
        client.enable_registry_reads(receipt, contract, day)
    for binding in registry_bindings:
        require(all(tree.get(binding["path"], {}).get("type") == "blob"
            and tree[binding["path"]].get("mode") == "100644"
            and tree[binding["path"]].get("sha") == binding["git_blob_sha1"]
            for tree in (base_tree, cas_tree)), "REGISTRY_NOT_IN_ORIGINAL_P0_BASE")
    if original_sources is None:
        for p in registered: sources[p] = reads.blob(cas_tree, p)
    count = gh.validate_original_source_inventory(receipt, contract, sources, day)
    for item in daily:
        if item["trade_date"] != day:
            require(cas_tree[item["path"]]["sha"] == item["git_blob_sha1"], "RECEIPT_HISTORICAL_GIT_BLOB_MISMATCH")
    calendar = receipt["inputs"]["calendar"]
    require(calendar.get("git_mode") == "100644" and calendar.get("git_blob_sha1") == cas_tree[gh.adapter.CALENDAR_PATH]["sha"],
        "RECEIPT_CALENDAR_GIT_BLOB_MISMATCH")
    for p, raw in sources.items():
        require(reads.blob(cas_tree, p) == raw, "CONTINUATION_ORIGINAL_API_SOURCE_BYTES_CHANGED")
    artifact_guard = gh.continuation_dependencies()[1]
    generation = artifact_guard.validate_generation_archive(candidate_zip, artifact, r0, receipt, cas, base_tree, cas_tree, reads.blob)
    upstream_commits = []
    for repository, field in (("a-top10", "pred_commit"), ("a-share-top3-data", "market_commit")):
        upstream_sha = (generation["resolved_schedule_upstreams"] if r0["event"] == "schedule"
            else generation["target_envelope"])[field]
        route = "/repos/njedu2023-prog/" + repository + "/git/commits/" + upstream_sha
        commit_doc = client.get_json(route)
        require(type(commit_doc) is dict and commit_doc.get("sha") == upstream_sha
            and type(commit_doc.get("committer")) is dict
            and gh.utc(commit_doc["committer"].get("date")) <= gh.utc(r0["created_at"]),
            "ORIGINAL_UPSTREAM_COMMIT_MUST_PREDATE_DISPATCH")
        upstream_commits.append({"repository": "njedu2023-prog/" + repository, "sha": upstream_sha,
            "committed_at": commit_doc["committer"]["date"]})
    _, deployed_tree = reads.tree(deployed_sha)
    code_profile, manifest_transition = validate_code_profiles(gh, reads, base_tree, cas_tree, deployed_tree,
        {'signal_date': day, 'original_run_id': original_run_id, 'original_head': r0['head_sha'],
         'original_cas': original_cas_commit, 'reuse_run_id': reuse_run_id, 'deployed_head': deployed_sha})
    runtime_bindings = (RELIABILITY_RUNTIME_CODE_BINDINGS if code_profile in RELIABILITY_PROFILES else CONTROLLED_RUNTIME_CODE_BINDINGS
        if code_profile is not ORIGINAL_P0_CODE_PROFILE else ORIGINAL_RUNTIME_CODE_BINDINGS)
    static_inputs = receipt["inputs"].get("committed_static_inputs", {})
    require(static_inputs.get("git_head") == r0["head_sha"]
        and static_inputs.get("runtime_code") == runtime_bindings,
        "ORIGINAL_RUNTIME_RECEIPT_CODE_BINDINGS_CHANGED")
    require(receipt["inputs"].get("promotion_model") == ORIGINAL_PROMOTION_MODEL
        and gh.sha256(reads.blob(base_tree, "models/decision_three_engines/promotion.joblib"))
            == ORIGINAL_PROMOTION_MODEL["artifact_sha256"], "ORIGINAL_PROMOTION_MODEL_PROFILE_CHANGED")
    redeploy_ancestry = reads.ancestry(original_cas_commit, deployed_sha)
    main_ref = reads.json("/git/ref/heads/main")
    require(main_ref.get("ref") == "refs/heads/main" and main_ref.get("object", {}).get("type") == "commit",
        "CONTINUATION_EXACT_MAIN_REF_REQUIRED")
    current_sha = gh.exact_sha(main_ref["object"].get("sha"), 40)
    source_main_sha = current_sha if source_main_sha is None else gh.exact_sha(source_main_sha, 40)
    require(not require_same_main or current_sha == source_main_sha, "CONTINUATION_MAIN_CHANGED_BEFORE_CAS")
    _, source_tree = reads.tree(source_main_sha)
    _, current_tree = reads.tree(current_sha)
    source_ancestry = reads.ancestry(original_cas_commit, source_main_sha)
    reads.ancestry(source_main_sha, current_sha)
    bindings = []
    for p, raw in sorted(sources.items()):
        expected = {"type": "blob", "mode": "100644", "sha": gh.git_blob(raw)}
        for tree in (cas_tree, deployed_tree, source_tree, current_tree):
            require(all(tree.get(p, {}).get(k) == v for k, v in expected.items()), "CONTINUATION_ORIGINAL_SOURCE_BYTES_CHANGED")
        bindings.append({"path": p, "sha256": gh.sha256(raw), "git_blob_sha1": gh.git_blob(raw), "git_mode": "100644", "bytes": len(raw)})
    if require_absent:
        names = [PREFIX + kind + "_" + day + ".json" for kind in ("day", "p0_sources", "local_freeze", "workflow")]
        require(not any(p in current_tree for p in names), "CONTINUATION_REQUIRES_FOUR_ABSENT_CANDIDATE_FILES")
    if now is not None:
        require(now.tzinfo is not None, "CONTINUATION_HOST_UTC_CLOCK_REQUIRED")
        close = datetime.strptime(day + "150000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        deadline = datetime.strptime(trade + "091500", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        require(close <= now < deadline, "CONTINUATION_NEW_FREEZE_T0915_DEADLINE")
    for path in (original_path, reuse_path):
        for suffix in ("", "/attempts/1/jobs?per_page=100", "/artifacts?per_page=100"):
            reads.json(path + suffix)
    require(reads.json("/git/ref/heads/main") == main_ref, "CONTINUATION_MAIN_CHANGED_DURING_READ")
    proof = {"schema_version": SCHEMA, "signal_date": day, "exec_date": trade, "exit_date": exit_day,
        "original_generation_run": generated, "original_generation_jobs": r0_jobs,
        "original_generation_artifact": artifact, "original_generation_artifact_binding": generation,
        "original_upstream_commits": upstream_commits,
        "original_p0_code_profile": dict(code_profile), "original_promotion_model": dict(ORIGINAL_PROMOTION_MODEL),
        "original_runtime_code_bindings": runtime_bindings,
        "original_cas_commit": original_cas_commit, "original_cas_tree": cas["tree"]["sha"],
        "original_cas_parent": r0["head_sha"], "successful_reuse_run": redeployed, "successful_reuse_jobs": r1_jobs,
        "successful_reuse_pages_artifact": pages, "successful_publication_git_sha": deployed_sha,
        "successful_reuse_revision_sha256": revision_sha, "source_main_sha": source_main_sha,
        "original_cas_to_redeploy_ancestry": redeploy_ancestry, "original_cas_to_source_main_ancestry": source_ancestry,
        "source_file_bindings": bindings, "original_file_count": count,
        "expected_p0_sha256": {k: gh.sha256(sources[p]) for k, p in paths.items()},
        "observed_p0_cas_job_completed_at": r0_jobs[1]["completed_at"],
        "source_generation_and_republication_are_distinct": True,
        "source_authority_issued": False, "new_prediction_freeze_verified": False, "production_activation_allowed": False}
    if manifest_transition is not None:
        proof["reviewed_manifest_transition"] = manifest_transition
    return proof, sources, revision


def validate_import_receipt(gh, imported):
    proof = imported.get("continuation")
    require(type(proof) is dict and imported.get("schema_version") == IMPORT_SCHEMA
        and proof.get("schema_version") == SCHEMA, "TYPED_CONTINUATION_RECEIPT_REQUIRED")
    pairs = {"signal_date": "signal_date", "exec_date": "exec_date", "exit_date": "exit_date",
        "published_git_sha": "original_cas_commit", "published_tree_sha": "original_cas_tree",
        "published_parent_sha": "original_cas_parent", "run": "original_generation_run",
        "critical_jobs": "original_generation_jobs", "pages_artifact": "successful_reuse_pages_artifact",
        "pages_revision_sha256": "successful_reuse_revision_sha256",
        "successful_publication_git_sha": "successful_publication_git_sha",
        "expected_p0_sha256": "expected_p0_sha256", "source_file_bindings": "source_file_bindings",
        "original_file_count": "original_file_count", "observed_p0_cas_job_completed_at": "observed_p0_cas_job_completed_at"}
    prior_reliability = imported.get("importer_sha256") == PRE_RELIABILITY_IMPORT_PROFILE["importer_sha256"]
    if prior_reliability:
        require(gh.date(imported.get("signal_date")) <= "20261110", "PRE_RELIABILITY_CONTINUATION_IMPORT_DATE_CHANGED")
    prior_suspension = imported.get("importer_sha256") == PRE_SUSPENSION_IMPORTER_SHA
    if prior_suspension:
        require(gh.date(imported.get("signal_date")) <= "20261109"
            and "reviewed_manifest_transition" not in proof, "PRE_SUSPENSION_CONTINUATION_IMPORT_DATE_CHANGED")
    previous = imported.get("importer_sha256") == PRE_NEXT_DAY_IMPORTER_SHA
    if previous:
        require(gh.date(imported.get("signal_date")) <= "20261106", "PRE_NEXT_DAY_CONTINUATION_IMPORT_DATE_CHANGED")
    require(all(imported.get(k) == proof.get(v) for k, v in pairs.items())
        and imported.get("continuation_dependency_sha256") == (PRE_RELIABILITY_CONTINUATION_PINS if prior_reliability else PRE_NEXT_DAY_CONTINUATION_PINS if previous else PRE_SUSPENSION_CONTINUATION_PINS if prior_suspension else gh.CONTINUATION_PINS)
        and imported.get("importer_sha256") == (PRE_RELIABILITY_IMPORT_PROFILE["importer_sha256"] if prior_reliability else PRE_NEXT_DAY_IMPORTER_SHA if previous else PRE_SUSPENSION_IMPORTER_SHA if prior_suspension else gh.SELF_SHA)
        and imported.get("source_adapter_sha256") == (PRE_RELIABILITY_IMPORT_PROFILE["source_adapter_sha256"] if (prior_reliability or prior_suspension or previous) else gh.ADAPTER_SHA)
        and imported.get("source_adapter_dependency_sha256") == (PRE_RELIABILITY_IMPORT_PROFILE["source_adapter_dependency_sha256"] if (prior_reliability or prior_suspension or previous) else gh.adapter.CODE_PINS),
        "CONTINUATION_IMPORT_PROOF_OR_CURRENT_CODE_MISMATCH")
    require(proof["original_generation_run"]["id"] != proof["successful_reuse_run"]["id"]
        and proof["original_generation_run"]["conclusion"] == "failure"
        and proof["successful_reuse_run"]["conclusion"] == "success"
        and proof["source_generation_and_republication_are_distinct"] is True,
        "CONTINUATION_RUN_IDENTITIES_MUST_REMAIN_DISTINCT")
    return proof

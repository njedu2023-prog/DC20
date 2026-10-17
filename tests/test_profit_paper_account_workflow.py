"""The paper account is part of the shared main/settlement publication, not a cron."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_account_generation_is_inside_the_exact_head_main_pages_artifact():
    workflow = (ROOT / ".github/workflows/deploy_dc20_pages.yml").read_text()
    ordered = [
        "Verify exact reusable-writer deployment revision",
        "Build independent compact statistics window",
        "Bind active candidate ranking and independent versioned summary",
        "Generate and bind simulated account with the main publication",
        "uses: actions/upload-pages-artifact@",
        "name: Deploy DC2.0 Pages",
        "Verify public simulated account and publication binding",
    ]
    positions = [workflow.index(label) for label in ordered]
    assert positions == sorted(positions)
    assert 'node-version: "24.19.0"' in workflow
    assert "node scripts/build_profit_paper_account.cjs --site-root _site --source-html decision.html" in workflow
    assert '--site-root _site --verify-public "$PAGE_URL"' in workflow
    assert "tests/test_profit_paper_account_build.cjs" in workflow
    assert "      - scripts/build_profit_paper_account.cjs" in workflow
    assert "      - tests/test_profit_paper_account*.cjs" in workflow


def test_daily_and_settlement_publications_keep_using_shared_deployment():
    main = (ROOT / ".github/workflows/run_primary_d_daily.yml").read_text()
    settlement = (ROOT / ".github/workflows/publish_candidate_profit.yml").read_text()
    assert "deploy-primary-pages-canonical:" in main
    assert "uses: ./.github/workflows/deploy_dc20_pages.yml" in main
    assert "DC20 · Settle natural candidate research slots" in settlement
    assert "uses: ./.github/workflows/deploy_dc20_pages.yml" in settlement


def test_account_frontend_consumes_published_artifact_not_browser_replay():
    html = (ROOT / "decision.html").read_text()
    refresh = html.split("async function refreshProfitPaperAccount(", 1)[1].split("function paperAccountMoney", 1)[0]
    assert "loadPublishedProfitPaperAccount" in refresh
    assert "buildProfitPaperAccount(" not in refresh
    assert "loadProfitPaperAccountInputs(" not in refresh
    assert "主任务及结算发布时自动重算" in html
    assert "已打开的页面需刷新读取新版" in html

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/supplier-engine-dry-run.yml"
RUN_CONTROLLER = ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportRun.controller.php"
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy-megasuppliers.yml"
BUILDER = ROOT / "scripts/build_megasuppliers_package.py"


def test_webasyst_dispatch_inputs_are_declared_by_workflow():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    controller = RUN_CONTROLLER.read_text(encoding="utf-8")
    dispatched = {
        "mode",
        "supplier_id",
        "request_id",
        "config_b64",
        "callback_url",
        "bridge_url",
        "webasyst_base_url",
        "source_secret",
        "previous_count",
        "approved_source_sha256",
    }
    for key in dispatched:
        assert "'%s' =>" % key in controller
        assert "      %s:" % key in workflow
    assert "      config:" in workflow


def test_workflow_has_runtime_dependencies_and_apply_arguments():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "pip install openpyxl requests" in workflow
    for arg in ("--mode", "--supplier-id", "--request-id", "--approved-source-sha256"):
        assert arg in workflow
    assert "WEBASYST_API_TOKEN: ${{ secrets.WEBASYST_API_TOKEN }}" in workflow
    assert "MEGASUPPLIERS_CALLBACK_SECRET: ${{ secrets.MEGASUPPLIERS_CALLBACK_SECRET }}" in workflow


def test_source_secret_namespace_is_restricted():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "MEGASUPPLIERS_SOURCE_" in workflow
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    assert "MEGASUPPLIERS_SOURCE_" in controller


def test_package_builder_contains_signed_public_routes():
    builder = BUILDER.read_text(encoding="utf-8")
    assert "megasuppliers-callback/" in builder
    assert "megasuppliers-bridge/" in builder
    assert 'VERSION = "1.1.0"' in builder


def test_production_deploy_is_manual_and_confirmation_gated():
    deploy = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in deploy
    assert "confirm_production" in deploy
    assert "inputs.confirm_production == 'DEPLOY'" in deploy
    assert "branches: [main]" not in deploy
    assert "schedule:" not in deploy

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/supplier-engine-dry-run.yml"
RUN_CONTROLLER = ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportRun.controller.php"
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy-megasuppliers.yml"
BUILDER = ROOT / "scripts/build_megasuppliers_package.py"
DEPLOY_SCRIPT = ROOT / "scripts/deploy_megasuppliers.py"


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


def test_apply_requires_active_profile_and_signed_callback_is_bound():
    run_controller = RUN_CONTROLLER.read_text(encoding="utf-8")
    callback = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendImportCallback.controller.php").read_text(encoding="utf-8")
    assert "PROFILE_DISABLED" in run_controller
    assert "mode_mismatch" in callback
    assert "config_mismatch" in callback


def test_autoimport_ui_exposes_guarded_apply_and_feature_codes_are_consistent():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    example = (ROOT / "supplier_engine/supplier.example.json").read_text(encoding="utf-8")
    assert "s-ms-apply" in panel
    assert "enable_writes" in panel
    assert "feature_codes" in controller
    assert "feature_codes" in example
    assert "feature_ids" not in example


def test_deployer_uses_the_same_release_builder_as_ci():
    deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "scripts/build_megasuppliers_package.py" in deploy_script
    assert "release_builder=yes" in deploy_script


def test_legacy_import_cannot_create_untyped_products():
    backend = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImport.controller.php").read_text(encoding="utf-8")
    api = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendApi.controller.php").read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")
    assert "create_missing' => false" in backend
    assert "CREATE_MISSING_REQUIRES_PROFILE" in api
    assert "creation new" not in builder.lower()
    assert "legacy_create" in builder

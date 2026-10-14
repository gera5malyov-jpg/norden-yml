from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/supplier-engine-dry-run.yml"
RUN_CONTROLLER = ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportRun.controller.php"
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy-megasuppliers.yml"
BUILDER = ROOT / "scripts/build_megasuppliers_package.py"
DEPLOY_SCRIPT = ROOT / "scripts/deploy_megasuppliers.py"
META_MODEL = ROOT / "webasyst/megasuppliers/lib/models/shopMegasuppliersMeta.model.php"


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
        "approved_snapshot_request_id",
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


def test_meta_model_uses_name_as_primary_key():
    model = META_MODEL.read_text(encoding="utf-8")
    assert "protected $table = 'shop_megasuppliers_meta';" in model
    assert "protected $id = 'name';" in model


def test_deployer_disables_legacy_create_missing_and_resets_report():
    deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "'create_missing' => false" in deploy_script
    assert 'REPORT.write_text("", encoding="utf-8")' in deploy_script


def test_supplier_run_prevents_concurrent_dispatch_and_ui_follows_current_run():
    run_controller = RUN_CONTROLLER.read_text(encoding="utf-8")
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    assert "RUN_ALREADY_ACTIVE" in run_controller
    assert "active_timeout = 350 * 60" in run_controller
    assert "run.request_id!==currentRequest" in panel
    assert "resumeCurrent" in panel
    assert "setRunBusy" in panel
    assert "обновить '+plan.update" in panel
    assert "создать '+plan.create" in panel
    assert "обнулить '+plan.zero_stock" in panel
    assert "пропустить '+plan.skipped" in panel


def test_dispatch_5xx_is_reconciled_by_request_id_without_false_failure():
    controller = RUN_CONTROLLER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    assert "findDispatchRun" in controller
    assert "dispatch_recovered" in controller
    assert "dispatch_uncertain" in controller
    assert "run-name: Supplier ${{ inputs.supplier_id || 'ci' }} ${{ inputs.request_id || github.run_id }}" in workflow
    assert "Запуск GitHub подтверждён" in panel
    assert "GitHub вернул временную ошибку" in panel

def test_autoimport_ui_is_compact_and_hides_legacy_admin_blocks():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    assert "Обновление прайсов поставщиков" in panel
    assert "s-ms-advanced" in panel
    assert "s-ms-suppliers" in panel
    assert "tidyLegacyUi" in panel
    assert "Проверить прайс" in panel
    assert "Применить" in panel
    assert "window.jQuery" not in panel
    assert "$(" not in panel


def test_stock_selector_uses_webasyst_names():
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    assert "shopStockModel()" in controller
    assert "'stocks' => $this->stockOptions()" in controller
    assert '<select name="stock_id">' in panel
    assert "function setStockOptions(" in panel
    assert "d.stocks||[]" in panel


def test_type_selector_uses_webasyst_names_and_legacy_sidebar_collapses():
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    sidebar = (ROOT / "webasyst/megasuppliers/patches/import_panel_method.txt").read_text(encoding="utf-8")
    assert "shopTypeModel()" in controller
    assert "'types' => $this->typeOptions()" in controller
    assert '<select name="type_id">' in panel
    assert "function setTypeOptions(" in panel
    assert "d.types||[]" in panel
    assert 'class="s-ms-sidebar-body" style="display:none"' in sidebar
    assert "s-ms-sidebar-toggle" in sidebar


def test_cancelled_run_status_controller_is_packaged():
    build = (ROOT / "scripts/build_megasuppliers_package.py").read_text(encoding="utf-8")
    controller = ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportCancel.controller.php"
    assert controller.exists()
    assert "shopMegasuppliersPluginBackendImportCancel.controller.php" in build



def test_supplier_profile_exposes_article_mode_selector():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    assert 'name="sku_mode"' in panel
    assert 'value="numeric"' in panel
    assert 'value="supplier"' in panel
    assert "Автоматический цифровой" in panel
    assert "Артикул поставщика" in panel
    assert "'sku_mode' => $sku_mode" in controller
    assert "array('numeric', 'supplier')" in controller



def test_product_supplier_fallback_has_legacy_type_map():
    helpers = (ROOT / "webasyst/megasuppliers/patches/plugin_helpers.txt").read_text(encoding="utf-8")
    builder = (ROOT / "scripts/build_megasuppliers_package.py").read_text(encoding="utf-8")
    assert "private function legacySupplierTypeMap()" in helpers
    assert "'NORDEN' => array(142)" in helpers
    assert "$this->legacySupplierTypeMap()" in helpers
    assert "patches/plugin_helpers.txt" in builder



def test_supplier_profile_can_zero_non_target_stocks():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    assert 'name="zero_other_stocks"' in panel
    assert "На остальных складах Webasyst ставить 0" in panel
    assert "'zero_other_stocks' =>" in controller
    assert "'stock_ids' => $this->stockIds()" in controller
    assert "private function stockIds()" in controller



def test_signed_bridge_can_create_supplier_characteristics():
    bridge = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendBridge.controller.php").read_text(encoding="utf-8")
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    assert "$action === 'ensure_features'" in bridge
    assert "shopFeatureModel" in bridge
    assert "shopTypeFeaturesModel" in bridge
    assert "TYPE_VARCHAR" in bridge
    assert "'status' => 'private'" in bridge
    assert 'name="auto_features"' in panel
    assert "'auto_features' =>" in controller



def test_standalone_production_bridge_supports_ensure_features():
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "if ($action === 'ensure_features')" in deploy
    assert "shopFeatureModel" in deploy
    assert "shopTypeFeaturesModel" in deploy
    assert "too_many_features" in deploy
    assert "product_type_not_found" in deploy


def test_bridge_errors_include_http_response_body():
    bridge = (ROOT / "supplier_engine/bridge.py").read_text(encoding="utf-8")
    assert "urllib.error.HTTPError" in bridge
    assert "body=%s" in bridge



def test_supplier_apply_has_sufficient_workflow_timeout():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    marker = "  supplier-run:"
    section = workflow.split(marker, 1)[1]
    assert "timeout-minutes: 330" in section



def test_supplier_profile_and_bridge_support_kit_export():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    bridge = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendBridge.controller.php").read_text(encoding="utf-8")
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    runner = (ROOT / "supplier_engine/runner.py").read_text(encoding="utf-8")
    kit_sync = (ROOT / "supplier_engine/kit_sync.py").read_text(encoding="utf-8")
    assert 'name="export_to_kit"' in panel
    assert "только товары, разложенные по категориям Webasyst" in panel
    assert "'export_to_kit' =>" in controller
    assert "$action === 'kit_manifest'" in bridge
    assert "if ($action === 'kit_manifest')" in deploy
    assert "bridge.kit_manifest(" in runner
    assert "sync_manifest(" in runner
    assert "СПБ привозной" in kit_sync
    assert 'Decimal("1.25")' in kit_sync
    assert 'Decimal("1.60")' in kit_sync
    assert '"kit_id"' in kit_sync


def test_supplier_workflow_exposes_kit_token_for_apply():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "YANDEX_KIT_TOKEN: ${{ secrets.YANDEX_KIT_TOKEN }}" in workflow


def test_supplier_matching_is_strictly_scoped_to_configured_product_type():
    runner = (ROOT / "supplier_engine/runner.py").read_text(encoding="utf-8")
    bridge_py = (ROOT / "supplier_engine/bridge.py").read_text(encoding="utf-8")
    bridge_php = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendBridge.controller.php").read_text(encoding="utf-8")
    sync = (ROOT / "supplier_engine/webasyst_sync.py").read_text(encoding="utf-8")

    assert "сверка товаров вне выбранного типа запрещена" in runner
    assert "type_id=type_id" in runner
    assert 'payload["type_id"] = int(type_id)' in bridge_py
    assert "JOIN shop_product p ON p.id=m.product_id" in bridge_php
    assert "p.type_id=" in bridge_php
    assert "product_type_mismatch" in bridge_php
    assert "current_type_id != target_type_id" in sync


def test_supplier_profile_exposes_full_description_mapping_and_rule():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    runner = (ROOT / "supplier_engine/runner.py").read_text(encoding="utf-8")
    sync = (ROOT / "supplier_engine/webasyst_sync.py").read_text(encoding="utf-8")

    assert 'name="description_field"' in panel
    assert 'name="update_description"' in panel
    assert "Поле описания" in panel
    assert "Обновлять полное описание" in panel
    assert "'description' => (string)waRequest::post('description_field'" in controller
    assert "'update_description' => (bool)waRequest::post('update_description'" in controller
    assert "array('yml', 'xml')" in controller
    assert "$config['mapping']['description'] = 'description';" in controller
    assert 'description_field = m.get("description")' in runner
    assert 'data["description"]' in sync



def test_multi_warehouse_profile_editor_and_server_validation():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    assert "s-ms-add-warehouse" in panel
    assert "readWarehouseMappings" in panel
    assert "warehouse_mappings" in panel
    assert "warehouse_mappings" in controller
    assert "postedWarehouseMappings" in controller
    assert "zero_other_stocks" in controller
    assert "in_array($id, $allowed_ids, true)" in controller



def test_multi_warehouse_kit_checkbox_has_safety_guard():
    panel = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    controller = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    assert "kit.disabled=hasMapped" in panel
    assert "kit.checked=false" in panel
    assert "'export_to_kit'" in controller
    assert "'zero_other_stocks'" in controller



def test_riva_apply_uses_immutable_source_artifact_and_preserves_hash_guard():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    run_controller = RUN_CONTROLLER.read_text(encoding="utf-8")
    callback = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendImportCallback.controller.php").read_text(encoding="utf-8")
    runner = (ROOT / "supplier_engine/runner.py").read_text(encoding="utf-8")
    assert "supplier-source-" in workflow
    assert "retention-days: 3" in workflow
    assert "gh run download" in workflow
    assert "actions: read" in workflow
    assert "--source-snapshot-output" in workflow
    assert "--source-snapshot-input" in workflow
    assert "'request_id' => (string)ifset($pending['request_id'])" in callback
    assert "'approved_snapshot_request_id' => $approved_snapshot_request_id" in run_controller
    assert "FRESH_DRY_RUN_REQUIRED" in run_controller
    assert "hmac.compare_digest" in runner
    assert "if is_riva and args.mode == \"dry-run\"" in runner
    assert 'raise ValueError("Для Riva требуется новая проверка' in runner



def test_production_physical_callback_saves_riva_approved_snapshot_request_id():
    # The website uses a physical /megasuppliers-callback/ endpoint rather than
    # Shop-Script's plugin route. Both implementations must persist the ID.
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    callback = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendImportCallback.controller.php").read_text(encoding="utf-8")
    controller = RUN_CONTROLLER.read_text(encoding="utf-8")
    assert "'request_id' => isset($pending['request_id']) ? (string)$pending['request_id'] : ''" in deploy
    assert "'request_id' => (string)ifset($pending['request_id'])" in callback
    assert "hash_equals((string)$last_dry['report']['source_sha256'], (string)$previous['report']['source_sha256'])" in controller
    assert "&& (string)ifset($previous['mode']) === 'dry-run'" in controller
    assert "&& (string)ifset($previous['finished_at']) === (string)ifset($last_dry['finished_at'])" in controller



def test_editable_supplier_profile_is_not_overwritten_with_red_black_defaults():
    config = (ROOT / "webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php").read_text(encoding="utf-8")
    ui = (ROOT / "webasyst/megasuppliers/patches/autoimport_panel.html").read_text(encoding="utf-8")
    runner = (ROOT / "supplier_engine/runner.py").read_text(encoding="utf-8")
    assert "applyRedBlackDefaults" not in config
    assert "preserveUneditedConfig" in config
    assert "array_merge($existing[$section], $new_section)" in config
    assert "$edited['safety'] = $existing['safety']" in config
    assert "supplier_sku_field" in ui
    assert "cache:'no-store'" in ui
    assert "var readback=paramsFromForm();" in ui
    assert "Настройки сохранены не полностью" in ui
    assert '_get(row, ident.get("supplier_sku_field")' in runner
    assert "m.update({" not in runner[runner.index("def normalize("):runner.index("def filter_incomplete_riva_rows(")]



def test_riva_generated_specs_are_public_only_for_riva():
    bridge = (ROOT / "webasyst/megasuppliers/lib/actions/frontend/shopMegasuppliersPluginFrontendBridge.controller.php").read_text(encoding="utf-8")
    assert "strtoupper(trim((string)ifset($feature_supplier['code']))) === 'RIVA'" in bridge
    assert "'status' => $is_riva ? 'public' : 'private'" in bridge
    assert "'available_for_sku' => 0" in bridge

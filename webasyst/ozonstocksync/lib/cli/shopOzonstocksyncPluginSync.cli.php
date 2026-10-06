<?php
class shopOzonstocksyncPluginSyncCli extends waCliController
{
    public function execute()
    {
        $plugin = wa('shop')->getPlugin('ozonstocksync');
        $settings = array();

        for ($i = 1; $i <= 10; $i++) {
            $suffix = ($i === 1) ? '' : '_' . $i;
            $settings['account_name' . $suffix] = $plugin->getSettings('account_name' . $suffix);
            $settings['client_id' . $suffix] = $plugin->getSettings('client_id' . $suffix);
            $settings['api_key' . $suffix] = $plugin->getSettings('api_key' . $suffix);
            $settings['mappings' . $suffix] = $plugin->getSettings('mappings' . $suffix);
        }

        $keys = array(
            'update_stocks', 'update_prices', 'tip_feature_code', 'acquiring_percent',
            'target_margin_percent', 'min_margin_percent', 'old_price_multiplier',
            'round_step', 'only_available', 'dry_run',
            'purchase_price_column',
            'stock_batch_size', 'stock_batch_pause_seconds',
            'price_batch_size', 'price_pre_wait_seconds', 'price_batch_pause_seconds', 'price_mapping_pause_seconds',
            'api_retry_count', 'api_retry_wait_seconds',
            'ozon_warehouse_id', 'set_id', 'webasyst_stock_id', 'reserve'
        );
        foreach ($keys as $key) {
            $settings[$key] = $plugin->getSettings($key);
        }

        // CLI overrides: allow cron command arguments like dry=0 stocks=1 prices=1.
        // Without this block the old version ignored dry=0 in cron and continued DRY_RUN.
        foreach ($this->getCliOverrides() as $key => $value) {
            $settings[$key] = $value;
        }

        $sync = new shopOzonstocksyncPluginSync($settings);
        $result = $sync->run();
        echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . PHP_EOL;
    }
    private function getCliOverrides()
    {
        $map = array(
            'dry' => 'dry_run',
            'dry_run' => 'dry_run',
            'stocks' => 'update_stocks',
            'update_stocks' => 'update_stocks',
            'prices' => 'update_prices',
            'update_prices' => 'update_prices',
            'stock_batch_size' => 'stock_batch_size',
            'stock_batch_pause_seconds' => 'stock_batch_pause_seconds',
            'price_batch_size' => 'price_batch_size',
            'price_pre_wait_seconds' => 'price_pre_wait_seconds',
            'price_batch_pause_seconds' => 'price_batch_pause_seconds',
            'price_mapping_pause_seconds' => 'price_mapping_pause_seconds',
            'api_retry_count' => 'api_retry_count',
            'api_retry_wait_seconds' => 'api_retry_wait_seconds',
            'only_available' => 'only_available',
        );

        $overrides = array();
        $argv = isset($_SERVER['argv']) && is_array($_SERVER['argv']) ? $_SERVER['argv'] : array();
        foreach ($argv as $arg) {
            if (strpos($arg, '=') === false) {
                continue;
            }
            list($raw_key, $value) = explode('=', $arg, 2);
            $raw_key = trim((string)$raw_key);
            $value = trim((string)$value);
            if ($raw_key === '' || !isset($map[$raw_key])) {
                continue;
            }
            $overrides[$map[$raw_key]] = $value;
        }
        return $overrides;
    }

}

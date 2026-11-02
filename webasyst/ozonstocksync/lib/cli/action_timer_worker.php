<?php
/**
 * Standalone CLI entry point for the Ozon promo-protection timer.
 * Does not respond to HTTP requests.
 *
 * Usage:
 * /usr/bin/php /path/to/wa-apps/shop/plugins/ozonstocksync/lib/cli/action_timer_worker.php
 */
if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit(1);
}
$root = dirname(__DIR__, 6);
if (!is_file($root . '/wa-config/SystemConfig.class.php')) {
    fwrite(STDERR, "Webasyst bootstrap not found\n");
    exit(1);
}
chdir($root);
require_once $root . '/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
require_once __DIR__ . '/shopOzonstocksyncPluginActionTimer.cli.php';
$worker = new shopOzonstocksyncPluginActionTimerCli();
$worker->execute();

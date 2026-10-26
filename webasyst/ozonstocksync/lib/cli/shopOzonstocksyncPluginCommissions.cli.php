<?php
/**
 * Commission-only worker. Safe for a daily cron and for the authenticated admin button.
 * The separate lock is provided by flock around this command.
 */
class shopOzonstocksyncPluginCommissionsCli extends waCliController
{
    public function execute()
    {
        $status_file = wa()->getDataPath('plugins/ozonstocksync/rfbs_refresh_status.json', false, 'shop', true);
        $this->writeStatus($status_file, array('status' => 'running', 'started_at' => time()));
        try {
            $plugin = wa('shop')->getPlugin('ozonstocksync');
            $settings = array('tip_feature_code' => $plugin->getSettings('tip_feature_code'));
            for ($i = 1; $i <= 10; $i++) {
                $suffix = $i === 1 ? '' : '_' . $i;
                foreach (array('account_name', 'client_id', 'api_key', 'mappings') as $field) {
                    $settings[$field . $suffix] = $plugin->getSettings($field . $suffix);
                }
            }
            $sync = new shopOzonstocksyncPluginSync($settings);
            $result = $sync->refreshCommissions();
            $this->writeStatus($status_file, array('status' => 'success', 'finished_at' => time(), 'result' => $result));
            echo json_encode(array('status' => 'success', 'result' => $result), JSON_UNESCAPED_UNICODE) . PHP_EOL;
        } catch (Exception $e) {
            $this->writeStatus($status_file, array(
                'status' => 'error', 'finished_at' => time(),
                'error' => substr($e->getMessage(), 0, 300)
            ));
            waLog::log('RFBS_INDEPENDENT_REFRESH_ERROR: ' . $e->getMessage(), 'ozonstocksync.log');
            echo "RFBS_REFRESH_FAILED" . PHP_EOL;
            exit(1);
        }
    }

    private function writeStatus($path, $data)
    {
        $raw = json_encode($data, JSON_UNESCAPED_UNICODE);
        if ($raw === false) return;
        $tmp = $path . '.tmp.' . getmypid();
        if (file_put_contents($tmp, $raw, LOCK_EX) === false || !rename($tmp, $path)) {
            @unlink($tmp);
            throw new waException('Commission refresh status could not be saved');
        }
    }
}

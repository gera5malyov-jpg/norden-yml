<?php
/**
 * Keep Ozon's 30-day minimum-price auto-promotion protection alive.
 *
 * This job does NOT change product prices, action prices, stocks or the
 * min_price_for_auto_actions_enabled switch. It only extends an existing,
 * enabled timer, and verifies the new expiration through Ozon Seller API.
 *
 * Usage: php cli.php shop ozonstocksyncPluginActionTimer [status_only=1]
 * Run daily with flock, separately from ordinary stock/price synchronisation.
 */
class shopOzonstocksyncPluginActionTimerCli extends waCliController
{
    const API = 'https://api-seller.ozon.ru';
    const PAGE_SIZE = 1000;
    const REFRESH_BEFORE_SECONDS = 2548800; // 29.5 days
    const VERIFY_MIN_SECONDS = 2505600; // 29 days

    public function execute()
    {
        $argv = isset($_SERVER['argv']) ? (array)$_SERVER['argv'] : array();
        $read_only = in_array('status_only=1', $argv, true);
        $plugin = wa('shop')->getPlugin('ozonstocksync');
        $status_path = wa()->getDataPath('plugins/ozonstocksync/action_timer_status.json', false, 'shop', true);
        $report = array('status' => 'running', 'started_at' => gmdate('c'), 'read_only' => $read_only, 'accounts' => array());
        $errors = 0;
        $found = 0;
        for ($index = 1; $index <= 10; $index++) {
            $suffix = $index === 1 ? '' : '_' . $index;
            $client = trim((string)$plugin->getSettings('client_id' . $suffix));
            $key = trim((string)$plugin->getSettings('api_key' . $suffix));
            if ($client === '' && $key === '') {
                continue;
            }
            $found++;
            $name = trim((string)$plugin->getSettings('account_name' . $suffix));
            if ($name === '') $name = 'Account ' . $index;
            $stats = array('name' => $name, 'index' => $index, 'seen' => 0,
                'enabled' => 0, 'due' => 0, 'renewed' => 0, 'skipped_disabled' => 0,
                'skipped_fresh' => 0, 'errors' => array());
            try {
                if ($client === '' || $key === '') throw new waException('Incomplete Ozon API credentials');
                $this->refreshAccount($client, $key, $stats, $read_only);
            } catch (Exception $e) {
                $stats['errors'][] = substr($e->getMessage(), 0, 250);
                $errors++;
            }
            $report['accounts'][] = $stats;
            waLog::log('ACTION_TIMER_ACCOUNT: ' . json_encode($stats, JSON_UNESCAPED_UNICODE), 'ozonstocksync-action-timer.log');
            // Persist partial progress even if a later account fails.
            $this->writeStatus($status_path, $report);
        }
        if (!$found) $errors++;
        $report['finished_at'] = gmdate('c');
        $report['status'] = $errors ? 'error' : ($read_only ? 'read_only' : 'success');
        if (!$found) $report['error'] = 'No configured Ozon accounts';
        $this->writeStatus($status_path, $report);
        waLog::log('ACTION_TIMER_FINISH: ' . json_encode($report, JSON_UNESCAPED_UNICODE), 'ozonstocksync-action-timer.log');
        echo json_encode($report, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . PHP_EOL;
        if ($errors) exit(1);
    }

    private function refreshAccount($client, $key, &$stats, $read_only)
    {
        $cursor = '';
        $seen_cursors = array();
        for ($page = 0; $page < 500; $page++) {
            $response = $this->api('/v3/product/list', array(
                'filter' => array('visibility' => 'ALL'), 'last_id' => $cursor,
                'limit' => self::PAGE_SIZE,
            ), $client, $key);
            $result = isset($response['result']) ? $response['result'] : null;
            if (!is_array($result) || !isset($result['items']) || !is_array($result['items'])) {
                throw new waException('Invalid /v3/product/list response');
            }
            $ids = array();
            foreach ($result['items'] as $item) {
                if (!is_array($item) || !isset($item['product_id'])) continue;
                $id = (string)$item['product_id'];
                if (ctype_digit($id) && $id !== '0') $ids[$id] = $id;
            }
            $stats['seen'] += count($ids);
            if ($ids) {
                // Status and timer updates both accept up to 1000 Ozon product_id values.
                $payload = array('product_ids' => array_values($ids));
                $before = $this->api('/v1/product/action/timer/status', $payload, $client, $key);
                $statuses = $this->validatedStatuses($before, array_values($ids));
                $due = array();
                foreach ($statuses as $id => $state) {
                    if (empty($state['min_price_for_auto_actions_enabled'])) {
                        $stats['skipped_disabled']++;
                        continue;
                    }
                    $stats['enabled']++;
                    $expiry = isset($state['expired_at']) ? strtotime((string)$state['expired_at']) : false;
                    if ($expiry === false || $expiry < time() + self::REFRESH_BEFORE_SECONDS) {
                        $due[] = $id;
                    } else {
                        $stats['skipped_fresh']++;
                    }
                }
                $stats['due'] += count($due);
                if ($due && !$read_only) {
                    $this->api('/v1/product/action/timer/update', array('product_ids' => $due), $client, $key);
                    $after = $this->api('/v1/product/action/timer/status', array('product_ids' => $due), $client, $key);
                    $verified = $this->validatedStatuses($after, $due);
                    foreach ($due as $id) {
                        $value = $verified[$id];
                        $expires = isset($value['expired_at']) ? strtotime((string)$value['expired_at']) : false;
                        if (empty($value['min_price_for_auto_actions_enabled'])
                            || $expires === false || $expires < time() + self::VERIFY_MIN_SECONDS) {
                            throw new waException('Timer was not renewed for product_id ' . $id);
                        }
                        $stats['renewed']++;
                    }
                }
            }
            $new_cursor = isset($result['last_id']) ? (string)$result['last_id'] : '';
            if (!$result['items'] || $new_cursor === '') return;
            if ($new_cursor === $cursor || isset($seen_cursors[$new_cursor])) {
                throw new waException('Product list pagination loop');
            }
            $seen_cursors[$new_cursor] = true;
            $cursor = $new_cursor;
            usleep(200000);
        }
        throw new waException('Product pagination safety cap reached');
    }

    private function validatedStatuses($response, $requested)
    {
        if (!isset($response['statuses']) || !is_array($response['statuses'])) {
            throw new waException('Invalid action timer status response');
        }
        $states = array();
        foreach ($response['statuses'] as $row) {
            if (!is_array($row) || !isset($row['product_id'])) continue;
            $states[(string)$row['product_id']] = $row;
        }
        foreach ($requested as $id) {
            if (!isset($states[(string)$id])
                || !array_key_exists('min_price_for_auto_actions_enabled', $states[(string)$id])) {
                throw new waException('Missing timer status for product_id ' . $id);
            }
        }
        return $states;
    }

    private function api($path, $data, $client, $key)
    {
        $last_error = 'Ozon API unavailable';
        for ($attempt = 1; $attempt <= 4; $attempt++) {
            $ch = curl_init(self::API . $path);
            curl_setopt_array($ch, array(
                CURLOPT_POST => true, CURLOPT_POSTFIELDS => json_encode($data),
                CURLOPT_HTTPHEADER => array('Content-Type: application/json',
                    'Client-Id: ' . $client, 'Api-Key: ' . $key),
                CURLOPT_RETURNTRANSFER => true, CURLOPT_CONNECTTIMEOUT => 12,
                CURLOPT_TIMEOUT => 75,
            ));
            $body = curl_exec($ch);
            $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
            $error = curl_error($ch);
            curl_close($ch);
            if ($body !== false && $code >= 200 && $code < 300) {
                $decoded = json_decode((string)$body, true);
                if ($body === '' || $body === 'null') return array();
                if (!is_array($decoded)) throw new waException('Invalid JSON for ' . $path);
                return $decoded;
            }
            // Never log credentials or raw API responses.
            $last_error = $path . ' HTTP ' . $code . ($error ? ' (transport error)' : '');
            if (!($code === 0 || $code === 429 || $code >= 500) || $attempt === 4) break;
            sleep(min(20, 3 * $attempt));
        }
        throw new waException($last_error);
    }

    private function writeStatus($path, $data)
    {
        $encoded = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
        if ($encoded === false) throw new waException('Timer report serialization failed');
        $tmp = $path . '.tmp.' . getmypid();
        if (file_put_contents($tmp, $encoded, LOCK_EX) === false || !rename($tmp, $path)) {
            @unlink($tmp);
            throw new waException('Timer status report could not be saved');
        }
    }
}

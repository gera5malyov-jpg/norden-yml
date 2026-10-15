<?php
/**
 * Independent, guarded Yandex Market Language importer.
 * Important: pictures are stored as [extimg] URLs in product summary.
 * No shop_product_images, wa-data/public image files, or YML PRO tables are touched.
 */
class shopUnivimportPluginImporter
{
    const MAX_FEED_BYTES = 209715200;
    const BATCH_SIZE = 50;
    const MAX_IMAGES = 30;

    private static function snapshotPath($id)
    {
        return shopUnivimportPluginProfiles::dir().'/source-'.$id.'.xml';
    }

    private static function lock($id)
    {
        $file = shopUnivimportPluginProfiles::dir().'/lock-'.$id;
        $handle = fopen($file, 'c');
        if (!$handle || !flock($handle, LOCK_EX | LOCK_NB)) {
            throw new RuntimeException('Этот профиль уже обрабатывается.');
        }
        return $handle;
    }

    private static function download($profile)
    {
        shopUnivimportPluginProfiles::checkUrl($profile['url']);
        $host = parse_url($profile['url'], PHP_URL_HOST);
        $ips = gethostbynamel($host);
        if (!$ips) {
            throw new RuntimeException('Адрес источника недоступен.');
        }
        $file = self::snapshotPath($profile['id']);
        $tmp = $file.'.'.getmypid().'.tmp';
        $handle = fopen($tmp, 'wb');
        if (!$handle) {
            throw new RuntimeException('Нет доступа к временному файлу.');
        }
        $total = 0;
        $ch = curl_init($profile['url']);
        curl_setopt_array($ch, array(
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_CONNECTTIMEOUT => 20,
            CURLOPT_TIMEOUT => 180,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_HTTPHEADER => array('Accept: application/xml, text/xml, */*'),
            CURLOPT_USERAGENT => 'Webasyst-Univimport/0.1',
            CURLOPT_RESOLVE => array($host.':443:'.$ips[0]),
            CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use ($handle, &$total) {
                $bytes = strlen($chunk);
                $total += $bytes;
                if ($total > self::MAX_FEED_BYTES) {
                    return 0;
                }
                return fwrite($handle, $chunk);
            },
        ));
        $ok = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);
        fclose($handle);
        if (!$ok || $status !== 200 || $total === 0 || $total > self::MAX_FEED_BYTES) {
            @unlink($tmp);
            throw new RuntimeException('Ошибка загрузки YML (HTTP '.$status.'): '.substr($error, 0, 160));
        }
        if (!rename($tmp, $file)) {
            @unlink($tmp);
            throw new RuntimeException('Не удалось сохранить снимок YML.');
        }
        return array('sha256' => hash_file('sha256', $file), 'size' => $total);
    }

    private static function openReader($path)
    {
        if (!is_file($path)) {
            throw new RuntimeException('Снимок YML отсутствует. Выполните проверку заново.');
        }
        $reader = new XMLReader();
        if (!$reader->open($path, null, LIBXML_NONET | LIBXML_COMPACT)) {
            throw new RuntimeException('Не удалось открыть файл XML.');
        }
        $reader->setParserProperty(XMLReader::SUBST_ENTITIES, false);
        $reader->setParserProperty(XMLReader::VALIDATE, false);
        return $reader;
    }

    public static function row($xml)
    {
        $v = @simplexml_load_string($xml, 'SimpleXMLElement', LIBXML_NONET);
        if (!$v) {
            throw new RuntimeException('Повреждён элемент offer.');
        }
        $id = trim((string)$v['id']);
        $name = trim((string)$v->name);
        if ($name === '') {
            $name = trim((string)$v->model);
        }
        $code = trim((string)$v->vendorCode);
        if ($code === '') {
            $code = $id;
        }
        if ($id === '' || $name === '' || strlen($id) > 180) {
            throw new RuntimeException('У offer отсутствует обязательный id/name или id слишком длинный.');
        }
        $price = trim((string)$v->price);
        if ($price !== '' && (!is_numeric($price) || (float)$price < 0)) {
            throw new RuntimeException('Некорректная цена у offer id='.$id);
        }
        $currency = trim((string)$v->currencyId);
        if ($currency !== '' && $currency !== 'RUR' && $currency !== 'RUB') {
            throw new RuntimeException('В offer id='.$id.' цена не в RUB, пересчёт валют пока не настроен.');
        }
        $count = null;
        foreach (array('quantity', 'stock', 'count') as $key) {
            if (isset($v->$key) && trim((string)$v->$key) !== '') {
                $value = trim((string)$v->$key);
                if (!is_numeric($value) || (float)$value < 0) {
                    throw new RuntimeException('Некорректный остаток у offer id='.$id);
                }
                $count = (float)$value;
                break;
            }
        }
        if ($count === null && strtolower(trim((string)$v['available'])) === 'false') {
            $count = 0;
        }
        $images = array();
        foreach ($v->picture as $picture) {
            self::addImage($images, (string)$picture);
        }
        if (isset($v->images)) {
            foreach ($v->images->image as $picture) {
                self::addImage($images, (string)$picture);
            }
        }
        return array(
            'id' => $id,
            'name' => $name,
            'sku' => $code,
            'description' => trim((string)$v->description),
            'price' => $price === '' ? null : (float)$price,
            'count' => $count,
            'images' => $images,
        );
    }

    private static function addImage(&$images, $raw)
    {
        $url = trim(html_entity_decode($raw, ENT_QUOTES, 'UTF-8'));
        $parts = parse_url($url);
        if (!$parts || !in_array(strtolower(isset($parts['scheme']) ? $parts['scheme'] : ''), array('https', 'http'), true)
            || empty($parts['host']) || !empty($parts['user']) || !empty($parts['pass'])
            || strlen($url) > 1800 || preg_match('/[\x00-\x20\[\]<>]/', $url)) {
            return;
        }
        if (count($images) < self::MAX_IMAGES && !in_array($url, $images, true)) {
            $images[] = $url;
        }
    }

    private static function eachOffer($path, $callback)
    {
        $reader = self::openReader($path);
        $count = 0;
        try {
            while ($reader->read()) {
                if ($reader->nodeType === XMLReader::DOC_TYPE) {
                    throw new RuntimeException('DOCTYPE запрещён для безопасности.');
                }
                if ($reader->nodeType !== XMLReader::ELEMENT || $reader->localName !== 'offer') {
                    continue;
                }
                $row = self::row($reader->readOuterXML());
                $callback($row, $count);
                $count++;
                if ($count > 1000000) {
                    throw new RuntimeException('Превышен лимит в 1 млн товаров.');
                }
            }
        } finally {
            $reader->close();
        }
        return $count;
    }

    public static function preview($profile)
    {
        $lock = self::lock($profile['id']);
        try {
            $file = self::download($profile);
            $ids = array();
            $result = array('count' => 0, 'images' => 0, 'samples' => array(), 'duplicates' => array());
            self::eachOffer(self::snapshotPath($profile['id']), function ($row) use (&$result, &$ids) {
                $key = $row['id'];
                if (isset($ids[$key])) {
                    if (count($result['duplicates']) < 20) {
                        $result['duplicates'][] = $key;
                    }
                }
                $ids[$key] = true;
                $result['count']++;
                $result['images'] += count($row['images']);
                if (count($result['samples']) < 5) {
                    $result['samples'][] = array('id'=>$key, 'name'=>$row['name'], 'images'=>count($row['images']));
                }
            });
            if (!$result['count'] || $result['duplicates']) {
                throw new RuntimeException('Проверка не пройдена: товаров '.(int)$result['count']
                    .', дубликатов ID '.count($result['duplicates']).'.');
            }
            $result['sha256'] = $file['sha256'];
            $result['bytes'] = $file['size'];
            $result['profile_sha256'] = self::profileHash($profile);
            $result['checked_at'] = time();
            $result['status'] = 'ok';
            shopUnivimportPluginProfiles::saveReport($profile['id'], $result);
            return $result;
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    private static function profileHash($profile)
    {
        return hash('sha256', json_encode($profile, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    }

    private static function ensureMap()
    {
        (new waModel())->exec(
            "CREATE TABLE IF NOT EXISTS shop_univimport_map (".
            "profile VARCHAR(40) NOT NULL, offer_id VARCHAR(180) NOT NULL,".
            "product_id INT UNSIGNED NOT NULL, sku_id INT UNSIGNED NOT NULL,".
            "updated_at DATETIME NOT NULL,".
            "PRIMARY KEY (profile, offer_id), KEY product_id (product_id)".
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        );
    }

    private static function summaryImages($summary, $images)
    {
        if (!$images) {
            return $summary;
        }
        // Replace only [extimg] blocks, preserving all manually written text.
        $summary = preg_replace('~\s*\[extimg\]\s*https?://[^\[\r\n]+\s*\[/extimg\]\s*~iu', "\n", (string)$summary);
        $summary = trim($summary);
        $blocks = array();
        foreach ($images as $url) {
            $blocks[] = "[extimg]\n".$url."\n[/extimg]";
        }
        return trim($summary."\n".implode("\n", $blocks));
    }

    private static function applyRow($profile, $row, $model)
    {
        $id = $model->escape($row['id']);
        $profileId = $model->escape($profile['id']);
        $old = $model->query("SELECT * FROM shop_univimport_map WHERE profile='".$profileId."' AND offer_id='".$id."'")->fetch();
        $skuCode = $profile['sku_prefix'].$row['sku'];
        if (mb_strlen($skuCode) > 255 || $skuCode === '') {
            throw new RuntimeException('Артикул пуст или слишком длинный.');
        }
        $skus = new shopProductSkusModel();
        if (!$old) {
            if (!$profile['create']) {
                return 'skipped';
            }
            // Never adopt somebody else's SKU, even when its code matches.
            if ($skus->getByField('sku', $skuCode)) {
                throw new RuntimeException('SKU уже существует вне профиля: '.$skuCode);
            }
            $data = array(
                'name' => $row['name'],
                'description' => $row['description'],
                'summary' => $profile['update_images'] ? self::summaryImages('', $row['images']) : '',
                'type_id' => (int)$profile['type_id'],
                'status' => 0,
                'currency' => 'RUB',
                'sku_type' => 0,
                'skus' => array(array(
                    'sku' => $skuCode,
                    'price' => $row['price'] === null ? 0 : $row['price'],
                    'available' => 1,
                    'status' => 1,
                )),
            );
            if ($profile['category_id']) {
                $data['categories'] = array((int)$profile['category_id']);
                $data['category_id'] = (int)$profile['category_id'];
            }
            $p = new shopProduct();
            $p->save($data);
            $productId = (int)$p['id'];
            if (!$productId) {
                throw new RuntimeException('Shop-Script не создал карточку товара.');
            }
            $sku = $skus->getByField(array('product_id' => $productId, 'sku' => $skuCode));
            if (!$sku) {
                // Compatibility fallback for older Shop-Script versions.
                $sku = $skus->getByField('product_id', $productId);
            }
            if (!$sku || empty($sku['id'])) {
                throw new RuntimeException('Shop-Script не создал артикул для товара '.$productId);
            }
            $skuId = (int)$sku['id'];
            $model->insert('shop_univimport_map', array(
                'profile'=>$profile['id'], 'offer_id'=>$row['id'],
                'product_id'=>$productId, 'sku_id'=>$skuId, 'updated_at'=>date('Y-m-d H:i:s')
            ));
            self::applyStock($profile, $row, $productId, $skuId, $skus);
            return 'created';
        }
        if (!$profile['update']) {
            return 'skipped';
        }
        $productId = (int)$old['product_id'];
        $skuId = (int)$old['sku_id'];
        $storedSku = $skus->getById($skuId);
        $products = new shopProductModel();
        $original = $products->getById($productId);
        if (!$storedSku || (int)$storedSku['product_id'] !== $productId || !$original ||
            (int)$original['type_id'] !== (int)$profile['type_id']) {
            throw new RuntimeException('Нарушена привязка товара: offer id='.$row['id']);
        }
        $p = new shopProduct($productId);
        if ($profile['update_name']) {
            $p['name'] = $row['name'];
        }
        if ($profile['update_description']) {
            $p['description'] = $row['description'];
        }
        if ($profile['update_images'] && $row['images']) {
            $p['summary'] = self::summaryImages((string)$p['summary'], $row['images']);
        }
        $p->save();
        if ($profile['update_prices'] && $row['price'] !== null) {
            $skus->updateById($skuId, array('price' => $row['price']));
        }
        self::applyStock($profile, $row, $productId, $skuId, $skus);
        $model->exec("UPDATE shop_univimport_map SET updated_at=NOW() WHERE profile='".
            $profileId."' AND offer_id='".$id."'");
        return 'updated';
    }

    private static function applyStock($profile, $row, $productId, $skuId, $skus)
    {
        if (!$profile['update_stocks'] || $row['count'] === null) {
            return;
        }
        (new shopProductStocksModel())->set(array(
            'product_id' => $productId,
            'sku_id' => $skuId,
            'stock_id' => (int)$profile['stock_id'],
            'count' => $row['count'],
        ));
        (new shopProductModel())->correct($productId);
    }

    public static function apply($profile, $offset)
    {
        if (!$profile['allow_write']) {
            throw new RuntimeException('Запись выключена в настройках профиля.');
        }
        $offset = (int)$offset;
        if ($offset < 0 || $offset > 1000000) {
            throw new RuntimeException('Некорректное смещение.');
        }
        $lock = self::lock($profile['id']);
        try {
            $report = shopUnivimportPluginProfiles::report($profile['id']);
            $file = self::snapshotPath($profile['id']);
            if (empty($report['sha256']) || empty($report['status']) || $report['status'] !== 'ok'
                || time() - (int)$report['checked_at'] > 1800
                || !hash_equals((string)$report['profile_sha256'], self::profileHash($profile))
                || !is_file($file) || !hash_equals((string)$report['sha256'], hash_file('sha256', $file))) {
                throw new RuntimeException('Нужна новая успешная проверка YML после сохранения текущих настроек.');
            }
            self::ensureMap();
            $model = new waModel();
            $counter = array('created'=>0, 'updated'=>0, 'skipped'=>0);
            $visited = 0;
            // Stop reading as soon as the requested batch has been written.
            $reader = self::openReader($file);
            try {
                while ($reader->read()) {
                    if ($reader->nodeType === XMLReader::DOC_TYPE) {
                        throw new RuntimeException('DOCTYPE запрещён.');
                    }
                    if ($reader->nodeType !== XMLReader::ELEMENT || $reader->localName !== 'offer') {
                        continue;
                    }
                    if ($visited < $offset) {
                        $visited++;
                        continue;
                    }
                    if ($visited >= $offset + self::BATCH_SIZE) {
                        break;
                    }
                    $row = self::row($reader->readOuterXML());
                    $kind = self::applyRow($profile, $row, $model);
                    $counter[$kind]++;
                    $visited++;
                }
            } finally {
                $reader->close();
            }
            $counter['next_offset'] = $visited;
            $counter['total'] = (int)$report['count'];
            $counter['done'] = $visited >= (int)$report['count'];
            return $counter;
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }
}

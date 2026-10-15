<?php
/** Companion for installed YML PRO. Original plugin remains unchanged. */
class shopUnivimportPluginYmlproPhotoSync
{
    public static function gallery($summary, $urls)
    {
        $blocks = array();
        foreach (array_unique($urls) as $url) {
            $blocks[] = "[extimg]\n".$url."\n[/extimg]";
        }
        return trim((string)$summary."\n".implode("\n", $blocks));
    }
    private static function downloadFeed($profileId)
    {
        $db = new waModel();
        $row = $db->query('SELECT config FROM shop_yml_profiles WHERE id='.(int)$profileId)->fetch();
        if (!$row) {
            throw new RuntimeException('YML PRO profile missing');
        }
        $cfg = json_decode($row['config'], true);
        if (!is_array($cfg)) {
            throw new RuntimeException('Invalid YML PRO profile config');
        }
        $url = isset($cfg['url']) ? (string)$cfg['url'] : '';
        if (strpos($url, 'https://') !== 0 || !empty($cfg['http_login'])) {
            throw new RuntimeException('Only public HTTPS feeds are enabled');
        }
        $tmp = tempnam(sys_get_temp_dir(), 'ymlpic-');
        $handle = fopen($tmp, 'wb');
        $total = 0;
        $ch = curl_init($url);
        curl_setopt_array($ch, array(
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_MAXREDIRS => 3,
            CURLOPT_CONNECTTIMEOUT => 15,
            CURLOPT_TIMEOUT => 120,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use ($handle, &$total) {
                $total += strlen($chunk);
                if ($total > 85000000) return 0;
                return fwrite($handle, $chunk);
            },
        ));
        $ok = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        fclose($handle);
        if (!$ok || $status !== 200) {
            @unlink($tmp);
            throw new RuntimeException('Feed download failed (HTTP '.$status.')');
        }
        return array($tmp, $cfg);
    }

    private static function pictures($xml, $max)
    {
        $offer = @simplexml_load_string($xml, 'SimpleXMLElement', LIBXML_NONET);
        if (!$offer) return array();
        $urls = array();
        foreach ($offer->picture as $picture) {
            $url = trim((string)$picture);
            $p = parse_url($url);
            if (!$p || empty($p['host']) || !in_array(isset($p['scheme'])?$p['scheme']:'', array('http','https'), true)
                || strlen($url)>1600 || preg_match('/[\\x00-\\x20<>\\[\\]]/', $url)) continue;
            if (!in_array($url, $urls, true)) $urls[] = $url;
            if (count($urls)>=$max) break;
        }
        return $urls;
    }

    public static function collect($profileId, $limit)
    {
        $profileId = (int)$profileId;
        $limit = min(max(1, (int)$limit), 50);
        list($file, $cfg) = self::downloadFeed($profileId);
        $max = isset($cfg['max_img']) ? (int)$cfg['max_img'] : 10;
        $max = $max > 0 ? min($max, 30) : 30;
        $db = new waModel();
        $map = array();
        $sql = 'SELECT yp.yml_id, yp.shop_id, p.summary, p.image_id '.
            'FROM shop_yml_products yp JOIN shop_product p ON p.id=yp.shop_id '.
            'WHERE yp.profile_id='.$profileId;
        foreach ($db->query($sql)->fetchAll() as $v) {
            $key = (string)$v['yml_id'];
            if (!isset($map[$key])) $map[$key] = array();
            $map[$key][] = $v;
        }
        $reader = new XMLReader();
        $selected = array();
        try {
            if (!$reader->open($file, null, LIBXML_NONET | LIBXML_COMPACT)) {
                throw new RuntimeException('Invalid YML');
            }
            $reader->setParserProperty(XMLReader::SUBST_ENTITIES, false);
            $reader->setParserProperty(XMLReader::VALIDATE, false);
            while ($reader->read()) {
                if ($reader->nodeType !== XMLReader::ELEMENT || $reader->localName !== 'offer') continue;
                $offerId = (string)$reader->getAttribute('id');
                if (!isset($map[$offerId]) || count($map[$offerId]) !== 1) continue;
                $v = $map[$offerId][0];
                $productId = (int)$v['shop_id'];
                if (stripos((string)$v['summary'], '[extimg]') !== false) continue;
                $other = (int)$db->query('SELECT COUNT(*) FROM shop_yml_products WHERE shop_id='.
                    $productId.' AND profile_id<>'.$profileId)->fetchField();
                if ($other) continue;
                $urls = self::pictures($reader->readOuterXML(), $max);
                if (!$urls) continue;
                $selected[] = array('product_id'=>$productId, 'photos'=>$urls,
                    'summary'=>(string)$v['summary'], 'native_image'=>(int)$v['image_id']);
                if (count($selected) >= $limit) break;
            }
        } finally {
            $reader->close();
            @unlink($file);
        }
        return $selected;
    }

}

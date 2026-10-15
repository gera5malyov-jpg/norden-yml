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

}

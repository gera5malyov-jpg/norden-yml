<?php
/**
 * Deephouse external-image synchronizer for already mapped YML PRO products.
 * Neither modifies YML PRO nor downloads a single product image.
 * The only writable catalog field is shop_product.summary.
 */
class shopUnivimportPluginDeephouseSync
{
    const PROFILE_ID = 20;
    const MAX_FEED_BYTES = 18000000;
    const MAX_PHOTOS = 60;

    private static function urls($text)
    {
        $found = array();
        preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~is', (string)$text, $matches);
        foreach ($matches[1] as $block) {
            foreach (preg_split('/\\r\\n|\\r|\\n/', trim($block)) as $line) {
                $url = trim($line);
                if (preg_match('~^https?://~i', $url) && !in_array($url, $found, true)) {
                    $found[] = $url;
                }
            }
        }
        return $found;
    }

    private static function mergeSummary($text, $urls)
    {
        $out = preg_replace('~\\s*\\[extimg\\].*?\\[/extimg\\]\\s*~isu', "\n", (string)$text);
        if ($out === null) throw new RuntimeException('Cannot remove obsolete photo blocks');
        $blocks = array();
        foreach ($urls as $url) $blocks[] = "[extimg]\n".$url."\n[/extimg]";
        return trim(trim($out)."\n".implode("\n", $blocks));
    }

    private static function photoUrls($xml)
    {
        $offer = @simplexml_load_string($xml, 'SimpleXMLElement', LIBXML_NONET);
        if (!$offer) throw new RuntimeException('Invalid YML offer XML');
        $photos = array();
        $nodes = array();
        foreach ($offer->picture as $v) $nodes[] = (string)$v;
        if (isset($offer->images)) {
            foreach ($offer->images->image as $v) $nodes[] = (string)$v;
        }
        foreach ($nodes as $item) {
            $url = trim($item);
            if ($url === '' || strlen($url) > 2000 || preg_match('/[\\x00-\\x20<>\\[\\]]/', $url)) continue;
            $p = parse_url($url);
            if (!$p || !isset($p['scheme'], $p['host'])
                || strtolower($p['scheme']) !== 'https'
                || strtolower($p['host']) !== 'deephouse.pro'
                || isset($p['user']) || isset($p['pass'])) {
                throw new RuntimeException('Unexpected image URL from supplier');
            }
            if (!in_array($url, $photos, true)) $photos[] = $url;
            if (count($photos) > self::MAX_PHOTOS) {
                throw new RuntimeException('Too many supplier photos in one offer');
            }
        }
        return $photos;
    }

    private static function downloadFeed($db)
    {
        $profile = $db->query('SELECT name,config FROM shop_yml_profiles WHERE id=20')->fetch();
        if (!$profile || trim((string)$profile['name']) !== 'deephouse') {
            throw new RuntimeException('Unexpected YML PRO profile');
        }
        $config = json_decode($profile['config'], true);
        if (!is_array($config) || (int)$config['images'] !== 4
            || !empty($config['http_login']) || !empty($config['http_auth'])) {
            throw new RuntimeException('Unsafe Deephouse profile configuration');
        }
        $url = (string)$config['url'];
        $p = parse_url($url);
        if (!$p || !isset($p['host']) || strtolower($p['host']) !== 'deephouse.pro'
            || !isset($p['scheme']) || !in_array(strtolower($p['scheme']), array('http','https'), true)) {
            throw new RuntimeException('Unrecognized Deephouse source URL');
        }
        // Valid TLS is available for this same supplier path; do not use plaintext HTTP.
        $url = preg_replace('~^http://~i', 'https://', $url);
        $file = tempnam(sys_get_temp_dir(), 'deephouse-photo-sync-');
        if (!$file) throw new RuntimeException('Temp file unavailable');
        $stream = fopen($file, 'wb');
        $bytes = 0;
        $curl = curl_init($url);
        curl_setopt_array($curl, array(
            CURLOPT_FOLLOWLOCATION=>false, CURLOPT_CONNECTTIMEOUT=>12, CURLOPT_TIMEOUT=>110,
            CURLOPT_SSL_VERIFYPEER=>true, CURLOPT_SSL_VERIFYHOST=>2,
            CURLOPT_WRITEFUNCTION=>function($ch,$chunk) use (&$bytes,$stream) {
                $bytes += strlen($chunk);
                if ($bytes > self::MAX_FEED_BYTES) return 0;
                return fwrite($stream,$chunk);
            },
        ));
        $ok = curl_exec($curl);
        $status = (int)curl_getinfo($curl, CURLINFO_HTTP_CODE);
        curl_close($curl);
        fclose($stream);
        if (!$ok || $status !== 200 || $bytes < 1000) {
            @unlink($file);
            throw new RuntimeException('The Deephouse HTTPS feed is unavailable');
        }
        return $file;
    }

    public static function prepare()
    {
        $db = new waModel();
        $map = array();
        $other = array();
        $count = 0;
        $rows = $db->query('SELECT yp.yml_id,yp.shop_id,p.summary,p.image_id FROM shop_yml_products yp '.
            'JOIN shop_product p ON p.id=yp.shop_id WHERE yp.profile_id=20')->fetchAll();
        foreach ($rows as $row) {
            $id = (string)$row['yml_id'];
            if ($id === '' || isset($map[$id])) throw new RuntimeException('Duplicate/blank YML offer ID');
            if ((int)$row['image_id'] > 0) throw new RuntimeException('Unexpected local images');
            $map[$id] = $row;
            $count++;
        }
        if ($count !== 1917) throw new RuntimeException('Mapping count changed since read-only audit');
        $shared = (int)$db->query('SELECT COUNT(*) FROM shop_yml_products a JOIN shop_yml_products b '.
            'ON a.shop_id=b.shop_id AND b.profile_id<>20 WHERE a.profile_id=20')->fetchField();
        if ($shared) throw new RuntimeException('Cross-profile product collision');
        $feed = self::downloadFeed($db);
        $r = new XMLReader();
        $changes = array();
        $matched = 0; $offers = 0; $allPics = 0; $unchanged = 0;
        $seen = array();
        try {
            if (!$r->open($feed, null, LIBXML_NONET | LIBXML_COMPACT)) throw new RuntimeException('Cannot parse feed');
            $r->setParserProperty(XMLReader::SUBST_ENTITIES, false);
            $r->setParserProperty(XMLReader::VALIDATE, false);
            while ($r->read()) {
                if ($r->nodeType !== XMLReader::ELEMENT || $r->localName !== 'offer') continue;
                $offers++;
                $offerId = (string)$r->getAttribute('id');
                if (!isset($map[$offerId]) || isset($seen[$offerId])) throw new RuntimeException('New or duplicate offer');
                $seen[$offerId] = true;
                $matched++;
                $pics = self::photoUrls($r->readOuterXML());
                if (!$pics) throw new RuntimeException('Offer missing images: refusing to clear old gallery');
                $allPics += count($pics);
                $row = $map[$offerId];
                $previous = self::urls($row['summary']);
                if ($previous === $pics) {$unchanged++;continue;}
                $changes[] = array('id'=>(int)$row['shop_id'], 'old'=>(string)$row['summary'],
                    'new'=>self::mergeSummary($row['summary'],$pics),'photos'=>count($pics));
            }
            $r->close();
        } finally {
            $r->close();
            @unlink($feed);
        }
        // A new YML revision should go through a new audited dry-run first.
        if ($offers !== 1025 || $matched !== 1025 || $allPics < 9500) {
            throw new RuntimeException('Feed changed materially since the audited snapshot');
        }
        return array('profile_id'=>20,'mapped'=>$count,'feed_offers'=>$offers,'matched'=>$matched,
            'unchanged'=>$unchanged,'changes'=>$changes,'photos'=>$allPics,'not_in_feed'=>$count-$matched);
    }

    public static function apply($plan)
    {
        $changes = $plan['changes'];
        if (!is_array($changes) || count($changes) > 1025) throw new RuntimeException('Invalid plan size');
        $dir = wa()->getDataPath('plugins/univimport/rollback', false, 'shop', true);
        if (!is_dir($dir)) waFiles::create($dir);
        $path = $dir.'/deephouse-all-'.gmdate('Ymd-His').'-'.getmypid().'.json';
        // Save the entire pre-update state before changing a single product.
        $backup = array();
        foreach ($changes as $item) $backup[] = array('id'=>$item['id'],'summary'=>$item['old']);
        $blob = json_encode(array('profile'=>20,'before'=>$backup),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        if (!$blob || file_put_contents($path,$blob,LOCK_EX) !== strlen($blob)) {
            throw new RuntimeException('Cannot store the entire rollback snapshot');
        }
        @chmod($path,0600);
        $products = new shopProductModel();
        $updated = 0; $skipped = 0;
        $sample = array();
        foreach ($changes as $item) {
            $current = $products->getById($item['id']);
            if (!$current || (string)$current['summary'] !== $item['old']) {$skipped++;continue;}
            $products->updateById($item['id'],array('summary'=>$item['new']));
            $updated++;
            if (count($sample)<8) $sample[]=array('id'=>$item['id'],'photos'=>$item['photos']);
        }
        return array('backup_exists'=>is_file($path),'updated'=>$updated,
            'concurrent_skipped'=>$skipped,'unchanged'=>$plan['unchanged'],
            'not_in_feed'=>$plan['not_in_feed'],'photo_links_in_feed'=>$plan['photos'],
            'sample'=>$sample);
    }
}

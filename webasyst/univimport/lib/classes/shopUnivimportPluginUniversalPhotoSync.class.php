<?php
/**
 * Companion for licensed YML PRO profiles. Read-only regarding YML PRO itself.
 * Syncs only shop_product.summary, never downloads individual product pictures.
 */
class shopUnivimportPluginUniversalPhotoSync
{
    const FEED_MAX_BYTES = 85000000;
    const MAX_PICTURES = 50;
    const LOCK_DIR = 'plugins/univimport/locks';
    const BACKUP_DIR = 'plugins/univimport/rollback';

    private static function validImage($url)
    {
        $url = trim(html_entity_decode((string)$url, ENT_QUOTES, 'UTF-8'));
        if ($url === '' || strlen($url)>1900 || preg_match('/[\\x00-\\x20<>\\[\\]]/', $url)) return null;
        $p = parse_url($url);
        if (!$p || empty($p['host']) || !isset($p['scheme']) || isset($p['user']) || isset($p['pass'])
            || !in_array(strtolower($p['scheme']), array('https', 'http'), true)) return null;
        return $url;
    }

    private static function existingUrls($summary)
    {
        $urls = array();
        preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~isu', (string)$summary, $blocks);
        foreach ($blocks[1] as $block) {
            foreach (preg_split('/\\r\\n|\\r|\\n/', trim($block)) as $line) {
                $u = self::validImage($line);
                if ($u !== null && !in_array($u,$urls,true)) $urls[]=$u;
            }
        }
        return $urls;
    }

    private static function newSummary($text, $photos)
    {
        $text = preg_replace('~\\s*\\[extimg\\].*?\\[/extimg\\]\\s*~isu', "\n", (string)$text);
        if ($text === null) throw new RuntimeException('Invalid existing extimg');
        $blocks = array();
        foreach ($photos as $url) $blocks[]="[extimg]\n".$url."\n[/extimg]";
        return trim(trim($text)."\n".implode("\n",$blocks));
    }

    private static function xmlPictures($outerXML)
    {
        $offer = @simplexml_load_string($outerXML,'SimpleXMLElement',LIBXML_NONET);
        if (!$offer) return array();
        $urls=array();
        $nodes=array();
        foreach ($offer->picture as $p) $nodes[]=(string)$p;
        if (isset($offer->images)) foreach ($offer->images->image as $p) $nodes[]=(string)$p;
        foreach ($nodes as $s) {
            $url=self::validImage($s);
            if ($url === null) continue;
            if (!in_array($url,$urls,true)) $urls[]=$url;
            if (count($urls)>self::MAX_PICTURES) return array();
        }
        return $urls;
    }

    private static function profile($db,$id)
    {
        $row=$db->query('SELECT id,name,config FROM shop_yml_profiles WHERE id='.(int)$id)->fetch();
        if (!$row) throw new RuntimeException('Profile not found');
        $c=json_decode($row['config'],true);
        if (!is_array($c)) throw new RuntimeException('Profile config invalid');
        $url=isset($c['url'])?trim((string)$c['url']):'';
        if ($url==='' && (int)$id===20 && trim((string)$row['name'])==='deephouse') {
            $url='https://deephouse.pro/upload/cutcat.xml';
        }
        $parts=$url?parse_url($url):array();
        if (!$parts || !is_array($parts) || empty($parts['host']) || empty($parts['scheme'])
            || !in_array(strtolower($parts['scheme']),array('http','https'),true)
            || !empty($parts['user']) || !empty($parts['pass'])
            || !empty($c['http_login']) || !empty($c['http_auth'])
            || (isset($c['source_type']) && $c['source_type']!=='remote')) {
            return array('id'=>(int)$id,'name'=>(string)$row['name'],'skip'=>'unsupported_source');
        }
        // Try secure version of the existing URL; never send image credentials anywhere.
        if (strtolower($parts['scheme'])==='http') $url='https://'.substr($url,7);
        return array('id'=>(int)$id,'name'=>(string)$row['name'],'url'=>$url,
            'mode'=>isset($c['images'])?(int)$c['images']:0);
    }

    private static function feedFile($url)
    {
        $file=tempnam(sys_get_temp_dir(),'yml-external-photo-');
        if (!$file) throw new RuntimeException('Temporary feed unavailable');
        $f=fopen($file,'wb'); $bytes=0;
        $ch=curl_init($url);
        curl_setopt_array($ch,array(
            CURLOPT_FOLLOWLOCATION=>false,CURLOPT_TIMEOUT=>120,CURLOPT_CONNECTTIMEOUT=>15,
            CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,
            CURLOPT_WRITEFUNCTION=>function($ch,$part)use($f,&$bytes){
                $bytes+=strlen($part);
                if ($bytes>self::FEED_MAX_BYTES) return 0;
                return fwrite($f,$part);
            }
        ));
        $ok=curl_exec($ch);
        $status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
        curl_close($ch);fclose($f);
        if (!$ok || $status!==200 || $bytes<300) {
            @unlink($file);
            throw new RuntimeException('Feed HTTPS unavailable (HTTP '.$status.')');
        }
        return $file;
    }
}

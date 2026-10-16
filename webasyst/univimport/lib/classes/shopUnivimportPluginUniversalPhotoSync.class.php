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
        // 33кровати and 4 Сезона are the same supplier. The already verified
        // published YML has matching offer IDs for all 2053 current offers.
        // Use only when the original profile URL is empty. Do not change YML PRO.
        if ($url==='' && (int)$id===289 && trim((string)$row['name'])==='33кровати'
            && isset($c['images']) && (int)$c['images']===4) {
            $url='https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/4s-mebel.yml';
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
    private static function mappedProducts($db, $profileId)
    {
        $map=array();$products=array();$badIds=array();
        $sql='SELECT m.yml_id,m.shop_id,p.summary,p.image_id FROM shop_yml_products m '.
            'JOIN shop_product p ON p.id=m.shop_id WHERE m.profile_id='.(int)$profileId;
        foreach($db->query($sql)->fetchAll() as $item){
            $key=(string)$item['yml_id'];$pid=(int)$item['shop_id'];
            if ($key==='' || isset($map[$key]) || isset($products[$pid])) {
                $badIds[$key]=true;
                continue;
            }
            $products[$pid]=true;
            $map[$key]=$item;
        }
        foreach($badIds as $key=>$flag) unset($map[$key]);
        $shared=array();
        $sql='SELECT DISTINCT a.shop_id FROM shop_yml_products a '.
          'JOIN shop_yml_products b ON b.shop_id=a.shop_id AND b.profile_id<>a.profile_id '.
          'WHERE a.profile_id='.(int)$profileId;
        foreach($db->query($sql)->fetchAll() as $row) $shared[(int)$row['shop_id']]=true;
        return array($map,$shared,count($badIds));
    }

    private static function snapshot($profileId,$changes)
    {
        $dir=wa()->getDataPath(self::BACKUP_DIR,false,'shop',true);
        if (!is_dir($dir)) waFiles::create($dir);
        $old=array();
        foreach ($changes as $item) $old[]=array('id'=>$item['id'],'summary'=>$item['old']);
        $encoded=json_encode(array('profile_id'=>$profileId,'created_at'=>gmdate('c'),
           'before'=>$old),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        if (!$encoded) throw new RuntimeException('Cannot encode rollback snapshot');
        $zip=gzencode($encoded,6);
        if (!$zip) throw new RuntimeException('Cannot compress rollback snapshot');
        $path=$dir.'/universal-photo-'.(int)$profileId.'-'.gmdate('Ymd-His').'-'.getmypid().'.json.gz';
        $tmp=$path.'.pending';
        if (file_put_contents($tmp,$zip,LOCK_EX)!==strlen($zip)) throw new RuntimeException('Backup cannot be saved');
        @chmod($tmp,0600);
        if (!rename($tmp,$path)) throw new RuntimeException('Backup cannot be finalized');
        return $path;
    }

    private static function plan($profileId,$profile,$db)
    {
        list($mapping,$shared,$bad)=self::mappedProducts($db,$profileId);
        if (!$mapping) throw new RuntimeException('No safe YML offer mappings');
        $source=self::feedFile($profile['url']);
        $reader=new XMLReader();
        $offers=0;$matched=0;$empty=0;$insecure=0;$conflicts=0;$same=0;$invalid=0;
        $changes=array();$seen=array();$sample=array();
        try {
            libxml_use_internal_errors(true);
            if (!$reader->open($source,null,LIBXML_NONET|LIBXML_COMPACT)) throw new RuntimeException('Invalid YML XML');
            $reader->setParserProperty(XMLReader::SUBST_ENTITIES,false);
            $reader->setParserProperty(XMLReader::VALIDATE,false);
            while($reader->read()){
                if($reader->nodeType!==XMLReader::ELEMENT || $reader->localName!=='offer')continue;
                if(++$offers>250000)throw new RuntimeException('Feed contains too many offers');
                $offerId=(string)$reader->getAttribute('id');
                if (!isset($mapping[$offerId]))continue;
                if(isset($seen[$offerId])){$invalid++;continue;}
                $seen[$offerId]=true;
                $matched++;
                $item=$mapping[$offerId];$productId=(int)$item['shop_id'];
                if(isset($shared[$productId])){$conflicts++;continue;}
                $photos=self::xmlPictures($reader->readOuterXML());
                if (!$photos){$empty++;continue;}
                $notSecure=false;
                foreach($photos as $photo){if(stripos($photo,'https://')!==0){$notSecure=true;break;}}
                if($notSecure){$insecure++;continue;}
                $summary=(string)$item['summary'];
                if(substr_count(strtolower($summary),'[extimg]')!==substr_count(strtolower($summary),'[/extimg]')){
                    $invalid++;continue;
                }
                $current=self::existingUrls($summary);
                if ($current===$photos){$same++;continue;}
                $new=self::newSummary($summary,$photos);
                if($new===$summary){$same++;continue;}
                $changes[]=array('id'=>$productId,'old'=>$summary,'new'=>$new,'count'=>count($photos));
                if (count($sample)<5)$sample[]=array('id'=>$productId,'photo_count'=>count($photos));
                if (count($changes)>21000)throw new RuntimeException('Too many updates in one profile');
            }
            $reader->close();
            $errors=libxml_get_errors();libxml_clear_errors();
            if(count($errors)>15)throw new RuntimeException('Malformed YML feed');
        } finally {
            $reader->close();
            @unlink($source);
        }
        $minMatched=max(1,min(30,(int)ceil(count($mapping)*0.1)));
        if ($matched<$minMatched)throw new RuntimeException('Unexpectedly low mapped offer coverage');
        return array('profile_id'=>$profileId,'profile_name'=>$profile['name'],'mapped'=>count($mapping),
          'offers'=>$offers,'matched'=>$matched,'eligible'=>$matched-$conflicts-$empty-$insecure-$invalid,
          'unchanged'=>$same,'changed'=>count($changes),'missing_photos'=>$empty,
          'insecure_images'=>$insecure,'cross_profile_skipped'=>$conflicts,
          'ambiguous_mapping'=>$bad,'duplicate_or_invalid'=>$invalid,
          'sample'=>$sample,'changes'=>$changes);
    }

    private static function testPhotos($changes)
    {
        if(!$changes)return true;
        // Validate one remote image as a guard against feed URLs pointing to error pages.
        $max=min(3,count($changes));
        for($i=0;$i<$max;$i++){
            preg_match('~\\[extimg\\]\\s*(https://[^\\s<]+)~',$changes[$i]['new'],$match);
            if(!isset($match[1]))return false;
            $ch=curl_init($match[1]);
            curl_setopt_array($ch,array(CURLOPT_NOBODY=>true,CURLOPT_RETURNTRANSFER=>true,
              CURLOPT_FOLLOWLOCATION=>false,CURLOPT_TIMEOUT=>12,CURLOPT_CONNECTTIMEOUT=>7,
              CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2));
            curl_exec($ch);
            $status=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
            $type=(string)curl_getinfo($ch,CURLINFO_CONTENT_TYPE);
            curl_close($ch);
            if ($status!==200 && $status!==206) return false;
            if ($type!=='' && stripos($type,'image/')!==0) return false;
        }
        return true;
    }

    public static function run($profileId,$apply=false)
    {
        $profileId=(int)$profileId;
        if($profileId<1)throw new RuntimeException('Invalid profile ID');
        $lockdir=wa()->getDataPath(self::LOCK_DIR,false,'shop',true);
        if(!is_dir($lockdir))waFiles::create($lockdir);
        $handle=fopen($lockdir.'/profile-'.$profileId.'.lock','c');
        if(!$handle)throw new RuntimeException('Cannot open profile sync lock');
        if(!flock($handle,LOCK_EX|LOCK_NB)){
            fclose($handle);
            return array('profile_id'=>$profileId,'status'=>'already_running');
        }
        try {
            $db=new waModel();
            $profile=self::profile($db,$profileId);
            if(isset($profile['skip']))return array('profile_id'=>$profileId,'status'=>'skipped',
                'reason'=>$profile['skip']);
            $plan=self::plan($profileId,$profile,$db);
            $result=array();
            foreach($plan as $key=>$value){
                if($key!=='changes')$result[$key]=$value;
            }
            $result['status']=$apply?'apply':'preview';
            $result['updated']=0;
            $result['concurrent_skipped']=0;
            if(!$apply || !$plan['changed'])return $result;
            if(!self::testPhotos($plan['changes'])){
                $result['status']='blocked';
                $result['reason']='photo_reachability_check_failed';
                return $result;
            }
            $backup=self::snapshot($profileId,$plan['changes']);
            $model=new shopProductModel();
            foreach($plan['changes'] as $item){
                $record=$model->getById($item['id']);
                if(!$record || (string)$record['summary']!==$item['old']){
                    $result['concurrent_skipped']++;
                    continue;
                }
                $model->updateById($item['id'],array('summary'=>$item['new']));
                $result['updated']++;
            }
            $result['backup_created']=is_file($backup);
            // Keep source configuration and native pictures intact.
            return $result;
        } finally {
            flock($handle,LOCK_UN);
            fclose($handle);
        }
    }

    public static function profiles()
    {
        $model=new waModel();
        $rows=$model->query('SELECT p.id,p.name,COUNT(m.shop_id) AS linked FROM shop_yml_profiles p '.
            'LEFT JOIN shop_yml_products m ON m.profile_id=p.id GROUP BY p.id,p.name ORDER BY p.id')->fetchAll();
        $result=array();
        foreach($rows as $row){
            $result[]=array('id'=>(int)$row['id'],'linked'=>(int)$row['linked']);
        }
        return $result;
    }

}

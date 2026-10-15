<?php
/**
 * Regression tests for compatibility with pureMegapolis42's active
 * [extimg] gallery: full-size image + every thumbnail, no media uploads.
 */
require_once dirname(__DIR__).'/lib/classes/shopUnivimportPluginImporter.class.php';

function verify($ok, $message)
{
    if (!$ok) {
        fwrite(STDERR, "FAIL: ".$message."\n");
        exit(1);
    }
}

$method = new ReflectionMethod('shopUnivimportPluginImporter', 'summaryImages');
$method->setAccessible(true);

$urls = array();
for ($i = 1; $i <= 11; $i++) {
    $urls[] = 'https://riva.ru/product-'.str_pad((string)$i, 2, '0', STR_PAD_LEFT).'.jpg';
}
$summary = $method->invoke(null, 'Описание товара', $urls);
verify(substr_count($summary, '[extimg]') === 11, '11 extimg opening blocks');
verify(substr_count($summary, '[/extimg]') === 11, '11 extimg closing blocks');
verify(strpos($summary, 'Описание товара') === 0, 'human-written summary preserved');

// Same regular expression used in the installed pureMegapolis42 parser.
preg_match_all('/\\[extimg\\](.*?)\\[\\/extimg\\]/is', $summary, $matches);
$extracted = array();
foreach ($matches[1] as $block) {
    foreach (explode("\n", str_replace("\r", "\n", $block)) as $line) {
        $url = trim($line);
        if ($url !== '' && preg_match('~^https?://~i', $url) && !in_array($url, $extracted, true)) {
            $extracted[] = $url;
        }
    }
}
verify($extracted === $urls, 'all 11 gallery images in original order');

// Repeated import does not append duplicates or destroy ordinary text.
$again = $method->invoke(null, $summary, $urls);
verify($again === $summary, 're-import is idempotent');
verify(substr_count($again, '[extimg]') === 11, 'no repeated photos');

// Legacy supplier format containing multiple lines in one [extimg] block.
$legacy = 'Текст до фото'."\n[extimg]\nhttps://old.ru/a.jpg\nhttps://old.ru/b.jpg\n[/extimg]\n".'Текст после фото';
$replaced = $method->invoke(null, $legacy, array('https://new.ru/a.jpg'));
verify(strpos($replaced, 'Текст до фото') !== false, 'text before retained');
verify(strpos($replaced, 'Текст после фото') !== false, 'text after retained');
verify(strpos($replaced, 'https://old.ru/') === false, 'legacy image URLs removed');
verify(substr_count($replaced, '[extimg]') === 1, 'only one new image');

// No image update must not change the original gallery.
verify($method->invoke(null, $legacy, array()) === $legacy, 'no images means no overwrite');
echo "External full-size gallery + all thumbnails: OK\n";

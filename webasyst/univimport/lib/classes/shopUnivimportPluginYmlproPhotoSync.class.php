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
}

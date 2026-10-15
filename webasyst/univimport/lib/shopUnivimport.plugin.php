<?php
class shopUnivimportPlugin extends shopPlugin
{
    public function backendExtendedMenu(&$params)
    {
        $params['menu']['univimport_yml'] = array(
            'name' => 'Импорт универсальный',
            'icon' => '<i class="fas fa-file-import"></i>',
            'url' => wa('shop')->getAppUrl(null, true).'?plugin=univimport',
        );
    }

    public function backendMenu()
    {
        $url = wa('shop')->getAppUrl(null, true).'?plugin=univimport';
        return array(
            'aux_li' => '<li><a href="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'">Импорт универсальный</a></li>',
        );
    }
}

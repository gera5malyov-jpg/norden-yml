<?php
class shopUnivimportPluginBackendAction extends waViewAction
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        $profiles = shopUnivimportPluginProfiles::all();
        $id = trim((string)waRequest::get('id', '', waRequest::TYPE_STRING_TRIM));
        $selected = isset($profiles[$id]) ? $profiles[$id] : shopUnivimportPluginProfiles::defaults();
        $this->view->assign(array(
            'profiles' => $profiles,
            'profile' => $selected,
            'report' => $id ? shopUnivimportPluginProfiles::report($id) : array(),
            'types' => (new shopTypeModel())->getAll(),
            'categories' => (new shopCategoryModel())->getAll(),
            'stocks' => (new shopStockModel())->getAll(),
            'base_url' => wa('shop')->getAppUrl(null, true).'?plugin=univimport',
            'flags' => array(
                'create' => 'Создавать новые товары скрытыми',
                'update' => 'Обновлять свои существующие товары',
                'update_name' => 'Обновлять название',
                'update_description' => 'Обновлять полное описание',
                'update_images' => 'Обновлять ссылки [extimg] в кратком описании',
                'update_prices' => 'Обновлять цены продажи',
                'update_stocks' => 'Обновлять остаток выбранного склада',
                'allow_write' => 'РАЗРЕШИТЬ ЗАПИСЬ В КАТАЛОГ (после проверки)',
            ),
        ));
    }
}

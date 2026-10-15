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
        ));
    }
}

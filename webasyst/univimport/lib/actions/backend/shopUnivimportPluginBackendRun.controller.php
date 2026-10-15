<?php
class shopUnivimportPluginBackendRunController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        try {
            $id = (string)waRequest::post('id', '', waRequest::TYPE_STRING_TRIM);
            $profile = shopUnivimportPluginProfiles::get($id);
            if (!$profile) {
                throw new InvalidArgumentException('Сначала сохраните профиль.');
            }
            $mode = (string)waRequest::post('mode', '', waRequest::TYPE_STRING_TRIM);
            if ($mode === 'preview') {
                $this->response = shopUnivimportPluginImporter::preview($profile);
            } elseif ($mode === 'apply') {
                $offset = waRequest::post('offset', 0, waRequest::TYPE_INT);
                $this->response = shopUnivimportPluginImporter::apply($profile, $offset);
            } else {
                throw new InvalidArgumentException('Неизвестный режим.');
            }
        } catch (Exception $e) {
            waLog::log(date('c').' '.$e->getMessage(), 'univimport-errors.log');
            $this->errors[] = $e->getMessage();
        }
    }
}

<?php
class shopUnivimportPluginBackendSaveController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        try {
            $input = waRequest::post('profile', array(), waRequest::TYPE_ARRAY);
            if (!is_array($input)) {
                throw new InvalidArgumentException('Форма повреждена.');
            }
            // No silent modification of a profile under a different identifier.
            $original = (string)waRequest::post('original_id', '', waRequest::TYPE_STRING_TRIM);
            if ($original !== '' && (!isset($input['id']) || $original !== $input['id'])) {
                throw new InvalidArgumentException('ID существующего профиля менять нельзя. Создайте новый профиль.');
            }
            $saved = shopUnivimportPluginProfiles::save($input);
            $this->response = array('id' => $saved['id'], 'name' => $saved['name']);
        } catch (Exception $e) {
            $this->errors[] = $e->getMessage();
        }
    }
}

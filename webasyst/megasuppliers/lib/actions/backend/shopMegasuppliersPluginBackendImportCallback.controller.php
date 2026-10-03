<?php
class shopMegasuppliersPluginBackendImportCallbackController extends waJsonController
{
    public function execute()
    {
        $this->errors = array('CALLBACK_MOVED_TO_SIGNED_FRONTEND_ENDPOINT');
        $this->getResponse()->setStatus(410);
    }
}

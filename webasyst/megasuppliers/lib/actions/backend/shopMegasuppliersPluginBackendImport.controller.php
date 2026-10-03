<?php
class shopMegasuppliersPluginBackendImportController extends waController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Недостаточно прав для управления поставщиками.');
        }
        $supplier_id = waRequest::post('supplier_id', 0, waRequest::TYPE_INT);
        if (!$supplier_id) {
            $this->redirectError('Перед импортом обязательно выберите поставщика.');
            return;
        }
        if (waRequest::post('create_missing', 0, waRequest::TYPE_INT)) {
            $this->redirectError('Создание новых товаров через старый ручной импорт отключено. Используйте автоматический профиль поставщика с явными type_id и stock_id.');
            return;
        }
        $file = waRequest::file('file');
        if (!$file || !$file->uploaded()) {
            $this->redirectError('Выберите XLSX или CSV-файл.');
            return;
        }
        try {
            $rows = shopMegasuppliersFileReader::read($file->tmp_name, $file->name);
            $service = new shopMegasuppliersImportService();
            $stats = $service->importRows($supplier_id, $rows, array(
                'source' => 'file',
                'filename' => $file->name,
                'create_missing' => false,
                'update_catalog' => (bool)waRequest::post('update_catalog', 0, waRequest::TYPE_INT),
            ));
            $msg = sprintf(
                'Импорт завершён: создано %d, обновлено %d, привязано %d, пропущено %d, ошибок %d.',
                $stats['created'],
                $stats['updated'],
                $stats['linked'],
                $stats['skipped'],
                count($stats['errors'])
            );
            wa()->getResponse()->redirect(wa('shop')->getAppUrl(null, true).'?plugin=megasuppliers&msg='.urlencode($msg));
        } catch (Exception $e) {
            $this->redirectError($e->getMessage());
        }
    }

    private function redirectError($msg)
    {
        wa()->getResponse()->redirect(wa('shop')->getAppUrl(null, true).'?plugin=megasuppliers&err='.urlencode($msg));
    }
}

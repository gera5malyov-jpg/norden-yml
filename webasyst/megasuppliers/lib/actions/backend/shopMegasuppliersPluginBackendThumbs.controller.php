<?php
class shopMegasuppliersPluginBackendThumbsController extends waJsonController
{
    public function execute()
    {
        $raw = trim((string)waRequest::get('ids', '', waRequest::TYPE_STRING));
        if ($raw === '') {
            $this->response = array('items' => array());
            return;
        }

        $ids = array();
        foreach (preg_split('/\s*,\s*/', $raw, -1, PREG_SPLIT_NO_EMPTY) as $id) {
            $id = (int)$id;
            if ($id > 0) {
                $ids[$id] = $id;
            }
            if (count($ids) >= 100) {
                break;
            }
        }
        if (!$ids) {
            $this->response = array('items' => array());
            return;
        }

        $product_model = new shopProductModel();
        $allowed_ids = array();
        foreach ($ids as $id) {
            if ($product_model->checkRights($id)) {
                $allowed_ids[$id] = $id;
            }
        }
        if (!$allowed_ids) {
            $this->response = array('items' => array());
            return;
        }

        $model = new waModel();
        $rows = $model->query(
            "SELECT id,summary,image_id FROM shop_product WHERE id IN (i:ids)",
            array('ids' => array_values($allowed_ids))
        )->fetchAll('id');

        $native = array();
        $native_rows = $model->query(
            "SELECT DISTINCT product_id FROM shop_product_images WHERE product_id IN (i:ids)",
            array('ids' => array_values($allowed_ids))
        )->fetchAll();
        foreach ($native_rows as $row) {
            $native[(int)$row['product_id']] = true;
        }

        $items = array();
        foreach ($rows as $id => $row) {
            $id = (int)$id;
            if (!empty($row['image_id']) || isset($native[$id])) {
                continue;
            }
            $url = $this->extractFirstExtImage((string)$row['summary']);
            if ($url !== '') {
                $items[(string)$id] = $url;
            }
        }

        $this->response = array('items' => $items);
    }

    private function extractFirstExtImage($summary)
    {
        if ($summary === '' || stripos($summary, '[extimg]') === false) {
            return '';
        }
        $summary = html_entity_decode($summary, ENT_QUOTES, 'UTF-8');
        if (!preg_match('~\[extimg\]\s*(https?://[^\s<\[]+)~iu', $summary, $m)) {
            return '';
        }
        $url = trim($m[1]);
        if (!filter_var($url, FILTER_VALIDATE_URL)) {
            return '';
        }
        $scheme = strtolower((string)parse_url($url, PHP_URL_SCHEME));
        if ($scheme !== 'http' && $scheme !== 'https') {
            return '';
        }
        return $url;
    }
}

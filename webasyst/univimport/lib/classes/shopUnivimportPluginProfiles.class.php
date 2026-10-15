<?php
/**
 * All profiles and dry-run reports are private to this plugin.
 * The commercial YML PRO plugin is never read or modified.
 */
class shopUnivimportPluginProfiles
{
    public static function dir()
    {
        $dir = wa()->getDataPath('plugins/univimport', false, 'shop', true);
        waFiles::create($dir);
        return $dir;
    }

    public static function profilePath()
    {
        return self::dir().'/profiles.json';
    }

    public static function all()
    {
        $path = self::profilePath();
        if (!is_file($path)) {
            return array();
        }
        $value = json_decode(file_get_contents($path), true);
        return is_array($value) ? $value : array();
    }

    public static function get($id)
    {
        $all = self::all();
        return isset($all[$id]) ? $all[$id] : null;
    }

    public static function defaults()
    {
        return array(
            'id' => '',
            'name' => '',
            'url' => '',
            'sku_prefix' => '',
            'type_id' => 0,
            'category_id' => 0,
            'stock_id' => 0,
            'create' => 1,
            'update' => 1,
            'update_name' => 1,
            'update_description' => 1,
            'update_images' => 1,
            'update_prices' => 1,
            'update_stocks' => 0,
            'allow_write' => 0,
        );
    }

    public static function normalize($raw)
    {
        $p = array_merge(self::defaults(), (array)$raw);
        $p['id'] = preg_replace('/[^a-z0-9_-]/', '', strtolower(trim((string)$p['id'])));
        if (strlen($p['id']) > 40 || $p['id'] === '') {
            throw new InvalidArgumentException('Укажите ID профиля латинскими буквами, цифрами, _ или - (до 40 символов).');
        }
        $p['name'] = mb_substr(trim((string)$p['name']), 0, 120);
        if ($p['name'] === '') {
            throw new InvalidArgumentException('Не указано название профиля.');
        }
        $p['url'] = trim((string)$p['url']);
        self::checkUrl($p['url']);
        $p['sku_prefix'] = mb_substr(trim((string)$p['sku_prefix']), 0, 35);
        foreach (array('type_id', 'category_id', 'stock_id') as $key) {
            $p[$key] = max(0, (int)$p[$key]);
        }
        foreach (array('create', 'update', 'update_name', 'update_description', 'update_images', 'update_prices', 'update_stocks', 'allow_write') as $key) {
            $p[$key] = !empty($p[$key]) ? 1 : 0;
        }
        if (!$p['type_id']) {
            throw new InvalidArgumentException('Для изоляции товаров обязательно выберите тип товара Webasyst.');
        }
        if ($p['update_stocks'] && !$p['stock_id']) {
            throw new InvalidArgumentException('Для обновления остатков выберите склад.');
        }
        return $p;
    }

    public static function checkUrl($url)
    {
        $parts = parse_url($url);
        if (!$parts || empty($parts['scheme']) || strtolower($parts['scheme']) !== 'https'
            || empty($parts['host']) || !empty($parts['user']) || !empty($parts['pass'])
            || (isset($parts['port']) && (int)$parts['port'] !== 443)) {
            throw new InvalidArgumentException('Источник должен иметь прямой HTTPS-адрес без логина и нестандартного порта.');
        }
        $host = strtolower($parts['host']);
        if ($host === 'localhost' || substr($host, -6) === '.local') {
            throw new InvalidArgumentException('Локальные хосты запрещены.');
        }
        $records = gethostbynamel($host);
        if (!is_array($records) || !$records) {
            throw new InvalidArgumentException('Не удалось определить IP источника.');
        }
        foreach ($records as $ip) {
            if (!filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) {
                throw new InvalidArgumentException('Нельзя загружать YML с внутреннего адреса.');
            }
        }
    }

    public static function save($input)
    {
        $p = self::normalize($input);
        $all = self::all();
        // Protect another profile from accidental rename/collision by stable ID.
        $all[$p['id']] = $p;
        $json = json_encode($all, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
        $path = self::profilePath();
        $tmp = $path.'.'.getmypid().'.tmp';
        if (file_put_contents($tmp, $json, LOCK_EX) === false || !rename($tmp, $path)) {
            throw new RuntimeException('Не удалось сохранить настройки.');
        }
        return $p;
    }

    public static function reportPath($id)
    {
        if (!preg_match('/^[a-z0-9_-]{1,40}$/', (string)$id)) {
            throw new InvalidArgumentException('Некорректный ID профиля.');
        }
        return self::dir().'/report-'.$id.'.json';
    }

    public static function report($id)
    {
        $path = self::reportPath($id);
        if (!is_file($path)) {
            return array();
        }
        $result = json_decode(file_get_contents($path), true);
        return is_array($result) ? $result : array();
    }

    public static function saveReport($id, $data)
    {
        $path = self::reportPath($id);
        if (file_put_contents($path, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), LOCK_EX) === false) {
            throw new RuntimeException('Не удалось сохранить результат проверки.');
        }
    }
}

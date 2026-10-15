<?php
return array(
    'name' => 'Импорт универсальный',
    'description' => 'Независимый YML-импорт с внешними изображениями в основной галерее витрины без скачивания на сервер.',
    'version' => '0.2.0',
    'vendor' => 'megapolis',
    'img' => 'img/icon.svg',
    'handlers' => array(
        'backend_extended_menu' => 'backendExtendedMenu',
        'backend_menu' => 'backendMenu',
    ),
);

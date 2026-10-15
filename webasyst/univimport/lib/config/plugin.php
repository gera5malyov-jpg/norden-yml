<?php
return array(
    'name' => 'Импорт универсальный',
    'description' => 'Независимый импорт YML в Shop-Script с хранением изображений внешними ссылками [extimg].',
    'version' => '0.1.0',
    'vendor' => 'megapolis',
    'img' => 'img/icon.svg',
    'handlers' => array(
        'backend_extended_menu' => 'backendExtendedMenu',
        'backend_menu' => 'backendMenu',
    ),
);

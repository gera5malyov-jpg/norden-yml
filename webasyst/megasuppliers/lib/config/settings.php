<?php
return array(
    'github_repo' => array(
        'title' => 'GitHub repository',
        'description' => 'Репозиторий в формате owner/repository.',
        'control_type' => waHtmlControl::INPUT,
        'value' => 'gera5malyov-jpg/norden-yml',
    ),
    'github_ref' => array(
        'title' => 'GitHub branch/ref',
        'description' => 'После проверки используйте main. Тестовую ветку можно указать отдельно.',
        'control_type' => waHtmlControl::INPUT,
        'value' => 'main',
    ),
    'github_token' => array(
        'title' => 'GitHub token',
        'description' => 'Токен хранится только в настройках Webasyst и не передаётся в workflow inputs.',
        'control_type' => waHtmlControl::PASSWORD,
        'value' => '',
    ),
    'callback_secret' => array(
        'title' => 'Callback secret',
        'description' => 'Должен совпадать с GitHub Actions secret MEGASUPPLIERS_CALLBACK_SECRET.',
        'control_type' => waHtmlControl::PASSWORD,
        'value' => '',
    ),
    'enable_writes' => array(
        'title' => 'Разрешить запись в каталог',
        'description' => 'По умолчанию выключено. При включении применение всё равно возможно только после свежего успешного dry-run того же прайса и конфигурации.',
        'control_type' => waHtmlControl::CHECKBOX,
        'value' => 0,
    ),
    'apply_window_minutes' => array(
        'title' => 'Срок действия проверки, минут',
        'description' => 'После истечения срока нужно снова выполнить dry-run.',
        'control_type' => waHtmlControl::INPUT,
        'value' => '120',
    ),
);

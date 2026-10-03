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
);

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    kit_token: str
    feed_url: str = 'https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml'
    warehouse_names: tuple[str, str] = ('СПБ', 'МСК')
    kit_base_url: str = 'https://api.kit.yandex.net'

    @classmethod
    def from_env(cls):
        token = os.environ.get('YANDEX_KIT_TOKEN', '').strip()
        if not token:
            raise RuntimeError('YANDEX_KIT_TOKEN is not configured')
        return cls(kit_token=token)

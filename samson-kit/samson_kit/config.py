from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    samson_api_key: str
    kit_token: str
    target_warehouse: str = 'СПБ'
    samson_base_url: str = 'https://api.samsonopt.ru/v1'
    kit_base_url: str = 'https://api.kit.yandex.net'

    @classmethod
    def from_env(cls):
        samson = os.environ.get('SAMSON_API_KEY', '').strip()
        kit = os.environ.get('YANDEX_KIT_TOKEN', '').strip()
        if not samson:
            raise RuntimeError('SAMSON_API_KEY is not configured')
        if not kit:
            raise RuntimeError('YANDEX_KIT_TOKEN is not configured')
        return cls(samson_api_key=samson, kit_token=kit)

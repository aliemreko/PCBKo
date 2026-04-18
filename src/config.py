from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_model: str
    deepseek_base_url: str
    ui_lang: str = "en"
    # Optional vision API for multimodal review
    vision_api_key: str = ""
    vision_api_url: str = ""
    vision_model: str = ""


    @staticmethod
    def from_env() -> "Settings":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-v3.2").strip()
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        ui_lang = os.getenv("UI_LANG", "tr").strip().lower()

        # Optional vision API
        vision_key = os.getenv("VISION_API_KEY", "").strip()
        vision_url = os.getenv("VISION_API_URL", "").strip()
        vision_model = os.getenv("VISION_MODEL", "gpt-4o").strip()

        if not api_key:
            # We allow empty key now because user can set it in GUI
            pass

        return Settings(
            deepseek_api_key=api_key,
            deepseek_model=model,
            deepseek_base_url=base_url,
            ui_lang=ui_lang,
            vision_api_key=vision_key,
            vision_api_url=vision_url,
            vision_model=vision_model,
        )

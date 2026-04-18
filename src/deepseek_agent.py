from __future__ import annotations

import json
from textwrap import dedent

from openai import OpenAI
from openai import BadRequestError

from .config import Settings
from .models import DesignPlan, ProjectSpec


class DeepSeekPcbAgent:
    def __init__(self, settings: Settings) -> None:
        self.client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.model = settings.deepseek_model

    def create_design_plan(self, spec: ProjectSpec) -> DesignPlan:
        system_prompt = dedent(
            """
            You are an expert PCB design agent.
            Produce a practical KiCad-compatible design plan.

            Requirements:
            - Return STRICT JSON only.
            - JSON must match this schema:
              {
                "title": string,
                "assumptions": string[],
                "components": [
                  {
                    "ref": string,
                    "value": string,
                    "notes": string
                  }
                ],
                "nets": [
                  {
                    "net_name": string,
                    "nodes": string[]
                  }
                ],
                "placement_hints": [
                  {
                    "target": string,
                    "hint": string
                  }
                ],
                "design_checks": string[]
              }
            - Do NOT output footprint or symbol fields — they will be auto-assigned.
            - Components must be minimal IDs + values only.
            - The "value" field must contain the exact part name (e.g. "ESP32-C3", "DHT22", "LM358", "2N3904").
            - For microcontroller modules use their exact module name as value (ESP32-C3, ESP32-S3, ATmega328P, STM32F103, etc.).
            - For sensors use exact sensor name (DHT22, DHT11, BME280, etc.).
            - Nodes must use format REF.PIN with NUMERIC pin numbers (example: U1.1, R3.2, U2.3).
            - For ESP32 modules use pin numbers 1-19 (matching WROOM module pinout).
            - For DHT22/DHT11 sensors use pins 1-4 (1=VCC, 2=DATA, 3=NC, 4=GND).
            - Include GND and power nets explicitly.
            - Keep it electrically coherent and manufacturable.
            """
        ).strip()

        user_prompt = f"Project spec JSON:\n{spec.model_dump_json(indent=2)}"

        models_to_try: list[str] = []
        for candidate in [self.model, "deepseek-chat", "deepseek-reasoner"]:
          if candidate and candidate not in models_to_try:
            models_to_try.append(candidate)

        response = None
        last_error: Exception | None = None
        for model_name in models_to_try:
          try:
            response = self.client.chat.completions.create(
              model=model_name,
              temperature=0.2,
              response_format={"type": "json_object"},
              messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
              ],
            )
            break
          except BadRequestError as exc:
            last_error = exc
            message = str(exc).lower()
            if "model not exist" in message:
              continue
            raise

        if response is None:
          if last_error is not None:
            raise last_error
          raise RuntimeError("DeepSeek yanıtı alınamadı.")

        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        return DesignPlan.model_validate(payload)

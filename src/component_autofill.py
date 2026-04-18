from __future__ import annotations

import re

from .footprint_finder import find_component as _auto_find
from .models import Component, DesignPlan


def _is_electrolytic(value: str) -> bool:
    v = value.lower().replace(" ", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)u", v)
    if not match:
        return False
    try:
        return float(match.group(1)) >= 1.0
    except ValueError:
        return False


def _connector_pin_count(value: str) -> int:
    match = re.search(r"0*1x0*([0-9]+)", value.lower())
    if match:
        return max(2, min(16, int(match.group(1))))
    return 3


# ── Well-known IC / module footprint map ──────────────────────────────
_IC_FOOTPRINT_MAP: list[tuple[str, str, str]] = [
    # (value_pattern, symbol, footprint)  — value_pattern is matched case-insensitively
    ("esp32-c3",   "RF_Module:ESP32-C3-WROOM-02",  "RF_Module:ESP32-C3-WROOM-02"),
    ("esp32-s3",   "RF_Module:ESP32-S3-WROOM-1",   "RF_Module:ESP32-S3-WROOM-1"),
    ("esp32-s2",   "RF_Module:ESP32-S2-MINI-1",    "RF_Module:ESP32-S2-MINI-1"),
    ("esp32",      "RF_Module:ESP32-WROOM-32",     "RF_Module:ESP32-WROOM-32"),
    ("esp8266",    "RF_Module:ESP-12E",            "RF_Module:ESP-12E"),
    ("dht22",      "Sensor:DHT11",                 "Sensor:Aosong_DHT11_5.5x12.0_P2.54mm"),
    ("dht11",      "Sensor:DHT11",                 "Sensor:Aosong_DHT11_5.5x12.0_P2.54mm"),
    ("lm358",      "Amplifier_Operational:LM358",  "Package_DIP:DIP-8_W7.62mm"),
    ("ne555",      "Timer:NE555P",                 "Package_DIP:DIP-8_W7.62mm"),
    ("555",        "Timer:NE555P",                 "Package_DIP:DIP-8_W7.62mm"),
    ("lm7805",     "Regulator_Linear:L7805",       "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("7805",       "Regulator_Linear:L7805",       "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("ams1117",    "Regulator_Linear:AMS1117-3.3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2"),
    ("atmega328",  "MCU_Microchip_ATmega:ATmega328P-PU", "Package_DIP:DIP-28_W7.62mm"),
    ("stm32",      "MCU_ST_STM32:STM32F103C8Tx",   "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
    ("2n3904",     "Transistor_BJT:2N3904",        "Package_TO_SOT_THT:TO-92_Inline"),
    ("2n2222",     "Transistor_BJT:2N2222",        "Package_TO_SOT_THT:TO-92_Inline"),
    ("bc547",      "Transistor_BJT:BC547",         "Package_TO_SOT_THT:TO-92_Inline"),
    ("irf540",     "Transistor_FET:IRF540N",       "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("74hc595",    "74xx:74HC595",                 "Package_DIP:DIP-16_W7.62mm"),
    ("74hc574",    "74xx:74HC574",                 "Package_DIP:DIP-20_W7.62mm"),
    ("74hc164",    "74xx:74HC164",                 "Package_DIP:DIP-14_W7.62mm"),
    ("74hc04",     "74xx:74HC04",                  "Package_DIP:DIP-14_W7.62mm"),
    ("max232",     "Interface_UART:MAX232",        "Package_DIP:DIP-16_W7.62mm"),
    ("cd4017",     "4xxx:CD4017",                  "Package_DIP:DIP-16_W7.62mm"),
    ("cd4060",     "4xxx:CD4060",                  "Package_DIP:DIP-16_W7.62mm"),
    ("pcf8574",    "Interface_Expansion:PCF8574",  "Package_DIP:DIP-16_W7.62mm"),
    ("ssd1306",    "Display_Graphic:SSD1306",      "Display_Module:OLED-SSD1306-128x64"),
    ("bme280",     "Sensor:BME280",                "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering"),
    ("mpu6050",    "Sensor_Motion:MPU-6050",       "Sensor_Motion:InvenSense_QFN-24_4x4mm_P0.5mm"),
    ("ds18b20",    "Sensor_Temperature:DS18B20",   "Package_TO_SOT_THT:TO-92_Inline"),
    ("nrf52832",   "MCU_Nordic:nRF52832-QFxx",     "Package_DFN_QFN:QFN-48-1EP_6x6mm_P0.4mm_EP4.6x4.6mm"),
    ("w25q32",     "Memory_Flash:W25Q32JVSS",      "Package_SO:SOIC-8_5.23x5.23mm_P1.27mm"),
]


def _lookup_ic(value: str) -> tuple[str, str] | None:
    """Return (symbol, footprint) for a known IC / module value string."""
    low = value.lower().replace(" ", "").replace("_", "")
    for pattern, sym, fp in _IC_FOOTPRINT_MAP:
        if pattern.replace("-", "").replace("_", "") in low.replace("-", ""):
            return sym, fp
    return None


def _autofill_component(comp: Component) -> Component:
    ref = comp.ref.strip().upper()
    value = comp.value.strip() or "GEN"

    symbol = comp.symbol.strip()
    footprint = comp.footprint.strip()

    prefix = ref[:1] if ref else ""

    # ── Try well-known IC / module lookup first ─────────────────
    ic_match = _lookup_ic(value)

    if not symbol:
        if ic_match:
            symbol = ic_match[0]
        elif prefix == "R":
            symbol = "Device:R"
        elif prefix == "C":
            symbol = "Device:C"
        elif prefix == "D":
            symbol = "Device:LED"
        elif prefix == "Q":
            ic_q = _lookup_ic(value)
            symbol = ic_q[0] if ic_q else "Transistor_BJT:NPN"
        elif prefix == "U":
            symbol = "Device:U"
        elif prefix == "J":
            pin_count = _connector_pin_count(value)
            symbol = f"Connector:Conn_01x{pin_count:02d}_Male"
        else:
            symbol = "Device:R"

    # ── Footprint: always override DIP-8 if IC is known to be something else
    if ic_match:
        # If DeepSeek wrongly assigned DIP-8 to a non-DIP-8 part, fix it
        if not footprint or footprint == "Package_DIP:DIP-8_W7.62mm":
            footprint = ic_match[1]

    if not footprint:
        if prefix == "R":
            footprint = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"
        elif prefix == "C":
            if _is_electrolytic(value):
                footprint = "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm"
            else:
                footprint = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm"
        elif prefix == "D":
            footprint = "LED_THT:LED_D5.0mm"
        elif prefix == "Q":
            footprint = "Package_TO_SOT_THT:TO-92_Inline"
        elif prefix == "U":
            footprint = "Package_DIP:DIP-8_W7.62mm"
        elif prefix == "J":
            pin_count = _connector_pin_count(value)
            footprint = f"Connector_PinHeader_2.54mm:PinHeader_1x{pin_count:02d}_P2.54mm_Vertical"
        else:
            footprint = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"

    # ── Auto-discovery fallback: search KiCad libraries if still generic ──
    _generic_fps = {
        "Package_DIP:DIP-8_W7.62mm",
        "Device:U", "Device:R", "",
    }
    if footprint in _generic_fps or symbol in ("Device:U", ""):
        try:
            auto_sym, auto_fp = _auto_find(value, prefix)
            if auto_fp and footprint in _generic_fps:
                footprint = auto_fp
            if auto_sym and symbol in ("Device:U", ""):
                symbol = auto_sym
        except Exception:
            pass  # auto-discovery is best-effort

    return comp.model_copy(update={"symbol": symbol, "footprint": footprint, "value": value})


def autofill_components(plan: DesignPlan) -> DesignPlan:
    filled = [_autofill_component(comp) for comp in plan.components]
    return plan.model_copy(update={"components": filled})

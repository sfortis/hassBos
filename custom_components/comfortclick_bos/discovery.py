"""Entity auto-discovery for ComfortClick bOS.

Walks the whole navigation tree (GetTheme -> GetPanel per panel) and classifies
every control into a Home Assistant entity descriptor:

- dimmer light: IntegerControl + ValueTemplate "Dimmer" + writable
- on/off light: BooleanControl + writable + ("Lamp" template or lamp icon)
- sensor:       Double/IntegerControl + read-only (unit -> device_class)
- binary sensor: BooleanControl + read-only (template -> device_class)

Some devices hide behind a button whose FormPanel is fetched via GetDeviceForm:
- Air Quality forms  -> CO2/PM/VOC sensors
- Air Conditioning forms -> one climate entity per A/C unit

Read-only over the network: GetTheme + GetPanel + GetDeviceForm. Never writes.
"""

from __future__ import annotations

import logging

from .api import BosClient, BosError
from .const import (
    ENT_DEVICE_CLASS,
    ENT_DIAGNOSTIC,
    ENT_ICON,
    ENT_FAN,
    ENT_FAN_MAP,
    ENT_FORM,
    ENT_KIND,
    ENT_MAX,
    ENT_MIN,
    ENT_MODE,
    ENT_MODE_MAP,
    ENT_NAME,
    ENT_OBJECT,
    ENT_ONOFF,
    ENT_OPTIONS,
    ENT_PANEL,
    ENT_PANEL_PATH,
    ENT_SETPOINT,
    ENT_STATE_CLASS,
    ENT_TEMP,
    ENT_UNIT,
    KIND_BINARY,
    KIND_CLIMATE,
    KIND_DIMMER,
    KIND_RGB,
    KIND_SELECT,
    KIND_SENSOR,
    KIND_SWITCH,
)

_LOGGER = logging.getLogger(__name__)

_THEMES_PREFIX = "Themes\\"
_AQ_FORM = "Air Quality"
_AC_FORM = "Air Conditioning"
_VENT_FORM = "Ventilation"

_UNIT_MAP: dict[str, tuple[str | None, str]] = {
    "°C": ("temperature", "°C"),
    "W": ("power", "W"),
    "kW": ("power", "kW"),
    "kWh": ("energy", "kWh"),
    "V": ("voltage", "V"),
    "A": ("current", "A"),
    "mA": ("current", "mA"),
    "Pa": ("pressure", "Pa"),
    "lux": ("illuminance", "lx"),
    "lx": ("illuminance", "lx"),
    "m/s": ("wind_speed", "m/s"),
    "Hz": ("frequency", "Hz"),
    "€": ("monetary", "EUR"),
}

_BINARY_MAP: dict[str, str] = {
    "Movement": "motion",
    "Rain": "moisture",
    "Alert": "problem",
    "Strong Wind": "safety",
}


def _iter_controls(controls: list | None):
    for control in controls or []:
        yield control
        nested = control.get("Controls")
        if isinstance(nested, list):
            yield from _iter_controls(nested)


def _object_name(control: dict) -> str | None:
    return (control.get("ObjectValue") or {}).get("ObjectName") or None


def _has_lamp_icon(control: dict) -> bool:
    icon = (control.get("Image") or {}).get("ObjectName", "") or ""
    fore = (control.get("StatusBarForeColor") or {}).get("ObjectName", "")
    return "Lamp" in icon or "Bulb" in icon or fore == "Lamp"


def _form_target(control: dict) -> str | None:
    """The GetDeviceForm object this control opens, if it is an AQ/AC button."""
    if control.get("EnableForm") is not True:
        return None
    form = (control.get("FormPanel") or {}).get("ObjectName") or ""
    return form if any(k in form for k in (_AQ_FORM, _AC_FORM, _VENT_FORM)) else None


def _enum_map(control: dict) -> dict[str, str]:
    """{bOS index (str): text} from a control's StatusValues."""
    result: dict[str, str] = {}
    for option in control.get("StatusValues") or []:
        value, text = option.get("Value"), option.get("Text")
        if value is None or not text:
            continue
        try:
            result[str(int(value))] = text
        except (TypeError, ValueError):
            continue  # skip a malformed option instead of aborting discovery
    return result


def _aqm_class(name: str, unit: str | None) -> tuple[str | None, str | None, str | None]:
    """(device_class, unit, icon) for an air-quality reading, keyed by name."""
    low = name.lower()
    if "co2" in low:
        return "carbon_dioxide", "ppm", None
    if "pm2" in low:
        return "pm25", unit or "µg/m³", None
    if "pm10" in low:
        return "pm10", unit or "µg/m³", None
    if "pm1" in low:
        return "pm1", unit or "µg/m³", None
    if "voc" in low:
        # No standard device_class/unit (it is an index), so give it an icon.
        return None, unit or None, "mdi:chemical-weapon"
    device_class, mapped_unit = _UNIT_MAP.get(unit or "", (None, unit or None))
    return device_class, mapped_unit, None


def _classify(control: dict) -> tuple[str, dict] | None:
    """Return (kind, extra fields) for a plain control, or None."""
    if not _object_name(control):
        return None
    ui = control.get("UIControlType", "")
    template = control.get("ValueTemplate")
    settable = control.get("ButtonSettable") is True

    if ui == "BOSTheme.Controls.ColorPickerControl" and settable:
        return KIND_RGB, {}
    if ui == "BOSTheme.Controls.IntegerControl" and settable and template == "Dimmer":
        return KIND_DIMMER, {
            ENT_MIN: control.get("MinValue"),
            ENT_MAX: control.get("MaxValue"),
        }
    if ui == "BOSTheme.Controls.BooleanControl" and settable:
        if template == "Lamp" or _has_lamp_icon(control):
            return KIND_SWITCH, {}
        return None
    if ui in ("BOSTheme.Controls.DoubleControl", "BOSTheme.Controls.IntegerControl"):
        device_class, unit = _UNIT_MAP.get(
            control.get("Unit") or "", (None, control.get("Unit") or None)
        )
        return KIND_SENSOR, {
            ENT_UNIT: unit,
            ENT_DEVICE_CLASS: device_class,
            ENT_STATE_CLASS: "measurement",
        }
    if ui == "BOSTheme.Controls.BooleanControl":
        if _form_target(control):
            return None  # form-opener button, handled via GetDeviceForm
        return KIND_BINARY, {ENT_DEVICE_CLASS: _BINARY_MAP.get(template or "")}
    return None


async def async_discover_entities(client: BosClient) -> list[dict]:
    """Return entity descriptors from the whole navigation tree."""
    theme = await client.get_theme()
    host = theme.get("Host", {}) or {}
    if not host:
        _LOGGER.warning("GetTheme returned no navigation host; cannot discover")
        return []

    entities: dict[str, dict] = {}
    seen_panels: set[str] = set()
    seen_forms: set[str] = set()
    await _walk(client, host, entities, seen_panels, seen_forms)
    _LOGGER.debug(
        "Discovered %d entities across %d panels", len(entities), len(seen_panels)
    )
    return list(entities.values())


async def _walk(
    client: BosClient,
    node: dict,
    entities: dict[str, dict],
    seen: set[str],
    seen_forms: set[str],
) -> None:
    raw_path = node.get("Path", "") or ""
    panel_path = raw_path.removeprefix(_THEMES_PREFIX)
    panel_name = node.get("Text") or panel_path.split("\\")[-1]

    if panel_path and panel_path not in seen:
        seen.add(panel_path)
        try:
            panel = await client.get_panel(panel_path)
        except BosError as err:
            _LOGGER.debug("Skipping panel %r: %s", panel_path, err)
            panel = None
        if panel:
            forms = _extract(panel, panel_name, panel_path, entities)
            for form_obj in forms:
                if form_obj in seen_forms:
                    continue
                seen_forms.add(form_obj)
                await _fetch_form(client, form_obj, panel_name, panel_path, entities)

    for child in node.get("Nodes", []) or []:
        await _walk(client, child, entities, seen, seen_forms)


def _extract(
    panel: dict,
    panel_name: str,
    panel_path: str,
    entities: dict[str, dict],
) -> list[str]:
    """Add plain entities; return AQ/AC form object paths to fetch."""
    theme_object = panel.get("ThemeObject", {}) or {}
    forms: list[str] = []
    for control in _iter_controls(theme_object.get("Controls")):
        form = _form_target(control)
        if form:
            forms.append(form)
        classified = _classify(control)
        if not classified:
            continue
        kind, extra = classified
        obj = _object_name(control)
        if obj in entities:
            continue
        entities[obj] = {
            ENT_OBJECT: obj,
            ENT_NAME: obj.split("\\")[-1],
            ENT_PANEL: panel_name,
            ENT_PANEL_PATH: panel_path,
            ENT_KIND: kind,
            **extra,
        }
    return forms


async def _fetch_form(
    client: BosClient,
    form_obj: str,
    panel_name: str,
    panel_path: str,
    entities: dict[str, dict],
) -> None:
    """Fetch a device form and add its entities (air quality or A/C climate)."""
    try:
        form = await client.get_device_form(form_obj)
    except BosError as err:
        _LOGGER.debug("GetDeviceForm %r failed: %s", form_obj, err)
        return
    if _AC_FORM in form_obj:
        _extract_climate(form, form_obj, panel_name, panel_path, entities)
    elif _VENT_FORM in form_obj:
        _extract_ventilation(form, form_obj, panel_name, panel_path, entities)
    else:
        _extract_air_quality(form, form_obj, panel_name, panel_path, entities)


def _extract_air_quality(
    form: dict,
    form_obj: str,
    panel_name: str,
    panel_path: str,
    entities: dict[str, dict],
) -> None:
    for control in _iter_controls(form.get("Controls")):
        obj = _object_name(control)
        if not obj or obj in entities:
            continue
        if control.get("UIControlType", "") not in (
            "BOSTheme.Controls.DoubleControl",
            "BOSTheme.Controls.IntegerControl",
        ):
            continue
        name = obj.split("\\")[-1]
        device_class, unit, icon = _aqm_class(name, control.get("Unit"))
        entities[obj] = {
            ENT_OBJECT: obj,
            ENT_NAME: name,
            ENT_PANEL: panel_name,
            ENT_PANEL_PATH: panel_path,
            ENT_KIND: KIND_SENSOR,
            ENT_UNIT: unit,
            ENT_DEVICE_CLASS: device_class,
            ENT_STATE_CLASS: "measurement",
            ENT_ICON: icon,
            ENT_FORM: form_obj,
        }


def _extract_climate(
    form: dict,
    form_obj: str,
    panel_name: str,
    panel_path: str,
    entities: dict[str, dict],
) -> None:
    """Build one climate descriptor from an A/C device form."""
    onoff = setpoint = mode = fan = temp = None
    smin = smax = None
    mode_map: dict[str, str] = {}
    fan_map: dict[str, str] = {}

    for control in _iter_controls(form.get("Controls")):
        obj = _object_name(control)
        if not obj:
            continue
        template = control.get("ValueTemplate")
        name = obj.split("\\")[-1]
        if template == "Power":
            onoff = obj
        elif template == "Temperature Setpoint [°C]":
            setpoint, smin, smax = obj, control.get("MinValue"), control.get("MaxValue")
        elif template == "Temperature [°C]" and temp is None:
            temp = obj
        elif template == "Mode":
            mode, mode_map = obj, _enum_map(control)
        elif "Fan Speed" in name:
            fan, fan_map = obj, _enum_map(control)

    if not (setpoint or mode):
        return  # not a recognisable A/C unit
    # Member objects belong to this climate entity, not standalone sensors that a
    # read-only panel summary may have already added.
    for member in (onoff, setpoint, mode, fan, temp):
        if member:
            entities.pop(member, None)
    entities[form_obj] = {
        ENT_OBJECT: form_obj,
        ENT_NAME: form_obj.split("\\")[-1],
        ENT_PANEL: panel_name,
        ENT_PANEL_PATH: panel_path,
        ENT_KIND: KIND_CLIMATE,
        ENT_FORM: form_obj,
        ENT_ONOFF: onoff,
        ENT_SETPOINT: setpoint,
        ENT_MODE: mode,
        ENT_FAN: fan,
        ENT_TEMP: temp,
        ENT_MIN: smin,
        ENT_MAX: smax,
        ENT_MODE_MAP: mode_map,
        ENT_FAN_MAP: fan_map,
    }


def _extract_ventilation(
    form: dict,
    form_obj: str,
    panel_name: str,
    panel_path: str,
    entities: dict[str, dict],
) -> None:
    """An ERV unit -> select (Mode) + status sensors. Only Mode is settable."""
    for control in _iter_controls(form.get("Controls")):
        obj = _object_name(control)
        if not obj:
            continue
        # Form-derived entities win over a read-only panel summary of the same object.
        template = control.get("ValueTemplate")
        name = obj.split("\\")[-1]
        settable = control.get("ButtonSettable") is True
        base = {
            ENT_OBJECT: obj,
            ENT_NAME: name,
            ENT_PANEL: panel_name,
            ENT_PANEL_PATH: panel_path,
            ENT_FORM: form_obj,
        }
        if template == "Mode" and settable:
            entities[obj] = {
                **base,
                ENT_KIND: KIND_SELECT,
                ENT_OPTIONS: _enum_map(control),
                ENT_ICON: "mdi:air-filter",
            }
        elif template == "Power":
            entities[obj] = {**base, ENT_KIND: KIND_BINARY, ENT_DEVICE_CLASS: "running"}
        elif "Fan Speed" in name:
            entities[obj] = {
                **base,
                ENT_KIND: KIND_SENSOR,
                ENT_DEVICE_CLASS: "enum",
                ENT_OPTIONS: _enum_map(control),
                ENT_STATE_CLASS: None,
                ENT_UNIT: None,
                ENT_ICON: "mdi:fan",
            }
        elif "Error Code" in name:
            entities[obj] = {
                **base,
                ENT_KIND: KIND_SENSOR,
                ENT_DEVICE_CLASS: "enum",
                ENT_OPTIONS: _enum_map(control),
                ENT_STATE_CLASS: None,
                ENT_UNIT: None,
                ENT_DIAGNOSTIC: True,
                ENT_ICON: "mdi:alert-circle-outline",
            }

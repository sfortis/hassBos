"""Entity auto-discovery for ComfortClick bOS.

Walks the whole navigation tree (GetTheme -> GetPanel per panel) and classifies
every control into a Home Assistant entity descriptor:

- dimmer light: IntegerControl + ValueTemplate "Dimmer" + writable
- on/off light: BooleanControl + writable + ("Lamp" template or lamp icon)
- sensor:       Double/IntegerControl + read-only (with a unit -> device_class)
- binary sensor: BooleanControl + read-only (template -> device_class)

Air-quality readings (CO2/PM/VOC) are not on any panel: they hide behind an
"Air Quality Sensor Button" (ValueTemplate "Gas") whose device form is fetched
separately via GetDeviceForm. Those become sensors too.

Read-only over the network: GetTheme + GetPanel + GetDeviceForm. Never writes.
"""

from __future__ import annotations

import logging

from .api import BosClient, BosError
from .const import (
    ENT_DEVICE_CLASS,
    ENT_FORM,
    ENT_KIND,
    ENT_MAX,
    ENT_MIN,
    ENT_NAME,
    ENT_OBJECT,
    ENT_PANEL,
    ENT_PANEL_PATH,
    ENT_STATE_CLASS,
    ENT_UNIT,
    KIND_BINARY,
    KIND_DIMMER,
    KIND_SENSOR,
    KIND_SWITCH,
)

_LOGGER = logging.getLogger(__name__)

_THEMES_PREFIX = "Themes\\"
_AQ_TEMPLATE = "Gas"  # air-quality sensor button; real values via GetDeviceForm

# Sensor unit -> (HA device_class, HA unit). Units not listed become plain
# sensors (no device_class), keeping their raw unit.
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

# Binary sensor ValueTemplate -> HA device_class.
_BINARY_MAP: dict[str, str] = {
    "Movement": "motion",
    "Rain": "moisture",
    "Alert": "problem",
    "Strong Wind": "safety",
}


def _iter_controls(controls: list | None):
    """Yield every control, recursing into nested (frame) controls."""
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


def _aqm_class(name: str, unit: str | None) -> tuple[str | None, str | None]:
    """device_class + unit for an air-quality reading, keyed by its name."""
    low = name.lower()
    if "co2" in low:
        return "carbon_dioxide", "ppm"
    if "pm2" in low:
        return "pm25", unit or "µg/m³"
    if "pm10" in low:
        return "pm10", unit or "µg/m³"
    if "pm1" in low:
        return "pm1", unit or "µg/m³"
    if "voc" in low:
        return None, unit or None  # index without a standard unit
    return _UNIT_MAP.get(unit or "", (None, unit or None))


def _classify(control: dict) -> tuple[str, dict] | None:
    """Return (kind, extra fields) for a control, or None if not an entity."""
    if not _object_name(control):
        return None
    ui = control.get("UIControlType", "")
    template = control.get("ValueTemplate")
    settable = control.get("ButtonSettable") is True

    if ui == "BOSTheme.Controls.IntegerControl" and settable and template == "Dimmer":
        return KIND_DIMMER, {
            ENT_MIN: control.get("MinValue"),
            ENT_MAX: control.get("MaxValue"),
        }
    if ui == "BOSTheme.Controls.BooleanControl" and settable:
        if template == "Lamp" or _has_lamp_icon(control):
            return KIND_SWITCH, {}
        return None  # settable non-lamp boolean (e.g. a socket) - skip
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
        if template == _AQ_TEMPLATE:
            return None  # air-quality button - values fetched via GetDeviceForm
        return KIND_BINARY, {ENT_DEVICE_CLASS: _BINARY_MAP.get(template or "")}
    return None


async def async_discover_entities(client: BosClient) -> list[dict]:
    """Return entity descriptors from the whole navigation tree.

    The navigation layout is project-specific (some installs use "Lights &
    Shading", others "Rooms", etc.), so the whole tree is scanned. Panels with
    nothing of interest simply contribute no entities.
    """
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
            # Container nodes have no panel of their own (404) - skip quietly.
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
    """Add this panel's entities; return air-quality form object paths to fetch."""
    theme_object = panel.get("ThemeObject", {}) or {}
    forms: list[str] = []
    for control in _iter_controls(theme_object.get("Controls")):
        if control.get("ValueTemplate") == _AQ_TEMPLATE:
            form_obj = (control.get("FormPanel") or {}).get("ObjectName")
            if form_obj:
                forms.append(form_obj)
        classified = _classify(control)
        if not classified:
            continue
        kind, extra = classified
        obj = _object_name(control)
        if obj in entities:
            continue  # same object can appear on multiple panels
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
    """Fetch an air-quality device form and add its readings as sensors."""
    try:
        form = await client.get_device_form(form_obj)
    except BosError as err:
        _LOGGER.debug("GetDeviceForm %r failed: %s", form_obj, err)
        return
    for control in _iter_controls(form.get("Controls")):
        obj = _object_name(control)
        if not obj or obj in entities:
            continue
        ui = control.get("UIControlType", "")
        if ui not in ("BOSTheme.Controls.DoubleControl", "BOSTheme.Controls.IntegerControl"):
            continue
        name = obj.split("\\")[-1]
        device_class, unit = _aqm_class(name, control.get("Unit"))
        entities[obj] = {
            ENT_OBJECT: obj,
            ENT_NAME: name,
            ENT_PANEL: panel_name,
            ENT_PANEL_PATH: panel_path,
            ENT_KIND: KIND_SENSOR,
            ENT_UNIT: unit,
            ENT_DEVICE_CLASS: device_class,
            ENT_STATE_CLASS: "measurement",
            ENT_FORM: form_obj,
        }

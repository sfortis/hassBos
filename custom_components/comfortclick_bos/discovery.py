"""Light auto-discovery for ComfortClick bOS.

Walks the navigation tree returned by GetTheme, restricted to the "Lights &
Shading" subtree, calling GetPanel per panel and extracting light controls.

Classification (verified against a live building):
- dimmable light: IntegerControl + ValueTemplate "Dimmer" + writable ObjectValue
- on/off light:   BooleanControl + writable + ("Lamp" template or a lamp icon)

Read-only: GetTheme + GetPanel. Never writes.
"""

from __future__ import annotations

import logging

from .api import BosClient, BosError
from .const import (
    KIND_DIMMER,
    KIND_SWITCH,
    LIGHT_KIND,
    LIGHT_MAX,
    LIGHT_MIN,
    LIGHT_NAME,
    LIGHT_OBJECT,
    LIGHT_PANEL,
    LIGHT_PANEL_PATH,
    LIGHTS_ROOT,
)

_LOGGER = logging.getLogger(__name__)

_THEMES_PREFIX = "Themes\\"


def _find_node(node: dict, text: str) -> dict | None:
    """Depth-first search for the navigation node whose Text matches."""
    if node.get("Text") == text:
        return node
    for child in node.get("Nodes", []) or []:
        found = _find_node(child, text)
        if found:
            return found
    return None


def _iter_controls(controls: list | None):
    """Yield every control, recursing into nested (frame) controls."""
    for control in controls or []:
        yield control
        nested = control.get("Controls")
        if isinstance(nested, list):
            yield from _iter_controls(nested)


def _object_name(control: dict) -> str | None:
    return (control.get("ObjectValue") or {}).get("ObjectName") or None


def _is_dimmer(control: dict) -> bool:
    return (
        control.get("UIControlType") == "BOSTheme.Controls.IntegerControl"
        and control.get("ValueTemplate") == "Dimmer"
        and bool(_object_name(control))
        and control.get("ButtonSettable") is True
    )


def _is_switch(control: dict) -> bool:
    if not (
        control.get("UIControlType") == "BOSTheme.Controls.BooleanControl"
        and bool(_object_name(control))
        and control.get("ButtonSettable") is True
    ):
        return False
    if control.get("ValueTemplate") == "Lamp":
        return True
    # Edge case: lamp-iconed boolean with an empty template (e.g. an RGBW group).
    icon = (control.get("Image") or {}).get("ObjectName", "") or ""
    fore = (control.get("StatusBarForeColor") or {}).get("ObjectName", "")
    return "Lamp" in icon or "Bulb" in icon or fore == "Lamp"


async def async_discover_lights(client: BosClient) -> list[dict]:
    """Return the list of light descriptors under the Lights & Shading subtree."""
    theme = await client.get_theme()
    host = theme.get("Host", {}) or {}
    root = _find_node(host, LIGHTS_ROOT)
    if not root:
        _LOGGER.warning("Navigation node %r not found; cannot discover", LIGHTS_ROOT)
        return []

    lights: dict[str, dict] = {}
    seen_panels: set[str] = set()
    await _walk(client, root, lights, seen_panels)
    _LOGGER.debug("Discovered %d lights across %d panels", len(lights), len(seen_panels))
    return list(lights.values())


async def _walk(
    client: BosClient,
    node: dict,
    lights: dict[str, dict],
    seen: set[str],
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
            _extract(panel, panel_name, panel_path, lights)

    for child in node.get("Nodes", []) or []:
        await _walk(client, child, lights, seen)


def _extract(
    panel: dict,
    panel_name: str,
    panel_path: str,
    lights: dict[str, dict],
) -> None:
    theme_object = panel.get("ThemeObject", {}) or {}
    for control in _iter_controls(theme_object.get("Controls")):
        if _is_dimmer(control):
            kind, min_v, max_v = KIND_DIMMER, control.get("MinValue"), control.get("MaxValue")
        elif _is_switch(control):
            kind, min_v, max_v = KIND_SWITCH, None, None
        else:
            continue
        obj = _object_name(control)
        if obj in lights:
            continue  # same object can appear on multiple panels
        lights[obj] = {
            LIGHT_OBJECT: obj,
            LIGHT_NAME: obj.split("\\")[-1],
            LIGHT_PANEL: panel_name,
            LIGHT_PANEL_PATH: panel_path,
            LIGHT_KIND: kind,
            LIGHT_MIN: min_v,
            LIGHT_MAX: max_v,
        }

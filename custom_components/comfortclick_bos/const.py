"""Constants for the ComfortClick bOS integration."""

from __future__ import annotations

DOMAIN = "comfortclick_bos"

# Config entry keys
CONF_BASE_URL = "base_url"
CONF_OBJECT_NAME = "object_name"
CONF_PANEL = "panel"
CONF_LIGHTS = "lights"

# Root navigation node to scan for lights.
LIGHTS_ROOT = "Lights & Shading"

# Discovered light kinds.
KIND_DIMMER = "dimmer"
KIND_SWITCH = "switch"

# Keys inside each stored light dict.
LIGHT_OBJECT = "object_name"
LIGHT_NAME = "name"
LIGHT_PANEL = "panel"
LIGHT_PANEL_PATH = "panel_path"
LIGHT_KIND = "kind"
LIGHT_MIN = "min"
LIGHT_MAX = "max"

# Gateway host prefix only. The user appends their project (AccessID) and enters
# the full base URL in the config flow, e.g. "<gateway>/<AccessID>".
DEFAULT_BASE_URL = "https://gateway-eu2.comfortclick.com/"

# The bOS DALI dim range observed on the gateway.
BOS_MIN = 0
BOS_MAX = 100

# Seconds between GetClientData polls. The official web client polls ~1s; we
# poll less aggressively. The server queues PropertyUpdates per session, so a
# slower poll still receives every change (batched) on the next call.
SCAN_INTERVAL = 5

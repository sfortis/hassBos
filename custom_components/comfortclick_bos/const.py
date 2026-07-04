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

# Seconds between GetClientData polls. Kept close to the official web client (~1s)
# on purpose: a short interval keeps ONE keep-alive connection (and its gateway
# session/backend) alive. A longer interval let the socket go idle, so each poll
# re-handshook TLS (slow, 3-8s) and sometimes hit a backend without the session
# (404). Do not raise this much.
SCAN_INTERVAL = 2

"""Constants for the ComfortClick bOS integration."""

from __future__ import annotations

DOMAIN = "comfortclick_bos"

# Config entry keys
CONF_BASE_URL = "base_url"
CONF_ENTITIES = "entities"

# Discovered entity kinds.
KIND_DIMMER = "dimmer"
KIND_SWITCH = "switch"
KIND_SENSOR = "sensor"
KIND_BINARY = "binary_sensor"
KIND_CLIMATE = "climate"
KIND_SELECT = "select"
KIND_RGB = "rgb"

# Keys inside each stored entity descriptor dict.
ENT_OBJECT = "object_name"
ENT_NAME = "name"
ENT_PANEL = "panel"
ENT_PANEL_PATH = "panel_path"
ENT_KIND = "kind"
ENT_MIN = "min"  # dimmer
ENT_MAX = "max"  # dimmer
ENT_UNIT = "unit"  # sensor
ENT_DEVICE_CLASS = "device_class"  # sensor / binary_sensor
ENT_STATE_CLASS = "state_class"  # sensor
ENT_FORM = "form"  # GetDeviceForm object path for seeding (AQ sensor / climate)

# Climate (A/C) descriptor keys: references to the unit's member objects.
ENT_ONOFF = "onoff"
ENT_SETPOINT = "setpoint"
ENT_MODE = "mode"
ENT_FAN = "fan"
ENT_TEMP = "temp"
ENT_MODE_MAP = "mode_map"  # {bOS index (str): bOS text} e.g. "1" -> "HEAT"
ENT_FAN_MAP = "fan_map"  # {bOS index (str): bOS text} e.g. "2" -> "MED"

# Select / enum-sensor keys.
ENT_OPTIONS = "options"  # {bOS index (str): text} for select or enum sensor
ENT_DIAGNOSTIC = "diagnostic"  # bool: place entity in the diagnostic category

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

# How often to refresh air-quality device forms (CO2/PM/VOC), in seconds. These
# change slowly and are fetched separately via GetDeviceForm, so poll them on a
# much slower cadence than the 2s live channel.
FORM_SCAN_INTERVAL = 300

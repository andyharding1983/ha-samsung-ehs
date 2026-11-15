"""Constants for the Samsung EHS integration."""

from __future__ import annotations

DOMAIN = "samsung_ehs"
MANUFACTURER = "Samsung"

# Connection (config entry data)
CONF_PORT = "port"
CONF_SLAVE = "slave"
CONF_BAUDRATE = "baudrate"
CONF_INDOOR_INDEX = "indoor_index"

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_SLAVE = 1
DEFAULT_BAUDRATE = 9600
DEFAULT_INDOOR_INDEX = 0

# Options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ROOM_SENSOR = "room_sensor"
CONF_POWER_SENSOR = "power_sensor"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_EXTENDED = "extended_registers"
CONF_WC_INTERVAL = "wc_interval"

DEFAULT_SCAN_INTERVAL = 15  # seconds
DEFAULT_WC_INTERVAL = 300  # seconds between weather-compensation writes
DEFAULT_EXTENDED = True

# Weather compensation parameter keys (number entities, restored across restarts)
WC_OUTDOOR_DESIGN = "wc_outdoor_design"  # coldest design outdoor temp
WC_FLOW_DESIGN = "wc_flow_design"  # flow temp at design outdoor temp
WC_OUTDOOR_MILD = "wc_outdoor_mild"  # mild outdoor temp
WC_FLOW_MILD = "wc_flow_mild"  # flow temp at mild outdoor temp
WC_ROOM_TARGET = "wc_room_target"
WC_ROOM_GAIN = "wc_room_gain"  # K flow per K room error
WC_ROOM_MAX_OFFSET = "wc_room_max_offset"
WC_FORECAST_HOURS = "wc_forecast_hours"
WC_FORECAST_WEIGHT = "wc_forecast_weight"  # 0..1
WC_FLOW_MIN = "wc_flow_min"
WC_FLOW_MAX = "wc_flow_max"
WC_HEATING_CUTOFF = "wc_heating_cutoff"  # effective outdoor temp above which flow = min

WC_DEFAULTS: dict[str, float] = {
    WC_OUTDOOR_DESIGN: -3.0,
    WC_FLOW_DESIGN: 45.0,
    WC_OUTDOOR_MILD: 15.0,
    WC_FLOW_MILD: 25.0,
    WC_ROOM_TARGET: 21.0,
    WC_ROOM_GAIN: 3.0,
    WC_ROOM_MAX_OFFSET: 5.0,
    WC_FORECAST_HOURS: 6.0,
    WC_FORECAST_WEIGHT: 0.3,
    WC_FLOW_MIN: 20.0,
    WC_FLOW_MAX: 55.0,
    WC_HEATING_CUTOFF: 17.0,
}

# Minimum change (K) before a new flow setpoint is written to the unit,
# to avoid hammering the bus/EEPROM with tiny adjustments.
WC_WRITE_DEADBAND = 0.5

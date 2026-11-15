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
CONF_HOT_WATER = "hot_water"
CONF_DHW_SCHEDULE = "dhw_schedule"
DEFAULT_HOT_WATER = True

DEFAULT_SCAN_INTERVAL = 15  # seconds
DEFAULT_WC_INTERVAL = 300  # minimum seconds between flow setpoint writes
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
WC_HEATING_CUTOFF = "wc_heating_cutoff"  # effective outdoor temp above which heating stops
WC_HYSTERESIS = "wc_hysteresis"  # K above target at which heating pauses
WC_MIN_ON = "wc_min_on"  # minutes; 20 => max 3 starts per hour
WC_MIN_OFF = "wc_min_off"  # minutes
WC_ADAPT_RATE = "wc_adapt_rate"  # K curve offset per hour per K room error
WC_ADAPT_LIMIT = "wc_adapt_limit"  # K
WC_WIND_GAIN = "wc_wind_gain"  # K flow per m/s above the threshold
WC_WIND_THRESHOLD = "wc_wind_threshold"  # m/s

# Heat emitter presets. Selecting an emitter type applies its values; they
# can then be fine-tuned individually with the number entities.
EMITTER_UFH = "underfloor"
EMITTER_RADIATORS = "radiators"
EMITTER_PRESETS: dict[str, dict[str, float]] = {
    # Screed floors respond over hours: low flow, slow learning.
    EMITTER_UFH: {
        "wc_flow_min": 25.0,
        "wc_flow_max": 45.0,
        "wc_flow_design": 40.0,
        "wc_flow_mild": 25.0,
        "wc_adapt_rate": 0.25,
    },
    # Radiators respond in minutes: higher flow, faster learning.
    EMITTER_RADIATORS: {
        "wc_flow_min": 30.0,
        "wc_flow_max": 55.0,
        "wc_flow_design": 50.0,
        "wc_flow_mild": 30.0,
        "wc_adapt_rate": 0.75,
    },
}
DEFAULT_EMITTER = EMITTER_UFH

WC_DEFAULTS: dict[str, float] = {
    WC_OUTDOOR_DESIGN: -3.0,
    WC_FLOW_DESIGN: 40.0,
    WC_OUTDOOR_MILD: 15.0,
    WC_FLOW_MILD: 25.0,
    WC_ROOM_TARGET: 21.0,
    WC_ROOM_GAIN: 3.0,
    WC_ROOM_MAX_OFFSET: 5.0,
    WC_FORECAST_HOURS: 6.0,
    WC_FORECAST_WEIGHT: 0.3,
    WC_FLOW_MIN: 25.0,
    WC_FLOW_MAX: 45.0,
    WC_HEATING_CUTOFF: 17.0,
    WC_HYSTERESIS: 1.0,
    WC_MIN_ON: 20.0,
    WC_MIN_OFF: 0.0,
    WC_ADAPT_RATE: 0.25,
    WC_ADAPT_LIMIT: 5.0,
    WC_WIND_GAIN: 0.3,
    WC_WIND_THRESHOLD: 5.0,
}

# Boost: run at maximum flow for this long, regardless of the thermostat.
BOOST_DURATION_S = 3600

# Minimum change (K) before a new flow setpoint is written to the unit,
# to avoid hammering the bus/EEPROM with tiny adjustments.
WC_WRITE_DEADBAND = 0.5

# Entity reference

Every entity the integration creates, what it means, and every attribute it exposes.

Entity IDs below assume the default device name **Samsung EHS** and an English UI. If you rename the
device, the IDs follow the new name.

* [How the control loop fits together](#how-the-control-loop-fits-together)
* [Climate: room thermostat](#climate-room-thermostat)
* [Water heater: hot water](#water-heater-hot-water)
* [Sensors](#sensors)
* [Binary sensors](#binary-sensors)
* [Switches](#switches)
* [Select: heat emitter type](#select-heat-emitter-type)
* [Numbers and buttons: tuning](#numbers-and-buttons-tuning)
* [Diagnostic entities](#diagnostic-entities)

---

## How the control loop fits together

```
 weather forecast ─┐
 outdoor temp ─────┼─► effective outdoor ─► heating curve ─┐
                   │                                       ├─► Flow target ──(write)──► Flow setpoint (on the unit)
 room temp ────────┼─► room offset (instant) ──────────────┤                                   │
 wind (now+fcst) ──┼─► wind offset ────────────────────────┤                                   │
                   └─► learned curve offset (slow) ────────┘                                   ▼
                                                                                    heat pump heats water to it
 room temp vs target ─► thermostat ─► Heat demand ─(write on/off)─► unit space heating on/off
```

On every poll (15 s by default) Home Assistant:

1. **Reads** the unit.
2. **Updates the learned curve offsets** at each end of the curve from the room error. This only happens while space heating is actually running:
   not paused, not defrosting and not heating the hot water cylinder.
3. **Calculates the Flow target** from the curve, the learned offset, the room offset and the wind offset.
4. **Runs the thermostat:** it pauses at *target + overshoot* and resumes at *target*, subject to the
   minimum on/off times.
5. **Switches space heating** on or off at the unit if the thermostat state changed.
6. **Writes the Flow target** to the unit as the **Flow setpoint**, but only if heating is on, the change is
   at least 0.5 °C, and the last write was at least 5 min ago. Changing the room target or a tuning
   number writes straight away.

### Flow target vs flow setpoint

| | Flow target | Flow setpoint |
|---|---|---|
| What it is | What Home Assistant *wants* the flow temperature to be (the maximum flow temperature during a boost) | What the heat pump is *actually using* (register 68, read back) |
| Where it comes from | Calculated in Home Assistant on every poll | The unit's own memory |
| Updates | Every poll | When Home Assistant writes to it, or someone changes it on the Samsung controller |
| Category | Main sensor | Diagnostic |

They normally agree to within 0.5 °C. They can differ when:

* The new target is within 0.5 °C of the current setpoint. That isn't worth a write.
* The last write was less than 5 min ago (*Minimum time between flow setpoint changes* option).
* Heating is **paused or off**. Nothing is written, so the unit keeps its last value.
* A write **failed**, for example because of a comms error. Check the logs.
* Someone changed it at the **Samsung controller**. Home Assistant overwrites it on the next write.

If the two stay apart for a long time while heating is running, the writes aren't reaching the unit.

---

## Climate: room thermostat

`climate.samsung_ehs`

Works like any room thermostat. The **target** is the room temperature you want, and the **current
temperature** comes from the room sensor chosen in the options. If no room sensor is chosen, it uses
the Samsung controller's own sensor.

| Mode | Behaviour |
|---|---|
| **Heat** | Home Assistant runs space heating on weather compensation and pauses and resumes it as a thermostat. |
| **Off** | Space heating is switched off at the unit straight away (this ignores the minimum on time). Hot water is not affected. |

| Preset | Behaviour |
|---|---|
| **None** | Normal control |
| **Boost** | Maximum flow temperature for one hour, even if the room is warm or the mode is Off. Same as the *Boost heating* button. Pick *None*, or press *Cancel boost*, to end it early. See [Boost](#boost). |

| HVAC action | Meaning |
|---|---|
| `heating` | Space heating on and compressor running |
| `idle` | Heat mode, but paused (room ≥ target + overshoot, or above the heating cut-off), or on with the compressor not running |
| `defrosting` | Outdoor unit is defrosting |
| `off` | Mode is Off |

Target range 5–28 °C in 0.5 °C steps. The mode and target are restored after a restart.

| Attribute | Meaning |
|---|---|
| `heat_demand` | `true` while the thermostat is calling for heat. Same as the *Heat demand* binary sensor. |
| `flow_target` | Current Flow target (°C), see above |
| `flow_temperature` | Actual flow temperature leaving the heat pump (°C) |
| `adaptive_offset` | Learned curve offset applied right now (K), see *Learned curve offset* |
| `boost_until` | When the current boost ends (UTC), or `null` if no boost is running |

---

## Water heater: hot water

`water_heater.samsung_ehs_hot_water`

Only created when the **Hot water cylinder** option is on, which is the default. On heating-only systems,
turn it off in the setup dialog or in *Configure*. The hot water entity and the hot water temperature sensor are
then removed.

Controls the domestic hot water cylinder directly on the unit. Apart from the optional schedule below,
Home Assistant doesn't add any logic of its own here.

| Operation | Samsung mode |
|---|---|
| `off` | Hot water off |
| `eco` | Eco: heat pump only, slowest, most efficient |
| `standard` | Standard |
| `performance` | Power: heat pump plus booster heater, faster |
| `high_demand` | Force: one-off forced heat-up (EHS only) |

Target range 30–70 °C. The current temperature is the cylinder sensor.

### Hot water boost

| Button | Meaning |
|---|---|
| `button.samsung_ehs_boost_hot_water` | Hot water on for one hour, keeping the current mode and target. Pressing again restarts the hour. |
| `button.samsung_ehs_cancel_hot_water_boost` | End it early |

When the boost ends, hot water follows the schedule if one is set (and available). Otherwise it goes back
to how it was before the boost. Schedule changes during a boost are ignored. Switching hot water off by
hand ends the boost. A restart of Home Assistant cancels a boost. The water heater's `boost_until`
attribute shows when the boost ends.

### Hot water schedule

Optional. Set it up in three steps:

1. Create a **Schedule** helper under *Settings → Devices & services → Helpers → Create helper →
   Schedule*.
2. Draw the times you want hot water, for example a cheap-tariff window.
3. Choose that helper as **Hot water schedule** in the integration's *Configure* dialog.

- When a block **starts**, hot water is switched on, keeping the mode and target you set.
- When a block **ends**, it is switched off and the tank just cools.
- At startup it is set to match the schedule.
- It only acts when a block starts or ends. If you switch hot water on or off by hand, for example for an
  extra shower, that lasts until the next block starts or ends.
- If the schedule helper is unavailable, hot water is left alone.

| Attribute | Meaning |
|---|---|
| `boost_until` | When the hot water boost ends (UTC), or `null` |
| `schedule` | The schedule helper being followed |
| `schedule_state` | `on` inside a block, `off` outside |
| `next_schedule_change` | When the schedule next turns on or off |

---

## Sensors

### Temperatures and flow

| Entity | Meaning |
|---|---|
| `sensor.samsung_ehs_flow_temperature` | Water leaving the heat pump (°C) |
| `sensor.samsung_ehs_return_temperature` | Water coming back from the heating circuit (°C) |
| `sensor.samsung_ehs_flow_return_difference` | Flow − return (K). Typically 3–7 K while heating. It goes negative during defrost. |
| `sensor.samsung_ehs_hot_water_temperature` | Hot water cylinder (°C). Only when the hot water option is on. |
| `sensor.samsung_ehs_outdoor_temperature` | Heat pump's outdoor sensor (°C). Needs *extended registers*. |
| `sensor.samsung_ehs_flow_rate` | Water flow rate (L/min). Needs *extended registers*. 0 when the pump is stopped. |
| `sensor.samsung_ehs_compressor_frequency` | Compressor speed (Hz). Needs *extended registers*. 0 = stopped. |

### Flow target

`sensor.samsung_ehs_flow_target`: the flow temperature Home Assistant has calculated (°C). Its attributes
show how it was worked out:

| Attribute | Meaning |
|---|---|
| `effective_outdoor` | Outdoor temperature used by the curve: a blend of the current outdoor temperature and the forecast mean, weighted by *Forecast weight* (°C) |
| `forecast_mean` | Mean forecast temperature over *Forecast look-ahead* hours (°C). `null` if no weather entity is configured or no forecast is available. |
| `base_flow` | Flow temperature from the heating curve alone at the current outdoor temperature, before any adjustment (°C). `base_flow` + `forecast_offset` = `curve_flow`. |
| `curve_flow` | Flow temperature from the heating curve alone at `effective_outdoor` (°C) |
| `forecast_offset` | Forecast adjustment: how much the forecast moves the flow compared with using the current outdoor temperature alone (K). Already included in `curve_flow`. |
| `adaptive_offset` | Learned correction applied at `effective_outdoor` (K), see *Learned curve offset* |
| `room_offset` | Instant room correction = *Room influence gain* × (target − room), limited to ± *Room influence limit* (K) |
| `wind_speed` | Wind used for the wind boost (m/s): the mean of the weather entity's current wind and the hourly forecast over *Forecast look-ahead*. `null` without a weather entity. |
| `boost_offset` | Extra flow added by a heating boost, up to the maximum flow temperature (K). `0` when no boost is running. |
| `wind_offset` | Wind boost = *Wind boost gain* × (`wind_speed` − *Wind boost threshold*), from 0 to 3 K |
| `limited` | `1` when the result is held at the maximum flow temperature, `-1` at the minimum, `0` otherwise. While it is held, curve learning doesn't wind up further in that direction. |
| `heating_needed` | `false` when `effective_outdoor` is at or above the *Heating cut-off*. Heating then stays off. |
| `heat_demand` | `true` while the thermostat is calling for heat |
| `last_write` | When a flow setpoint was last written to the unit (UTC). `null` since startup if nothing has been written yet. |
| `boost_until` | When the current boost ends (UTC), or `null`. During a boost the unit is sent the maximum flow temperature instead of this sensor's value. |

The state = `curve_flow` + `adaptive_offset` + `room_offset`, limited to the min/max flow temperature and
rounded to 0.5 °C. Above the cut-off it equals the minimum flow temperature.

### Learned curve offset

`sensor.samsung_ehs_learned_curve_offset` (K): the self-learned correction to the heating curve that is
being applied **right now**, at today's effective outdoor temperature.

The integration learns a separate correction for **each end of the curve**, the cold (design) end and the
mild end, so both the level and the **slope** of the curve can change:

```
learned offset = w · design_end_offset + (1 − w) · mild_end_offset
w = 1 at the design outdoor temperature, 0 at the mild one, linear in between (held beyond the ends)
```

While space heating runs (not defrosting and not diverted to the hot water cylinder), the room error is
credited to the two ends in proportion to `w`. Cold days mainly teach the cold end, and mild days the
mild end. The learning speed is the same everywhere: the offset applied at the current outdoor
temperature changes by *Adaptive learning rate* × (target − room) per hour. The underfloor default of
0.25 means 1 °C too cold for an hour adds 0.25 K. Each end is limited to ± *Adaptive offset limit*.
Nothing is learned while heating is paused or off, above the heating cut-off, or while the room sensor
is unavailable. The values are saved and survive restarts.

**Anti-windup:** if the flow target is already held at the *maximum* flow temperature, it won't learn any
further upwards. Raising it would change nothing now, and would only cause an overshoot once the weather
warms up. The same applies downwards at the *minimum*. It can always learn back towards zero.

| Attribute | Meaning |
|---|---|
| `design_end_offset` | Learned correction at the cold end of the curve (K) |
| `mild_end_offset` | Learned correction at the mild end of the curve (K) |
| `learned_flow_at_design` | Curve flow at the design temperature including learning (°C). This is what *Apply learned curve* would set. |
| `learned_flow_at_mild` | The same at the mild temperature (°C) |
| `design_weight` | `w` at today's effective outdoor temperature (0–1) |
| `curve` | The whole curve sampled every 1 °C from 10 °C below the design temperature up to the heating cut-off: a list of `{outdoor, set, learned}`. `set` is your curve, `learned` includes the learned offsets. Both are limited to min/max flow. It's for plotting and isn't stored in the recorder database. |

### Learned flow at cold end / mild end

`sensor.samsung_ehs_learned_flow_at_cold_end` and `sensor.samsung_ehs_learned_flow_at_mild_end` (°C)
show the two ends of the learned curve as ordinary temperature sensors, so they have a history graph.
They have the same values as `learned_flow_at_design` and `learned_flow_at_mild` above.

- If both lines rise together, the curve is too low overall.
- If only the cold end rises, the curve is too flat: it needs more heat as it gets colder.
- If only the mild end rises, the curve is too steep.

| Attribute | Meaning |
|---|---|
| `outdoor_temperature` | Outdoor temperature at this end of the curve (°C) |
| `set_flow` | The flow you set for this end (°C) |
| `learned_offset` | What learning has added at this end (K) |

### Viewing the learned curve

**History, with no extra cards:** add a *History graph* card containing the two *Learned flow at …* sensors.
You'll see how each end has moved over days and weeks.

**Curve plot (optional, needs [ApexCharts card](https://github.com/RomRider/apexcharts-card) from HACS):**
this plots your set curve, the learned curve and today's flow target against outdoor temperature.

ApexCharts card only supports time axes, so the card spreads the outdoor range `LO`…`HI` across the time
window and relabels the axis. If you change the range, set `LO` and `HI` to the same values in all three
places.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Heating curve
graph_span: 1d
update_interval: 1min
yaxis:
  - min: 20
    max: 50
    decimals: 0
apex_config:
  chart:
    height: 300
  legend:
    show: true
  markers:
    size: [0, 0, 7]
  stroke:
    dashArray: [5, 0, 0]
  xaxis:
    type: numeric
    tickAmount: 6
    labels:
      formatter: |
        EVAL:function (v, t, o) {
          const LO = -10, HI = 20, g = o.w.globals;
          return Math.round(LO + (v - g.minX) / (g.maxX - g.minX) * (HI - LO)) + '°';
        }
  tooltip:
    x:
      formatter: |
        EVAL:function (v, o) {
          const LO = -10, HI = 20, g = o.w.globals;
          return 'Outdoor ' + (LO + (v - g.minX) / (g.maxX - g.minX) * (HI - LO)).toFixed(1) + ' °C';
        }
series:
  - entity: sensor.samsung_ehs_learned_curve_offset
    name: Set curve
    extend_to: false
    show:
      legend_value: false
    color: '#9e9e9e'
    data_generator: |
      const LO = -10, HI = 20, t0 = start.getTime(), w = end.getTime() - t0;
      return (entity.attributes.curve || [])
        .filter((p) => p.outdoor >= LO && p.outdoor <= HI)
        .map((p) => [t0 + (p.outdoor - LO) / (HI - LO) * w, p.set]);
  - entity: sensor.samsung_ehs_learned_curve_offset
    name: Learned curve
    extend_to: false
    show:
      legend_value: false
    color: '#ff7043'
    data_generator: |
      const LO = -10, HI = 20, t0 = start.getTime(), w = end.getTime() - t0;
      return (entity.attributes.curve || [])
        .filter((p) => p.outdoor >= LO && p.outdoor <= HI)
        .map((p) => [t0 + (p.outdoor - LO) / (HI - LO) * w, p.learned]);
  - entity: sensor.samsung_ehs_flow_target
    name: Flow target now
    extend_to: false
    show:
      legend_value: false
    color: '#29b6f6'
    data_generator: |
      const LO = -10, HI = 20, t0 = start.getTime(), w = end.getTime() - t0;
      const x = entity.attributes.effective_outdoor;
      if (x == null || entity.state === 'unavailable') return [];
      return [[t0 + (Math.min(HI, Math.max(LO, x)) - LO) / (HI - LO) * w, Number(entity.state)]];
```

`extend_to: false` matters: by default ApexCharts card extends each series' last point to the right edge
of the chart, which would draw a false horizontal line.

The curves run from 10 °C below the design temperature up to the heating cut-off. Beyond the design
point the line simply keeps its slope until it reaches the maximum flow. At the mild end it flattens at
the minimum flow before the cut-off, and above the cut-off there's no heating at all.

The blue dot is today's flow target, plotted at the effective outdoor temperature. It sits above or below
the learned curve by the current room correction (`room_offset`).

**Folding it in:** once the offsets have settled, press **Apply learned curve**. It copies
`learned_flow_at_design` and `learned_flow_at_mild` into the two curve points and resets both offsets to
zero, so the heat pump behaves exactly the same but the curve numbers now show what was learned.
**Reset curve learning** throws the learned offsets away.

### Heat output, COP and energy

| Entity | Meaning |
|---|---|
| `sensor.samsung_ehs_heat_output` | Thermal power delivered (W) = flow rate × 4.18 × (flow − return). Negative during defrost. |
| `sensor.samsung_ehs_cop` | Instant COP = heat output ÷ electrical power. Shown as unknown below 100 W input (standby) and limited to 10. |
| `sensor.samsung_ehs_electrical_power` | Electrical input (W), copied from the power sensor chosen in the options. In simulator mode, when no power sensor is set, it's the simulated value. |
| `sensor.samsung_ehs_electrical_energy_today` | Electrical energy used today (kWh), integrated from the power above. Resets at local midnight. |
| `sensor.samsung_ehs_cop_today` | Heat energy today ÷ electrical energy today. Resets at local midnight. |
| `sensor.samsung_ehs_cop_lifetime` | The same since the integration was installed |
| `sensor.samsung_ehs_heat_energy` | Total heat delivered (kWh), including defrost losses. Can go into the Energy dashboard. |
| `sensor.samsung_ehs_heat_energy_today` | Heat delivered today (kWh) |

Electrical power comes from the *power sensor* option. Use a meter on the outdoor unit supply. In
simulator mode with no power sensor, the simulator supplies its own figure. COP accuracy depends
heavily on the flow rate reading. Treat it as a guide, not as metrology.

---

## Binary sensors

| Entity | Meaning |
|---|---|
| `binary_sensor.samsung_ehs_heat_demand` | On while the thermostat is calling for heat and the climate mode is Heat. Off when paused or off. |
| `binary_sensor.samsung_ehs_compressor` | On while the compressor is running (frequency > 0) |
| `binary_sensor.samsung_ehs_defrost` | On while the outdoor unit is defrosting |
| `binary_sensor.samsung_ehs_problem` | On if the module, indoor or outdoor unit reports an error. See the error code sensors. |

---

## Switches

| Entity | Meaning |
|---|---|
| `switch.samsung_ehs_quiet_mode` | Samsung quiet (silent) mode. Lower fan and compressor speed, so less capacity. |

---

## Select: heat emitter type

`select.samsung_ehs_heat_emitter_type` (Configuration)

Choosing an emitter type applies a set of sensible starting values. You can then fine-tune each one
with the numbers below. Choosing a type again re-applies its preset and overwrites your fine-tuning.

| Setting | Underfloor heating (default) | Radiators |
|---|---|---|
| Minimum flow temperature | 25 °C | 30 °C |
| Maximum flow temperature | 45 °C | 55 °C |
| Curve: flow at design temperature (−3 °C) | 40 °C | 50 °C |
| Curve: flow at mild temperature (15 °C) | 25 °C | 30 °C |
| Adaptive learning rate | 0.25 | 0.75 |

Underfloor heating in screed responds over hours and must not run too hot, because that damages the screed
and floor coverings. Check your installer's maximum flow temperature, which is often 40–50 °C. Radiators
respond in minutes and need higher flow temperatures.

---

## Numbers and buttons: tuning

All of these are under the device's **Configuration** section and are restored after a restart. Changing any
of them recalculates and, if heating is on, writes the new flow temperature straight away.

### Heating curve

The curve is a straight line through two points, (design outdoor, design flow) and (mild outdoor, mild flow).
It **keeps going past those points**: with the underfloor defaults it asks for about 46 °C at −10 °C. The room
and learned offsets are added on top. The **minimum and maximum flow temperatures are the hard limits on
the final result**, whatever produced it. Set the maximum for what your heat emitters can safely take.

| Entity | Default | Meaning |
|---|---|---|
| `number.samsung_ehs_curve_design_outdoor_temperature` | −3 °C | Coldest outdoor temperature you design for |
| `number.samsung_ehs_curve_flow_at_design_temperature` | 40 °C ¹ | Flow temperature needed at that outdoor temperature |
| `number.samsung_ehs_curve_mild_outdoor_temperature` | 15 °C | A mild outdoor temperature |
| `number.samsung_ehs_curve_flow_at_mild_temperature` | 25 °C ¹ | Flow temperature needed at that outdoor temperature |
| `number.samsung_ehs_minimum_flow_temperature` | 25 °C ¹ | Lowest flow ever written. It stops the flow sinking towards room temperature in mild weather, where it delivers almost no heat. The unit's own limit is 15 °C. |
| `number.samsung_ehs_maximum_flow_temperature` | 45 °C ¹ | Highest flow ever written. A safety limit for your emitters. The unit's own limit is 55 °C. |
| `number.samsung_ehs_heating_cut_off_outdoor_temperature` | 17 °C | At or above this *effective* outdoor temperature, heating stays off |

¹ Underfloor default. Set by the [heat emitter type](#select-heat-emitter-type).

### Forecast

| Entity | Default | Meaning |
|---|---|---|
| `number.samsung_ehs_forecast_look_ahead` | 6 h | How far ahead to average the hourly forecast |
| `number.samsung_ehs_forecast_weight` | 0.3 | 0 = ignore the forecast, 1 = use only the forecast. 0.3 = 70 % now + 30 % forecast. |

### Wind boost

Wind increases heat loss through draughts and wind-chilled walls, which the outdoor temperature alone
doesn't capture. It needs a weather entity. Wind is read in whatever unit the weather entity uses and
converted to m/s (1 m/s ≈ 2.2 mph ≈ 3.6 km/h).

| Entity | Default | Meaning |
|---|---|---|
| `number.samsung_ehs_wind_boost_gain` | 0.3 | K of flow added per m/s of wind above the threshold. 0 turns wind boost off. |
| `number.samsung_ehs_wind_boost_threshold` | 5 m/s | Wind below this (about 11 mph, a moderate breeze) adds nothing |

With the defaults, 10 m/s (22 mph) adds 1.5 °C and 15 m/s (34 mph, a gale) adds the maximum of 3 °C.
If the room still drops in strong wind, raise the gain. If it overshoots, lower it.

### Room influence (instant)

| Entity | Default | Meaning |
|---|---|---|
| `number.samsung_ehs_room_influence_gain` | 3 | K of flow added per K the room is below target (and removed when above) |
| `number.samsung_ehs_room_influence_limit` | 5 K | Maximum room correction either way |

### Curve learning (slow)

| Entity | Default | Meaning |
|---|---|---|
| `number.samsung_ehs_adaptive_learning_rate` | 0.25 ¹ | K per hour per K of room error. 0 turns learning off and holds the current offset. |
| `number.samsung_ehs_adaptive_offset_limit` | 5 K | Maximum learned offset at each curve end, either way |

| Button | Meaning |
|---|---|
| `button.samsung_ehs_apply_learned_curve` | Copy the learned offsets into the two curve points and reset them to zero |
| `button.samsung_ehs_reset_curve_learning` | Discard the learned offsets |

### Boost

| Button | Meaning |
|---|---|
| `button.samsung_ehs_boost_heating` | Run space heating at the maximum flow temperature for one hour, even if the room is warm or the climate is Off. Pressing again restarts the hour. |
| `button.samsung_ehs_cancel_boost` | End a boost early |

The climate entity's *Boost* preset does the same thing. During a boost nothing is learned, so the curve
isn't skewed. Afterwards the thermostat takes over again. The minimum on time counts from the start of
the boost, so after an hour heating can pause straight away if the room is warm. A restart of Home
Assistant cancels a boost.

### Thermostat

| Entity | Default | Meaning |
|---|---|---|
| `number.samsung_ehs_thermostat_overshoot` | 1.0 K | Pause heating when the room reaches target + this. Resume at target. |
| `number.samsung_ehs_minimum_on_time` | 20 min | Heating can't be paused until it has run this long. 20 min means at most 3 starts per hour. |
| `number.samsung_ehs_minimum_off_time` | 0 min | Heating can't restart until it has been paused this long |

Turning the climate entity Off, or back to Heat, ignores both minimum times.

---

## Diagnostic entities

Found under the device's **Diagnostic** section.

| Entity | Meaning |
|---|---|
| `sensor.samsung_ehs_flow_setpoint` | Flow temperature setpoint the unit is actually using (register 68). See [Flow target vs flow setpoint](#flow-target-vs-flow-setpoint). |
| `sensor.samsung_ehs_outdoor_unit_error_code` | 0 = OK, otherwise the Samsung error code (e.g. 201) |
| `sensor.samsung_ehs_indoor_unit_error_code` | 0 = OK, otherwise the Samsung error code |
| `sensor.samsung_ehs_controller_room_temperature` | Room temperature from the Samsung wired controller (disabled by default). Used only if no room sensor is configured. |

A **diagnostics download** (device page → ⋮ → *Download diagnostics*) contains the raw decoded
registers, all tuning values, the latest calculation and the energy counters. Attach it to bug reports.

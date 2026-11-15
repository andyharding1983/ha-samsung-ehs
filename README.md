# Samsung EHS Heat Pump for Home Assistant

[![Validate](https://github.com/andyharding1983/ha-samsung-ehs/actions/workflows/validate.yml/badge.svg)](https://github.com/andyharding1983/ha-samsung-ehs/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Local control of Samsung EHS air-to-water heat pumps through
Samsung's **MIM-B19N** Modbus interface module, with Home Assistant-side
**room thermostat with weather compensation** (forecast-aware, room influence, self-learning curve) and **COP tracking**.

No cloud, no SmartThings.

## Features

| Platform | Entities |
|---|---|
| Climate | Room thermostat: Heat/Off, room target temperature, Boost preset |
| Water heater | Hot water on/off, Eco / Standard / Power / Force, tank target, optional time schedule (can be turned off for heating-only systems) |
| Sensor | Flow, return, ΔT, hot water tank, outdoor temp, flow rate, compressor frequency, heat output, electrical power, COP (instant / today / lifetime), heat and electrical energy, flow target, learned curve offset, error codes |
| Binary sensor | Heat demand, defrost, compressor running, problem |
| Switch | Quiet mode |
| Select | Heat emitter type: underfloor or radiators, which applies sensible presets |
| Number | Heating curve, thermostat and learning tuning |
| Button | Boost heating, boost hot water (1 h), cancel either, apply learned curve, reset curve learning |

📖 **[Full entity reference](docs/entities.md)**: every entity, attribute and setting explained, including
[flow target vs flow setpoint](docs/entities.md#flow-target-vs-flow-setpoint).

## Hardware

* Samsung **MIM-B19N** (or MIM-B19NT) connected to the indoor unit's F1/F2 bus.
* A USB–RS485 adapter wired to the MIM-B19N's **R1/R2** terminals (A/B).
* Serial settings are fixed by the module: **9600 baud, 8 data bits, even parity, 1 stop bit**.
* The Modbus address is set with the DIP switches (SW4/SW5) plus the rotary switch (SW1).
  Power-cycle the module after changing it.

> **One Modbus master only.** If something else is already polling the MIM-B19N
> (a third-party controller, Home Assistant's built-in `modbus:` YAML, etc.), disconnect it
> first. Two masters on one RS485 bus will corrupt each other's traffic.

## Installation

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/andyharding1983/ha-samsung-ehs`, category *Integration*.
2. Install **Samsung EHS Heat Pump** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Samsung EHS*.
4. Enter the serial port (prefer `/dev/serial/by-id/…`), the Modbus address and the indoor index (`0` for a single unit).

Then open **Configure** on the integration to set:

| Option | Used for |
|---|---|
| Hot water cylinder | Turn off on heating-only systems to hide all hot water controls |
| Hot water schedule | Optional Schedule helper: hot water on during its blocks, off outside ([details](docs/entities.md#hot-water-schedule)) |
| Room temperature sensor | Room influence (falls back to the Samsung controller's sensor) |
| Heat pump electrical power sensor | COP (W or kW, ideally a dedicated meter on the outdoor unit supply) |
| Weather forecast | Forecast blending for weather compensation |
| Outdoor temperature sensor override | Use instead of the heat pump's own outdoor sensor |
| Read extended registers | Outdoor temperature, flow rate and compressor frequency (see below) |

## Dashboard

A ready-made dashboard is in [`dashboards/samsung_ehs.yaml`](dashboards/samsung_ehs.yaml). It has sections
for heating, weather compensation (with the curve chart and learning history), hot water, and performance
(power, heat, COP).

1. Create a new dashboard from scratch.
2. Open its **Raw configuration editor** and paste the file in.

The curve chart needs [ApexCharts card](https://github.com/RomRider/apexcharts-card). Everything else uses
built-in cards. The file's header has the details.

## Trying it without hardware

Enter `simulator` as the serial port. A simulated heat pump follows the outdoor temperature
from your chosen weather entity (or outdoor sensor override). It responds to setpoint writes,
reheats hot water and defrosts in cold weather. If no power sensor is configured, it also
supplies its own electrical power reading, so COP works. This is useful for trying out
weather compensation settings and dashboards before you connect the real unit.

## How heating is controlled

Home Assistant always controls the heat pump. The climate entity works like a normal room thermostat.
Set a room target and Home Assistant does the rest. Turn off the Samsung controller's own water-law
(WL) setting so the two don't fight.

### Weather-compensated flow temperature

```
effective_outdoor = (1 − w) · outdoor_now + w · mean(forecast over next N hours)
curve_flow        = straight line through (design outdoor, design flow) and (mild outdoor, mild flow)
room_offset       = clamp(gain · (room_target − room_temp), ±limit)
wind_offset       = clamp(wind gain · (wind − wind threshold), 0 … 3 K)
flow_setpoint     = clamp(curve_flow + adaptive_offset + room_offset + wind_offset, min flow, max flow), rounded to 0.5 °C
```

* **Forecast blending** starts warming the house before a cold snap and eases off before a mild
  spell. This matters for slow systems such as underfloor heating. Set the weight to `0` to disable it.
* **Room influence** reacts straight away to the room error. A gain of 3 means 1 °C below target
  raises the flow by 3 °C.
* **Wind boost** adds flow when it's windy. It uses the wind now plus the forecast wind over the
  look-ahead, so it starts before the room cools. The default is +0.3 °C per m/s above 5 m/s, up to +3 °C.
  Set the gain to `0` to turn it off.
* The **learned curve** adapts slowly. While space heating runs (not during defrost or hot water), the
  room error is learned separately at the **cold and mild ends of the curve**, so the curve's slope
  corrects itself as well as its level, and the room settles on target in all weathers. It won't wind
  up while the flow is held at its min/max, and it survives restarts. Press **Apply learned curve** to
  copy what it has learned into the curve points.
* **Min/max flow temperature** are the hard limits on the result. The curve itself keeps going in cold
  weather. Choose your **heat emitter type** (underfloor 25–45 °C, radiators 30–55 °C) to set sensible
  limits, curve and learning speed, then fine-tune.
* A new setpoint is only written when it changes by ≥ 0.5 °C, and at most every 5 min
  (configurable), to keep bus traffic and EEPROM writes low.

### Thermostat on/off

* When the room reaches **target + overshoot** (default 1 °C), space heating is switched off at the unit.
* When the room falls back to **target**, it is switched on again.
* Above the **heating cut-off** effective outdoor temperature, heating stays off.
* **Minimum on time** (default 20 min) protects the compressor. With no minimum off time, that
  limits the unit to **3 starts per hour**. An optional minimum off time is also available.
* If the room sensor becomes unavailable, heating is never paused. It runs on weather
  compensation alone until the sensor comes back.
* Hot water is controlled separately and keeps working while space heating is paused.

The `Flow target` sensor shows the working: curve flow, adaptive offset, room offset,
effective outdoor temperature, forecast mean, heat demand and last write. The `Flow setpoint`
diagnostic sensor is the value the unit is actually using. See
[flow target vs flow setpoint](docs/entities.md#flow-target-vs-flow-setpoint).

### Boost

**Boost heating** (a button, or the climate entity's *Boost* preset) runs space heating at the maximum
flow temperature for one hour, even if the thermostat is satisfied or set to Off. Pressing it again
restarts the hour. **Cancel boost** ends it early. Nothing is learned during a boost. Afterwards normal
control resumes, and the minimum on time counts from the start of the boost. A restart of Home Assistant
cancels a boost.

### Seeing what it has learned

The two *Learned flow at cold end / mild end* sensors have a history graph that shows how the curve is
changing. There's an optional ApexCharts card that plots the set and learned curves against outdoor
temperature. See [docs/entities.md](docs/entities.md#viewing-the-learned-curve).

### Tuning tips

1. Get the curve roughly right. If the heat pump keeps pausing on overshoot, the curve is too high.
   If the learned offset keeps climbing, it is too low.
2. Lower the flow temperatures until the house *just* holds temperature. Lower flow means higher COP,
   and long steady runs beat on/off cycling.
3. Once the learned offsets have settled over a few weeks of varied weather, press **Apply learned curve**.

## COP

* **Heat output** = flow rate (L/min) ÷ 60 × 4.18 kJ/kg·K × (flow − return).
* **COP** = heat output ÷ electrical power (blank below 100 W, capped at 10).
* **COP today / lifetime** use energy integrated from both, including defrost losses.

## Register map

From Samsung's MIM-B19N installation manual (Table 2). PDU addresses, indoor unit 0 (base 50):

| Addr | Value | Access |
|---|---|---|
| 0 | Module error status (bitfield) | R |
| 1 | Outdoor unit error code | R |
| 2 | Defrost (0 / 0xFF = off) | R |
| 52 | On/off | R/W |
| 53 | Mode (0 auto, 1 cool, 4 heat) | R/W |
| 59 | Room temperature ×10 | R |
| 63 | Indoor unit error code | R |
| 65 | Water in (return) ×10 | R |
| 66 | Water out (flow) ×10 | R |
| 68 | Water out setpoint ×10 (heat 15–55) | R/W |
| 72 | Hot water on/off | R/W |
| 73 | Hot water mode (0 eco, 1 std, 2 power, 3 force) | R/W |
| 74 | Hot water setpoint ×10 (30–70) | R/W |
| 75 | Hot water temperature ×10 | R |
| 78 | Quiet mode | R/W |
| 79 | Away mode | R/W |

### Extended registers

Values that aren't in the standard table are exposed by writing NASA message IDs to
6000 (outdoor) and 7000 (indoor). The integration requests:

| Read addr | NASA ID | Value |
|---|---|---|
| 5 | `0x8204` | Outdoor temperature ×10 |
| 6 | `0x8238` | Compressor frequency (Hz) |
| 7 | `0x42E9` | Water flow rate ×10 (L/min) |

This mechanism isn't in Samsung's public manual. If these sensors look wrong on your unit,
please open an issue with a debug log.

## Debug logging

```yaml
logger:
  logs:
    custom_components.samsung_ehs: debug
    pymodbus: info
```

## Disclaimer

Not affiliated with or endorsed by Samsung. You use this at your own risk. The integration writes
setpoints to your heat pump, so keep an eye on it after installation.

# Samsung EHS Heat Pump for Home Assistant

[![Validate](https://github.com/andyharding1983/ha-samsung-ehs/actions/workflows/validate.yml/badge.svg)](https://github.com/andyharding1983/ha-samsung-ehs/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Local control of Samsung EHS air-to-water heat pumps through
Samsung's **MIM-B19N** Modbus interface module, with Home Assistant-side
**weather compensation** (forecast-aware, with room influence) and **COP tracking**.

No cloud, no SmartThings.

## Features

| Platform | Entities |
|---|---|
| Climate | Space heating/cooling on/off, mode, flow setpoint — or room target when weather compensation is on |
| Water heater | Hot water on/off, Eco / Standard / Power / Force, tank target |
| Sensor | Flow, return, ΔT, flow setpoint, hot water tank, outdoor temp, flow rate, compressor frequency, heat output, COP (instant / today / lifetime), heat energy, WC target, error codes |
| Binary sensor | Defrost, compressor running, problem |
| Switch | Quiet mode, away mode, weather compensation |
| Number | Heating curve and weather compensation tuning |

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
| Room temperature sensor | Room influence (falls back to the Samsung controller's sensor) |
| Heat pump electrical power sensor | COP (W or kW, ideally a dedicated meter on the outdoor unit supply) |
| Weather forecast | Forecast blending for weather compensation |
| Outdoor temperature sensor override | Use instead of the heat pump's own outdoor sensor |
| Read extended registers | Outdoor temperature, flow rate and compressor frequency (see below) |

## Weather compensation

When the **Weather compensation** switch is on (and the heat pump is on in Heat mode), Home Assistant
calculates the flow temperature and writes it to the unit. Turn off the Samsung controller's own
water-law (WL) setting so the two don't fight.

```
effective_outdoor = (1 − w) · outdoor_now + w · mean(forecast over next N hours)
curve_flow        = straight line through (design outdoor, design flow) and (mild outdoor, mild flow)
room_offset       = clamp(gain · (room_target − room_temp), ±limit)
flow_setpoint     = clamp(curve_flow + room_offset, min flow, max flow), rounded to 0.5 °C
```

* **Forecast blending** starts warming the house before a cold snap and eases off before a mild
  spell. This matters for slow systems such as underfloor heating. Set the weight to `0` to disable it.
* **Room influence** corrects the curve using a reference room. A gain of 3 means
  1 °C below target raises the flow by 3 °C.
* Above the **heating cut-off** effective outdoor temperature, the flow is held at the minimum.
* A new setpoint is only written when it changes by ≥ 0.5 °C, at most every *WC interval*
  (default 5 min), to keep bus traffic and EEPROM writes low.

While weather compensation is on, the climate entity's target is the **room target**. With it off,
the climate entity's target is the **flow setpoint**, written directly.

The `Weather compensation target` sensor exposes the working: curve flow, room offset, effective
outdoor temperature, forecast mean and last write.

### Tuning tips

1. Start with room gain `0` and tune the curve until rooms are roughly right in steady weather.
2. Lower the flow temperatures until the house *just* holds temperature. Lower flow means higher COP.
3. Then add a room gain of 1–3 and a limit of 3–5 K.

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

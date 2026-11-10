"""CO2 unit conversion: P_CO2 [mmHg] = F_CO2 [%] / 100 · (P_B − P_H2O)."""

DEFAULT_PH2O_MMHG = 47  # saturated water vapor at 37 °C


def pct_to_mmhg(pct: float, barometric_mmhg: float, water_vapor: bool = False, ph2o: float = DEFAULT_PH2O_MMHG) -> float:
    return (pct / 100) * (barometric_mmhg - (ph2o if water_vapor else 0))


def mmhg_to_pct(mmhg: float, barometric_mmhg: float, water_vapor: bool = False, ph2o: float = DEFAULT_PH2O_MMHG) -> float:
    return (mmhg / (barometric_mmhg - (ph2o if water_vapor else 0))) * 100


# ---------- gas conditions for volumes


def water_vapor_mmhg(temp_c: float) -> float:
    """Saturated water-vapor pressure, mmHg (Antoine equation; 47.1 at 37 °C)."""
    return 10 ** (8.07131 - 1730.63 / (233.426 + temp_c))


def btps_factor(ambient_temp_c: float, barometric_mmhg: float) -> float:
    """ATPS → BTPS: what a volume measured at room temperature, saturated, becomes at
    body temperature (37 °C), saturated. About 1.09 at 22 °C.

    V_BTPS = V_ATPS · (310.15 / (273.15 + T)) · (P_B − P_H2O(T)) / (P_B − 47)
    """
    ph2o = water_vapor_mmhg(ambient_temp_c)
    return (310.15 / (273.15 + ambient_temp_c)) * (barometric_mmhg - ph2o) / (barometric_mmhg - DEFAULT_PH2O_MMHG)


#: dynamic viscosity at about 20 °C, µPa·s
VISCOSITY = {"n2": 17.6, "o2": 20.2, "co2": 14.7}
VISCOSITY_AIR = 0.2095 * VISCOSITY["o2"] + 0.7905 * VISCOSITY["n2"]


def gas_factor(o2_pct: float, co2_pct: float = 0.0) -> float:
    """What to multiply the flow of a linear (Fleisch or screen) pneumotach by when it
    was calibrated in air but is measuring a mix of O2, CO2 and the rest N2.

    Such a pneumotach's pressure drop is proportional to viscosity, so it over-reads
    in a more viscous gas: true flow = reading × μ_air / μ_mix. The mix viscosity is
    taken as the mole-fraction average, a first-order estimate (within about 2 %);
    calibrating the syringe stroke in the bag gas is more accurate. Pure O2 gives 0.90.
    """
    o2 = min(100.0, max(0.0, o2_pct)) / 100
    co2 = min(1 - o2, max(0.0, co2_pct) / 100)
    mix = o2 * VISCOSITY["o2"] + co2 * VISCOSITY["co2"] + (1 - o2 - co2) * VISCOSITY["n2"]
    return VISCOSITY_AIR / mix

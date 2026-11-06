"""CO2 unit conversion: P_CO2 [mmHg] = F_CO2 [%] / 100 · (P_B − P_H2O)."""

DEFAULT_PH2O_MMHG = 47  # saturated water vapor at 37 °C


def pct_to_mmhg(pct: float, barometric_mmhg: float, water_vapor: bool = False, ph2o: float = DEFAULT_PH2O_MMHG) -> float:
    return (pct / 100) * (barometric_mmhg - (ph2o if water_vapor else 0))


def mmhg_to_pct(mmhg: float, barometric_mmhg: float, water_vapor: bool = False, ph2o: float = DEFAULT_PH2O_MMHG) -> float:
    return (mmhg / (barometric_mmhg - (ph2o if water_vapor else 0))) * 100

export const DEFAULT_PH2O_MMHG = 47; // saturated water vapor at 37 °C

/** P_CO2 [mmHg] = F_CO2 [%] / 100 · (P_B − P_H2O) */
export function pctToMmHg(pct: number, barometricMmHg: number, waterVapor = false, ph2o = DEFAULT_PH2O_MMHG): number {
  return (pct / 100) * (barometricMmHg - (waterVapor ? ph2o : 0));
}

export function mmHgToPct(mmHg: number, barometricMmHg: number, waterVapor = false, ph2o = DEFAULT_PH2O_MMHG): number {
  return (mmHg / (barometricMmHg - (waterVapor ? ph2o : 0))) * 100;
}

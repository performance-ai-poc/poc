
export type Band = "low" | "medium" | "high";


export type Source = "mock" | "instrumented" | "inferred" | "unavailable";

export interface DriftMetric {
  id: string;
  label: string;
  value: number;
  band: Band;
  source: Source;
}

export interface TechnicalMetric {
  id: string;
  label: string;
  value: number;
  band: Band;
  source: Source;
}

export interface ResourceMetric {
  id: string;
  label: string;
  percent: number;
  band: Band;
  source: Source;
}

export interface CorrectiveAction {
  id: string;
  label: string;
  enabled: boolean;
}

export interface DashboardData {
  driftMetrics: DriftMetric[];
  technicalMetrics: TechnicalMetric[];
  resourceMetrics: ResourceMetric[];
  correctiveActions: CorrectiveAction[];
}
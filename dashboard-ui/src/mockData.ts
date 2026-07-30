import type { DashboardData } from "./types";

export const mockDashboardData: DashboardData = {
  driftMetrics: [
    { id: "concept-drift", label: "Concept Drift", value: 31, band: "low", source: "mock" },
    { id: "covariate-shift", label: "Covariate Shift", value: 50, band: "medium", source: "mock" },
    { id: "label-drift", label: "Label Drift", value: 100, band: "high", source: "mock" },
    { id: "feature-drift", label: "Feature Drift", value: 35, band: "medium", source: "mock" },
    { id: "prediction-drift", label: "Prediction Drift", value: 31, band: "low", source: "mock" },
  ],
  technicalMetrics: [
    { id: "completeness", label: "Completeness", value: 20, band: "high", source: "mock" },
    { id: "avoidance", label: "Avoidance", value: 13, band: "high", source: "mock" },
    { id: "hallucination", label: "Hallucination", value: 17, band: "high", source: "mock" },
    { id: "excessive-sentiment", label: "Excessive Sentiment", value: 15, band: "high", source: "mock" },
    { id: "excessive-agency", label: "Excessive Agency", value: 11, band: "high", source: "mock" },
    { id: "accuracy", label: "Accuracy", value: 8, band: "medium", source: "mock" },
    { id: "contexted", label: "Contexted", value: 9, band: "medium", source: "mock" },
    { id: "relevancy", label: "Relevancy", value: 7, band: "medium", source: "mock" },
    { id: "latency", label: "Latency", value: 3, band: "low", source: "mock" },
    { id: "toxicity", label: "Toxicity", value: 8, band: "low", source: "mock" },
    { id: "fluency", label: "Fluency", value: 15, band: "medium", source: "mock" },
  ],
  resourceMetrics: [
    { id: "memory", label: "Memory", percent: 25, band: "low", source: "mock" },
    { id: "compute", label: "Compute", percent: 20, band: "low", source: "mock" },
    { id: "storage", label: "Storage", percent: 40, band: "medium", source: "mock" },
    { id: "bandwidth", label: "Bandwidth", value: 78.4, unit: "Mbps", band: "high", source: "mock" },
  ],
  correctiveActions: [
    { id: "micro-retrain", label: "Micro Retrain", enabled: true },
    { id: "micro-randomize", label: "Micro Randomize", enabled: true },
    { id: "covariate-reset", label: "Covariate Reset", enabled: true },
    { id: "revert-model", label: "Revert Model", enabled: true },
  ],
};

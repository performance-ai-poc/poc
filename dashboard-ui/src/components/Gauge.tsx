import GaugeComponent from "react-gauge-component";
import type { Band } from "../types";

interface GaugeProps {
  value: number;
  band: Band;
  displayValue: string;
}

const BAND_COLOR: Record<Band, string> = {
  low: "#16a34a",
  medium: "#f59e0b",
  high: "#dc2626",
};

const TRACK_COLOR = "#e4e5ea";

export default function Gauge({ value, band, displayValue }: GaugeProps) {
  const clamped = Math.max(0, Math.min(100, value));

  return (
    <div 
      className="gauge" 
      style={{ 
        display: "flex", 
        flexDirection: "column", 
        alignItems: "center", 
        width: "100%" 
      }}
    >
      <div style={{ fontSize: "10px", color: "#6b7280", textTransform: "uppercase" }}>
        {band}
      </div>

      <GaugeComponent
          type="semicircle"
          marginInPercent={0.015} 
          value={clamped}
          minValue={0}
          maxValue={100}
          arc={{
            width: 0.1,
            padding: 0,
            cornerRadius: 4,
            subArcs: [
              { limit: clamped, color: BAND_COLOR[band] },
              { color: TRACK_COLOR },
            ],
          }}
          pointer={{
            type: "needle",
            color: "#ef4444", 
            baseColor: "#ef4444", 
            width: 4, 
            animate: true,
          }}
          labels={{
            valueLabel: { hide: true },
            tickLabels: { hideMinMax: true },
          }}
        />

      <div 
        className="gauge-labels"
        style={{
          display: "flex",
          justifyContent: "space-between",
          width: "100%",
          marginTop: "1px",
          fontSize: "10px",
          color: "#9ca3af",
          boxSizing: "border-box" 
        }}
      >
        <span>LOW</span>
        <span>HIGH</span>
      </div>

      <div className="gauge-value" style={{ marginTop: "1rem", fontSize: "24px", fontWeight: "bold" }}>
        {displayValue}
      </div>
      
      <div className={`band-pill band-${band}`} style={{ marginTop: "0.5rem" }}>
        {band}
      </div>
    </div>
  );
}
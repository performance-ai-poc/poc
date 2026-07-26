import type { Band } from "../types";

interface RingMetricProps {
  label: string;
  value: number;
  band: Band;
  displayValue?: string;
}

const RADIUS = 30;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const SEGMENT_DASHARRAY = "15 3.85";


const BAND_COLORS: Record<Band, { fg: string; bg: string }> = {
  low: { fg: "#16a34a", bg: "#dcfce7" },    
  medium: { fg: "#f59e0b", bg: "#fef3c7" }, 
  high: { fg: "#dc2626", bg: "#fee2e2" },   
};

export default function RingMetric({ label, value, band, displayValue }: RingMetricProps) {
  const filled = Math.max(0, Math.min(100, value * 4));
  const offset = CIRCUMFERENCE * (1 - filled / 100);
  
  const colors = BAND_COLORS[band] || BAND_COLORS.high;
  
  const maskId = `segment-mask-${label.replace(/\s+/g, '-')}`;

  return (
    <div className="ring-metric" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      
      <div style={{ fontSize: "12px", fontWeight: "bold", textTransform: "uppercase", marginBottom: "0.5rem" }}>
        {label}
      </div>

      <svg viewBox="0 0 72 72" className="ring-svg" style={{ width: "60px", height: "60px", transform: "rotate(-90deg)" }}>
        <defs>
          <mask id={maskId}>
            <circle
              cx={36} cy={36} r={RADIUS}
              fill="transparent"
              stroke="white"
              strokeWidth="8" 
              strokeDasharray={SEGMENT_DASHARRAY}
            />
          </mask>
        </defs>

        <circle
          cx={36} cy={36} r={RADIUS}
          fill="transparent"
          stroke={colors.bg}
          strokeWidth="6"
          mask={`url(#${maskId})`} 
        />
        
        <circle
          cx={36} cy={36} r={RADIUS}
          fill="transparent"
          stroke={colors.fg}
          strokeWidth="6"
          strokeDasharray={CIRCUMFERENCE} 
          strokeDashoffset={offset}       
          mask={`url(#${maskId})`}        
          style={{ transition: "stroke-dashoffset 0.5s ease" }}
        />
      </svg>
      
      <div className="ring-value" style={{ fontSize: "20px", fontWeight: "bold", marginTop: "0.5rem" }}>
        {displayValue ?? value}
      </div>
      
    </div>
  );
}
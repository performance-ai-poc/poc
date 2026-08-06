import PanelHeader from "./components/PanelHeader";
import Gauge from "./components/Gauge";
import CorrectiveOptions from "./components/CorrectiveOptions";
import { useDashboardData } from "./useDashboardData";
import "./App.css";

function App() {
  // The Technical/quality panel is intentionally not rendered yet: those tiles
  // need the content-eval pipeline, which is upcoming work. The analytics API
  // still returns them, so re-enabling is just adding the panel back here.
  const { driftMetrics, resourceMetrics, correctiveActions } =
    useDashboardData();

  return (
    <main className="dashboard-main">
      <section className="panel">
        <PanelHeader title="Drift Condition" />
        <div className="drift-grid">
          {driftMetrics.map((metric) => (
            <div key={metric.id} className="metric-card">
              <div className="metric-card-label">{metric.label.toUpperCase()}</div>
              <Gauge
                value={metric.value}
                band={metric.band}
                displayValue={
                  metric.source === "unavailable" ? "N/A" : `${metric.value}%`
                }
              />
            </div>
          ))}
        </div>
      </section>

      <section className="panel panel-resources">
        <PanelHeader title="Resources" />
        <div className="resource-grid">
          {resourceMetrics.map((metric) => (
            <div key={metric.id} className="metric-card">
              <div className="metric-card-label">{metric.label.toUpperCase()}</div>
              {metric.percent != null ? (
                <Gauge
                  value={metric.percent}
                  band={metric.band}
                  displayValue={
                    metric.source === "unavailable" ? "N/A" : `${metric.percent}%`
                  }
                />
              ) : (
                <div className="resource-value">
                  <div className={`band-pill band-${metric.band}`}>{metric.band}</div>
                  <div className="resource-number">
                    {metric.source === "unavailable"
                      ? "N/A"
                      : `${metric.value?.toFixed(1)} ${metric.unit ?? ""}`.trim()}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <PanelHeader title="Corrective Options" />
        <CorrectiveOptions actions={correctiveActions} />
      </section>
    </main>
  );
}

export default App;

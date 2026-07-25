import PanelHeader from "./components/PanelHeader";
import Gauge from "./components/Gauge";
import RingMetric from "./components/RingMetric";
import CorrectiveOptions from "./components/CorrectiveOptions";
import { useDashboardData } from "./useDashboardData";
import "./App.css";

function App() {
  const { driftMetrics, technicalMetrics, resourceMetrics, correctiveActions } =
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

      <div className="panel-row">
        <section className="panel panel-technical">
          <PanelHeader title="Technical" />
          <div className="technical-grid">
            {technicalMetrics.map((metric) => (
              <RingMetric
                key={metric.id}
                label={metric.label.toUpperCase()}
                value={metric.value}
                band={metric.band}
                displayValue={metric.source === "unavailable" ? "N/A" : undefined}
              />
            ))}
          </div>
        </section>

        <section className="panel panel-resources">
          <PanelHeader title="Resources" />
          <div className="resource-grid">
            {resourceMetrics.map((metric) => (
              <div key={metric.id} className="metric-card">
                <div className="metric-card-label">{metric.label.toUpperCase()}</div>
                <Gauge
                  value={metric.percent}
                  band={metric.band}
                  displayValue={
                    metric.source === "unavailable" ? "N/A" : `${metric.percent}%`
                  }
                />
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="panel">
        <PanelHeader title="Corrective Options" />
        <CorrectiveOptions actions={correctiveActions} />
      </section>
    </main>
  );
}

export default App;
interface PanelHeaderProps {
  title: string;
}

export default function PanelHeader({ title }: PanelHeaderProps) {
  return (
    <div className="panel-header">
      <h2>
        <span className="panel-bullet">•</span> {title}
      </h2>
      <div className="panel-controls">
        <button type="button" className="btn-outline" disabled>
          Export
        </button>
        <button type="button" className="btn-outline" disabled>
          Daily
        </button>
      </div>
    </div>
  );
}
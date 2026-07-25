import type { CorrectiveAction } from "../types";

interface CorrectiveOptionsProps {
  actions: CorrectiveAction[];
}

export default function CorrectiveOptions({ actions }: CorrectiveOptionsProps) {
  function handleClick(actionId: string) {
    console.log(`[corrective-options] "${actionId}" clicked — not wired to any backend action yet.`);
  }

  return (
    <div className="corrective-options">
      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          className="corrective-btn"
          disabled={!action.enabled}
          onClick={() => handleClick(action.id)}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}
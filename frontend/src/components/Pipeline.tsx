import type { StoreStatus } from "../types";

const STAGES: { key: StoreStatus; label: string }[] = [
  { key: "requested", label: "Requested" },
  { key: "provisioning", label: "Provisioning" },
  { key: "initializing", label: "Initializing" },
  { key: "ready", label: "Ready" },
];

export default function Pipeline({ status }: { status: StoreStatus }) {
  if (status === "deleting") {
    return (
      <div className="removing" role="status">
        <span>Removing the namespace and all store data</span>
        <div className="removing-bar" />
      </div>
    );
  }

  const current = STAGES.findIndex((stage) => stage.key === status);
  return (
    <ol className="pipeline" data-state={status === "failed" ? "failed" : "running"}>
      {STAGES.map((stage, index) => {
        const state =
          status === "failed"
            ? "pending"
            : index < current || status === "ready"
              ? "done"
              : index === current
                ? "current"
                : "pending";
        return (
          <li key={stage.key} data-stage={state} aria-current={state === "current" ? "step" : undefined}>
            <span className="node" />
            <span className="stage-label">{stage.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

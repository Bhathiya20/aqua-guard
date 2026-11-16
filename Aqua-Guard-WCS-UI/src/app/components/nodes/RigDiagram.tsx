/** How to connect the nodes physically for a series calibration: Supply -> Node A -> Node B -> ... -> outlet into a measuring container. */
export function RigDiagram({ labels }: { labels: string[] }) {
  const n = Math.max(labels.length, 1);
  const W = 120 + n * 150 + 120, H = 130;
  const xs = labels.map((_, i) => 130 + i * 150);
  const endX = 130 + n * 150 - 20;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Calibration rig: supply, ${labels.join(", ")}, measuring container`} data-testid="rig-diagram" className="w-full max-w-full h-auto rounded-md border bg-card">
      <defs><marker id="arr" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" /></marker></defs>
      <g className="text-sky-700" stroke="currentColor" fill="none">
        <line x1="40" y1="60" x2={endX + 20} y2="60" strokeWidth="6" strokeLinecap="round" opacity="0.35" />
      </g>
      {/* supply tap */}
      <g className="text-foreground" stroke="currentColor" fill="none" strokeWidth="2">
        <rect x="8" y="42" width="34" height="36" rx="4" />
        <path d="M18 42 v-10 h14 v10" />
        <path d="M25 78 v8" />
      </g>
      <text x="25" y="104" textAnchor="middle" className="fill-current text-[11px]">Supply</text>
      {/* nodes */}
      {labels.map((l, i) => (
        <g key={l + i}>
          <rect x={xs[i] - 40} y="38" width="80" height="44" rx="8" className="fill-blue-50 stroke-blue-600 dark:fill-blue-950" strokeWidth="2" />
          <text x={xs[i]} y="58" textAnchor="middle" className="fill-current text-[12px] font-semibold">{l}</text>
          <g className="text-blue-700" stroke="currentColor" fill="currentColor"><line x1={xs[i] - 14} y1="70" x2={xs[i] + 14} y2="70" strokeWidth="2" markerEnd="url(#arr)" /></g>
          <text x={xs[i]} y="104" textAnchor="middle" className="fill-muted-foreground text-[10px]">arrow = flow direction</text>
        </g>
      ))}
      {/* container */}
      <g className="text-foreground" stroke="currentColor" fill="none" strokeWidth="2">
        <path d={`M${endX + 24} 44 l6 44 h36 l6 -44`} />
        <path d={`M${endX + 40} 58 q10 -6 20 0 t0 0`} className="text-sky-600" />
        <path d={`M${endX + 16} 60 h12`} strokeDasharray="3 3" />
      </g>
      <text x={endX + 48} y="104" textAnchor="middle" className="fill-current text-[11px]">Measuring container</text>
    </svg>
  );
}

export const CONNECT_NOTES = [
  "Connect the supply tap to the first node, then each node to the next one, with short hoses or pipe. No branches, no leaks.",
  "Water must enter each sensor at the arrow side. Keep the nodes at about the same height and the hoses full of water (no loops that trap air).",
  "Let the last hose run into a container whose volume you can measure exactly (a marked bucket, a jug, or a scale: 1 kg = 1 L).",
  "Use a tap you can open slowly, medium and fully: the three runs should use different flow rates.",
];

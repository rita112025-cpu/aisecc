/** Unified diff rendered as text; React escapes every line (no raw HTML). */
export function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <div className="state">沒有差異</div>;
  return (
    <pre className="diff" aria-label="差異內容">
      {diff.split("\n").map((line, i) => {
        const cls = line.startsWith("@@") ? "hunk"
          : line.startsWith("+") && !line.startsWith("+++") ? "add"
            : line.startsWith("-") && !line.startsWith("---") ? "del" : undefined;
        return cls ? <span key={i} className={cls}>{line}</span> : <span key={i}>{line}{"\n"}</span>;
      })}
    </pre>
  );
}

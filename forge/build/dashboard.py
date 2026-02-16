"""Forge dashboard — HTML report of run history, scores, and costs.

Generates a self-contained HTML file with charts and tables
showing pipeline performance over time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path

from rich.console import Console

console = Console()


@dataclass
class RunRecord:
    """A single pipeline run record."""
    objective: str
    planner: str
    coder: str
    quality_score: int
    grade: str
    duration_secs: float
    cost_usd: float
    total_rounds: int
    approved: bool
    timestamp: str = ""
    files_created: int = 0
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


HISTORY_FILE = ".forge-history.json"


def save_run(working_dir: str, record: RunRecord) -> None:
    """Append a run record to history."""
    history_file = Path(working_dir) / HISTORY_FILE
    runs = _load_runs(history_file)
    runs.append(asdict(record))
    history_file.write_text(json.dumps(runs, indent=2))


def load_history(working_dir: str) -> list[dict]:
    """Load run history."""
    return _load_runs(Path(working_dir) / HISTORY_FILE)


def _load_runs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def generate_dashboard(working_dir: str) -> Path:
    """Generate an HTML dashboard from run history.

    Returns the path to the generated HTML file.
    """
    runs = load_history(working_dir)
    output = Path(working_dir) / ".forge-dashboard.html"

    # Compute stats
    if runs:
        avg_score = sum(r.get("quality_score", 0) for r in runs) / len(runs)
        total_cost = sum(r.get("cost_usd", 0) for r in runs)
        best_run = max(runs, key=lambda r: r.get("quality_score", 0))
        approval_rate = sum(1 for r in runs if r.get("approved")) / len(runs) * 100
    else:
        avg_score = total_cost = approval_rate = 0
        best_run = {}

    scores_json = json.dumps([r.get("quality_score", 0) for r in runs])
    costs_json = json.dumps([round(r.get("cost_usd", 0), 4) for r in runs])
    labels_json = json.dumps([
        r.get("timestamp", "")[:10] for r in runs
    ])

    # Build run rows
    run_rows = ""
    for r in reversed(runs[-50:]):
        grade = r.get("grade", "?")
        color = {"A": "#22c55e", "B": "#86efac", "C": "#fbbf24", "D": "#f97316", "F": "#ef4444"}.get(grade, "#888")
        run_rows += f"""
        <tr>
          <td>{r.get('timestamp', '?')[:19]}</td>
          <td title="{r.get('objective', '')[:100]}">{r.get('objective', '?')[:40]}...</td>
          <td>{r.get('planner', '?')}</td>
          <td>{r.get('coder', '?')}</td>
          <td><span style="color:{color};font-weight:700">{grade}</span></td>
          <td>{r.get('quality_score', 0)}</td>
          <td>{r.get('duration_secs', 0):.1f}s</td>
          <td>${r.get('cost_usd', 0):.4f}</td>
          <td>{'✅' if r.get('approved') else '⚠️'}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Forge Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 2rem; }}
  h1 {{ font-size: 2rem; margin-bottom: 1.5rem; }}
  h1 span {{ color: #38bdf8; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem; margin-bottom: 2rem; }}
  .stat {{ background: #1e293b; border-radius: 12px; padding: 1.5rem;
           border: 1px solid #334155; }}
  .stat .label {{ font-size: 0.85rem; color: #94a3b8; margin-bottom: 0.5rem; }}
  .stat .value {{ font-size: 2rem; font-weight: 700; }}
  .chart-container {{ background: #1e293b; border-radius: 12px; padding: 1.5rem;
                      border: 1px solid #334155; margin-bottom: 2rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ text-align: left; padding: 0.75rem; border-bottom: 2px solid #334155;
       color: #94a3b8; font-weight: 600; }}
  td {{ padding: 0.75rem; border-bottom: 1px solid #1e293b; }}
  tr:hover {{ background: #1e293b; }}
  .empty {{ text-align: center; padding: 3rem; color: #64748b; }}
</style>
</head>
<body>
  <h1>⚡ <span>Forge</span> Dashboard</h1>

  <div class="stats">
    <div class="stat">
      <div class="label">Total Runs</div>
      <div class="value">{len(runs)}</div>
    </div>
    <div class="stat">
      <div class="label">Avg Quality Score</div>
      <div class="value">{avg_score:.0f}</div>
    </div>
    <div class="stat">
      <div class="label">Approval Rate</div>
      <div class="value">{approval_rate:.0f}%</div>
    </div>
    <div class="stat">
      <div class="label">Total Cost</div>
      <div class="value">${total_cost:.2f}</div>
    </div>
  </div>

  <div class="chart-container">
    <canvas id="scoreChart" height="80"></canvas>
  </div>

  <div class="chart-container" style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>Time</th><th>Objective</th><th>Planner</th><th>Coder</th>
          <th>Grade</th><th>Score</th><th>Duration</th><th>Cost</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        {run_rows if run_rows else '<tr><td colspan="9" class="empty">No runs yet. Run forge duo to get started!</td></tr>'}
      </tbody>
    </table>
  </div>

  <script>
    const ctx = document.getElementById('scoreChart').getContext('2d');
    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: {labels_json},
        datasets: [
          {{ label: 'Quality Score', data: {scores_json}, borderColor: '#38bdf8',
             backgroundColor: 'rgba(56,189,248,0.1)', fill: true, tension: 0.3 }},
          {{ label: 'Cost ($)', data: {costs_json}, borderColor: '#fbbf24',
             backgroundColor: 'rgba(251,191,36,0.1)', fill: true, tension: 0.3,
             yAxisID: 'y1' }}
        ]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
        scales: {{
          x: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#1e293b' }} }},
          y: {{ position: 'left', ticks: {{ color: '#64748b' }}, grid: {{ color: '#1e293b' }},
               min: 0, max: 100 }},
          y1: {{ position: 'right', ticks: {{ color: '#fbbf24' }}, grid: {{ display: false }},
                min: 0 }}
        }}
      }}
    }});
  </script>
</body>
</html>"""

    output.write_text(html)
    return output

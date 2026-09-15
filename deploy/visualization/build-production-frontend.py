"""Apply only the visualization UI to the verified main frontend baseline."""

import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
target = Path(sys.argv[1]).resolve()
assert not target.exists(), "Use a new empty output directory"
target.mkdir(parents=True)
archive = target / "baseline.tar"
with archive.open("wb") as output:
    subprocess.run(
        ["git", "archive", "main", "frontend"], cwd=repo, stdout=output, check=True
    )
with tarfile.open(archive) as package:
    package.extractall(target, filter="data")
archive.unlink()
files = [
    "src/lib/api.ts",
    "src/lib/signal-series.ts",
    "src/lib/signal-series.test.ts",
    "src/app/[locale]/app/sensors/SignalChart.tsx",
]
for name in files:
    shutil.copyfile(repo / "frontend" / name, target / "frontend" / name)
page = target / "frontend/src/app/[locale]/app/sensors/page.tsx"
content = page.read_text()
content, count = re.subn(
    r"import \{\s*LineChart,[\s\S]*?\} from 'recharts';",
    "import SignalChart from './SignalChart';",
    content,
    count=1,
)
assert count == 1
replacement = """<SignalChart
                              key={series.sensor_id}
                              series={series}
                              color={config.color}
                              unit={config.unit}
                              formatTick={(t) => {
                                if (timeRange === 'live') return formatTimeWithSeconds(t);
                                if (timeRange === '1h' || timeRange === '24h') return formatTime(t);
                                return formatDate(t);
                              }}
                            />"""
content, count = re.subn(
    r'<ResponsiveContainer width="100%" height=\{220\}>[\s\S]*?</ResponsiveContainer>',
    replacement,
    content,
    count=1,
)
assert count == 1
page.write_text(content)
print("Production frontend: main baseline plus visualization files only")

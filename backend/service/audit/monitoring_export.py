"""Create a private observation export and run the actual offline monitoring tool."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from service.shared.database import get_engine
from service.audit.prediction_monitoring import build_report, export_observations, monitoring_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    bundle, thresholds = monitoring_settings()
    payload, coverage = export_observations(get_engine(), synthetic=args.synthetic)
    previous = os.umask(0o077)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        source = args.output / 'export.json'
        source.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
        (args.output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
        run = subprocess.run([sys.executable, '-m', 'loadpilot_model.evaluation.monitoring', '--input', str(source),
            '--bundle', str(bundle), '--output', str(args.output / 'monitoring')], check=False)
        if run.returncode:
            return run.returncode
        result = build_report(payload, coverage, bundle, thresholds)
        (args.output / 'alerts.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    finally:
        os.umask(previous)
    print(f"Monitoring status: {result['status']}; genuine evidence: {coverage['has_genuine_observations']}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

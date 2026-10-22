"""Export original backend evidence and run the model's offline audit CLI."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from service.shared.database import get_engine
from service.audit.live_audit import export_live_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory under data/ for export, coverage and audit report')
    parser.add_argument('--run-id', type=UUID)
    parser.add_argument('--synthetic', action='store_true', help='Export explicitly synthetic captures only')
    args = parser.parse_args()
    payload, coverage = export_live_audit(get_engine(), args.run_id, synthetic=args.synthetic)
    previous_umask = os.umask(0o077)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        source = args.output / 'export.json'
        source.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
        (args.output / 'coverage.json').write_text(json.dumps(coverage, indent=2, allow_nan=False) + '\n')
        result = subprocess.run([sys.executable, '-m', 'loadpilot_model.evaluation.live_audit',
            '--input', str(source), '--output', str(args.output / 'audit')], check=False)
    finally:
        os.umask(previous_umask)
    if not coverage['has_genuine_observations']:
        print('No genuine captured decisions in this export; genuine live validation remains pending.')
    if coverage['skipped_captures']:
        print('Some captures cannot fit the strict audit schema; investigate coverage.json and native audit records.')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())

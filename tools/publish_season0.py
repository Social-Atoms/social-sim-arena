"""Rebuild S0 closure metadata and immutable result snapshots, offline only."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import refresh, season0


def publish(path=ROOT / 'site/data.json'):
    path = Path(path)
    data = json.loads(path.read_text())
    data['season0_main'] = refresh.build_main_board(data['rounds'])
    season0.publish_artifacts(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1)+'\n')
    print('S0:',data['season0_main']['phase'],data['season0_main']['settled_rounds'],
          'settled;',data['season0_main']['pending_count'],'pending')


if __name__ == '__main__':
    publish()

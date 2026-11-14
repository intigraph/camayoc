"""Disposable review-routing fixture; no HTTP, state or live identities."""
import argparse
from pathlib import Path
import sys
p=argparse.ArgumentParser(description=__doc__);p.add_argument('scripts',type=Path);p.add_argument('--fixed',action='store_true');args=p.parse_args()
sys.path.insert(0,str(args.scripts.resolve()))
import seed_work_items as native
import change_adapter as adapter
board=native.Board('https://example.org/project/demo','demo');reader=native.Reader(None,board)
parent='demo-example';subject=board.subject(parent)
changes=[dict(entity=subject+'/comment/36',graph=board.graph,attribute=native.SCHEMA+'dateModified'),dict(entity=subject,graph=board.graph,attribute=native.SCHEMA+'version')]
def no_http(*args):raise AssertionError('routing must perform no HTTP')
print('CANDIDATE' if args.fixed else 'BASE')
try:
 result=adapter.targets(no_http,'review',changes,[],native_reader=reader)
 assert args.fixed and result==[parent]
 print('comment + parent changes: OK; one exact parent policy key')
except native.NativeUnknown as exc:
 assert not args.fixed
 print('comment + parent changes: REFUSED;',str(exc))
print('PASS; no HTTP, database, delivery, replay or live-state changes')

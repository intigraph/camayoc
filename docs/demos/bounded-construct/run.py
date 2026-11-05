#!/usr/bin/env python3
"""Repeat the bounded CONSTRUCT on a private backup of the supplied producer DB."""
import hashlib,json,os,re,sqlite3,subprocess,tempfile,time
from pathlib import Path
ROOT=Path(os.environ.get('PL07_SOURCE',Path(__file__).resolve().parents[3])).resolve()
BASE='b3b80b1ac93dac0069cce4a47496598bbec4d6da'
binary=Path(os.environ['PL07_ENGINE']).resolve(strict=True)
source=Path(os.environ['PL07_PRODUCER_DB']).resolve(strict=True)
before=subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':queries/repository-share-camayoc.rq'],text=True)
after=(ROOT/'queries/repository-share-camayoc.rq').read_text()
print('Private query-only fixture; no publication or live graph',flush=True)
print('engine_sha256='+hashlib.sha256(binary.read_bytes()).hexdigest(),flush=True)
with tempfile.TemporaryDirectory() as folder:
    work=Path(folder);db=work/'producer.db'
    with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as original:
        with sqlite3.connect(db) as target:original.backup(target)
    (work/'.bobbin').mkdir();(work/'.bobbin/config.toml').write_text('[quipu.search]\nquery_timeout_ms = 30000\n')
    for label,query in [('BEFORE',before),('AFTER',after)]:
        start=time.monotonic()
        result=subprocess.run([str(binary),'query',query,'--db',str(db)],cwd=work,capture_output=True,text=True,timeout=45)
        elapsed=time.monotonic()-start
        timed_out=bool(re.search(r'query timeout: exceeded 30000ms',result.stderr))
        footer=re.search(r'\n(\d+) triples\s*$',result.stdout)
        print(label,json.dumps({'seconds':round(elapsed,3),'exit':result.returncode,'query_sha256':hashlib.sha256(query.encode()).hexdigest(),'query_timeout':timed_out,'triples':int(footer[1]) if footer else None}),flush=True)
        if label=='BEFORE':
            assert result.returncode!=0 and timed_out
        else:
            assert result.returncode==0 and footer and int(footer[1])>3

"""arming: agent-invoked -- bounded proof against a disposable loopback server.

Supply the reviewed server executable; never connects to a configured store.
Run under the repository's HOME-escape guard. No production credentials copied.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.request

import seed_work_item_sync as sync
import seed_change_custody as custody


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True,type=Path)
    parser.add_argument('--expected-sha',required=True)
    args=parser.parse_args()
    binary=args.server.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='fused-work-item-lab-') as directory:
        root=Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={k:v for k,v in os.environ.items() if not k.startswith(('QUIPU_','BOBBIN_'))}
        env['HOME']=str(root)
        with (root/'server.log').open('w+') as log:
            process=subprocess.Popen([str(binary),'--db',str(root/'fresh.db'),'--bind',f'127.0.0.1:{port}'],cwd=root,env=env,stdout=log,stderr=log)
            calls=[]
            def post(endpoint,body):
                request=urllib.request.Request(base+endpoint,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Quipu-Client':'agent-adhoc'})
                start=time.monotonic()
                with urllib.request.urlopen(request,timeout=5) as response:
                    raw=response.read(262145)
                assert len(raw)<=262144,'lab response exceeds existing byte budget'
                calls.append(dict(endpoint=endpoint,seconds=time.monotonic()-start,bytes=len(raw)))
                return json.loads(raw)
            try:
                for _ in range(100):
                    if process.poll() is not None:raise RuntimeError('lab server exited')
                    try:
                        with urllib.request.urlopen(base+'/health',timeout=.2):break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('lab health deadline')
                with urllib.request.urlopen(base+'/version',timeout=3) as response:version=json.load(response)
                assert version['git_sha']==args.expected_sha and version['git_dirty'] is False,version
                graph='urn:camayoc:fused-proof:lab'
                post('/graph/create',{'graph':graph})
                expected='exact "escaped"\ncomment'
                envelope={'request':{'graph':graph},'description':expected}
                def write(item,kind,comments):
                    subject=sync.projection.A+item
                    triples=[] if kind is None else [f'<{subject}> a <{sync.projection.A+kind}> .']
                    triples += [f'<{subject}> <{sync.native.RDFS}comment> {json.dumps(v)} .' for v in comments]
                    result=post('/knot',{'graph':graph,'turtle':'\n'.join(triples),'source':'fused-proof-lab','snapshot':item,'replace_snapshot':True})
                    assert result.get('conforms') is True and type(result.get('tx_id')) is int,result
                cases=[('good','WorkItem',[expected],True),('extra','WorkItem',[expected,'other'],True),
                       ('wrong-type','Other',[expected],None),('missing-type',None,[expected],None),
                       ('no-comment','WorkItem',[],False),('wrong-comment','WorkItem',['other'],False),
                       ('boundary128','WorkItem',[expected]+[str(i) for i in range(127)],True),
                       ('overflow129','WorkItem',[expected]+[str(i) for i in range(128)],None)]
                results=[]
                for suffix,kind,comments,wanted in cases:
                    item='lab-'+suffix;write(item,kind,comments)
                    observed=[]
                    for method in [sync.verify,sync.verify_fused]:
                        before=len(calls)
                        try:value=method(post,envelope,item)
                        except custody.CustodyUnknown:value=None
                        observed.append(value)
                        if method is sync.verify_fused:assert len(calls)-before==1
                    assert observed==[wanted,wanted],(suffix,observed,wanted)
                    results.append(dict(case=suffix,legacy=observed[0],fused=observed[1]))
                item='lab-good'
                profiles=[]
                def profiled_post(endpoint,body):
                    value=post(endpoint,dict(body,execution_profile=True))
                    profiles.append(value.get('execution_profile'))
                    return value
                assert sync.verify_fused(profiled_post,envelope,item)
                assert len(profiles)==1 and isinstance(profiles[0],dict),'actual query profile unavailable'
                # Representative bound-comment visitor access shape, not a
                # claim to capture the complete compiled SPARQL statement set.
                # The custom GROUP BY function is registered for EXPLAIN only;
                # RDF term semantics are exercised by the real server above.
                with sqlite3.connect(f'file:{root/"fresh.db"}?mode=ro',uri=True) as connection:
                    connection.create_function('quipu_term_key',1,lambda value:value,deterministic=True)
                    def term(iri):return connection.execute('SELECT id FROM terms WHERE iri=?',(iri,)).fetchone()[0]
                    sql=('SELECT e,a,v FROM facts WHERE e IN (?) AND a IN (?) AND op=1 '
                         'AND g IN (?) AND valid_to IS NULL GROUP BY e,a,quipu_term_key(v)')
                    plan=connection.execute('EXPLAIN QUERY PLAN '+sql,
                        (term(sync.projection.A+item),term(sync.native.RDFS+'comment'),term(graph))).fetchall()
                    assert any('SEARCH' in row[3] for row in plan),plan
                timings={'legacy':[],'fused':[]};counts=collections.Counter()
                for n in range(10):
                    order=[('legacy',sync.verify),('fused',sync.verify_fused)]
                    if n%2:order.reverse()
                    for label,method in order:
                        before=len(calls);start=time.monotonic();assert method(post,envelope,item)
                        timings[label].append(time.monotonic()-start);counts[label]+=len(calls)-before
                # Every committed state is invalid: type+wrong description OR
                # other type+expected description. No fused read may invent a
                # valid combination while the writer atomically alternates.
                item='lab-concurrent';write(item,'WorkItem',['wrong'])
                errors=[];writes=[]
                def writer():
                    try:
                        for n in range(20):
                            write(item,'WorkItem' if n%2 else 'Other',['wrong' if n%2 else expected])
                            writes.append(time.monotonic());time.sleep(.005)
                    except BaseException as error:errors.append(repr(error))
                thread=threading.Thread(target=writer);start=time.monotonic();thread.start()
                for _ in range(20):
                    try:value=sync.verify_fused(post,envelope,item)
                    except custody.CustodyUnknown:value=None
                    assert value is not True,'inconsistent type/comment combination accepted'
                end=time.monotonic();thread.join(timeout=10)
                assert not thread.is_alive() and not errors,(errors,thread.is_alive())
                overlap=sum(start<t<end for t in writes)
                assert overlap>0,'snapshot concurrency arm did not overlap any commit'
                report={'status':'PASS','server_version':version,'server_binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                        'semantic_cases':results,'execution_profile':profiles[0],
                        'representative_bound_comment_explain':plan,'explain_scope':'visitor access shape only; not a complete physical SPARQL trace','paired_samples_per_method':10,'proof_requests':dict(counts),
                        'median_seconds':{k:statistics.median(v) for k,v in timings.items()},'raw_seconds':timings,
                        'concurrent_reads':20,'atomic_writes':len(writes),'commits_during_reads':overlap}
                print(json.dumps(report,sort_keys=True),flush=True)
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
    return 0


if __name__=='__main__':raise SystemExit(main())

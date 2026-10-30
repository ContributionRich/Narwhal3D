#!/usr/bin/env python3
"""Bounded local request/plan/indexed-snapshot timings; not a whole-app speed claim."""
import argparse
import json
from pathlib import Path
import statistics
import time
import test_ai_sustained_lifecycle as lifecycle
from test_ai_plan import plan, step
from test_automation_context import fixture
from test_automation_bridge import load_modules, unload, SOURCES


def measured(call, rounds=50):
    timings=[]
    for _ in range(rounds):
        start=time.perf_counter_ns();call();timings.append(time.perf_counter_ns()-start)
    return round(statistics.median(timings)/1000,2)


def run(library):
    lifecycle.LIBRARY=library
    report={'units':'median_microseconds','rounds':50,'network_calls':0,'geometry_operations':0}
    with lifecycle.session('openai_responses') as (_state,w):
        for count in (1,16):
            data=plan(*(step('s'+str(i)) for i in range(count)))
            report['validate_plan_'+str(count)]=measured(lambda:w.validate_plan(data))
    for count in (0,64):
        context={'selection':{'objects':[{'object_id':f'00000000-0000-0000-0000-{i+1:012x}','name':'fixture'} for i in range(count)]}}
        def assemble():
            with lifecycle.session('openai_responses') as (_state,w):
                request=w.prepare(lifecycle.DOC,user_text='inspect and plan',context=context)
                raw=json.dumps(request.data['body'],separators=(',',':')).encode()
                assert len(raw)<=256*1024
                assert len(request.data['body']['input'])==2
                report['request_bytes_'+str(count)]=len(raw)
        report['create_prepare_encode_close_'+str(count)]=measured(assemble)
    package,tools,_bridge,_kernel=load_modules(SOURCES)
    try:
        for count in (32,1000000):
            app=fixture(count=count);reader=tools.ApplicationReader(app,'fixture')
            offset=0 if count==32 else 9000
            def page():
                table=app.model.file.Objects;table.indices.clear()
                result=reader.read('narwhal.objects',{'offset':offset,'limit':32})
                assert table.indices==list(range(offset,offset+32))
                assert len(result['objects'])==32
                assert result['total_count']==count
            report['indexed_objects_'+str(count)]=measured(page)
    finally: unload(package)
    print(json.dumps(report,sort_keys=True,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--library',type=Path,required=True)
    run(p.parse_args().library.resolve())

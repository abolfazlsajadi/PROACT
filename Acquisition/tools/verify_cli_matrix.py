#!/usr/bin/env python3
"""Exhaustive offline CLI acceptance matrix; never opens an instrument."""
from __future__ import annotations
import itertools, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = str(ROOT / 'run.sh')
TARGETS = ('aes1','aes2','sw_rv','sw_rv_masked','xoodyak','ascon')
results=[]
counter=0
started=time.monotonic()

def invoke(name,args,ok=True,need=()):
    global counter
    counter += 1
    suffix=f'_offline_cli_audit_{counter:03d}'
    cmd=[RUN,*args]
    if '--suffix' not in args:
        cmd += ['--suffix',suffix]
    env=os.environ.copy(); env.update(PYTHONDONTWRITEBYTECODE='1',TERM='dumb',NO_COLOR='1')
    p=subprocess.run(cmd,cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
    passed=(p.returncode==0) if ok else (p.returncode!=0)
    if ok:
        passed = passed and 'Disk estimate for' in p.stdout and all(x in p.stdout for x in need)
    else:
        passed = passed and ('Error:' in p.stdout or 'Usage:' in p.stdout) and 'Traceback' not in p.stdout and all(x in p.stdout for x in need)
    results.append({'name':name,'expected':'success' if ok else 'reject','passed':passed,'rc':p.returncode,'cmd':cmd,'output':p.stdout[-1200:]})
    if not passed:
        print('FAIL',name,'rc',p.returncode,'\n',p.stdout,file=sys.stderr)

# Every target and all save-format selections, including repeat normalization.
format_sets=[([], 'native'),(['--format','h5'],'native + h5'),(['--format','csv'],'native + csv'),(['--format','native','--format','h5','--format','csv','--format','h5'],'native + h5 + csv')]
for target,(fmt,label) in itertools.product(TARGETS,format_sets):
    invoke(f'{target}:format:{label}',['--target',target,'--traces','11','--samples','100',*fmt,'--estimate'],need=(f'save formats  : {label}',))

# Per-target automatic CPA; TVLA block/alternate; combined TVLA+CPA; warm-up.
for target in TARGETS:
    invoke(f'{target}:cpa',['--target',target,'--traces','13','--auto-cpa','--samples','100','--estimate'],need=('analysis      : CPA',))
    for order in ('block','alternate'):
        invoke(f'{target}:tvla:{order}',['--target',target,'--traces','7','--tvla','--tvla-order',order,'--samples','100','--estimate'],need=('x 14 traces','7 fixed + 7 random',f'order={order}','analysis      : TVLA'))
    invoke(f'{target}:tvla+cpa',['--target',target,'--traces','7','--tvla','--auto-cpa','--samples','100','--estimate'],need=('analysis      : TVLA+CPA',))
    invoke(f'{target}:warmup',['--target',target,'--traces','11','--warmup','1k','--samples','100','--estimate'],need=('1,000 discarded operations/session',))

# Scope matrix: every target, SCPI dialect, board-clock source; never opens VISA/Husky.
for target,dialect,clocksrc in itertools.product(TARGETS,('auto','keysight','tek'),('husky','external')):
    channel='8' if dialect=='tek' else '1'
    trig='AUX' if dialect=='tek' else 'CHANnel2'
    invoke(f'{target}:scope:{dialect}:{clocksrc}',['--target',target,'--traces','9','--backend','scope','--scope-resource','TCPIP0::192.0.2.1::INSTR','--scope-dialect',dialect,'--scope-clock-source',clocksrc,'--scope-channel',channel,'--scope-trigger-source',trig,'--scope-transfer-timeout-ms','17000','--samples','100','--format','h5','--estimate'],need=('scope-controlled','calibration/clipping validated','transfer wait : 17,000 ms','additional h5'))

# Trigger selections: auto/core/firmware for AES/Sw-RV; valid seven-bit AEAD values.
for target,mode in itertools.product(TARGETS[:4],('auto','core','firmware')):
    invoke(f'{target}:trigger:{mode}',['--target',target,'--traces','5','--trigger',mode,'--samples','100','--estimate'])
for target,trig in itertools.product(TARGETS[4:],('0x00','0x11','0x12','0x23','0x7f')):
    invoke(f'{target}:trigger:{trig}',['--target',target,'--traces','5','--trigger',trig,'--samples','100','--estimate'])

# Clock/UART matrix, auto-sized/explicit samples, policies, offsets, gain, count parser.
for target,clock in itertools.product(TARGETS,('12.5','25','50','100')):
    invoke(f'{target}:clock:{clock}',['--target',target,'--traces','5','--clock-mhz',clock,'--samples','100','--estimate'],need=(f'target clock  : {clock} MHz',))
for clock,baud in (('25','57600'),('50','115200'),('50','115741'),('100','230400')):
    invoke(f'uart:{clock}:{baud}',['--target','aes1','--traces','5','--clock-mhz',clock,'--baud',baud,'--samples','100','--estimate'],need=(f'host {int(baud):,} baud (explicit)',))
for target,key,inp in itertools.product(TARGETS,('fixed','random'),('fixed','random')):
    invoke(f'{target}:policy:{key}:{inp}',['--target',target,'--traces','5','--key',key,'--input',inp,'--samples','100','--estimate'])
for target,samples in itertools.product(TARGETS,('0','100','596')):
    invoke(f'{target}:samples:{samples}',['--target',target,'--traces','5','--samples',samples,'--estimate'])
for value in ('10','1k','1.5k','2M'):
    invoke(f'count:{value}',['--target','aes1','--traces',value,'--samples','100','--estimate'])
invoke('husky:offset',['--target','aes1','--traces','5','--offset','13','--samples','100','--estimate'])
invoke('husky:gain',['--target','aes1','--traces','5','--gain','42.5','--samples','100','--estimate'],need=('gain          : 42.5 dB on Husky',))
invoke('misc:flags',['--target','aes1','--traces','5','--samples','100','--reset','--allow-nofit','--seed','123','--chunk','1','--port','/dev/null','--estimate'],need=('UART port     : /dev/null',))

# Fail-closed argument cases. All must reject before any capture path.
invalid=[
 ('missing-target',['--traces','5','--estimate'],('give --target and --traces',)),
 ('missing-traces',['--target','aes1','--estimate'],('give --target and --traces',)),
 ('bad-target',['--target','bogus','--traces','5','--estimate'],('Invalid value for',)),
 ('zero-traces',['--target','aes1','--traces','0','--estimate'],('greater than zero',)),
 ('negative-traces',['--target','aes1','--traces=-1','--estimate'],('greater than zero',)),
 ('bad-traces',['--target','aes1','--traces','frog','--estimate'],()),
 ('zero-warmup',['--target','aes1','--traces','5','--warmup=-1','--estimate'],('non-negative',)),
 ('bad-warmup',['--target','aes1','--traces','5','--warmup','frog','--estimate'],()),
 ('small-samples',['--target','aes1','--traces','5','--samples','99','--estimate'],('at least 100',)),
 ('negative-samples',['--target','aes1','--traces','5','--samples=-1','--estimate'],('at least 100',)),
 ('bad-samples',['--target','aes1','--traces','5','--samples','frog','--estimate'],('Invalid value for',)),
 ('negative-offset',['--target','aes1','--traces','5','--offset=-1','--estimate'],('non-negative',)),
 ('scope-offset',['--target','aes1','--traces','5','--backend','scope','--offset','1','--estimate'],('nonzero --offset',)),
 ('zero-chunk',['--target','aes1','--traces','5','--chunk','0','--estimate'],('--chunk must be greater than zero',)),
 ('zero-clock',['--target','aes1','--traces','5','--clock-mhz','0','--estimate'],('positive and finite',)),
 ('negative-clock',['--target','aes1','--traces','5','--clock-mhz=-1','--estimate'],('positive and finite',)),
 ('nan-clock',['--target','aes1','--traces','5','--clock-mhz','nan','--estimate'],('positive and finite',)),
 ('inf-clock',['--target','aes1','--traces','5','--clock-mhz','inf','--estimate'],('positive and finite',)),
 ('zero-baud',['--target','aes1','--traces','5','--baud','0','--estimate'],('positive integer',)),
 ('bad-baud',['--target','aes1','--traces','5','--baud','frog','--estimate'],()),
 ('mismatch-baud',['--target','aes1','--traces','5','--baud','9600','--estimate'],('maximum 2%',)),
 ('nan-gain',['--target','aes1','--traces','5','--gain','nan','--estimate'],('finite',)),
 ('inf-gain',['--target','aes1','--traces','5','--gain','inf','--estimate'],('finite',)),
 ('tvla-random-key',['--target','aes1','--traces','5','--tvla','--key','random','--estimate'],('--tvla requires --key fixed',)),
 ('tvla-fixed-input',['--target','aes1','--traces','5','--tvla','--input','fixed','--estimate'],('--tvla controls fixed/random inputs',)),
 ('cpa-random-key',['--target','aes1','--traces','5','--auto-cpa','--key','random','--estimate'],('--auto-cpa requires --key fixed',)),
 ('cpa-fixed-input',['--target','aes1','--traces','5','--auto-cpa','--input','fixed','--estimate'],('--auto-cpa requires --input random',)),
 ('odd-tvla-block',['--target','aes1','--traces','5','--tvla','--tvla-block-size','3','--estimate'],('even integer',)),
 ('tiny-tvla-block',['--target','aes1','--traces','5','--tvla','--tvla-block-size','0','--estimate'],('even integer',)),
 ('bad-core-trigger',['--target','aes1','--traces','5','--trigger','0x11','--estimate'],('trigger must be auto',)),
 ('bad-aead-trigger-text',['--target','ascon','--traces','5','--trigger','core','--estimate'],('7-bit number',)),
 ('negative-aead-trigger',['--target','ascon','--traces','5','--trigger=-1','--estimate'],('seven bits',)),
 ('large-aead-trigger',['--target','ascon','--traces','5','--trigger','0x80','--estimate'],('seven bits',)),
 ('scope-aead-cpa',['--target','ascon','--traces','5','--backend','scope','--auto-cpa','--estimate'],('require Husky triggercfg 0x12',)),
 ('narrow-aead-cpa',['--target','xoodyak','--traces','5','--trigger','0x11','--auto-cpa','--estimate'],('require Husky triggercfg 0x12',)),
 ('bad-channel-low',['--target','aes1','--traces','5','--backend','scope','--scope-channel','0','--estimate'],('not in the range',)),
 ('bad-channel-high',['--target','aes1','--traces','5','--backend','scope','--scope-channel','9','--estimate'],('not in the range',)),
 ('bad-trigger-source',['--target','aes1','--traces','5','--backend','scope','--scope-trigger-source','CH1;RUN','--estimate'],('one SCPI name',)),
 ('port-control',['--target','aes1','--traces','5','--port','bad\nport','--estimate'],('control characters',)),
 ('resource-control',['--target','aes1','--traces','5','--backend','scope','--scope-resource','bad\nresource','--estimate'],('control characters',)),
 ('suffix-traversal',['--target','aes1','--traces','5','--suffix','../../escape','--estimate'],('suffix may contain only',)),
 ('suffix-backslash',['--target','aes1','--traces','5','--suffix','bad\\path','--estimate'],('suffix may contain only',)),
 ('bad-format',['--target','aes1','--traces','5','--format','mat','--estimate'],('Invalid value for',)),
 ('bad-backend',['--target','aes1','--traces','5','--backend','ad3','--estimate'],('Invalid value for',)),
 ('bad-dialect',['--target','aes1','--traces','5','--backend','scope','--scope-dialect','rigol','--estimate'],('Invalid value for',)),
 ('bad-clock-source',['--target','aes1','--traces','5','--backend','scope','--scope-clock-source','none','--estimate'],('Invalid value for',)),
]
for name,args,need in invalid:
    invoke('reject:'+name,args,ok=False,need=need)

summary={'total':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),'success_cases':sum(r['expected']=='success' for r in results),'rejection_cases':sum(r['expected']=='reject' for r in results),'elapsed_s':round(time.monotonic()-started,3),'results':results}
Path('/tmp/proact_cli_matrix_audit.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='results'},indent=2))
if summary['failed']:
    sys.exit(1)

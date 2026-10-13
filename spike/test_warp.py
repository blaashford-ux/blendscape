import numpy as np, sys
sys.path.insert(0, '.')
from warp import warp
from synth import kick, SR as sr
bpm=124.; beat=60/bpm*sr; off=0.37*sr; n_b=100
x=np.zeros((int((0.37+n_b*60/bpm+2)*sr),2),np.float32)
k=kick().astype(np.float32)
for i in range(n_b):
    j=int(round(off+i*beat)); x[j:j+len(k)]+=k[:,None]
def peaks(sig, exp_n):
    e=np.abs(sig.mean(1)); out=[]; 
    # onset = first sample exceeding 0.2*max within each window
    thr=0.2*e.max(); i=0
    while i<len(e):
        if e[i]>thr: out.append(i); i+=int(0.6*60/bpm*sr)
        else: i+=1
    return np.array(out)
for tgt in (128., 120., 124.):
    r=bpm/tgt
    src=off+beat*np.arange(n_b); dst=src*r
    y=warp(x,sr,src,dst); delay=0
    p=peaks(y,n_b)[:90]
    exp=dst[:len(p)]
    err=p-exp
    print(f'target {tgt}: ratio out/in {len(y)/len(x):.4f}  delay={delay}  err mean {err.mean():.1f} std {err.std():.1f} min {err.min():.0f} max {err.max():.0f} samples')
# ramp: ratio varies 1 -> 124/128 over beats 20..52
r_b=np.ones(n_b); r_b[20:52]=np.linspace(1,124/128,32); r_b[52:]=124/128
src=off+beat*np.arange(n_b+1)
dst=np.concatenate([[off],off+np.cumsum(beat*r_b)])
y=warp(x,sr,src,dst); delay=0
p=peaks(y,n_b)[:90]; err=p-dst[:len(p)]
print(f'ramp: err mean {err.mean():.1f} std {err.std():.1f} min {err.min():.0f} max {err.max():.0f}; n peaks {len(p)}')
print('--- per-beat error pattern, target 128')
r=bpm/128; src=off+beat*np.arange(n_b); dst=src*r
y=warp(x,sr,src,dst); p=peaks(y,n_b)[:90]; err=p-dst[:len(p)]
print(np.round(err[:40]).astype(int))
print(np.round(err[40:90]).astype(int))

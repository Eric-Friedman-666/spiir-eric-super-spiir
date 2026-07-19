#!/usr/bin/env python3
"""Strict read-only validator for live no-injection single backgrounds.

It never reads normal multi statistics and never writes a commit, manifest,
background, or event output.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, re, stat, sys
from pathlib import Path

HEX=re.compile(r"^[0-9a-f]{64}$")
MAX_SINGLE=256*1024*1024
MAX_META=8*1024*1024
TOP=("schema_version","background_kind","run_namespace_sha256",
"source_manifest_sha256","runtime_manifest_sha256","config_sha256",
"segment_xml_sha256","segment_canonical_sha256","template_shape_map_sha256",
"worker_id","worker_count","worker_bank_ids","accepted_version","epoch_gps",
"window_start_gps","window_end_gps","window_duration","update_period",
"far_floor_count","tail_log10_far","backgrounds")
IDS=("run_namespace_sha256","source_manifest_sha256","runtime_manifest_sha256",
"config_sha256","segment_xml_sha256","segment_canonical_sha256",
"template_shape_map_sha256")

class ContractError(RuntimeError): pass

def strict(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise ContractError("duplicate JSON key: "+k)
        out[k]=v
    return out

def exact(v,keys,label):
    if type(v) is not dict or tuple(v)!=tuple(keys):
        raise ContractError(label+" keys/order mismatch")
def integer(v,label,minimum=0,maximum=(1<<63)-1):
    if type(v) is not int or not minimum<=v<=maximum:
        raise ContractError(label+" is not a bounded integer")
    return v
def digest(v,label):
    if type(v) is not str or not HEX.fullmatch(v):
        raise ContractError(label+" is not lowercase SHA256")
    return v
def gps(v,label):
    exact(v,("seconds","nanoseconds"),label)
    return integer(v["seconds"],label+".seconds")*1_000_000_000+integer(
        v["nanoseconds"],label+".nanoseconds",0,999_999_999)
def binary64(v,label):
    if type(v) is not str or len(v)>=64: raise ContractError(label+" is not binary64")
    try: x=float.fromhex(v)
    except ValueError as e: raise ContractError(label+" is not binary64") from e
    if not math.isfinite(x) or (x==0 and v.startswith("-")) or x.hex()!=v:
        raise ContractError(label+" is not unique canonical binary64")
    return x

def snapshot(path,label,limit):
    if not path.is_absolute(): raise ContractError(label+" path is not absolute")
    try: a=os.lstat(path)
    except OSError as e: raise ContractError(label+" cannot be stat'ed: "+str(e))
    if stat.S_ISLNK(a.st_mode) or not stat.S_ISREG(a.st_mode):
        raise ContractError(label+" must be a regular non-symlink file")
    if not 0<a.st_size<=limit: raise ContractError(label+" size is invalid")
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    try:
        b=os.fstat(fd); parts=[]; left=limit+1
        while left:
            chunk=os.read(fd,min(left,1024*1024))
            if not chunk: break
            parts.append(chunk); left-=len(chunk)
    finally: os.close(fd)
    c=os.lstat(path); data=b"".join(parts)
    ident=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns)
    if ident(a)!=ident(b) or ident(b)!=ident(c) or len(data)!=b.st_size:
        raise ContractError(label+" changed while being validated")
    if len(data)>limit: raise ContractError(label+" exceeds byte limit")
    return {"path":str(path),"sha256":hashlib.sha256(data).hexdigest(),
            "size":len(data),"mtime_ns":b.st_mtime_ns,"bytes":data}

def env_values(s,label):
    try: text=s["bytes"].decode("utf-8")
    except UnicodeDecodeError as e: raise ContractError(label+" is not UTF-8") from e
    out={}
    for line in text.splitlines():
        if not line or line.startswith("#"): continue
        if "=" not in line: raise ContractError(label+" has malformed line")
        k,v=line.split("=",1)
        if not k or k in out: raise ContractError(label+" has duplicate/empty key")
        out[k]=v
    return out

def producer_ids(root):
    if not root.is_absolute(): raise ContractError("producer root must be absolute")
    root=root.resolve(strict=True)
    if not root.is_dir(): raise ContractError("producer root is not a directory")
    ns=snapshot(root/"provenance/schema4/run_namespace.txt","producer run namespace",MAX_META)
    if ns["bytes"]!=("run_root="+str(root)+"\n").encode():
        raise ContractError("producer run namespace does not bind producer root")
    src=snapshot(root/"provenance/schema4/source_manifest.env","producer source manifest",MAX_META)
    run=snapshot(root/"provenance/runtime_snapshot/runtime_manifest.env","producer runtime manifest",MAX_META)
    vals=env_values(run,"producer runtime manifest")
    cfg=snapshot(root/"scripts/crashcar.env","producer config",MAX_META)
    cfg_values=env_values(cfg,"producer config")
    tail_text=(cfg_values.get("tail_log_FAR") or cfg_values.get("tai_log_FAR")
               or cfg_values.get("TAIL_LOG_FAR"))
    if tail_text:
        try: tail_log10_far=float(tail_text)
        except ValueError as e: raise ContractError("producer tail_log_FAR invalid") from e
    else:
        boundary_text=(cfg_values.get("tail_FAR") or cfg_values.get("far_fit_boundary")
                       or cfg_values.get("FAR_FIT_BOUNDARY") or "0.01")
        try: boundary=float(boundary_text)
        except ValueError as e: raise ContractError("producer tail_FAR invalid") from e
        if not math.isfinite(boundary) or not 0.0<boundary<1.0:
            raise ContractError("producer tail_FAR invalid")
        tail_log10_far=math.log10(boundary)
    if not math.isfinite(tail_log10_far) or not tail_log10_far<0.0:
        raise ContractError("producer tail_log_FAR invalid")
    shape=snapshot(root/"artifacts/crashcar_template_shape_map.csv","producer template map",MAX_SINGLE)
    ids={"run_namespace_sha256":ns["sha256"],"source_manifest_sha256":src["sha256"],
         "runtime_manifest_sha256":vals.get("runtime_files_manifest_sha256",""),
         "config_sha256":cfg["sha256"],
         "segment_xml_sha256":vals.get("crashcar_segment_xml_sha256",""),
         "segment_canonical_sha256":vals.get("crashcar_segment_livetime_json_sha256",""),
         "template_shape_map_sha256":shape["sha256"]}
    for k,v in ids.items(): digest(v,"producer "+k)
    return root,ids,tail_log10_far

def parse_ifo(v,ifo,start,end,duration):
    exact(v,("livetime","support_count","tail_fit","far_llr_points"),ifo+" background")
    live=gps(v["livetime"],ifo+".livetime")
    count=integer(v["support_count"],ifo+".support_count",1,1_000_000)
    if live>duration or live*5<=duration:
        raise ContractError(ifo+" occupancy is not strictly greater than 20 percent")
    tail=v["tail_fit"]
    exact(tail,("method","r_tail","slope","fit_unique_rank_count"),ifo+".tail_fit")
    if tail["method"]!="anchored_ols_all_unique_ranks_ge_r_tail":
        raise ContractError(ifo+" tail method mismatch")
    rtail=binary64(tail["r_tail"],ifo+".r_tail")
    if not binary64(tail["slope"],ifo+".slope")<0: raise ContractError(ifo+" slope invalid")
    fit=integer(tail["fit_unique_rank_count"],ifo+".fit_count",2,count)
    pts=v["far_llr_points"]
    if type(pts) is not list or len(pts)!=count: raise ContractError(ifo+" point count mismatch")
    prev=None; ranks=[]
    for n,pt in enumerate(pts):
        exact(pt,("gps","llr","far"),ifo+" point")
        t=gps(pt["gps"],ifo+".gps"); rank=binary64(pt["llr"],ifo+".llr")
        far=binary64(pt["far"],ifo+".far")
        if not start<=t<end or not far>0: raise ContractError(ifo+" point invalid")
        order=(rank,t)
        if prev is not None and order<prev: raise ContractError(ifo+" points not sorted")
        prev=order; ranks.append(rank)
    begin=0; live_s=live/1_000_000_000.0
    while begin<count:
        stop=begin+1
        while stop<count and ranks[stop]==ranks[begin]: stop+=1
        expected=((count-begin)/live_s).hex()
        if any(pts[j]["far"]!=expected for j in range(begin,stop)):
            raise ContractError(ifo+" Calculated FAR bit mismatch")
        begin=stop
    if len({r for r in ranks if r>=rtail})!=fit: raise ContractError(ifo+" tail count mismatch")
    return {"livetime_ns":live,"support_count":count}
def validate(root,worker,count,bpw,startbank):
    root,expected,expected_tail_log10_far=producer_ids(root)
    banks=list(range(startbank+bpw*worker,startbank+bpw*(worker+1)))
    snap=snapshot(root/"run"/f"{worker:03d}"/"single_background.json","single background",MAX_SINGLE)
    data=snap["bytes"]
    if not data.endswith(b"\n") or b"\r" in data or b"\n" in data[:-1]:
        raise ContractError("single background is not one canonical record")
    try: obj=json.loads(data.decode("ascii"),object_pairs_hook=strict)
    except (UnicodeDecodeError,json.JSONDecodeError) as e: raise ContractError("single JSON invalid") from e
    exact(obj,TOP,"single background")
    if obj["schema_version"]!=4 or obj["background_kind"]!="no_injection":
        raise ContractError("single kind/schema mismatch")
    if obj["worker_id"]!=worker or obj["worker_count"]!=count:
        raise ContractError("single worker mismatch")
    if obj["worker_bank_ids"]!=banks: raise ContractError("single bank geometry mismatch")
    version=integer(obj["accepted_version"],"accepted_version",1)
    epoch=gps(obj["epoch_gps"],"epoch_gps"); ws=gps(obj["window_start_gps"],"window_start_gps")
    we=gps(obj["window_end_gps"],"window_end_gps"); duration=gps(obj["window_duration"],"window_duration")
    update=gps(obj["update_period"],"update_period")
    if epoch!=we or ws>=we or duration<1 or update<1 or we-ws!=duration:
        raise ContractError("single time/coverage mismatch")
    tail_value=obj["tail_log10_far"]
    if (obj["far_floor_count"]!=1 or type(tail_value) not in (int,float)
            or isinstance(tail_value,bool) or not math.isfinite(float(tail_value))
            or not float(tail_value)<0.0
            or float(tail_value)!=expected_tail_log10_far):
        raise ContractError("single FAR contract mismatch")
    got={}
    for k in IDS:
        got[k]=digest(obj[k],k)
        if got[k]!=expected[k]: raise ContractError("single producer provenance mismatch: "+k)
    exact(obj["backgrounds"],("H1","L1"),"backgrounds")
    ifos={ifo:parse_ifo(obj["backgrounds"][ifo],ifo,ws,we,duration) for ifo in ("H1","L1")}
    return {"worker_id":worker,"worker_count":count,"worker_bank_ids":banks,
            "accepted_version":version,"coverage_end_gps_ns":we,
            "tail_log10_far":expected_tail_log10_far,
            "single_background_path":snap["path"],"single_background_sha256":snap["sha256"],
            "single_background_size":snap["size"],"single_background_mtime_ns":snap["mtime_ns"],
            "identities":got,"ifos":ifos}
def geometry(cmd,one):
    cmd.add_argument("--producer-root",required=True,type=Path)
    cmd.add_argument("--worker-count",required=True,type=int)
    cmd.add_argument("--banks-per-worker",required=True,type=int)
    cmd.add_argument("--start-bank",required=True,type=int)
    if one: cmd.add_argument("--worker",required=True,type=int)
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command",required=True)
    geometry(sub.add_parser("validate-single"),True); geometry(sub.add_parser("validate-all-singles"),False)
    a=p.parse_args()
    if not 1<=a.worker_count<=4096 or a.banks_per_worker<1 or a.start_bank<0:
        raise ContractError("invalid worker geometry")
    if hasattr(a,"worker") and not 0<=a.worker<a.worker_count: raise ContractError("worker out of range")
    if a.command=="validate-single":
        out=validate(a.producer_root,a.worker,a.worker_count,a.banks_per_worker,a.start_bank)
    else:
        root=a.producer_root.resolve(strict=True)
        workers=[validate(root,w,a.worker_count,a.banks_per_worker,a.start_bank) for w in range(a.worker_count)]
        out={"kind":"crashcar_live_single_validation","producer_root":str(root),
             "worker_count":a.worker_count,"banks_per_worker":a.banks_per_worker,
             "start_bank":a.start_bank,"workers":workers}
    print(json.dumps(out,ensure_ascii=True,separators=(",",":"),sort_keys=True))
if __name__=="__main__":
    try: main()
    except (ContractError,OSError,ValueError) as e:
        print("crashcar_live_background: "+str(e),file=sys.stderr); raise SystemExit(2)

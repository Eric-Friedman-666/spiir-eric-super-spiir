import json
import math
import os
from array import array
from itertools import groupby

import numpy as np

NS = 1000000000
BETA_STEP = float.fromhex("0x1.89374bc6a7efap-9")
LOG_2PI = float.fromhex("0x1.d67f1c864beb4p+0")
LOG_64 = float.fromhex("0x1.0a2b23f3bab73p+2")
OWNER = {"H1": 0, "H1V1": 0, "L1": 1, "L1V1": 1, "H1L1": 2, "H1L1V1": 2}
POINT = np.dtype([("gps", "i8"), ("llr", "f8"), ("far", "f8"), ("count", "u4")])

def _gps(value):
    return (value["seconds"] * NS + value["nanoseconds"] if isinstance(value, dict)
            else value.gpsSeconds * NS + value.gpsNanoSeconds)

def _llr(rho, chisq, shape, dof):
    if rho < 4.0 or chisq <= 0.0 or shape <= 0.0:
        return 0.0
    rho2, x = rho * rho, dof * chisq
    lam = rho2 * shape
    def normal(noncentral):
        mean, variance = dof + noncentral, 2.0 * (dof + 2.0 * noncentral)
        return -0.5 * (LOG_2PI + math.log(variance) +
                       (x - mean) * (x - mean) / variance)
    noise = normal(lam)
    terms = [normal(beta * beta * lam) for beta in
             (BETA_STEP + BETA_STEP * index for index in range(64))]
    maximum = max(terms)
    return (maximum + math.log(sum(math.exp(value - maximum)
                                   for value in terms))
            - LOG_64 - noise + rho2 / 2.0)

class SingleFar:
    def __init__(self, shapes=None, segments=None):
        self.producer = os.environ["CRASHCAR_ROLE"] == "A"
        self.path = os.environ["CRASHCAR_SINGLE_BACKGROUND_JSON"]
        names = ("RUN_NAMESPACE SOURCE_MANIFEST RUNTIME_MANIFEST CONFIG "
                 "SEGMENT_XML SEGMENT_CANONICAL TEMPLATE_SHAPE_MAP").split()
        self.provenance = {name.lower() + "_sha256": os.getenv(
            "CRASHCAR_" + name + "_SHA256", "") for name in names}
        self.start, self.window, self.update = (round(float(os.environ[name]) * NS) for name in
                                                ("DATA_START_TIME", "BACKGROUND_ACCUMULATION_SECONDS", "BACKGROUND_UPDATE_TRIGGER_SECONDS"))
        self.tail = float(os.environ["TAIL_LOG_FAR"])
        self.bucket, self.bucket_start = self.update, self.start
        self.support = [[array("q"), array("d")] for unused in range(2)]
        self.active = self.pending = None
        self.last_publish = self.last_refresh = 0
        self.detail = open(os.environ["CRASHCAR_DETAIL_OUTPUT_FNAME"], "w", buffering=1)
        self.detail.write("event_id,bankid,tmplt_idx,end_time,end_time_ns,ifo_id,snglsnr,chisq,llr,far_assigned_exact,feature_gps,background_version\n")
        if shapes is None:
            with open(os.environ["CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME"]) as source:
                next(source)
                values = (float.fromhex(line.split(",", 4)[3]) for line in source)
                shapes = np.fromiter(values, float, count=768000).reshape(2, 384, 1000)
        self.shapes = shapes
        if self.producer:
            if segments is None:
                with open(os.environ["CRASHCAR_SEGMENT_LIVETIME_JSON"]) as source:
                    data = json.load(source)
                segments = [[(_gps(span["start"]), _gps(span["end"])) for span in
                             data["targets"][ifo]["intervals"]] for ifo in ("H1", "L1")]
            self.segments = segments

    def process(self, events):
        ordered = sorted(events, key=lambda event: _gps(event.postcoh_inspiral.end))
        for event_gps, group in groupby(ordered, key=lambda event: _gps(event.postcoh_inspiral.end)):
            if self.producer and self.pending and event_gps > self.pending["available"]:
                self.active, self.pending = self.pending, None
            if not self.producer:
                self._refresh(event_gps)
            for event in group:
                row = event.postcoh_inspiral
                owner = OWNER.get(row.ifos, 3)
                row.H1_LLR = row.L1_LLR = 0.0
                if owner < 2:
                    row.far_sngl[owner] = 0.0
                for ifo in range(2):
                    if owner == 3 or owner < 2 and owner != ifo:
                        continue
                    llr = _llr(
                        float(row.snglsnr[ifo]), float(row.chisq[ifo]),
                        self.shapes[ifo, row.bankid, row.tmplt_idx],
                        120.0 if row.bankid < 100 else 600.0)
                    setattr(row, ("H1_LLR", "L1_LLR")[ifo], llr)
                    if llr == 0.0:
                        continue
                    single_gps = int(row.end_time_sngl[ifo]) * NS + int(row.end_time_ns_sngl[ifo])
                    assigned = 0.0
                    if owner == ifo and self.active:
                        assigned = self._assign(self.active, ifo, llr)
                        row.far_sngl[ifo] = np.float32(assigned)
                    values = (row.event_id, row.bankid, row.tmplt_idx, row.end_time_sngl[ifo],
                              row.end_time_ns_sngl[ifo], ifo, row.snglsnr[ifo], row.chisq[ifo], llr,
                              assigned, single_gps / NS, (self.active or {}).get("version", 0))
                    self.detail.write(",".join(map(str, values)) + "\n")
                    if self.producer:
                        self.support[ifo][0].append(single_gps)
                        self.support[ifo][1].append(llr)
            if self.producer:
                self._advance(event_gps)

    def _advance(self, gps):
        while gps >= self.bucket_start + self.bucket:
            self._flush(self.bucket_start + self.bucket)
        first = self.start + self.window
        if gps < first: return
        boundary = first + (gps - first) // self.update * self.update
        if boundary <= self.last_publish: return
        old_start = max(self.start, self.last_publish - self.window)
        for timestamp in range(old_start, boundary - self.window, self.bucket):
            os.unlink(self._bucket_path(timestamp))
        self.last_publish = boundary
        background = self._background(boundary - self.window, boundary)
        if background is None: return
        version = max((self.active or {}).get("version", 0), (self.pending or {}).get("version", 0)) + 1
        background.update(version=version, available=gps)
        self._write(background)
        self.pending = background

    def _flush(self, end):
        packed = []
        for ifo in range(2):
            raw = [np.frombuffer(values, dtype=kind) for values, kind in zip(self.support[ifo], ("i8", "f8"))]
            mask = raw[0] < end
            old = np.rec.fromarrays((raw[0][mask], raw[1][mask]), names="gps,llr")
            old.sort(order=("llr", "gps"))
            keep = math.ceil(10 ** self.tail * self.window / NS)
            cut = max(len(old) - keep, 0)
            while cut and old["llr"][cut - 1] == old["llr"][cut]:
                cut -= 1
            old["llr"][:cut] = 16 * np.sinh(np.floor(np.arcsinh(old["llr"][:cut] / 16) * 1024) / 1024)
            values, index, counts = np.unique(old["llr"], return_index=True, return_counts=True)
            points = np.zeros(len(values), dtype=POINT)
            points["gps"], points["llr"], points["count"] = old["gps"][index], values, counts
            packed.append(points)
            self.support[ifo] = [array(kind, values[~mask]) for kind, values in zip("qd", raw)]
        path = self._bucket_path(self.bucket_start)
        np.savez(path + ".tmp", H1=packed[0], L1=packed[1])
        os.replace(path + ".tmp.npz", path)
        self.bucket_start = end

    def _background(self, start, end):
        buckets = [[], []]
        for timestamp in range(start, end, self.bucket):
            with np.load(self._bucket_path(timestamp)) as bucket:
                buckets[0].append(bucket["H1"])
                buckets[1].append(bucket["L1"])
        results = []
        for ifo in range(2):
            curve = np.concatenate(buckets[ifo])
            curve.sort(order=("llr", "gps"))
            values, index = np.unique(curve["llr"], return_index=True)
            counts = np.add.reduceat(curve["count"].astype(np.uint64), index)
            live = sum(max(0, min(end, stop) - max(start, begin)) for begin, stop in self.segments[ifo]) / NS
            if not len(curve) or live <= self.window / NS / 5:
                return None
            fars = np.cumsum(counts[::-1], dtype=np.uint64)[::-1] / live
            curve["far"] = np.repeat(fars, np.diff(np.r_[index, len(curve)]))
            logs = [math.log10(far) for far in fars]
            tail = min(range(len(values)), key=lambda i: abs(logs[i] - self.tail))
            offsets = [(value - values[tail], log_far - self.tail) for value, log_far in
                       zip(values[tail:], logs[tail:])]
            numerator = sum((dx * dy for dx, dy in offsets), 0.0)
            denominator = sum((dx * dx for dx, unused in offsets), 0.0)
            slope = numerator / denominator if denominator else math.nan
            if len(values) - tail < 2 or not slope < 0: return None
            results.append((curve, live, int(counts.sum()), float(values[tail]), float(slope),
                            len(values) - tail))
        keys = "curve livetime support r_tail slope fit_count".split()
        return {"start": start, "end": end, "tail": self.tail,
                **dict(zip(keys, map(list, zip(*results))))}

    def _assign(self, background, ifo, llr):
        curve = background["curve"][ifo]
        if llr > background["r_tail"][ifo]:
            exponent = background["tail"] + background["slope"][ifo] * (llr - background["r_tail"][ifo])
            return 10 ** exponent
        body = curve[curve["llr"] <= background["r_tail"][ifo]]
        return body["far"][np.argmin(np.abs(body["llr"] - llr))]

    def _write(self, background):
        document = {
            "schema_version": 4, "background_kind": "no_injection", "accepted_version": background["version"],
            "epoch_gps": _gps_json(background["end"]),
            "window_start_gps": _gps_json(background["start"]),
            "window_end_gps": _gps_json(background["end"]),
            "window_duration": _gps_json(self.window), "update_period": _gps_json(self.update),
            "far_floor_count": 1, "tail_log10_far": self.tail, "backgrounds": {}}
        document.update(self.provenance, worker_id=int(os.getenv("CRASHCAR_WORKER_ID", "0")),
                        worker_count=int(os.getenv("CRASHCAR_WORKER_COUNT", "1")),
                        worker_bank_ids=list(map(int, os.getenv(
                            "CRASHCAR_WORKER_BANK_IDS_EXPECTED", "0").split(","))))
        for ifo, name in enumerate(("H1", "L1")):
            document["backgrounds"][name] = {
                "livetime": _gps_json(round(background["livetime"][ifo] * NS)),
                "support_count": background["support"][ifo],
                "tail_fit": {"method": "anchored_ols_all_unique_ranks_ge_r_tail", "r_tail":
                             background["r_tail"][ifo].hex(), "slope": background["slope"][ifo].hex(),
                             "fit_unique_rank_count": background["fit_count"][ifo]},
                "far_llr_points": [
                    {"gps": _gps_json(int(point["gps"])), "llr": float(point["llr"]).hex(),
                     "far": float(point["far"]).hex(), "count": int(point["count"])}
                    for point in background["curve"][ifo]]}
        with open(self.path + ".tmp", "w") as output:
            output.write(json.dumps(document, separators=(",", ":")) + "\n")
        os.replace(self.path + ".tmp", self.path)

    def _refresh(self, gps):
        if self.last_refresh and gps - self.last_refresh < self.update:
            return
        try:
            with open(self.path) as source:
                candidate = _load_background(json.load(source))
            if not self.active or candidate["version"] > self.active["version"]:
                self.active = candidate
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.last_refresh = gps

    def _bucket_path(self, start):
        return "%s.hist.%d" % (self.path, start // NS)

def _gps_json(value): return {"seconds": value // NS, "nanoseconds": value % NS}

def _load_background(document):
    banks = list(map(int, os.getenv("CRASHCAR_WORKER_BANK_IDS_EXPECTED", "0").split(",")))
    if (document["schema_version"] != 4 or document["background_kind"] != "no_injection"
            or document["worker_id"] != int(os.getenv("CRASHCAR_WORKER_ID", "0"))
            or document["worker_count"] != int(os.getenv("CRASHCAR_WORKER_COUNT", "1"))
            or document["worker_bank_ids"] != banks or document["accepted_version"] < 1
            or not document["tail_log10_far"] < 0):
        raise ValueError("incompatible single background")
    def load_one(name):
        source = document["backgrounds"][name]
        curve = np.array([(_gps(point["gps"]), float.fromhex(point["llr"]),
                           float.fromhex(point["far"]), point["count"])
                          for point in source["far_llr_points"]], dtype=POINT)
        r_tail, slope = (float.fromhex(source["tail_fit"][key]) for key in ("r_tail", "slope"))
        valid = (len(curve) and np.all(curve["count"]) and np.all(curve["far"] > 0)
                 and np.isfinite(curve["llr"]).all() and np.isfinite(curve["far"]).all()
                 and int(curve["count"].sum()) == source["support_count"] and slope < 0
                 and np.isfinite(r_tail) and np.isfinite(slope) and _gps(source["livetime"])
                 > round(float(os.environ["BACKGROUND_ACCUMULATION_SECONDS"]) * NS) / 5)
        if not valid:
            raise ValueError("invalid single background")
        return curve, r_tail, slope
    values = [load_one(name) for name in ("H1", "L1")]
    return {"version": document["accepted_version"], "tail": document["tail_log10_far"],
            "curve": [value[0] for value in values], "r_tail": [value[1] for value in values],
            "slope": [value[2] for value in values]}

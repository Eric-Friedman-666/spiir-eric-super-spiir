#!/usr/bin/env python3
"""Narrow read-only feature/ranking API used only by crashcar_plot.py."""

import hashlib as _hashlib
import importlib.util as _importlib_util
import math as _math
from pathlib import Path as _Path

__all__ = (
    "FLAG_FOREGROUND",
    "crashcar_numeric",
    "features_from_feature_csv_row",
    "feature_gps_seconds",
    "rank_feature",
)


def _load_exact_sibling_numeric():
    numeric_path = _Path(__file__).resolve().with_name("crashcar_numeric.py")
    if not numeric_path.is_file():
        raise ImportError(
            f"crashcar plot numeric helper unavailable at exact path: {numeric_path}"
        )
    module_name = "_crashcar_plot_numeric_" + _hashlib.sha256(
        str(numeric_path).encode("utf-8")
    ).hexdigest()[:16]
    spec = _importlib_util.spec_from_file_location(module_name, numeric_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import spec for crashcar numeric: {numeric_path}")
    module = _importlib_util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ImportError(f"failed to load crashcar numeric {numeric_path}: {exc}") from exc
    required = (
        "TAIL_FAR",
        "strict_nonnegative_integer",
        "source_class_and_dof",
        "gaussian_llr_for_template",
        "calculated_far",
        "tail_model",
        "evaluate_far",
    )
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise ImportError(
            f"crashcar numeric {numeric_path} is missing required symbols: {missing}"
        )
    loaded_path = _Path(module.__file__).resolve()
    if loaded_path != numeric_path:
        raise ImportError(
            f"crashcar numeric path mismatch: expected {numeric_path}, loaded {loaded_path}"
        )
    return module, numeric_path


crashcar_numeric, _CRASHCAR_NUMERIC_PATH = _load_exact_sibling_numeric()

FLAG_FOREGROUND = 0
_FLAG_BACKGROUND = 1
_FLAG_EMPTY = 2


def _finite_positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not _math.isfinite(number) or number <= 0.0:
        return None
    return number


def _first_present(row, names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _parse_row_flag(value):
    if value in (None, ""):
        return FLAG_FOREGROUND
    try:
        return int(float(value))
    except (TypeError, ValueError):
        lowered = str(value).strip().lower()
        if lowered in ("background", "bg"):
            return _FLAG_BACKGROUND
        if lowered in ("empty", "livetime"):
            return _FLAG_EMPTY
        return FLAG_FOREGROUND


def _validated_template_shape(mapping, ifo, bankid, tmplt_idx):
    if ifo not in ("H1", "L1"):
        raise ValueError("crashcar plot support accepts H1/L1 features only")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("exact template-shape mapping is required")
    bankid = crashcar_numeric.strict_nonnegative_integer(bankid, "bankid")
    tmplt_idx = crashcar_numeric.strict_nonnegative_integer(tmplt_idx, "tmplt_idx")
    source_class, required_dof = crashcar_numeric.source_class_and_dof(bankid)
    key = f"{ifo}:{bankid}:{tmplt_idx}"
    entry = mapping.get(key)
    if not isinstance(entry, dict):
        raise ValueError(f"missing exact template-shape mapping: {key}")
    expected_ifo_id = 0 if ifo == "H1" else 1
    try:
        entry_ifo_id = crashcar_numeric.strict_nonnegative_integer(
            entry.get("ifo_id"), "ifo_id"
        )
        entry_bankid = crashcar_numeric.strict_nonnegative_integer(
            entry.get("bankid"), "bankid"
        )
        entry_tmplt_idx = crashcar_numeric.strict_nonnegative_integer(
            entry.get("tmplt_idx"), "tmplt_idx"
        )
    except ValueError as exc:
        raise ValueError(f"template-shape identity mismatch for {key}: {exc}") from exc
    if (
        entry.get("ifo") != ifo
        or entry_ifo_id != expected_ifo_id
        or entry_bankid != bankid
        or entry_tmplt_idx != tmplt_idx
        or entry.get("source_class") != source_class
    ):
        raise ValueError(f"template-shape identity/class mismatch for {key}")
    a_eff = _finite_positive(entry.get("autocorr_power"))
    mapped_dof = _finite_positive(entry.get("dof"))
    if a_eff is None:
        raise ValueError(f"invalid A_eff for {key}")
    if mapped_dof != required_dof:
        raise ValueError(f"template dof conflicts with bank class for {key}")
    return bankid, tmplt_idx, a_eff, required_dof


class _SingleDetectorFeature:
    __slots__ = (
        "ifo",
        "rho",
        "chisq",
        "tmplt_idx",
        "bankid",
        "autocorr_power",
        "dof",
        "end_time",
        "end_time_ns",
        "is_background",
        "source_row",
    )

    def __init__(
        self,
        ifo,
        rho,
        chisq,
        tmplt_idx,
        bankid,
        autocorr_power,
        dof,
        end_time,
        end_time_ns,
        is_background,
        source_row,
    ):
        self.ifo = ifo
        self.rho = float(rho)
        self.chisq = float(chisq)
        self.tmplt_idx = tmplt_idx
        self.bankid = bankid
        self.autocorr_power = float(autocorr_power)
        self.dof = float(dof)
        self.end_time = end_time
        self.end_time_ns = end_time_ns
        self.is_background = is_background
        self.source_row = source_row


def features_from_feature_csv_row(
    row,
    ifos,
    min_snr,
    autocorr_power_by_template,
    source_row_index=None,
):
    allowed_ifos = tuple(ifos)
    if allowed_ifos != ("H1", "L1"):
        raise ValueError("crashcar plot support requires ordered ifos H1,L1")
    min_snr = float(min_snr)
    row_ifos = row.get("ifos")
    generic_ifo = row.get("ifo")
    if generic_ifo:
        if generic_ifo not in allowed_ifos:
            return []
        candidate_ifos = (generic_ifo,)
    else:
        candidate_ifos = allowed_ifos
    tmplt_idx = _first_present(row, ("tmplt_idx", "template_id"))
    bankid = _first_present(row, ("bankid", "bank_id"))
    is_background = _parse_row_flag(row.get("is_background"))
    features = []
    for ifo in candidate_ifos:
        if row_ifos and ifo not in str(row_ifos):
            continue
        rho = _first_present(
            row, (f"snglsnr_{ifo}", f"rho_{ifo}", "rho", "snglsnr")
        )
        chisq = _first_present(
            row, (f"chisq_{ifo}", f"chi2_{ifo}", "chisq", "chi2")
        )
        rho_value = _finite_positive(rho)
        chisq_value = _finite_positive(chisq)
        if rho_value is None or chisq_value is None or rho_value <= min_snr:
            continue
        exact_bankid, exact_tmplt_idx, a_eff, dof = _validated_template_shape(
            autocorr_power_by_template, ifo, bankid, tmplt_idx
        )
        source_info = dict(row)
        if source_row_index is not None:
            source_info["_feature_csv_row_index"] = source_row_index
        features.append(
            _SingleDetectorFeature(
                ifo=ifo,
                rho=rho_value,
                chisq=chisq_value,
                tmplt_idx=exact_tmplt_idx,
                bankid=exact_bankid,
                autocorr_power=a_eff,
                dof=dof,
                end_time=_first_present(
                    row,
                    (f"end_time_sngl_{ifo}", f"end_time_{ifo}", "end_time"),
                ),
                end_time_ns=_first_present(
                    row,
                    (
                        f"end_time_ns_sngl_{ifo}",
                        f"end_time_ns_{ifo}",
                        "end_time_ns",
                    ),
                ),
                is_background=is_background,
                source_row=source_info,
            )
        )
    return features


def feature_gps_seconds(feature):
    if feature.end_time is None:
        return None
    try:
        seconds = float(feature.end_time)
    except (TypeError, ValueError):
        return None
    if feature.end_time_ns is not None:
        try:
            seconds += float(feature.end_time_ns) * 1.0e-9
        except (TypeError, ValueError):
            pass
    return seconds if _math.isfinite(seconds) else None


def rank_feature(feature):
    if feature.ifo not in ("H1", "L1"):
        raise ValueError("crashcar plot support ranks H1/L1 features only")
    return crashcar_numeric.gaussian_llr_for_template(
        feature.rho,
        feature.chisq,
        feature.autocorr_power,
        feature.bankid,
        mapped_dof=feature.dof,
    )

from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import scipy.stats
import argparse
import spiir.io
import sys
import os

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare two run directories."
    )
    parser.add_argument(
        "--artifacts_dir",
        type=str,
        default="/spiir/artifacts/HLVK-1187006000-300",
        help="Hash of the control commit.",
    )
    parser.add_argument(
        "--ifos",
        type=str,
        default="HL_H1L1",
        help="IFO combo string.",
    )
    parser.add_argument(
        "--control",
        type=str,
        default="spiir-O4-EW-development",
        help="Directory of the control commit.",
    )
    parser.add_argument(
        "--test",
        type=str,
        help="Directory of the test commit.",
    )
    parser.add_argument(
        "--node_id",
        type=str,
        default="000",
        help="Directory of node's output.",
    )
    # parse command line arguments
    args = parser.parse_args()
    # c_log.setLevel(level=args.loglevel)  # console logger

    # For now we assume that the last created spiir-O4 directory is its most recent commit.
    control_dirs = [f for f in sorted(Path(args.artifacts_dir).glob(
        f'*{args.control}*'), key=os.path.getctime)]
    if len(control_dirs) == 0:
        print("Control directory not found.")
        exit()

    control_dir = control_dirs[-1]

    test_dirs = [f for f in sorted(Path(args.artifacts_dir).glob(
        f'*{args.test}*'), key=os.path.getctime)]
    if len(test_dirs) == 0:
        print("Test directory not found.")
        exit()

    test_dir = test_dirs[-1]

    run_labels = {
        "Control": control_dir / args.ifos,
        "Test": test_dir / args.ifos,
    }

    comparisons = Path(
        f"{control_dir.name}-vs-{test_dir.name}/") / args.ifos
    comparisons.mkdir(exist_ok=True, parents=True)

    zerolags = {
        key: spiir.io.ligolw.load_table_from_xmls(
            paths=list(run_dir.glob("*/zerolag_*_*.xml.gz")),
            table="postcoh",
            nproc=4,
            verbose=True)
        for key, run_dir in run_labels.items()
    }

    nrows, ncols = 1, 2
    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=ncols,
        figsize=(12, 6),
        sharex=True,
        sharey=True,
        layout="tight",
        facecolor="white",
    )

    for key, ax in zip(zerolags, axes):
        ax.scatter(
            zerolags[key]['cohsnr'],
            zerolags[key]['cmbchisq'],
            s=20, alpha=1.0, marker='.'
        )
        ax.grid(False)
        ax.set(
            xscale="log",
            yscale="log",
            xlabel="Coherent SNR",
            ylabel=r"Combined $\chi^{2}$",
            # ylabel="Combined ChiSq",
            title=f"{run_labels[key]}_{args.ifos}",
        )

    fig.suptitle(
        r"Background Seed Run Analysis of Coherent SNR and Combined $\chi^{2}$", fontsize=14)
    # fig.show()

    fig.savefig(comparisons / "background_seed_cohsnr_vs_cmbchisq.png")

    sys.path.append('/tanghyd/spiir-python-tests/tests')
    import test_zerolags
    f = open(comparisons / 'zerolags_diff.txt', 'w')
    sys.stdout = f
    test_zerolags.main([(run_labels['Control'] / args.node_id ).as_posix(),
                       (run_labels['Test'] / args.node_id ).as_posix(), '--verbose'])

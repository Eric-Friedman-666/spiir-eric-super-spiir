from pathlib import Path
import click
import matplotlib.pyplot as plt
import argparse
import spiir.io
import sys
import os

@click.command()
@click.option('--control',
              help="Directory of the control commit run.",
              type=Path)
@click.option('--test',
              help="Directory of the test commit run.",
              type=Path)
def main(control, test):
    # c_log.setLevel(level=args.loglevel)  # console logger

    run_labels = {
        "Control": control,
        "Test": test,
    }

    comparisons = Path(f"{control.parent.name}-vs-{test.parent.name}/{control.name}")
    comparisons.mkdir(exist_ok=True, parents=True)

    zerolags = {
        key: spiir.io.ligolw.load_table_from_xmls(paths=list(
            run_dir.glob("*/zerolag_*_*.xml.gz")),
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
        ax.scatter(zerolags[key]['cohsnr'],
                   zerolags[key]['cmbchisq'],
                   s=20,
                   alpha=1.0,
                   marker='.')
        ax.grid(False)
        ax.set(
            xscale="log",
            yscale="log",
            xlabel="Coherent SNR",
            ylabel=r"Combined $\chi^{2}$",
            # ylabel="Combined ChiSq",
            title=f"{run_labels[key]}",
        )

    fig.suptitle(
        r"Background Seed Run Analysis of Coherent SNR and Combined $\chi^{2}$",
        fontsize=14)

    fig.savefig(comparisons / "background_seed_cohsnr_vs_cmbchisq.png")

if __name__ == '__main__':
    main()

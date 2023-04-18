import argparse
import concurrent.futures
import logging
import multiprocessing
import subprocess
import sys
import shutil
from functools import partial
from pathlib import Path
from tqdm import tqdm
import pandas as pd
import re
import asyncio
import click
import math

from glue.ligolw import ligolw, lsctables, array, param, utils

# FIXME:  require calling code to provide the content handler
class DefaultContentHandler(ligolw.LIGOLWContentHandler):
    pass


array.use_in(DefaultContentHandler)
param.use_in(DefaultContentHandler)
lsctables.use_in(DefaultContentHandler)

# initialise logging
logger = logging.getLogger("fits.generate")
logger.setLevel(logging.DEBUG)

c_log = logging.StreamHandler()  # console logger
c_log.setLevel(logging.WARNING)

f_log = logging.FileHandler("fits_generate.log")
f_log.setLevel(logging.DEBUG)

# create formatter and add it to the handlers
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
f_log.setFormatter(formatter)
c_log.setFormatter(formatter)
logger.addHandler(f_log)
logger.addHandler(c_log)



def ligo_skymap_plot(inj_lines, out_path, time):
    logger.debug(f"fits file generated in {out_path}")

    fits_files = list([f for f in Path(out_path).glob("*.fits")])

    if len(fits_files) == 0:
        logger.info(f"No fits detected.")
    else:
        logger.debug(f"Number of fits files detected: {len(fits_files)}")
        subprocs = []

        for i, fits_path in enumerate(fits_files):
            # find coordinates of injection associated with coinc
            # assumes coincs within 20 seconds of injection to be associated.
            long = 0
            lat = 0
            found = False
            for line in inj_lines:
                long, lat, det_time = str(line.rstrip().decode()).split(',')
                if (abs(int(det_time) - int(time)) < 20):
                    long = float(long) * (180.0 / math.pi)
                    lat = float(lat) * (180.0 / math.pi)
                    found = True
                    break

            # plot skymap of coinc's .fits and save as png
            skymap_path = out_path + "_bayestar.png"
            cmd = [
                "ligo-skymap-plot",
                str(fits_path), "-o", skymap_path, "--annotate", "--contour",
                "50", "90"
            ]
            if (found):
                cmd += ["--radec", str(long), str(lat)]
                print(line.rstrip().decode(), cmd, time, det_time, long, lat)
            logger.debug(f"subprocess cmd: {' '.join(cmd)}")

            subprocs.append(cmd)

        return subprocs, out_path


# plot snr_series' in coinc xml and save as png
def plot_snr_series(file_path):
    xmldoc = utils.load_filename(file_path,
                                contenthandler=DefaultContentHandler,
                                verbose=False)

    first = True
    for root in (
            elem
            for elem in xmldoc.getElementsByTagName(ligolw.LIGO_LW.tagName)
            if elem.hasAttribute(u"Name") and elem.Name == "COMPLEX8TimeSeries"
    ):
        snr_series = array.get_array(root, 'snr').array
        df = pd.DataFrame({
            'Time':
            snr_series[0],
            str(root.childNodes[0].pcdata):
            (snr_series[1] * snr_series[1] +
            snr_series[2] * snr_series[2])**0.5
        })
        if first:
            ax = df.plot(x=0, y=[1], figsize=(15, 4))
            first = False
        else:
            df.plot(x=0, y=[1], ax=ax)

    ax.get_figure().savefig(f"{file_path}_snrs.png")


def bayestar_localise_coincs(inj_lines, file_path, time):
    # generate fits
    out_path = str(file_path) + "_out"
    cmd = [
        "bayestar-localize-coincs",
        str(file_path),
        str(file_path), "-o", out_path, "--enable-snr-series"
    ]
    logger.debug(f"subprocess cmd: {' '.join(cmd)}")

    subprocess.run(cmd)
    return ligo_skymap_plot(inj_lines, out_path, time)

class Skymap_Creator:

    def __init__(self, data_dir, run_name, cohsnr_filter, n_workers):
        self.n_workers = n_workers

        # Run our program until it is complete
        self.loop = asyncio.get_running_loop()
        self.executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=self.n_workers)

        self.cohsnr_filter = cohsnr_filter

        # get filepaths to coinc files
        self.file_paths = list(
            [f for f in data_dir.glob(f"{run_name}/*_*_*_*.xml")])
        if len(self.file_paths) == 0:
            logger.info(f"No files detected. Program aborting.")
            sys.exit()
        else:
            logger.debug(f"Number of files detected: {len(self.file_paths)}")

        # find path for zerolag file
        zerolags_path = list([
            f for f in data_dir.glob(f"{run_name}/*/zerolag_*_*.xml.gz")
        ])[0]

        proc = subprocess.Popen(
            ['ligolw_print', '-c', 'cohsnr', '-c', 'end_time', f'{zerolags_path}'],
            stdout=subprocess.PIPE)
        self.triggers_lines = proc.stdout.readlines()

        proc = subprocess.Popen([
            'ligolw_print', '-t', 'sim_inspiral', '-c', 'longitude', '-c',
            'latitude', '-c', 'geocent_end_time', f'{data_dir}/fake_inj.xml'
        ],
                                stdout=subprocess.PIPE)
        self.inj_lines = proc.stdout.readlines()

    async def add_success_callback(self, fut, callback, *args):
        result = await fut
        await callback(result, *args)
        return result

    async def create(self):
        futures = []

        for i, file_path in enumerate(self.file_paths):
            # get time of coinc
            result = re.search(r"([0-9]+)_[0-9]+_[0-9]+\.xml", str(file_path))
            time = int(result.group(1))

            # filter coincs to cohsnrs greater than cohsnr_filter
            found = False
            for line in self.triggers_lines:
                cohsnr, det_time = str(line.rstrip().decode()).split(',')
                if (int(det_time) == int(time)
                        and float(cohsnr) > self.cohsnr_filter):
                    found = True
                    break

            if not found:
                continue

            logging.debug("Appending futures.")

            futures.append(
                self.loop.run_in_executor(self.executor, bayestar_localise_coincs, self.inj_lines, file_path, time))
            futures.append(asyncio.create_task(self.add_success_callback(futures[-1], self.process_fits)))
            futures.append(self.loop.run_in_executor(self.executor, plot_snr_series, file_path))

        await asyncio.wait(futures)

    async def process_fits(self, result):
        logging.debug(f"process_fits {result}")
        if not result:
            return
        subprocs, out_path = result
        subfutures = []
        for proc in subprocs:
            logging.debug("Appending subfutures.")
            subfutures.append(
                self.loop.run_in_executor(self.executor, subprocess.run, proc))
            
        task = asyncio.create_task(asyncio.wait(subfutures))
        await asyncio.create_task(self.add_success_callback(task, self.cleanup, out_path))
        
    async def cleanup(self, task, out_path):
        logging.debug(f"Cleaning .fits folder for {task}")
        shutil.rmtree(out_path)

async def run_loop(data_dir, run_name, cohsnr_filter, n_workers):
    skymapCreator = Skymap_Creator(data_dir, run_name, cohsnr_filter, n_workers)
    await skymapCreator.create()


@click.command()
@click.option('--data_dir',
              help="Path to the base data directory.",
              type=Path,
              default=Path(__file__).absolute().parent)
@click.option('--run_name',
              help="Name of folder encapsulating SPIIR run results.",
              type=str)
@click.option('--cohsnr_filter',
              help="Only accept cohsnrs above this value.",
              type=int,
              default=12)
@click.option(
    "-n",
    "--n_workers",
    type=int,
    default=multiprocessing.cpu_count(),
    help="Number of cores to use for multiprocessing.",
)
@click.option(
    "-d",
    "--debug",
    is_flag=True,
    help="Display all developer debug logging statements",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Set logging level to INFO and display progress and information",
)
def main(data_dir, run_name, cohsnr_filter, n_workers, debug, verbose):
    loglevel = None
    if debug:
        loglevel = logging.DEBUG
    elif verbose:
        loglevel = logging.INFO
    c_log.setLevel(level=loglevel)  # console logger
    assert (1 <= n_workers <= multiprocessing.cpu_count()
            ), f"Program requires 1 <= n <= {multiprocessing.cpu_count()}"
    
    asyncio.run(run_loop(data_dir, run_name, cohsnr_filter, n_workers))


if __name__ == '__main__':
    main()
    logger.info(f"Program complete.")

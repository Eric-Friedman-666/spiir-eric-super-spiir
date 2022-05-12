import argparse
import concurrent.futures
import logging
import multiprocessing
import subprocess
import sys
from functools import partial
from pathlib import Path
from tqdm import tqdm
import pandas as pd
import re

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
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
f_log.setFormatter(formatter)
c_log.setFormatter(formatter)
logger.addHandler(f_log)
logger.addHandler(c_log)

# TO DO List:
# - Fix: If no -c then ligolw_print extracts all, but script won't know column names!
# - We should be able to loop through a directory of files

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate fits from coinc XML files."
    )
    parser.add_argument(
        "-psd",
        "--psd",
        type=str,
        default="H1L1V1K1-REFERENCE_PSD-1187006000-86400.xml.gz",
        help="PSD xml.",
    )
    parser.add_argument(
        "--data_dir",
        type=Path,
        default=Path(__file__).absolute().parent,
        help="Path to the base data directory.",
    )
    parser.add_argument(
        "--run_name",
        type=str,
        # default="bbh_3det_inj",
        help="Name of folder encapsulating SPIIR run results.",
    )
    parser.add_argument(
        "-n",
        "--n_workers",
        type=int,
        default=multiprocessing.cpu_count(),
        help="Number of cores to use for multiprocessing.",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
        default=logging.WARNING,
        help="Display all developer debug logging statements",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        dest="loglevel",
        const=logging.INFO,
        help="Set logging level to INFO and display progress and information",
    )
    # parse command line arguments
    args = parser.parse_args()
    c_log.setLevel(level=args.loglevel)  # console logger
    assert (
        1 <= args.n_workers <= multiprocessing.cpu_count()
    ), f"Program requires 1 <= n <= {multiprocessing.cpu_count()}"

    # get filepaths to coinc files
    file_paths = list([f for f in args.data_dir.glob(f"{args.run_name}/*_*_*_*.xml")])
    # file_paths = [f for f in file_paths if re.search(r'_\d{9,12}_\d{1}_\d{3}\.xml$', f)]
    if len(file_paths) == 0:
        logger.info(f"No files detected. Program aborting.")
        sys.exit()
    else:
        logger.debug(f"Number of files detected: {len(file_paths)}")

    # find path for zerolag file
    zerolags_path = list([f for f in args.data_dir.glob(f"{args.run_name}/*_*_*_zerolag_*_*.xml.gz")])[0]

    # subprocess spawner
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n_workers) as executor:
        with tqdm(
            desc=f"Generating fits from coinc XML files (n_workers={args.n_workers})",
            total=len(file_paths),
            miniters=1,
            disable=c_log.level != logging.INFO,  # only verbose
        ) as progress:
            futures = {}
            for i, file_path in enumerate(file_paths):
                # get time of coinc
                result = re.search(r"([0-9]+)_[0-9]+_[0-9]+\.xml", str(file_path))
                time = int(result.group(1))

                # filter coincs to cohsnr's greater than 12
                found = False
                proc = subprocess.Popen(['ligolw_print', '-c', 'cohsnr', '-c', 'end_time', f'{zerolags_path}'], stdout=subprocess.PIPE) # redundant compute, TODO only read once
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break

                    cohsnr, det_time = str(line.rstrip().decode()).split(',')
                    if (int(det_time) == int(time) and float(cohsnr) > 12):
                        found = True
                        break

                if not found:
                    continue

                # generate fits with and without the snr series
                out_path = str(file_path) + "_out"
                cmd = ["bayestar-localize-coincs", str(file_path), args.psd, "-o", out_path, "--disable-snr-series"]
                logger.debug(f"subprocess cmd: {' '.join(cmd)}")

                # submit to thread pool and store as {future: fh} for later retrieval
                futures[executor.submit(partial(subprocess.run), cmd)] = (time, out_path)

                out_path = str(file_path) + "_out_snr"
                cmd = ["bayestar-localize-coincs", str(file_path), args.psd, "-o", out_path, "--enable-snr-series"]
                logger.debug(f"subprocess cmd: {' '.join(cmd)}")

                futures[executor.submit(partial(subprocess.run), cmd)] = (time, out_path)

                # plot snr_series' in coinc xml and save as png
                xmldoc = utils.load_filename(file_path,
                                 contenthandler=DefaultContentHandler,
                                 verbose=False)
                                 
                first = True
                for root in (
                        elem
                        for elem in xmldoc.getElementsByTagName(ligolw.LIGO_LW.tagName)
                        if elem.hasAttribute(u"Name")
                        and elem.Name == "COMPLEX8TimeSeries"):
                    snr_series = array.get_array(root, 'snr').array
                    df = pd.DataFrame({'Time': snr_series[0], str(root.childNodes[0].pcdata): (snr_series[1]*snr_series[1] + snr_series[2]*snr_series[2])**0.5})
                    if first:
                        ax = df.plot(x=0, y=[1], figsize=(15,4))
                        first = False
                    else:
                        df.plot(x=0, y=[1], ax=ax)

                ax.get_figure().savefig(f"{file_path}_snrs.png")

            # update a progress bar once a subprocess call is complete
            for future in concurrent.futures.as_completed(futures):

                # https://stackoverflow.com/a/54510643
                # subprocesses will fail silently (e.g. not loading virtual env)
                # mypy error: Exception must be derived from BaseExceptionmypy(error)
                if future.exception() is not None:
                    raise future.exception()

                time, out_path = futures[future]

                logger.debug(f"fits file generated in {out_path}")

                fits_files = list([f for f in Path(out_path).glob("*.fits")])

                if len(file_paths) == 0:
                    logger.info(f"No fits detected.")
                else:
                    logger.debug(f"Number of fits files detected: {len(fits_files)}")
                    subfutures = {}
                    for i, fits_path in enumerate(fits_files):
                        # find coordinates of injection associated with coinc
                        long = 0
                        lat = 0
                        found = False
                        proc = subprocess.Popen(['ligolw_print', '-t', 'sim_inspiral', '-c', 'longitude', '-c', 'latitude', '-c', 'geocent_end_time', f'{args.data_dir}/fake_inj.xml'], stdout=subprocess.PIPE)
                        while True:
                            line = proc.stdout.readline()
                            if not line:
                                break

                            long, lat, det_time = str(line.rstrip().decode()).split(',')
                            if (abs(int(det_time) - int(time)) < 20):
                                long = float(long)*(180.0/3.14159265358979323)
                                lat = float(lat)*(180.0/3.14159265358979323)
                                found = True
                                break

                        # plot skymap of coinc's .fits and save as png
                        skymap_path = out_path+"_bayestar.png"
                        cmd = ["ligo-skymap-plot", str(fits_path), "-o", skymap_path, "--annotate", "--contour", "50", "90"]
                        if (found):
                            cmd += ["--radec", str(long), str(lat)]
                            print(line.rstrip().decode(), cmd, time, det_time, long, lat)
                        logger.debug(f"subprocess cmd: {' '.join(cmd)}")

                        subfutures[executor.submit(partial(subprocess.run), cmd)] = skymap_path
                    
                    for subfuture in concurrent.futures.as_completed(subfutures):
                        if subfuture.exception() is not None:
                            raise subfuture.exception()
                        
                        logger.debug(f"png file generated in {subfutures[subfuture]}")

                progress.update(1)
            # progress.refresh()

    logger.info(f"Program complete.")
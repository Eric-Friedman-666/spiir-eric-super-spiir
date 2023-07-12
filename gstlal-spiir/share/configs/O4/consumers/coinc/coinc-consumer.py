#!/usr/bin/env python

import argparse
import json
import logging
import time
import toml
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from igwn_alert import client

from spiir.logging import setup_logger
from spiir.io.igwn.alert.consumers import CoincConsumer


def parse_cli_arguments() -> argparse.Namespace:
    """Parses command line arguments for running the coinc Consumer with IGWN Alert."""

    parser = argparse.ArgumentParser(
        description="Run the SPIIR CoincConsumer.")
    parser.add_argument(
        "--save_dir",
        type=str,
        default="./results/",
        help=
        "Output directory for input payload and output psd and snd series plots.",
    )
    parser.add_argument(
        "--upload-gracedb",
        action="store_true",
        default=False,
        help="Whether to upload the plot outputs to GraceDb",
    )
    parser.add_argument(
        "-s",
        "--server",
        type=str,
        default="kafka://kafka.scimma.org/",
        help="URL of hop-client server to stream topics from.",
    )
    parser.add_argument(
        "-g",
        "--group",
        type=str,
        default="gracedb-playground",
        help="Name of GraceDB group",
    )
    parser.add_argument(
        "-t",
        "--topics",
        type=str,
        nargs="+",
        default=["test_spiir"],
        help="IGWN Kafka topics for the listener to subscribe to.",
    )
    parser.add_argument(
        "-u",
        "--username",
        type=str,
        help="Username for SCIMMA hop authentication credentials in auth.toml",
    )
    parser.add_argument(
        "-c",
        "--credentials",
        type=str,
        default="~/.config/hop/auth.toml",
        help="Location of auth.toml credentials file",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_const",
        dest="log_level",
        const=logging.DEBUG,
        default=logging.WARNING,
        help="Display all developer debug logging statements",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        dest="log_level",
        const=logging.INFO,
        help="Set logging level to INFO and display progress and information",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Specify location to output log file",
    )
    return parser.parse_args()


def main(
    save_dir: str = "./results/",
    upload_gracedb: bool = False,
    topics: List[str] = ["test_spiir"],
    group: str = "gracedb-playground",
    server: str = "kafka://kafka.scimma.org/",
    username: Optional[str] = None,
    credentials: Optional[str] = None,
    log_level: int = logging.WARNING,
    log_file: Optional[Union[str, Path]] = None,
):
    setup_logger("spiir", log_level, log_file)

    with CoincConsumer(
            save_dir=save_dir,
            topics=topics,
            group=group,
            server=server,
            username=username,
            credentials=credentials,
            upload_gracedb=upload_gracedb,
            save_payload=True,
    ) as consumer:
        consumer.subscribe()


if __name__ == '__main__':
    args = parse_cli_arguments()
    main(**args.__dict__)

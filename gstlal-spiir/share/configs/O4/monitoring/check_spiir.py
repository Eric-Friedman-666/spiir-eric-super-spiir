import lal
import json
import click
from pathlib import Path
import shutil
import re
import subprocess


@click.command()
@click.option('--condor_jobs',
              prompt='Input condor jobs: ',
              help='Input condor jobs.',
              multiple=True,
              type=Path)
@click.option('--stale_age',
              prompt='How old latencies have to be to consider a node stale: ',
              help='How old in seconds latencies have to be to consider a node stale.',
              type=int,
              default=1800)
@click.option('--output_status',
              prompt='Output status file: ',
              help='Output status file.',
              type=Path)
def main(condor_jobs, stale_age, output_status):
    # Total nodes monitored
    total = 0

    # Condor string described runs that have Completed, Held, or Idle jobs.
    condor_string = ""

    # Stale nodes are nodes that have not gotten a recent latency, but only while the state_vector indicates that it's not in a gap.
    stale_string = ""
    stale_num_total = 0

    # High nodes are nodes that have a latency above a threshold in the last 1000 triggers.
    high_string = ""
    high_num_total = 0

    for condor_job in condor_jobs:
        # Check status of condor job
        condor_q = subprocess.run(["condor_q", "-long", condor_job],
                                  capture_output=True,
                                  text=True)
        JobsHeld = None
        JobsIdle = None
        JobsRunning = None
        run_dir = None
        for line in condor_q.stdout.splitlines():
            result = re.search(r'DAG_JobsHeld = (.*)', line)
            if result:
                JobsHeld = result.group(1)

            result = re.search(r'DAG_JobsIdle = (.*)', line)
            if result:
                JobsIdle = result.group(1)

            result = re.search(r'DAG_JobsRunning = (.*)', line)
            if result:
                JobsRunning = result.group(1)

            result = re.search(r'Iwd = "(.*)"', line)
            if result:
                run_dir = Path(result.group(1))

        # The strings to be logged are appended to incrementally.
        stale_nodes = f"{run_dir.name}: "
        stale_num = 0
        high_nodes = f"{run_dir.name}: "
        high_num = 0

        for node in sorted(run_dir.glob('[0-9][0-9][0-9]')):
            # We loop over each IFO, read state_vector, and compare it with the last time we read it to determine whether the IFO is on or off.
            ifos_on = 0
            for ifo in node.glob('[HLVK]1'):
                new_state_file = Path(ifo / "state_vector_on_off_gap.txt")
                new_on_state = 0
                if new_state_file.is_file():
                    with open(new_state_file) as f:
                        for line in f:
                            new_on_state = int(line.split(' ')[1])

                old_state_file = Path(ifo / "state_vector_on_off_gap.txt-old")
                old_on_state = 0
                if old_state_file.is_file():
                    with open(old_state_file) as f:
                        for line in f:
                            old_on_state = int(line.split(' ')[1])

                # If on_buffers has incremented then we consider the IFO to have been 'on' for this period of time.
                if new_on_state - old_on_state > 0:
                    ifos_on += 1

                shutil.copy(new_state_file, old_state_file)

            current_time = int(lal.GPSTimeNow())
            last_state_time = current_time
            last_run_state = 0
            run_state_file = Path(node / 'run_state.txt')
            if run_state_file.is_file():
                with open(run_state_file, "r") as f:
                    line = f.read().split(' ')
                    last_state_time = int(line[0])
                    last_run_state = int(line[1])

            # We read the latency_history.txt and get the highest latency value and also the timestamp of the latest recorded trigger.
            trigger_time = None
            highest_latency = None
            latency_file = Path(node / 'latency_history.txt')
            if latency_file.is_file():
                with open(latency_file) as f:
                    for line in f:
                        line_words = line.split(' ')
                        end_time = int(float(line_words[0]))
                        latency = int(float(line_words[1]))
                        trigger_time = end_time + latency
                        # Check latencies within the last 30 minutes.
                        if current_time - trigger_time < 1800 and (
                                highest_latency is None
                                or latency > highest_latency):
                            highest_latency = latency

            if ifos_on < 2 and last_run_state == 1:
                last_run_state = 0
                last_state_time = current_time
                with open(run_state_file, "w") as f:
                    f.write(f"{current_time} {last_run_state}")

            if ifos_on >= 2 and last_run_state == 0:
                last_run_state = 1
                last_state_time = current_time
                with open(run_state_file, "w") as f:
                    f.write(f"{current_time} {last_run_state}")

            # If at least 2 IFOs are on AND we haven't seen a new trigger in a while then the node is stale.
            if ifos_on >= 2 and current_time - last_state_time > stale_age and (
                    trigger_time is None
                    or current_time - trigger_time > stale_age):
                stale_nodes += f" {node.name}"
                stale_num += 1

            # If latency_history.txt contains a high latency then the node is high.
            if highest_latency is not None and highest_latency > 120:
                high_nodes += f" {node.name}"
                high_num += 1

            total += 1

        # Add this run's critical info to the log if any faults have been found.
        # JobsSubmitted should always have one extra than JobsRunning.
        if int(JobsHeld) + int(JobsIdle) > 0:
            condor_string += f"{run_dir.name}: {int(JobsHeld)} jobs held. {int(JobsIdle)} jobs idle.  "

        if stale_num > 0:
            stale_string += stale_nodes + ".  "
            stale_num_total += stale_num

        if high_num > 0:
            high_string += high_nodes + ".  "
            high_num_total += high_num

    # Set status to CRITICAL if any faulty nodes have been found.
    critical_string = []
    if condor_string != "":
        critical_string.append(condor_string)
    if stale_num_total > 0:
        critical_string.append(
            f"{stale_num_total} nodes have no triggers for >{stale_age} seconds:  {stale_string}"
        )
    if high_num_total > 0:
        critical_string.append(
            f"{high_num_total} nodes have latencies >120 seconds in the last 30 minutes:  {high_string}"
        )
    if len(critical_string) > 0:
        critical_string = "\n".join(critical_string)
        status_intervals = [{
            'num_status': 2,
            'txt_status': f"CRITICAL: {critical_string}",
            'start_sec': 0
        }]
    else:
        status_intervals = [{
            'num_status': 0,
            'txt_status': 'OK: No reported problems',
            'start_sec': 0
        }, {
            'num_status': 1,
            'txt_status': 'WARNING: last report >3 min ago',
            'start_sec': 180
        }]
    # start_sec indicates that if we don't rerun this script within the next 360 seconds, then the status will be CRITICAL.
    status_intervals.append({
        'num_status': 2,
        'txt_status': 'CRITICAL: last report >6 min ago',
        'start_sec': 360
    })
    status = {
        'author': 'Luke Davis',
        'email': 'luke.davis@ligo.org',
        'created_gps': int(lal.GPSTimeNow()),
        'status_intervals': status_intervals
    }
    with open(output_status, 'w') as status_fp:
        json.dump(status, status_fp)


if __name__ == '__main__':
    main()

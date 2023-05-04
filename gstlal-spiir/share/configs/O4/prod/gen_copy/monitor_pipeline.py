from pathlib import Path
import re
import click
import logging
import spiir.io
from astropy.time import Time
import time
import signal
import pandas as pd
from sqlalchemy import create_engine, Table, Column, Integer, String, MetaData, Double, UniqueConstraint, BigInteger
from sqlalchemy.sql import text
import hashlib

# initialise logging
logger = logging.getLogger("monitor")
logger.setLevel(logging.DEBUG)

c_log = logging.StreamHandler()  # console logger
c_log.setLevel(logging.DEBUG)

# create formatter and add it to the handlers
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
c_log.setFormatter(formatter)
logger.addHandler(c_log)

meta = MetaData()

runs = Table(
    'runs', meta, Column('run_id', Integer), Column('unixtime', BigInteger),
    Column('run_dir', String), Column('commit', String),
    UniqueConstraint('run_id', name='unique_runs',
                     sqlite_on_conflict='IGNORE'))

hosts = Table(
    'hosts', meta, Column('unixtime', Double), Column('gpstime', Double),
    Column('run_id', Integer), Column('node_id', String),
    Column('host', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'unixtime',
                     name='unique_hosts',
                     sqlite_on_conflict='IGNORE'))

latencies = Table(
    'latencies', meta, Column('unixtime', Double), Column('gpstime', Double),
    Column('run_id', Integer), Column('node_id', String),
    Column('latency', Double), Column('cohsnr', Double),
    Column('cmbchisq', Double),
    UniqueConstraint('run_id',
                     'node_id',
                     'unixtime',
                     name='unique_latencies',
                     sqlite_on_conflict='IGNORE'))

state_vector = Table(
    'state_vector', meta, Column('unixtime', Double),
    Column('gpstime', Double), Column('run_id', Integer),
    Column('node_id', String), Column('ifo',
                                      String), Column('on_col', Integer),
    Column('off', Integer), Column('gap', Integer),
    UniqueConstraint('run_id',
                     'node_id',
                     'ifo',
                     'unixtime',
                     name='unique_state_vector',
                     sqlite_on_conflict='IGNORE'))

strain = Table(
    'strain', meta, Column('unixtime', Double), Column('gpstime', Double),
    Column('run_id', Integer), Column('node_id', String),
    Column('ifo', String), Column('add_col', Integer),
    Column('drop_col', Integer),
    UniqueConstraint('run_id',
                     'node_id',
                     'ifo',
                     'unixtime',
                     name='unique_strain',
                     sqlite_on_conflict='IGNORE'))

process = Table(
    'process', meta, Column('comment', String), Column('cvs_entry_time',
                                                       String),
    Column('cvs_repository', String), Column('domain', String),
    Column('end_time', String), Column('ifos', String),
    Column('is_online', Integer), Column('jobid', Integer),
    Column('node', String), Column('process_id', BigInteger),
    Column('program', String), Column('start_time', Integer),
    Column('unix_procid', Integer), Column('username', String),
    Column('version', String), Column('run_id', Integer),
    Column('node_id', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'start_time',
                     'jobid',
                     'node',
                     'process_id',
                     'unix_procid',
                     name='unique_process',
                     sqlite_on_conflict='IGNORE'))

process_params = Table(
    'process_params', meta, Column('param', String),
    Column('process_id', BigInteger), Column('program', String),
    Column('type', String), Column('value', String), Column('run_id', Integer),
    Column('node_id', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'param',
                     'process_id',
                     'program',
                     'value',
                     name='unique_process_params',
                     sqlite_on_conflict='IGNORE'))

sngl_inspiral = Table(
    'sngl_inspiral', meta, Column('Gamma0', Double), Column('Gamma1', Double),
    Column('bank_chisq', Double), Column('bank_chisq_dof', Integer),
    Column('chisq', Double), Column('chisq_dof', Integer),
    Column('coa_phase', Double), Column('eff_distance', Double),
    Column('end_time', Integer), Column('end_time_ns', Integer),
    Column('event_id', BigInteger), Column('ifo', String),
    Column('mass1', Double), Column('mass2', Double),
    Column('process_id', BigInteger), Column('sigmasq', Double),
    Column('snr', Double), Column('spin1x', Double), Column('spin1y', Double),
    Column('spin1z', Double), Column('spin2x',
                                     Double), Column('spin2y', Double),
    Column('spin2z', Double), Column('search', String),
    Column('channel', String), Column('end_time_gmst', Double),
    Column('impulse_time', Integer), Column('impulse_time_ns', Integer),
    Column('template_duration', Double), Column('event_duration', Double),
    Column('amplitude', Double), Column('mchirp', Double),
    Column('mtotal', Double), Column('eta', Double), Column('kappa', Double),
    Column('chi', Double), Column('tau0', Double), Column('tau2', Double),
    Column('tau3', Double), Column('tau4', Double), Column('tau5', Double),
    Column('ttotal', Double), Column('psi0', Double), Column('psi3', Double),
    Column('alpha', Double), Column('alpha1',
                                    Double), Column('alpha2', Double),
    Column('alpha3', Double), Column('alpha4', Double),
    Column('alpha5', Double), Column('alpha6', Double), Column('beta', Double),
    Column('f_final', Double), Column('cont_chisq', Double),
    Column('cont_chisq_dof', Integer), Column('rsqveto_duration', Double),
    Column('Gamma2', Double), Column('Gamma3',
                                     Double), Column('Gamma4', Double),
    Column('Gamma5', Double), Column('Gamma6',
                                     Double), Column('Gamma7', Double),
    Column('Gamma8', Double), Column('Gamma9', Double),
    Column('run_id', Integer),
    UniqueConstraint('run_id',
                     'end_time',
                     'end_time_ns',
                     'event_id',
                     'process_id',
                     name='unique_sngl_inspiral',
                     sqlite_on_conflict='IGNORE'))

coinc_definer = Table(
    'coinc_definer', meta, Column('coinc_def_id', BigInteger),
    Column('description', String), Column('search', String),
    Column('search_coinc_type', BigInteger), Column('run_id', Integer),
    UniqueConstraint('run_id',
                     'coinc_def_id',
                     'search_coinc_type',
                     name='unique_coinc_definer',
                     sqlite_on_conflict='IGNORE'))

coinc_event = Table(
    'coinc_event', meta, Column('coinc_def_id', BigInteger),
    Column('coinc_event_id', BigInteger), Column('instruments', String),
    Column('likelihood', Double), Column('nevents', BigInteger),
    Column('process_id', BigInteger), Column('time_slide_id', BigInteger),
    Column('run_id', Integer),
    UniqueConstraint('run_id',
                     'coinc_def_id',
                     'coinc_event_id',
                     'process_id',
                     'time_slide_id',
                     name='unique_coinc_event',
                     sqlite_on_conflict='IGNORE'))

coinc_event_map = Table(
    'coinc_event_map', meta, Column('coinc_event_id', BigInteger),
    Column('event_id', BigInteger), Column('table_name', String),
    Column('run_id', Integer),
    UniqueConstraint('run_id',
                     'coinc_event_id',
                     'event_id',
                     'table_name',
                     name='unique_coinc_event_map',
                     sqlite_on_conflict='IGNORE'))

time_slide = Table(
    'time_slide', meta, Column('instrument', String), Column('offset', Double),
    Column('process_id', BigInteger), Column('time_slide_id', BigInteger),
    Column('run_id', Integer),
    UniqueConstraint('run_id',
                     'process_id',
                     'time_slide_id',
                     'instrument',
                     'offset',
                     name='unique_time_slide',
                     sqlite_on_conflict='IGNORE'))

coinc_inspiral = Table(
    'coinc_inspiral', meta, Column('coinc_event_id', BigInteger),
    Column('combined_far', Double), Column('end_time', Integer),
    Column('end_time_ns', Integer), Column('false_alarm_rate', Double),
    Column('ifos', String), Column('mass', Double), Column('mchirp', Double),
    Column('minimum_duration', Double), Column('snr', Double),
    Column('run_id', Integer),
    UniqueConstraint('run_id',
                     'coinc_event_id',
                     'end_time',
                     'end_time_ns',
                     name='unique_coinc_inspiral',
                     sqlite_on_conflict='IGNORE'))

postcoh = Table(
    'postcoh', meta, Column('bankid', Integer), Column('chisq_H1', Double),
    Column('chisq_K1', Double), Column('chisq_L1', Double),
    Column('chisq_V1', Double), Column('cmbchisq', Double),
    Column('coaphase_H1', Double), Column('coaphase_K1', Double),
    Column('coaphase_L1', Double), Column('coaphase_V1', Double),
    Column('cohsnr', Double), Column('dec', Double), Column('deff_H1', Double),
    Column('deff_K1', Double), Column('deff_L1', Double),
    Column('deff_V1', Double), Column('end_time', Integer),
    Column('end_time_ns', Integer), Column('end_time_ns_sngl_H1', Integer),
    Column('end_time_ns_sngl_K1', Integer),
    Column('end_time_ns_sngl_L1', Integer),
    Column('end_time_ns_sngl_V1', Integer), Column('end_time_sngl_H1',
                                                   Integer),
    Column('end_time_sngl_K1', Integer), Column('end_time_sngl_L1', Integer),
    Column('end_time_sngl_V1', Integer), Column('eta', Double),
    Column('event_id', BigInteger), Column('f_final', Double),
    Column('fap', Double), Column('far', Double), Column('far_1d', Double),
    Column('far_1d_sngl_H1', Double), Column('far_1d_sngl_K1', Double),
    Column('far_1d_sngl_L1', Double), Column('far_1d_sngl_V1', Double),
    Column('far_1w', Double), Column('far_1w_sngl_H1', Double),
    Column('far_1w_sngl_K1', Double), Column('far_1w_sngl_L1', Double),
    Column('far_1w_sngl_V1', Double), Column('far_2h', Double),
    Column('far_2h_sngl_H1', Double), Column('far_2h_sngl_K1', Double),
    Column('far_2h_sngl_L1', Double), Column('far_2h_sngl_V1', Double),
    Column('far_sngl_H1', Double), Column('far_sngl_K1', Double),
    Column('far_sngl_L1', Double), Column('far_sngl_V1', Double),
    Column('ifos', String), Column('is_background', Integer),
    Column('livetime', Integer), Column('livetime_1w', Integer),
    Column('livetime_1w_sngl_H1', Integer),
    Column('livetime_1w_sngl_K1', Integer),
    Column('livetime_1w_sngl_L1', Integer),
    Column('livetime_1w_sngl_V1', Integer), Column('livetime_1d', Integer),
    Column('livetime_1d_sngl_H1', Integer),
    Column('livetime_1d_sngl_K1', Integer),
    Column('livetime_1d_sngl_L1', Integer),
    Column('livetime_1d_sngl_V1', Integer), Column('livetime_2h', Integer),
    Column('livetime_2h_sngl_H1', Integer),
    Column('livetime_2h_sngl_K1', Integer),
    Column('livetime_2h_sngl_L1', Integer),
    Column('livetime_2h_sngl_V1', Integer), Column('nevent_1w', Integer),
    Column('nevent_1w_sngl_H1', Integer), Column('nevent_1w_sngl_K1', Integer),
    Column('nevent_1w_sngl_L1', Integer), Column('nevent_1w_sngl_V1', Integer),
    Column('nevent_1d', Integer), Column('nevent_1d_sngl_H1', Integer),
    Column('nevent_1d_sngl_K1', Integer), Column('nevent_1d_sngl_L1', Integer),
    Column('nevent_1d_sngl_V1', Integer), Column('nevent_2h', Integer),
    Column('nevent_2h_sngl_H1', Integer), Column('nevent_2h_sngl_K1', Integer),
    Column('nevent_2h_sngl_L1', Integer), Column('nevent_2h_sngl_V1', Integer),
    Column('mass1', Double), Column('mass2', Double), Column('mchirp', Double),
    Column('mtotal', Double), Column('nullsnr', Double),
    Column('pivotal_ifo', String), Column('pix_idx', Integer),
    Column('process_id', BigInteger), Column('ra', Double),
    Column('rank', Double), Column('skymap_fname', String),
    Column('snglsnr_H1', Double), Column('snglsnr_K1', Double),
    Column('snglsnr_L1', Double), Column('snglsnr_V1', Double),
    Column('spearman_pval', Double), Column('spin1x', Double),
    Column('spin1y', Double), Column('spin1z',
                                     Double), Column('spin2x', Double),
    Column('spin2y', Double), Column('spin2z', Double),
    Column('template_duration', Double), Column('tmplt_idx', Integer),
    Column('run_id', Integer), Column('node_id', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'end_time',
                     'end_time_ns',
                     'event_id',
                     'process_id',
                     name='unique_postcoh',
                     sqlite_on_conflict='IGNORE'))

segment_definer = Table(
    'segment_definer', meta, Column('comment', String), Column('ifos', String),
    Column('name', String), Column('process_id', BigInteger),
    Column('segment_def_id', BigInteger), Column('version', String),
    Column('run_id', Integer), Column('node_id', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'process_id',
                     'segment_def_id',
                     name='unique_segment_definer',
                     sqlite_on_conflict='IGNORE'))

segment_summary = Table(
    'segment_summary', meta, Column('comment', String),
    Column('end_time', Integer), Column('end_time_ns', Integer),
    Column('process_id', BigInteger), Column('segment_def_id', BigInteger),
    Column('segment_sum_id', BigInteger), Column('start_time', Integer),
    Column('start_time_ns', Integer), Column('run_id', Integer),
    Column('node_id', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'process_id',
                     'segment_def_id',
                     'segment_sum_id',
                     name='unique_segment_summary',
                     sqlite_on_conflict='IGNORE'))

segment = Table(
    'segment', meta, Column('end_time', Integer),
    Column('end_time_ns', Integer), Column('process_id', BigInteger),
    Column('segment_def_id', BigInteger), Column('segment_id', BigInteger),
    Column('start_time', Integer), Column('start_time_ns', Integer),
    Column('run_id', Integer), Column('node_id', String),
    UniqueConstraint('run_id',
                     'node_id',
                     'process_id',
                     'segment_def_id',
                     'segment_id',
                     name='unique_segment',
                     sqlite_on_conflict='IGNORE'))


class Monitor:
    kill_now = False

    def __init__(self, run_dir):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

        self.nodes = {}
        self.xmls = {}

        self.run_dir = run_dir

        # Get int from first 32 bits of sha256 hashed run_dir string.
        self.run_id = int.from_bytes(hashlib.sha256(
            bytes(self.run_dir.name, encoding='utf-8')).digest()[:4],
                                     'little',
                                     signed=True)

        # This connection cannot be async, will run into lock errors.
        self.sqliteEngine = create_engine('sqlite:///monitoring.db')
        self.sqliteConn = self.sqliteEngine.connect()

        self.psqlEngine = create_engine(
            'postgresql+psycopg2://luke.davis:monitoring@ldas-pcdev11:5432/monitoring'
        )
        self.psqlConn = self.psqlEngine.connect()

        meta.create_all(self.sqliteEngine)
        meta.create_all(self.psqlEngine)

        result = re.match('^run-.*-([0-9]*)-([a-z0-9]*)$', self.run_dir.name)
        if result:
            unixtime = result.group(1)
            commit = result.group(2)
        else:
            unixtime = '0'
            commit = '0'

        sqlcmd = 'INSERT INTO "runs" ("run_id", "unixtime", "run_dir", "commit") VALUES (\'%s\', \'%s\', \'%s\', \'%s\')' % (
            self.run_id, unixtime, self.run_dir.name, commit)

        self.sqliteConn.execute(text(sqlcmd))
        self.psqlConn.execute(
            text(sqlcmd + " ON CONFLICT ON CONSTRAINT unique_runs DO NOTHING"))

        self.sqliteConn.commit()
        self.psqlConn.commit()

        self.lastTimes = {}
        sqlcmd = 'SELECT node_id, max(end_time) as last_time FROM "postcoh" WHERE run_id=\'%s\' GROUP BY node_id;' % self.run_id

        result = self.sqliteConn.execute(text(sqlcmd))
        for row in result:
            self.lastTimes[row[0]] = row[1]

        result = self.psqlConn.execute(text(sqlcmd))
        for row in result:
            if row[1] > self.lastTimes[row[0]]:
                self.lastTimes[row[0]] = row[1]

    def exit_gracefully(self, *args):
        logger.info("Stopping gracefully.")
        self.kill_now = True

    def doSleep(self, num):
        for sec in range(num):
            time.sleep(1)
            if self.kill_now:
                break

    def convert_gps_to_unix_timestamp(self, gps_times):
        gps_times = Time(gps_times, format="gps")
        utc_times = Time(gps_times, format="unix")
        return utc_times.value

    def doExecute(self, table_name, records):
        sqlinsert = "INSERT INTO \"%s\"(" % table_name
        sqlvalues = ") VALUES("
        for key in records[0].keys():
            sqlinsert += "\"%s\", " % key
            sqlvalues += ":%s, " % key
        sqlcmd = sqlinsert[:-2] + sqlvalues[:-2] + ")"

        self.sqliteConn.execute(text(sqlcmd), records)
        self.psqlConn.execute(
            text(sqlcmd +
                 " ON CONFLICT ON CONSTRAINT \"unique_%s\" DO NOTHING" %
                 table_name), records)

        self.sqliteConn.commit()
        self.psqlConn.commit()

    def program(self):
        while not self.kill_now:
            for subpath in self.run_dir.glob('**/*'):
                if self.kill_now:
                    break
                if subpath.parent.name + subpath.name in self.xmls:
                    continue
                if re.match('^[0-9][0-9][0-9]_registry.txt$', subpath.name):
                    logger.info("Loading %s" % subpath.name)
                    host_fqdn = open(subpath, 'r').readlines()[0]
                    host_match = re.match('http://(.*):.*', host_fqdn)
                    if host_match:
                        host = host_match.group(1)
                        node_id = subpath.name[:3]
                        if subpath.name in self.nodes:
                            # If host or nodeID has changed
                            if self.nodes[subpath.name][
                                    'host'] == host and self.nodes[
                                        subpath.name]['node_id'] == node_id:
                                continue
                        self.nodes[subpath.name] = {
                            'host': host,
                            'node_id': node_id
                        }
                        self.doExecute(table_name='hosts',
                                       records=[{
                                           'run_id':
                                           self.run_id,
                                           'node_id':
                                           node_id,
                                           'host':
                                           host,
                                           'unixtime':
                                           Time(Time.now(),
                                                format="unix").value
                                       }])
                    continue

                if re.match('^latency_history.txt$', subpath.name):
                    node_id = subpath.parent.name
                    logger.info("Loading %s/latency_history.txt" % node_id)
                    data = pd.read_csv(
                        subpath,
                        sep=' ',
                        names=['gpstime', 'latency', 'cohsnr', 'cmbchisq'])
                    data['gpstime'] = pd.to_numeric(data['gpstime'])
                    data['unixtime'] = self.convert_gps_to_unix_timestamp(
                        data['gpstime'])
                    data['run_id'] = self.run_id
                    data['node_id'] = node_id
                    if len(data) > 0:
                        self.doExecute(table_name='latencies',
                                       records=data.to_dict('records'))
                    continue

                if re.match('^state_vector_on_off_gap.txt$', subpath.name):
                    IFO = subpath.parent.name
                    node_id = subpath.parent.parent.name
                    logger.info("Loading %s/state_vector_on_off_gap.txt" %
                                node_id)
                    data = pd.read_csv(
                        subpath,
                        sep=' ',
                        names=['gpstime', 'on_col', 'off', 'gap'])
                    data['gpstime'] = pd.to_numeric(data['gpstime'])
                    data['unixtime'] = self.convert_gps_to_unix_timestamp(
                        data['gpstime'])
                    data['run_id'] = self.run_id
                    data['node_id'] = node_id
                    data['ifo'] = IFO
                    if len(data) > 0:
                        self.doExecute(table_name='state_vector',
                                       records=data.to_dict('records'))
                    continue

                if re.match('^strain_add_drop.txt$', subpath.name):
                    IFO = subpath.parent.name
                    node_id = subpath.parent.parent.name
                    logger.info("Loading %s/strain_add_drop.txt" % node_id)
                    data = pd.read_csv(
                        subpath,
                        sep=' ',
                        names=['gpstime', 'add_col', 'drop_col'])
                    data['gpstime'] = pd.to_numeric(data['gpstime'])
                    data['unixtime'] = self.convert_gps_to_unix_timestamp(
                        data['gpstime'])
                    data['run_id'] = self.run_id
                    data['node_id'] = node_id
                    data['ifo'] = IFO
                    if len(data) > 0:
                        self.doExecute(table_name='strain',
                                       records=data.to_dict('records'))
                    continue

                xmlMatch = re.match(
                    '^[A-Z0-9]*_SEGMENTS_([0-9]*)_([0-9]*).xml.gz$',
                    subpath.name)
                if not xmlMatch:
                    xmlMatch = re.match(
                        '^[0-9]*_zerolag_([0-9]*)_([0-9]*).xml.gz$',
                        subpath.name)
                if xmlMatch:
                    logger.info("Loading %s" % subpath.name)
                    startTime = int(xmlMatch.group(1))
                    duration = int(xmlMatch.group(2))
                    node_id = subpath.parent.name

                    if node_id not in self.lastTimes or self.lastTimes[
                            node_id] < startTime + duration:
                        xmldoc = spiir.io.ligolw.load_ligolw_xmldoc(subpath)
                        df = spiir.io.ligolw.table.get_tables_from_xmldoc(
                            xmldoc)
                        for tbl in df:
                            logger.info("Loading %s: %d rows" %
                                        (tbl, len(df[tbl])))
                            if len(df[tbl]) == 0:
                                continue
                            df[tbl]['run_id'] = self.run_id
                            df[tbl]['node_id'] = node_id
                            self.doExecute(table_name=tbl,
                                           records=df[tbl].to_dict('records'))
                    self.xmls[subpath.parent.name + subpath.name] = True
                    continue

            self.doSleep(30)


@click.command()
@click.option('--run_dir',
              prompt='Run directory: ',
              help='Run directory.',
              type=Path)
def main(run_dir):

    monitor = Monitor(run_dir=run_dir)
    monitor.program()

    monitor.sqliteConn.close()
    monitor.psqlConn.close()


if __name__ == '__main__':
    main()

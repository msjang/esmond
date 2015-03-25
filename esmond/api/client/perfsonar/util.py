"""
Utility code for perfsonar esmond client programs.
"""

import calendar
import copy
import cStringIO
import csv
import datetime
import json
import socket
import sys
import urllib

from optparse import OptionParser
from collections import OrderedDict
from dateutil.parser import parse

from .query import ApiFilters

# Event types with an associated "formatting type" to be 
# used by esmond-get, etc.
EVENT_MAP = OrderedDict([
    ('failures', 'failures'),
    ('histogram-owdelay', 'histogram'),
    ('histogram-rtt', 'histogram'),
    ('histogram-ttl', 'histogram'),
    ('histogram-ttl-reverse', 'histogram'),
    ('ntp-delay', 'numeric'),
    ('ntp-dispersion', 'numeric'),
    ('ntp-jitter', 'numeric'),
    ('ntp-offset', 'numeric'),
    ('ntp-polling-interval', 'numeric'),
    ('ntp-reach', 'numeric'),
    ('ntp-stratum', 'numeric'),
    ('ntp-wander', 'numeric'),
    ('packet-duplicates', 'numeric'),
    ('packet-duplicates-bidir', 'numeric'),
    ('packet-loss-rate', 'numeric'),
    ('packet-loss-rate-bidir', 'numeric'),
    ('packet-trace', 'packet_trace'),
    ('packet-count-lost', 'numeric'),
    ('packet-count-lost-bidir', 'numeric'),
    ('packet-count-sent', 'numeric'),
    ('packet-reorders', 'numeric'),
    ('packet-reorders-bidir', 'numeric'),
    ('packet-retransmits', 'numeric'),
    ('packet-retransmits-subintervals', 'subintervals'),
    ('path-mtu', 'numeric'),
    ('streams-packet-retransmits', 'number_list'),
    ('streams-packet-retransmits-subintervals', 'subinterval_list'),
    ('streams-throughput', 'number_list'),
    ('streams-throughput-subintervals', 'subinterval_list'),
    ('throughput', 'numeric'),
    ('throughput-subintervals', 'subintervals'),
    ('time-error-estimates', 'numeric'),
])

EVENT_TYPES = EVENT_MAP.keys()

def event_format(et):
    return EVENT_MAP[et]

DEFAULT_FIELDS = [
        'source', 
        'destination', 
        'measurement_agent',
        'input_source',
        'input_destination',
        'tool_name', 
]

# Exceptions for client operations

class EsmondClientException(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

class EsmondClientWarning(Warning): pass

# Command line argument validation functions

def check_url(options, parser):
    if not options.url:
        print '--url is a required arg\n'
        parser.print_help()
        sys.exit(-1)
    try:
        urllib.urlopen(options.url)
    except Exception, e:
        print 'Could not open --url {0} - error: {1}'.format(options.url, e)

def check_valid_hostnames(options, parser, hn_args=[]):
    try:
        for hn in hn_args:
            if getattr(options, hn):
                socket.gethostbyname(getattr(options, hn))
    except:
        print '--{0} arg had invalid hostname: {1}'.format(hn, getattr(options, hn))
        sys.exit(-1)

def check_event_types(options, parser, require_event):
    if options.type and options.type not in EVENT_TYPES:
        print '{0} is not a valid event type'.format(options.type)
        list_event_types()
        sys.exit(-1)
    if require_event and not options.type:
        print 'The --event-type arg is required. Use -L to see a list.\n'
        parser.print_help()
        sys.exit(-1)

def check_formats(options, parser):
    f_args = ['human', 'json', 'csv']
    if options.format not in f_args:
        print '{0} is not a valid --output-format arg (one of: {1})'.format(options.format, f_args)
        sys.exit(-1)
    if options.format == 'csv' and options.metadata:
        print '--output-format csv can not be used with --metadata-extended'
        sys.exit(-1)

def check_summary(options, parser):
    s_args = ['aggregation', 'average', 'statistics']
    if options.summary_type and options.summary_type not in s_args:
        print '{0} is not a valid --summary-type arg (one of: {1})'.format(options.summary_type, s_args)
        sys.exit(-1)

def src_dest_required(options, parser):
    if not options.src or not (options.dest or options.type.startswith('ntp-')):
        print '--src and --dest args are required\n'
        parser.print_help()
        sys.exit(-1)

# Utility functions to import into clients.

def get_start_and_end_times(options):
    """
    See:
    https://dateutil.readthedocs.org/en/latest/examples.html#parse-examples
    To see the variety of date formats that it will accept.
    """
    start = end = None

    if not options.start:
        start = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    else:
        try:
            start = parse(options.start)
        except:
            print 'could not parse --start-time arg: {0}'.format(options.start)
            sys.exit(-1)

    if not options.end:
        end = datetime.datetime.utcnow()
    else:
        try:
            end = parse(options.end)
        except:
            print 'could not parse --end-time arg: {0}'.format(options.end)
            sys.exit(-1)

    return start, end

# Misc

def list_event_types():
    print '\nValid event types:'
    for et in EVENT_TYPES:
        print '    {0}'.format(et)

# Canned option parsers for clients

def perfsonar_client_opts(require_src_dest=False, require_event=False):
    """
    Return a standard option parser for the perfsonar clients.
    """
    usage = '%prog [ -u URL -s SRC -d DEST | -a AGENT | -e EVENT | -t TOOL | -L | -o FORMAT | -v ]'
    usage += '\n--begin and --end args parsed by python-dateutil so fairly flexible with the date formats.'
    parser = OptionParser(usage=usage)
    parser.add_option('-u', '--url', metavar='URL',
            type='string', dest='url', 
            help='URL of esmond API you want to talk to.')
    parser.add_option('-s', '--src', metavar='SRC',
            type='string', dest='src', 
            help='Host originating the test.')
    parser.add_option('-d', '--dest', metavar='DEST',
            type='string', dest='dest', 
            help='Test endpoint.')
    parser.add_option('-a', '--agent', metavar='AGENT',
            type='string', dest='agent', 
            help='Host that initiated the test - useful for central MAs.')
    parser.add_option('-e', '--event-type', metavar='EVENT',
            type='string', dest='type', 
            help='Type of data (loss, latency, throughput, etc) - see -L arg.')
    parser.add_option('-t', '--tool', metavar='TOOL',
            type='string', dest='tool', 
            help='Tool used to run test (bwctl/iperf3, powstream, "bwctl/tracepath,traceroute", gridftp, etc).')
    parser.add_option('-S', '--start-time', metavar='START',
            type='string', dest='start', 
            help='Start time of query (default: 24 hours ago).')
    parser.add_option('-F', '--filter', metavar='FILTER',
            type='string', dest='filter', action='append',
            help='Specify additional query filters - format: -F key:value. Can be used multiple times, invalid filters will be ignored.')
    parser.add_option('-E', '--end-time', metavar='END',
            type='string', dest='end', 
            help='End time of query (default: now).')
    parser.add_option('-L', '--list-events',
            dest='list_event', action='store_true', default=False,
            help='List available event types.')
    parser.add_option('-M', '--metadata-extended',
            dest='metadata', action='store_true', default=False,
            help='Show extended metadata tool-specific values (can not be used with -o csv).')
    parser.add_option('-T', '--summary-type', metavar='SUMMARY_TYPE',
            type='string', dest='summary_type', 
            help='Request summary data of type [aggregation, average, statistics].')
    parser.add_option('-W', '--summary-window', metavar='SUMMARY_WINDOW',
            type='int', dest='summary_window', default=0,
            help='Timeframe in seconds described by the summary (default: %default).')
    parser.add_option('-o', '--output-format', metavar='O_FORMAT',
            type='string', dest='format', default='human',
            help='Output format [human, json, csv] (default: human).')
    parser.add_option('-I', '--ip',
            dest='ip', action='store_true', default=False,
            help='Show source/dest as IP addresses, not hostnames.')
    parser.add_option('-v', '--verbose',
        dest='verbose', action='store_true', default=False,
        help='Verbose output.')
    options, args = parser.parse_args()

    if options.list_event:
        list_event_types()
        sys.exit(0)

    check_url(options, parser)

    if require_src_dest:
        src_dest_required(options, parser)

    check_valid_hostnames(options, parser, hn_args=['src', 'dest', 'agent'])

    check_event_types(options, parser, require_event)

    check_summary(options, parser)

    check_formats(options, parser)

    return options, args

def perfsonar_client_filters(options):
    """
    Return a standard filter object based on the opts in 
    perfsonar_client_opts()
    """

    start, end = get_start_and_end_times(options)

    filters = ApiFilters()
    filters.source = options.src
    filters.destination = options.dest
    filters.measurement_agent = options.agent
    filters.event_type = options.type
    filters.time_start = calendar.timegm(start.utctimetuple())
    filters.time_end = calendar.timegm(end.utctimetuple())
    filters.tool_name = options.tool
    filters.summary_type = options.summary_type
    if options.summary_window:
        filters.summary_window = options.summary_window
    filters.verbose = options.verbose

    if options.filter:
        # Apply arbritrary metadata filters
        for f in options.filter:
            if f.find(':') == -1:
                print '--filter arg {0} should be of the format key:value'.format(f)
                continue
            k,v = f.split(':')
            key = k.replace('-', '_')
            if not hasattr(filters, k):
                print '--filter arg {0} is not a valid filtering value'.format(key)
                continue
            setattr(filters, key, v)

    return filters

# Output classes for clients

class EsmondOutput(object):
    def __init__(self, data, columns):
        self._data = data
        self._columns = columns
        self._output = None

        self._list_fields = None

        if not isinstance(self._data, list):
            raise EsmondClientException('Data arg must be a list')

        if len(self._data) and not isinstance(self._data[0], dict):
            raise EsmondClientException('Data arg must be a list of dicts')

    def get_output(self):
        raise NotImplementedError('Implement in subclasses.')

    def _massage_row_dict(self, d):
        # scan first instance to see if we need to fix anything - 
        # no point in processing each row if not necessary.
        if self._list_fields == None:
            self._list_fields = []
            for k,v in d.items():
                if isinstance(v, list):
                    self._list_fields.append(k)

        # if no changes need to be made, just quit
        if len(self._list_fields) == 0:
            return d

        # don't change the original data
        new_d = copy.copy(d)

        # turn any lists into comma separated sequences
        for lf in self._list_fields:
            new_d[lf] = ', '.join( [ str(x) for x in new_d.get(lf) ] )

        return new_d


class HumanOutput(EsmondOutput):
    def __init__(self, data, columns, extended_data=False):
        super(HumanOutput, self).__init__(data, columns)

        self._extended_data = extended_data

    def get_output(self):
        entry_delim = '= + = + = + = + = + =\n'

        if not self._output:
            self._output = ''
            for row in self._data:
                row = self._massage_row_dict(row)
                for c in self._columns:
                    self._output += '{0}: {1}\n'.format(c, row.get(c))
                if self._extended_data:
                    for k,v in row.items():
                        if k in self._columns: continue
                        self._output += '{0}: {1}\n'.format(k,v)
                self._output += entry_delim
            self._output = self._output[:self._output.rfind(entry_delim)]

        return self._output

class JsonOutput(EsmondOutput):
    def get_output(self):
        if not self._output:
            self._output = json.dumps(self._data)
        return self._output

class CSVOutput(EsmondOutput):
    def get_output(self):
        if not self._output:
            cfile = cStringIO.StringIO()

            writer = csv.DictWriter(cfile, fieldnames=self._columns, extrasaction='ignore')
            writer.writeheader()
            for row in self._data:
                writer.writerow(self._massage_row_dict(row))

            self._output = cfile.getvalue()
            cfile.close()
        return self._output

def output_factory(options, data, columns):
    if options.format == 'human':
        if not options.metadata:
            return HumanOutput(data, columns)
        else:
            return HumanOutput(data, columns, extended_data=True)
    elif options.format == 'json':
        return JsonOutput(data, None)
    elif options.format == 'csv':
        return CSVOutput(data, columns)





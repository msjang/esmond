import json
import time
import calendar
import datetime
import os

import pprint

pp = pprint.PrettyPrinter(indent=4)

import mock 

# This MUST be here in any testing modules that use cassandra!
os.environ['ESMOND_UNIT_TESTS'] = 'True'

from django.core.urlresolvers import reverse
from django.test import TestCase
from django.utils.timezone import make_aware, utc

from rest_framework.test import APIClient

from esmond.api.models import *
from esmond.api.tests.example_data import (build_default_metadata, 
    build_pdu_metadata, build_sample_inventory_from_metadata)
from esmond.cassandra import AGG_TYPES
from esmond.api import SNMP_NAMESPACE, OIDSET_INTERFACE_ENDPOINTS
from esmond.api.dataseries import QueryUtil

def datetime_to_timestamp(dt):
    return calendar.timegm(dt.timetuple())

from django.test import TestCase

class DeviceAPITestsBase(TestCase):
    fixtures = ["oidsets.json"]
    def setUp(self):
        super(DeviceAPITestsBase, self).setUp()

        self.client = APIClient()

        self.td = build_default_metadata()

    def get_api_client(self, admin_auth=False):
        client = APIClient()

        if admin_auth:
            client.credentials(HTTP_AUTHORIZATION='Token {0}'.format(self.td.user_admin_apikey.key))

        return client


class DeviceAPITests(DeviceAPITestsBase):
    def test_get_device_list(self):
        url = '/v2/device/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        # by default only currently active devices are returned _a, _alu, _inf
        data = json.loads(response.content)
        # print pp.pprint(data)
        self.assertEquals(len(data), 3)

        # get all three devices, with date filters
        begin = datetime_to_timestamp(self.td.rtr_b.begin_time)
        response = self.client.get(url, dict(begin=begin))
        data = json.loads(response.content)
        # print pp.pprint(data)
        self.assertEquals(len(data), 4)

        # exclude rtr_b by date

        begin = datetime_to_timestamp(self.td.rtr_a.begin_time)
        response = self.client.get(url, dict(begin=begin))
        data = json.loads(response.content)
        # print pp.pprint(data)
        self.assertEquals(len(data), 3)
        for d in data:
            self.assertNotEqual(d['name'], 'rtr_b')

        # exclude all routers with very old end date
        response = self.client.get(url, dict(end=0))
        data = json.loads(response.content)
        # print pp.pprint(data)
        self.assertEquals(len(data), 0)

        # test for equal (gte/lte)
        begin = datetime_to_timestamp(self.td.rtr_b.begin_time)
        response = self.client.get(url, dict(begin=0, end=begin))
        data = json.loads(response.content)
        # print pp.pprint(data)
        self.assertEquals(len(data), 1)
        self.assertEquals(data[0]['name'], 'rtr_b')

        end = datetime_to_timestamp(self.td.rtr_b.end_time)
        response = self.client.get(url, dict(begin=0, end=end))
        data = json.loads(response.content)
        # print pp.pprint(data)
        self.assertEquals(len(data), 1)
        self.assertEquals(data[0]['name'], 'rtr_b')

    def test_get_device_detail(self):
        url = '/v2/device/rtr_a/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)
        # print json.dumps(data, indent=4)
        for field in [
            'active',
            'begin_time',
            'end_time',
            'id',
            'leaf',
            'name',
            'resource_uri',
            'uri',
            ]:
            self.assertIn(field,data)

        children = {}
        for child in data['children']:
            children[child['name']] = child
            for field in ['leaf','name','uri']:
                self.assertIn(field, child)

        for child_name in ['all', 'interface', 'system']:
            self.assertIn(child_name, children)
            child = children[child_name]
            self.assertEqual(child['uri'], url + child_name + '/')

    def test_post_device_list_unauthenticated(self):
        # We don't allow POSTs at this time.  Once that capability is added
        # these tests will need to be expanded.

        r = self.client.post('/v2/device/entries/', format='json',
                    data=self.td.rtr_z_post_data)
        self.assertEqual(r.status_code, 401)

    def test_get_device_interface_list(self):
        url = '/v2/device/rtr_a/interface/'

        # single interface at current time
        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        # print json.dumps(data, indent=4)
        self.assertEquals(len(data['children']), 1)

        # no interfaces if we are looking in the distant past
        response = self.client.get(url, dict(end=0))
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['children']), 0)

        url = '/v2/device/rtr_b/interface/'

        begin = datetime_to_timestamp(self.td.rtr_b.begin_time)
        end = datetime_to_timestamp(self.td.rtr_b.end_time)

        # rtr_b has two interfaces over it's existence, but three ifrefs
        response = self.client.get(url, dict(begin=begin, end=end))
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['children']), 3)

        # rtr_b has only one interface during the last part of it's existence
        begin = datetime_to_timestamp(self.td.rtr_b.begin_time +
                datetime.timedelta(days=8))
        response = self.client.get(url, dict(begin=begin, end=end))
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data['children']), 1)
        self.assertEquals(data['children'][0]['ifName'], 'xe-1/0/0')

        url = '/v2/device/rtr_alu/interface/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(len(data['children']), 1)
        self.assertEquals(data['children'][0]['ifName'], '3/1/1')

    def test_get_device_interface_detail(self):
        for device, iface in (
                ('rtr_a', 'xe-0/0/0'),
                ('rtr_alu', '3/1/1'),
                ('rtr_inf', 'xe-3/0/0'),
            ):

            url = '/v2/device/{0}/interface/{1}/'.format(device,
                    atencode(iface))

            response = self.client.get(url)
            self.assertEquals(response.status_code, 200)

            data = json.loads(response.content)
            # print json.dumps(data, indent=4)
            self.assertEquals(data['ifName'], iface.replace("_", "/"))

            for field in [
                    'begin_time',
                    'children',
                    'device_uri',
                    'end_time',
                    'ifAlias',
                    'ifName',
                    'ifHighSpeed',
                    'ifIndex',
                    'ifSpeed',
                    'ipAddr',
                    'leaf',
                    'uri',
                ]:
                self.assertIn(field, data)

            self.assertNotEqual(len(data['children']), 0)

            children = {}
            for child in data['children']:
                children[child['name']] = child
                for field in ['leaf','name','uri']:
                    self.assertIn(field, child)

            for oidset in Device.objects.get(name=device).oidsets.all():
                if oidset.name not in OIDSET_INTERFACE_ENDPOINTS.endpoints:
                    continue

                for child_name in OIDSET_INTERFACE_ENDPOINTS.endpoints[oidset.name].keys():
                    self.assertIn(child_name , children)
                    child = children[child_name]
                    self.assertEqual(child['uri'], url + child_name)
                    self.assertTrue(child['leaf'])

    def test_get_device_interface_detail_with_multiple_ifrefs(self):
        iface = "xe-2/0/0"
        url = '/v2/device/rtr_b/interface/{0}/'.format(atencode(iface))

        # get the first xe-2/0/0 ifref
        begin = datetime_to_timestamp(self.td.rtr_b.begin_time)
        end = datetime_to_timestamp(self.td.rtr_b.begin_time +
                datetime.timedelta(days=1))

        response = self.client.get(url, dict(begin=begin, end=end))
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(data['ifName'], iface)
        self.assertEquals(data['ifAlias'], "test interface")
        self.assertEquals(data['ipAddr'], "10.0.0.2")

        # get the second xe-2/0/0 ifref
        begin = datetime_to_timestamp(self.td.rtr_b.begin_time + 
                datetime.timedelta(days=5))
        end = datetime_to_timestamp(self.td.rtr_b.end_time)

        response = self.client.get(url, dict(begin=begin, end=end))
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(data['ifName'], iface)
        self.assertEquals(data['ifAlias'], "test interface with new ifAlias")
        self.assertEquals(data['ipAddr'], "10.0.1.2")

        # query covering whole range should get the later xe-2/0/0 ifref
        begin = datetime_to_timestamp(self.td.rtr_b.begin_time)
        end = datetime_to_timestamp(self.td.rtr_b.end_time)

        response = self.client.get(url, dict(begin=begin, end=end))
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(data['ifName'], iface)
        self.assertEquals(data['ifAlias'], "test interface with new ifAlias")
        self.assertEquals(data['ipAddr'], "10.0.1.2")

    def test_get_device_interface_list_hidden(self):
        url = '/v2/device/rtr_a/interface/'

        response = self.client.get(url)
        data = json.loads(response.content)

        self.assertEquals(response.status_code, 200)
        self.assertEquals(len(data['children']), 1)
        for child in data['children']:
            self.assertTrue(":hide:" not in child['ifAlias'])

        response = self.get_api_client(admin_auth=True).get(url)
        data = json.loads(response.content)
        # print json.dumps(data, indent=4)
        self.assertEquals(len(data['children']), 2)

    def test_get_device_interface_detail_hidden(self):
        url = '/v2/device/rtr_a/interface/xe-1@2F0@2F0/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 404)

        response = self.get_api_client(admin_auth=True).get(url)
        data = json.loads(response.content)
        # print json.dumps(data, indent=4)
        self.assertEquals(response.status_code, 200)
        self.assertTrue(":hide:" in data['ifAlias'])

    def test_inventory_endpoint(self):

        build_sample_inventory_from_metadata()

        url = '/v2/inventory/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data), 53)

        url += '?row_key__contains=Errors'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEquals(len(data), 12)

class MockCASSANDRA_DB(object):
    def __init__(self, config):
        pass

    def query_baserate_timerange(self, path=None, freq=None, ts_min=None, ts_max=None):
        # Mimic returned data, format elsehwere
        self._test_incoming_args(path, freq, ts_min, ts_max)
        if path[0] not in [SNMP_NAMESPACE] : return []
        if path[1] not in ['rtr_a', 'rtr_b', 'rtr_inf'] : return []
        s_bin = (ts_min/freq)*freq
        if s_bin < ts_min:
            s_bin += freq
        return [
            {'is_valid': 2, 'ts': s_bin, 'val': 10},
            {'is_valid': 2, 'ts': s_bin+freq, 'val': 20},
            {'is_valid': 2, 'ts': s_bin+(freq*2), 'val': 40},
            {'is_valid': 0, 'ts': s_bin+(freq*3), 'val': 80}
        ]

    def query_raw_data(self, path=None, freq=None, ts_min=None, ts_max=None):
        if 'SentryPoll' in path:
            s_bin = (ts_min/freq)*freq
            e_bin = (ts_max/freq)*freq
            n_bins = (e_bin - s_bin) / freq
            return [ {'ts': s_bin+(i*freq), 'val': 1200} for i in range(n_bins) ]
        else:
            return self.query_baserate_timerange(path, freq, ts_min, ts_max)

    def query_aggregation_timerange(self, path=None, freq=None, ts_min=None, ts_max=None, cf=None):
        self._test_incoming_args(path, freq, ts_min, ts_max, cf)
        s_bin = (ts_min/freq)*freq
        if s_bin < ts_min:
            s_bin += freq
        if cf == 'average':
            return [
                {'ts': s_bin, 'val': 60, 'cf': 'average'},
                {'ts': s_bin+freq, 'val': 120, 'cf': 'average'},
                {'ts': s_bin+(freq*2), 'val': 240, 'cf': 'average'},
            ]
        elif cf == 'min':
            return [
                {'ts': s_bin, 'val': 0, 'cf': 'min', 'm_ts': 2},
                {'ts': s_bin+freq, 'val': 10, 'cf': 'min', 'm_ts': 12},
                {'ts': s_bin+(freq*2),'val': 20, 'cf': 'min', 'm_ts': 22},
            ]
        elif cf == 'max':
            return [
                {'ts': s_bin, 'val': 75, 'cf': 'max', 'm_ts': 2},
                {'ts': s_bin+freq, 'val': 150, 'cf': 'max', 'm_ts': 12},
                {'ts': s_bin+(freq*2), 'val': 300, 'cf': 'max', 'm_ts': 22},
            ]
        else:
            pass

    def _test_incoming_args(self, path, freq, ts_min, ts_max, cf=None):
        assert isinstance(path, list)
        assert isinstance(freq, int)
        assert isinstance(ts_min, int)
        assert isinstance(ts_max, int)
        if cf:
            assert isinstance(cf, str) or isinstance(cf, unicode)
            assert cf in AGG_TYPES

    def update_rate_bin(self, ratebin):
        pass

    def set_raw_data(self, rawdata):
        pass

    def flush(self):
        pass

class APIDataTestResults(object):
    @staticmethod
    def get_agg_range(agg, in_ms=False):
        end = 1386090000
        if in_ms: end = end*1000
        begin = end - (agg*3)
        params = {'begin': begin, 'end': end, 'agg': agg}
        return params


class DeviceAPIDataTests(DeviceAPITestsBase):
    def setUp(self):
        super(DeviceAPIDataTests, self).setUp()
        # mock patches names where used/imported, not where defined
        # This form will patch a class when it is instantiated by the executed code:
        # self.patcher = mock.patch("esmond.api.api.CASSANDRA_DB", MockCASSANDRA_DB)
        # This form will patch a module-level class instance:
        self.patcher = mock.patch("esmond.api.api_v2.db", MockCASSANDRA_DB(None))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_bad_endpoints(self):
        # there is no router called nonexistent
        url = '/v2/device/nonexistent/interface/xe-0@2F0@2F0/in'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)

        # rtr_a does not have an nonexistent interface
        url = '/v2/device/rtr_a/interface/nonexistent/in'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)

        # there is no nonexistent sub collection in traffic
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/nonexistent'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)

        # there is no nonexistent collection 
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/nonexistent/in'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)

        # rtr_b has no traffic oidsets defined
        url = '/v2/device/rtr_b/interface/xe-0@2F0@2F0/nonexistent/in'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)


    def test_get_device_interface_data_detail(self):
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/in'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(int(data['agg']), 30)
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][1]['ts'], data['data'][0]['ts']+30)
        self.assertEquals(data['data'][1]['val'], 20)

        url = '/v2/device/rtr_inf/interface/xe-3@2F0@2F0/out'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(int(data['agg']), 30)
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], data['data'][0]['ts']+60)
        self.assertEquals(data['data'][2]['val'], 40)

        # make sure it works with a trailing slash too
        url = '/v2/device/rtr_inf/interface/xe-3@2F0@2F0/out/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(int(data['agg']), 30)
        self.assertEquals(data['resource_uri'], url.rstrip("/"))
        self.assertEquals(data['data'][2]['ts'], data['data'][0]['ts']+60)
        self.assertEquals(data['data'][2]['val'], 40)

        # test for interfaces with multiple IfRefs
        url = '/v2/device/rtr_b/interface/xe-2@2F0@2F0/in'

        begin = datetime_to_timestamp(self.td.rtr_b.begin_time)
        end = datetime_to_timestamp(self.td.rtr_b.end_time)

        response = self.client.get(url, dict(begin=begin, end=end))
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(int(data['agg']), 30)
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][1]['ts'], data['data'][0]['ts']+30)
        self.assertEquals(data['data'][1]['val'], 20)


    def test_get_device_interface_data_detail_hidden(self):
        url = '/v2/device/rtr_a/interface/xe-1@2F0@2F0/in'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

        response = self.get_api_client(admin_auth=True).get(url)

        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content)
        self.assertTrue(len(data['data']) > 0)

    def test_bad_aggregations(self):
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/in'

        params = {'agg': '3601'} # this agg does not exist

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

        params = {'agg': '3600', 'cf': 'bad'} # this cf does not exist

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)


    def test_get_device_interface_data_aggs(self):
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/in'

        ts = time.time()

        params = { 'agg': '3600' , 'begin': ts-(3600*3)}

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(data['agg'], params['agg'])
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], data['data'][0]['ts']+int(params['agg'])*2)
        self.assertEquals(data['data'][2]['val'], 240)

        # try the same agg, different cf
        params['cf'] = 'min'

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['cf'], 'min')
        self.assertEquals(data['agg'], params['agg'])
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], data['data'][0]['ts']+int(params['agg'])*2)
        self.assertEquals(data['data'][2]['val'], 20)

        # and the last cf
        params['cf'] = 'max'

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['cf'], 'max')
        self.assertEquals(data['agg'], params['agg'])
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], data['data'][0]['ts']+int(params['agg'])*2)
        self.assertEquals(data['data'][2]['val'], 300)

    def test_get_device_errors(self):
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/error/in'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(data['resource_uri'], url)

        # print json.dumps(data, indent=4)

        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/discard/out'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(data['resource_uri'], url)

    def test_timerange_limiter(self):
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/in'

        params = { 
            'begin': int(time.time() - datetime.timedelta(days=31).total_seconds())
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/out'

        params = {
            'agg': '3600',
            'begin': int(time.time() - datetime.timedelta(days=366).total_seconds())
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/in'

        params = {
            'agg': '86400',
            'begin': int(time.time() - datetime.timedelta(days=366*10).total_seconds())
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

    def test_float_timestamp_input(self):
        url = '/v2/device/rtr_a/interface/xe-0@2F0@2F0/in'

        # pass in floats
        params = { 
            'begin': time.time() - 3600,
            'end': time.time()
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['begin_time'], int(params['begin']))
        self.assertEquals(data['end_time'], int(params['end']))

        # print json.dumps(data, indent=4)

    #
    # The following tests are for the /timeseries rest namespace.
    #

    def test_bad_timeseries_endpoints(self):
        # url = '/v1/timeseries/BaseRate/rtr_d/FastPollHC/ifHCInOctets/fxp0.0/30000'

        # all of these endpoints are incomplete and just 
        # return 404 Not Found (changed from 404 Bad Request
        # because re-writing a bunch of url patterns to parse 
        # incomplete paths is silly).
        url = '/v2/timeseries/'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 404)

        url = '/v2/timeseries/BaseRate/'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 404)

        url = '/v2/timeseries/BaseRate/snmp/'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 404)

        url = '/v2/timeseries/BaseRate/snmp/rtr_a/'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 404)

        # This does not end in a parsable frequency
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 404)

        # This type does not exist and is therefore 400 Bad Request.
        url = '/v2/timeseries/BadType/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/30000'
        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)


    def test_timeseries_data_detail(self):
        agg = 30000
        params = APIDataTestResults.get_agg_range(agg, in_ms=True)

        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/{0}'.format(agg)

        response = self.client.get(url, params)
        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(int(data['agg']), agg)
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][1]['ts'], params['begin']+agg)
        self.assertEquals(data['data'][1]['val'], 20)
        self.assertEquals(len(data['data']), 4)
        
        # make sure it works with a trailing slash too and check the 
        # padding/fill as well.
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/{0}/'.format(agg)

        params['begin'] -= agg*2

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(int(data['agg']), agg)
        self.assertEquals(data['resource_uri'], url.rstrip("/"))
        self.assertEquals(data['data'][2]['ts'], params['begin']+(agg*2))
        self.assertEquals(data['data'][2]['val'], 40)
        self.assertEquals(len(data['data']), 6)
        self.assertEquals(data['data'][-1]['val'], None)
        self.assertEquals(data['data'][-2]['val'], None)
        self.assertEquals(data['data'][-3]['val'], None)

        # Raw data as well.
        url = '/v2/timeseries/RawData/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/{0}'.format(agg)

        response = self.client.get(url, params)
        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'raw')
        self.assertEquals(int(data['agg']), agg)
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][1]['ts'], params['begin']+agg)
        self.assertEquals(data['data'][1]['val'], 20)

    def test_timeseries_data_aggs(self):

        agg = 3600000

        params = APIDataTestResults.get_agg_range(agg, in_ms=True)

        url = '/v2/timeseries/Aggs/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/{0}'.format(agg)

        response = self.client.get(url, params)
        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'average')
        self.assertEquals(data['agg'], str(agg))
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], params['begin']+agg*2)
        self.assertEquals(data['data'][2]['val'], 240)
        # check padding
        self.assertEquals(data['data'][3]['ts'] - data['data'][2]['ts'], agg)
        self.assertEquals(data['data'][3]['val'], None)

        params['cf'] = 'min'

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['cf'], 'min')
        self.assertEquals(data['agg'], str(agg))
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], params['begin']+agg*2)
        self.assertEquals(data['data'][2]['val'], 20)
        # check padding
        self.assertEquals(data['data'][3]['ts'] - data['data'][2]['ts'], agg)
        self.assertEquals(data['data'][3]['val'], None)

        params['cf'] = 'max'

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['cf'], 'max')
        self.assertEquals(data['agg'], str(agg))
        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(data['data'][2]['ts'], params['begin']+agg*2)
        self.assertEquals(data['data'][2]['val'], 300)

    def test_timeseries_bad_aggregations(self):
        url = '/v2/timeseries/Aggs/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/3600000'

        params = {'cf': 'bad'} # this cf does not exist

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

    def test_timeseries_timerange_limiter(self):
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/30000'
        params = { 
            'begin': int(time.time() - datetime.timedelta(days=31).total_seconds())
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

        url = '/v2/timeseries/Aggs/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/3600000'

        params = {
            'begin': int(time.time() - datetime.timedelta(days=366).total_seconds())
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

        params = {
            'begin': int(time.time() - datetime.timedelta(days=366*10).total_seconds())
        }

        url = '/v2/timeseries/Aggs/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/86400000'

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

        # This is an invalid aggregation/frequency
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/31000'

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 400)

    def test_timeseries_float_timestamp_input(self):
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/30000'

        # pass in floats
        params = { 
            'begin': time.time() - 30000*4,
            'end': time.time()
        }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEquals(data['begin_time'], int(params['begin']))
        self.assertEquals(data['end_time'], int(params['end']))

    def test_bad_timeseries_post_requests(self):
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/30000'

        # permission denied
        response = self.client.post(url)
        self.assertEquals(response.status_code, 401)

        # incorrect header
        response = self.get_api_client(admin_auth=True).post(url)
        self.assertEquals(response.status_code, 400)

        # correct header but payload not serialized as json
        response = self.get_api_client(admin_auth=True).post(url, data={},
                format='json')
        self.assertEquals(response.status_code, 400)

        # Below: correct header and json serialization, but incorrect
        # data structures and values being sent.

        # NOTE: CONTENT_TYPE and content_type kwargs do different 
        # things!  Former just sets the header in the test
        # client and the latter is passed to the underlying django
        # client and impacts serialization (and header).

        payload = { 'bunk': 'data is not a list' }

        response = self.get_api_client(admin_auth=True).post(url, data=payload,
                format='json')
        self.assertEquals(response.status_code, 400)

        payload = [
            ['this', 'should not be a list']
        ]

        response = self.get_api_client(admin_auth=True).post(url, data=payload,
                format='json')
        self.assertEquals(response.status_code, 400)

        payload = [
            {'this': 'has', 'the': 'wrong key names'}
        ]

        response = self.get_api_client(admin_auth=True).post(url, data=payload,
                format='json')
        self.assertEquals(response.status_code, 400)

        payload = [
            {'val': 'dict values', 'ts': 'should be numbers'}
        ]

        response = self.get_api_client(admin_auth=True).post(url, data=payload,
                format='json')
        self.assertEquals(response.status_code, 400)

    def test_timeseries_post_requests(self):
        url = '/v2/timeseries/BaseRate/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/30000'

        params = { 
            'ts': int(time.time()) * 1000, 
            'val': 1000 
        }

        # Params sent as json list and not post vars now.
        payload = [ params ]

        response = self.get_api_client(admin_auth=True).post(url, data=payload, format='json')
        self.assertEquals(response.status_code, 201) # not 200!

        url = '/v2/timeseries/RawData/snmp/rtr_a/FastPollHC/ifHCInOctets/fxp0.0/30000'

        response = self.get_api_client(admin_auth=True).post(url, data=payload, format='json')
        self.assertEquals(response.status_code, 201) # not 200!


class PDUAPITests(DeviceAPITestsBase):
    fixtures = ["oidsets.json"]

    def setUp(self):
        super(PDUAPITests, self).setUp()

        self.pdutd = build_pdu_metadata()

    def test_get_pdu_list(self):
        url = '/v2/pdu/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        # by default only currently active devices are returned
        data = json.loads(response.content)
        self.assertEquals(len(data), 1)
        self.assertEquals(data[0]["name"], "sentry_pdu")

    def test_get_pdu(self):
        url = '/v2/pdu/sentry_pdu/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        # by default only currently active devices are returned
        data = json.loads(response.content)
        # print json.dumps(data, indent=4)

        children = {}
        for child in data['children']:
            children[child['name']] = child
            for field in ['leaf','name','uri']:
                self.assertIn(field, child)

        for child_name in ['outlet']:
            self.assertIn(child_name, children)
            child = children[child_name]
            self.assertEqual(child['uri'], url + child_name + '/')

    def test_get_pdu_outlet_list(self):
        url = '/v2/pdu/sentry_pdu/outlet/'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        # by default only currently active devices are returned
        data = json.loads(response.content)
        # print json.dumps(data, indent=4)

        children = data['children']
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]['outletID'], 'AA')
        self.assertEqual(children[0]['outletName'], 'rtr_a:PEM1:50A')
        self.assertEqual(len(children[0]['children']), 1)
        self.assertEqual(children[0]['children'][0]['name'], 'load')

    def test_search_outlet_names(self):
        url = '/v2/outlet/?outletName__contains=rtr_a'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        # by default only currently active devices are returned
        data = json.loads(response.content)
        #print json.dumps(data, indent=4)

        children = data['children']
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]['outletID'], 'AA')
        self.assertEqual(children[0]['outletName'], 'rtr_a:PEM1:50A')

        url = '/v2/outlet/?outletName__contains=not_valid_query_string'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 200)

        # by default only currently active devices are returned
        data = json.loads(response.content)
        # print json.dumps(data, indent=4)

        children = data['children']
        self.assertEqual(len(children), 0)

class PDUAPIDataTests(DeviceAPITestsBase):
    def setUp(self):
        super(PDUAPIDataTests, self).setUp()
        # mock patches names where used/imported, not where defined
        # This form will patch a class when it is instantiated by the executed code:
        # self.patcher = mock.patch("esmond.api.api.CASSANDRA_DB", MockCASSANDRA_DB)
        # This form will patch a module-level class instance:
        self.patcher = mock.patch("esmond.api.api_v2.db", MockCASSANDRA_DB(None))
        self.patcher.start()
        self.td = build_pdu_metadata()

    def tearDown(self):
        self.patcher.stop()

    def test_get_load(self):
        url = '/v2/pdu/sentry_pdu/outlet/AA/load'

        params = { }

        response = self.client.get(url, params)
        self.assertEquals(response.status_code, 200)

        data = json.loads(response.content)

        # print json.dumps(data, indent=4)

        self.assertEquals(data['resource_uri'], url)
        self.assertEquals(len(data['data']), 60)

    def test_bogus_dataset(self):
        url = '/v2/pdu/sentry_pdu/outlet/AA/bogus_dataset'

        response = self.client.get(url)
        self.assertEquals(response.status_code, 400)

class QueryUtilTests(TestCase):
    def test_coerce_to_bins(self):
        data_in = [
            {
                "ts": 1391216201000,
                "val": 1100
            },
            {
                "ts": 1391216262000,
                "val": 1100
            },
            {
                "ts": 1391216323000,
                "val": 1100
            }
        ]

        data_out = [{ 'ts': 1391216160, 'val': 1100}, { 'ts': 1391216220, 'val': 1100}, { 'ts': 1391216280, 'val': 1100}]
        data_check = QueryUtil.format_cassandra_data_payload(data_in, coerce_to_bins=60000)
        self.assertEquals(data_check, data_out)

        data_out_nocoerce = [{ 'ts': 1391216201, 'val': 1100}, { 'ts': 1391216262, 'val': 1100}, { 'ts': 1391216323, 'val': 1100}]
        data_check = QueryUtil.format_cassandra_data_payload(data_in)
        self.assertEquals(data_check, data_out_nocoerce)

# coding=utf-8
from __future__ import absolute_import, print_function, unicode_literals
import os
import sys
import copy
import yaml
import json
import logging
import tarfile
from io import BytesIO

sys.modules['pkg_resources'] = None

import salt.utils.stringutils
import salt.utils.parsers
import salt.utils.verify
import salt.exceptions
import salt.defaults.exitcodes
import salt.cli.caller
import salt.cli.support
import salt.cli.support.console


log = logging.getLogger(__name__)


class SupportDataCollector(object):
    '''
    Data collector. It behaves just like another outputter,
    except it grabs the data to the archive files.
    '''
    def __init__(self, name, path=None, format='bz2'):
        '''
        constructor of the data collector
        :param name:
        :param path:
        :param format:
        '''
        if format not in ['bz2', 'gz']:
            format = 'bz2'
        _name = '{}.tar.{}'.format(name, format)
        _full_name = os.path.join(path or '/tmp', _name)
        if os.path.exists(_full_name):
            raise salt.exceptions.SaltClientError(
                'The archive {} already exists. Please remove it first!'.format(_full_name))
        self.archive_path = _full_name
        self.__format = format
        self.__arch = None
        self.__current_section = None
        self.__current_section_name = None

    def open(self):
        '''
        Opens archive.
        :return:
        '''
        if self.__arch is not None:
            raise salt.exceptions.SaltException('Archive already opened.')
        self.__arch = tarfile.TarFile.bz2open(self.archive_path, 'w')

    def close(self):
        '''
        Closes the archive.
        :return:
        '''
        if self.__arch is None:
            raise salt.exceptions.SaltException('Archive already closed')
        self._flush_content()
        self.__arch.close()
        self.__arch = None

    def _flush_content(self):
        '''
        Flush content to the archive
        :return:
        '''
        if self.__current_section is not None:
            buff = BytesIO()
            for action_return in self.__current_section:
                for title, ret_data in action_return.items():
                    buff.write(salt.utils.stringutils.to_bytes(title + '\n'))
                    buff.write(salt.utils.stringutils.to_bytes(('-' * len(title)) + '\n\n'))
                    buff.write(salt.utils.stringutils.to_bytes(ret_data))
                    buff.write(salt.utils.stringutils.to_bytes('\n\n\n'))
            buff.seek(0)
            tar_info = tarfile.TarInfo(name=self.__current_section_name)
            if not hasattr(buff, 'getbuffer'):  # Py2's BytesIO is older
                buff.getbuffer = buff.getvalue
            tar_info.size = len(buff.getbuffer())
            self.__arch.addfile(tarinfo=tar_info, fileobj=buff)

    def add(self, name):
        '''
        Start a new section.
        :param name:
        :return:
        '''
        if self.__current_section:
            self._flush_content()
        self.__current_section = []
        self.__current_section_name = name

    def write(self, title, data):
        '''
        Add a data to the current opened section.
        :return:
        '''
        self.__current_section.append(
            {
                title: yaml.safe_dump(json.loads(json.dumps(data)).get('return', {}),
                                      default_flow_style=False, indent=4)
            }
        )


class SaltSupport(salt.utils.parsers.SaltSupportOptionParser):
    '''
    Class to run Salt Support subsystem.
    '''
    def _get_caller(self, conf):
        if not getattr(self, '_caller', None):
            self._caller = salt.cli.caller.Caller.factory(conf)
        else:
            self._caller.opts = conf

        return self._caller

    def _local_call(self, call_conf):
        '''
        Execute local call
        '''
        conf = copy.deepcopy(self.config)

        conf['file_client'] = 'local'
        conf['fun'] = ''
        conf['arg'] = []
        conf['cache_jobs'] = False
        conf['print_metadata'] = False
        conf.update(call_conf)

        return self._get_caller(conf).call()

    def _get_action(self, action_meta):
        '''
        Parse action and turn into a calling point.
        :param action_meta:
        :return:
        '''
        conf = {
            'fun': action_meta.keys()[0],
            'arg': [],
        }
        action_meta = action_meta[conf['fun']]
        info = action_meta.get('info', 'Action for {}'.format(conf['fun']))
        for arg in action_meta.get('args') or []:
            if not isinstance(arg, dict):
                conf['arg'].append(arg)

        return info, conf

    def collect_master_data(self):
        '''
        Collects master system data.
        :return:
        '''
        scenario = salt.cli.support.get_scenario()
        for category_name in scenario:
            self.out.put(category_name)
            self.collector.add(category_name)
            for action in scenario[category_name]:
                info, conf = self._get_action(action)
                self.out.put('Collecting {}'.format(info.lower()), indent=2)
                self.collector.write(info, self._local_call(conf))

    def collect_targets_data(self):
        '''
        Collects minion targets data
        :return:
        '''

    def run(self):
        self.parse_args()
        if self.config['log_level'] not in ('quiet', ):
            self.setup_logfile_logger()
            salt.utils.verify.verify_log(self.config)

        self.out = salt.cli.support.console.MessagesOutput()
        try:
            self.collector = SupportDataCollector('master-info')
        except Exception as ex:
            self.out.error(ex)
            exit_code = salt.defaults.exitcodes.EX_GENERIC
        else:
            try:
                self.collector.open()
                self.collect_master_data()
                self.collect_targets_data()
                self.collector.close()

                archive_path = self.collector.archive_path
                self.out.highlight('\nSupport data has been written to "{}" file.\n', archive_path, _main='YELLOW')
                exit_code = salt.defaults.exitcodes.EX_OK
            except Exception as ex:
                self.out.error(ex)
                exit_code = salt.defaults.exitcodes.EX_SOFTWARE

        sys.exit(exit_code)

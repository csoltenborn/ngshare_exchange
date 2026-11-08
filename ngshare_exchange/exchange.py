#!/usr/bin/python
import os
import shutil
import glob
import requests
import fnmatch
from pathlib import Path
from urllib.parse import quote
from rapidfuzz import fuzz

from textwrap import dedent

from traitlets import Unicode, Bool, default
from jupyter_core.paths import jupyter_data_dir

from nbgrader.exchange.abc import Exchange as ABCExchange
from nbgrader.exchange import ExchangeError
from nbgrader.utils import ignore_patterns
import base64
import json


class Exchange(ABCExchange):

    username = (
        os.environ['JUPYTERHUB_USER']
        if 'JUPYTERHUB_USER' in os.environ
        else os.environ.get('USER', os.environ.get('USERNAME', ''))
    )

    _ngshare_url = Unicode(
        help=dedent(
            '''
            Override default ngshare URL
            '''
        ),
    ).tag(config=True)

    @property
    def ngshare_url(self):

        if self._ngshare_url:
            return self._ngshare_url
        if 'PROXY_PUBLIC_SERVICE_HOST' in os.environ:
            # we are in a kubernetes environment, so dns based service discovery should work
            # assuming the service is called ngshare, which it should
            return 'http://proxy-public/services/ngshare'
        else:
            raise ValueError(
                'ngshare url not configured in a non-k8s environment! Please configure the URL manually in nbgrader_config.py'
            )

    def _ngshare_api_check_error(self, response, url):
        if response.status_code != requests.codes.ok:
            self.log.error(
                'ngshare service returned invalid status code %d.',
                response.status_code,
            )

        try:
            response = response.json()
        except Exception:
            self.log.exception(
                'ngshare service returned non-JSON content: "%s".',
                response.text,
            )
            return None

        if not response['success']:
            if 'message' not in response:
                self.log.error(
                    'ngshare endpoint %s returned failure without an error message.',
                    url,
                )
            else:
                self.log.error(
                    'ngshare endpoint %s returned failure: %s',
                    url,
                    response['message'],
                )
            return None
        return response

    def ngshare_api_request(self, method, url, data=None, params=None):
        encoded_url = self.encode_url(url)
        try:
            headers = None
            if 'JUPYTERHUB_API_TOKEN' in os.environ:
                headers = {
                    'Authorization': 'token '
                    + os.environ['JUPYTERHUB_API_TOKEN']
                }
            response = requests.request(
                method,
                self.ngshare_url + encoded_url,
                headers=headers,
                data=data,
                params=params,
            )
        except Exception:
            self.log.exception(
                'An error occurred when querying the ngshare ' 'endpoint %s',
                url,
            )
            return None
        return self._ngshare_api_check_error(response, url)

    def encode_url(self, url):
        return quote(url, safe='/', encoding=None, errors=None)

    def ngshare_api_get(self, url, params=None):
        return self.ngshare_api_request('GET', url, params=params)

    def ngshare_api_post(self, url, data, params=None):
        return self.ngshare_api_request('POST', url, data=data, params=params)

    def ngshare_api_put(self, url, data=None, params=None):
        return self.ngshare_api_request('PUT', url, data=data, params=params)

    def ngshare_api_delete(self, url, params=None):
        return self.ngshare_api_request('DELETE', url, params=params)

    def get_counted_assignments(self, course_id):
        response = self.ngshare_api_get(
            '/counted_assignments/{}'.format(course_id)
        )
        if response is None:
            return None
        return response.get('counted_assignments', [])

    def set_assignment_counted(self, course_id, assignment_id, counted):
        response = self.ngshare_api_post(
            '/counted_assignment/{}/{}'.format(course_id, assignment_id),
            {'counted': 'true' if counted else 'false'},
        )
        return response is not None and bool(response.get('success'))

    def get_student_results(self, course_id, student_id=None):
        target_student = student_id if student_id else self.username
        return self.ngshare_api_get(
            '/results/{}/{}'.format(course_id, target_student)
        )

    def get_course_results(self, course_id):
        return self.ngshare_api_get(
            '/course_results/{}'.format(course_id)
        )

    def get_students(self, course_id):
        res = self.ngshare_api_get(f'/students/{course_id}')
        if res is None:
            return None
        return res.get('students', [])

    def add_or_update_student(
        self, course_id, student_id, first_name=None, last_name=None, email=None
    ):
        data = {
            'first_name': first_name or '',
            'last_name': last_name or '',
            'email': email or '',
        }
        res = self.ngshare_api_post(
            f'/student/{course_id}/{student_id}', data=data
        )
        if res is None:
            return {'success': False, 'message': 'Failed to update student in ngshare'}
        return res

    def remove_student(self, course_id, student_id):
        res = self.ngshare_api_delete(f'/student/{course_id}/{student_id}')
        if res is None:
            return {'success': False, 'message': 'Failed to remove student from ngshare'}
        return res

    assignment_dir = Unicode(
        '.',
        help=dedent(
            '''
            Local path for storing student assignments.  Defaults to '.'
            which is normally Jupyter's notebook_dir.
            '''
        ),
    ).tag(config=True)

    cache = Unicode(
        '',
        help='Local cache directory for nbgrader submit and nbgrader list. Defaults to $JUPYTER_DATA_DIR/nbgrader_cache',
    ).tag(config=True)

    @default('cache')
    def _cache_default(self):
        return os.path.join(jupyter_data_dir(), 'nbgrader_cache')

    path_includes_course = Bool(
        False,
        help=dedent(
            '''
            Whether the path for fetching/submitting  assignments should be
            prefixed with the course name. If this is `False`, then the path
            will be something like `./ps1`. If this is `True`, then the path
            will be something like `./course123/ps1`.
            '''
        ),
    ).tag(config=True)

    def decode_dir(self, src_dir, dest_dir, ignore=None, noclobber=False):
        """
        decode an encoded directory tree and saw the decoded files to des
        src_dir: en encoded directory tree
        dest: destination directory path for decoded files
        ignore: a function that returns true if the file should be ignored,
        false otherwise. This function takes as arguments the file directory path,
        file name, and file size in KB.
        """
        # check if the destination directory exists
        if not os.path.exists(dest_dir):
            Path(dest_dir).mkdir(parents=True)

        for src_file in src_dir:
            src_path = src_file['path']
            if any(part.startswith(".") for part in Path(src_path).parts):
                self.log.info(f"Skipping hidden file/dir: {src_path}")
                continue
            path_components = os.path.split(src_path)
            dir_name = path_components[0]
            file_name = path_components[1]

            dest_path = os.path.join(dest_dir, src_path)
            if noclobber and os.path.isfile(dest_path):
                continue
            # the file could be in a subdirectory, check if directory exists
            if not os.path.exists(dir_name) and dir_name != '':
                subdir = os.path.join(dest_dir, dir_name)
                if not os.path.exists(subdir):
                    os.makedirs(subdir, exist_ok=True)
                dest_path = os.path.join(subdir, file_name)

            self.log.info('Decoding: {}'.format(dest_path))
            decoded_content = base64.b64decode(src_file['content'])
            file_size = len(decoded_content)

            if ignore:
                if ignore(dest_path, file_name, file_size):
                    continue

            with open(dest_path, 'wb') as d:
                d.write(decoded_content)

    def encode_dir(self, src_dir, ignore=None):
        encoded_files = []
        for (subdir, dirs, files) in os.walk(src_dir):
            for file_name in files:
                file_path = subdir + os.sep + file_name
                data_bytes = open(file_path, 'rb').read()
                if ignore:
                    if ignore(subdir, file_name, len(data_bytes)):
                        continue

                # check if you have a subdir
                sub_dir = subdir.split(os.sep)[-1]
                if sub_dir != self.coursedir.assignment_id:
                    file_path = os.path.join(
                        os.path.relpath(subdir, src_dir), file_name
                    )
                else:
                    file_path = file_name

                self.log.info('Encoding: {}'.format(file_path))
                encoded = base64.b64encode(data_bytes)
                content = str(encoded, 'utf-8')
                file_map = {'path': file_path, 'content': content}
                encoded_files.append(file_map)

        dir_tree = {'user': self.username, 'files': json.dumps(encoded_files)}
        return dir_tree

    def init_src(self):
        '''Compute and check the source paths for the transfer.'''

        raise NotImplementedError

    def init_dest(self):
        '''Compute and check the destination paths for the transfer.'''

        raise NotImplementedError

    def copy_files(self):
        '''Actually do the file transfer.'''

        raise NotImplementedError

    def start(self):
        return super(Exchange, self).start()

    def _assignment_not_found(self, src_path, other_path):
        msg = "Assignment not found at: {}".format(src_path)
        self.log.fatal(msg)
        found = glob.glob(other_path)
        if found:
            scores = sorted([(fuzz.ratio(self.src_path, x), x) for x in found])
            self.log.error("Did you mean: %s", scores[-1][1])

        raise ExchangeError(msg)

    def do_copy(self, src, dest, log=None):
        """
        Copy the src dir to the dest dir, omitting excluded
        file/directories, non included files, and too large files, as
        specified by the options coursedir.ignore, coursedir.include
        and coursedir.max_file_size.
        """
        shutil.copytree(
            src,
            dest,
            ignore=ignore_patterns(
                exclude=self.coursedir.ignore,
                include=self.coursedir.include,
                max_file_size=self.coursedir.max_file_size,
                log=self.log,
            ),
        )

    def ignore_patterns(self):
        """
        Returns a function which decides whether or not a file should be
        ignored. The function has the signature
            ignore_patterns(directory, filename, filesize) -> bool
        The directory and filename are the same parameters as described in
        shutil.ignore_patterns. filesize is the size of the file in kilobytes.
        All filenames matching patterns in self.coursedir.ignore, if it exists,
        will be ignored. If self.coursedir.include exists, filenames not
        matching the patterns will be ignored. If self.coursedir.max_file_size
        exists, files exceeding that size in kilobytes will be ignored.
        """
        exclude = self.coursedir.ignore
        include = self.coursedir.include
        max_file_size = self.coursedir.max_file_size
        log = self.log

        def ignore_patterns(directory, filename, filesize):
            fullname = os.path.join(directory, filename)
            if exclude and any(
                fnmatch.fnmatch(filename, glob) for glob in exclude
            ):
                if log:
                    log.debug(
                        'Ignoring excluded file "{}" (see config option '
                        'CourseDirectory.ignore)'.format(fullname)
                    )
                return True
            elif include and not any(
                fnmatch.fnmatch(filename, glob) for glob in include
            ):
                if log:
                    log.debug(
                        'Ignoring non included file "{}" (see config '
                        'option CourseDirectory.include)'.format(fullname)
                    )
                return True
            elif max_file_size and filesize > 1000 * max_file_size:
                if log:
                    log.warning(
                        'Ignoring file too large "{}" (see config '
                        'option CourseDirectory.max_file_size)'.format(fullname)
                    )
                return True
            return False

        return ignore_patterns

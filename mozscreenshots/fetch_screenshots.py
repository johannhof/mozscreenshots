# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import logging
import os
import pprint
import requests
import sys

from datetime import date
from mozscreenshots import __version__


DEFAULT_REQUEST_HEADERS = {
    'Accept': 'application/json',
    'User-Agent': 'mozscreenshots/%s' % __version__,
}
TH_API = 'https://treeherder.mozilla.org/api'

log = logging.getLogger('fetch_screenshots')
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
log.addHandler(handler)

def resultset_response_for_id(project, resultset_id):
    print 'Fetching resultset for id: %d' % resultset_id
    resultset_url = '%s/project/%s/resultset/%s/' % (TH_API, project, resultset_id)
    log.info(resultset_url)
    try:
        json = fetch_json(resultset_url)
    except requests.exceptions.HTTPError:
        log.error('Invalid resultset for id: %d' % resultset_id)
        return None

    if not json['id']:
        log.error('No resultset for id: %s' % resultset_id)
        return None
    log.debug('resultset_response_for_id: %s' % pprint.pformat(json, depth=1))
    return json

def resultset_response_for_push(project, rev):
    print 'Fetching resultset for revision: %s' % rev
    resultset_url = '%s/project/%s/resultset/?count=2&full=true&revision=%s' % (TH_API, project, rev)
    log.info(resultset_url)
    response = fetch_json(resultset_url)

    if len(response['results']) == 0:
        log.error('No resultset for revision: %s' % rev)
        return None
    elif len(response['results']) > 1:
        log.error('Multiple resultsets for revision: %s' % rev)
        return None
    log.debug('resultset_for_push: %s' % pprint.pformat(response['results'][0]))
    return response

def jobs_for_resultset(project, resultset_id, job_type_name):
    print 'Fetching jobs for resultset: %d' % resultset_id

    jobs_url = '%s/project/%s/jobs/?count=2000&result_set_id=%d&job_type_name=%s&exclusion_profile=false' % (TH_API, project, resultset_id, job_type_name)
    log.info(jobs_url)
    jobs = fetch_json(jobs_url)
    if len(jobs['results']) == 0:
        log.error('No jobs found for resultset: %d' % resultset_id)
        return None
    log.debug('jobs_for_resultset: %s' % pprint.pformat(jobs))
    return jobs['results']

def download_image_artifacts_for_job(project, job, dir_path):
    print 'Fetching artifact list for job: %d' % job['id']
    artifacts_url = '%s/project/%s/artifact/?job_id=%d&name=Job+Info&type=json' % (TH_API, project, job['id'])
    log.info(artifacts_url)
    artifacts = fetch_json(artifacts_url)

    job_dir = os.path.join(dir_path, '%s-%s' % (job['platform'], job['id']))
    try:
        os.makedirs(job_dir)
    except OSError:
        if not os.path.isdir(job_dir):
            log.error('Error creating directory: %s' % job_dir)
            return

    for artifact in artifacts:
        if 'blob' not in artifact:
            log.debug('No blob in artifact: %d' % artifact['id'])
            continue

        blob = artifact['blob']

        if 'job_details' not in blob:
            log.debug('No job_details in artifact blob: %d' % artifact['id'])
            continue

        job_details = blob['job_details']

        for detail in job_details:
            log.debug('artifact blob job detail: %s' % pprint.pformat(detail))
            if not detail['value'].endswith('.png'):
                continue
            download_artifact(detail['url'], os.path.join(job_dir, detail['value']))

    # Remove any empty directories that we created
    try:
        os.rmdir(job_dir)
    except OSError:
        return job_dir
        pass


def download_artifact(url, filepath):
    print 'Downloading %s' % filepath,
    if os.path.isfile(filepath):
        print '- Not overwriting existing file'
        return
    image = requests.get(url)
    if image.status_code == 200:
        print
    else:
        print '- FAILED'
        log.error('%s: %s' % (filepath, image.content))
        return
    file = open(filepath, 'wb')
    file.write(image.content)
    file.close()

def nightly_jobs_for_date(project, date):
    job_type_name = 'Nightly'
    jobs_url = '%s/project/%s/jobs/?count=100&last_modified__gte=%sT00:00:00.000&last_modified__lte=%sT23:59:59.999&job_type_name=%s&exclusion_profile=false' % (TH_API, project, date, date, job_type_name)
    log.debug(jobs_url)
    jobs = fetch_json(jobs_url)

    found_result_set_ids = set()
    for job in jobs['results']:
        if job['result_set_id'] not in found_result_set_ids:
            found_result_set_ids.add(job['result_set_id']);
            log.debug('Found Nightly: %s with resultset id: %d' % (job['ref_data_name'], job['result_set_id']))

    return found_result_set_ids

def fetch_json(url):
    response = requests.get(url, headers=DEFAULT_REQUEST_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()

def run(args):
    resultsets = []
    if args.rev:
        resultset_response = resultset_response_for_push(args.project, args.rev)
        if not resultset_response:
            sys.exit(1)
        resultsets.append(resultset_response['results'][0])
    else:
        resultset_ids = nightly_jobs_for_date(args.project, args.nightly)
        for resultset_id in resultset_ids:
            resultset = resultset_response_for_id(args.project, resultset_id)
            if not resultset:
                continue
            resultsets.append(resultset)

    for resultset in resultsets:
        run_for_resultset(args, resultset)

def run_for_resultset(args, resultset):
    jobs = jobs_for_resultset(args.project, resultset['id'], args.job_type_name)
    if not jobs:
        sys.exit(1)

    rev_dir = os.path.join(args.project, resultset['revision'])
    try:
        os.makedirs(rev_dir)
    except OSError:
        if not os.path.isdir(rev_dir):
            log.error('Error creating directory: %s' % rev_dir)
            sys.exit(1)

    job_dirs = []
    for job in jobs:
        job_dir = download_image_artifacts_for_job(args.project, job, rev_dir)
        if not job_dir:
            continue
        job_dirs.append(job_dir)

    return job_dirs

def cli():
    parser = argparse.ArgumentParser(description='Fetch screenshots from automation')

    required = parser.add_mutually_exclusive_group(required=True)
    required.add_argument('-n', '--nightly', metavar='YYYY-MM-DD',
                          help='Date to fetch nightly screenshots from')
    required.add_argument('-r', '--rev',
                          help='Revision to fetch screenshots from')


    parser.add_argument('--job-type-name', default='Mochitest Browser Screenshots',
                        help='Type of job to fetch from (aka. job_type_name) [Default="Mochitest Browser Screenshots"]')
    parser.add_argument('--log-level', default='WARNING')

    parser.add_argument('--project',
                        help='Project that the revision is from. [Default="mozilla-central" for --nightly, "try" otherwise]')

    args = parser.parse_args()
    if not args.project:
        args.project = "mozilla-central" if args.nightly else "try"
    log.setLevel(getattr(logging, args.log_level))

    run(args)

if __name__ == '__main__':
    cli()

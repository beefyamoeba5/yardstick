##############################################################################
# Copyright (c) 2016 Huawei Technologies Co.,Ltd.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
# http://www.apache.org/licenses/LICENSE-2.0
##############################################################################
from __future__ import absolute_import

import logging
import os
import time

from oslo_serialization import jsonutils
import requests

from yardstick.benchmark.scenarios import base


LOG = logging.getLogger(__name__)


class StorPerf(base.Scenario):
    """Execute StorPerf benchmark.
    Once the StorPerf container has been started and the ReST API exposed,
    you can interact directly with it using the ReST API. StorPerf comes with a
    Swagger interface that is accessible through the exposed port at:
    http://StorPerf:5000/swagger/index.html

  Command line options:
    target = [device or path] (Optional):
    The path to either an attached storage device (/dev/vdb, etc) or a
    directory path (/opt/storperf) that will be used to execute the performance
    test. In the case of a device, the entire device will be used.
    If not specified, the current directory will be used.

    workload = [workload module] (Optional):
    If not specified, the default is to run all workloads.
    The workload types are:
        rs: 100% Read, sequential data
        ws: 100% Write, sequential data
        rr: 100% Read, random access
        wr: 100% Write, random access
        rw: 70% Read / 30% write, random access

    report = [job_id] (Optional):
    Query the status of the supplied job_id and report on metrics.
    If a workload is supplied, will report on only that subset.

    """
    __scenario_type__ = "StorPerf"

    def __init__(self, scenario_cfg, context_cfg):
        """Scenario construction."""
        super(StorPerf, self).__init__()
        self.scenario_cfg = scenario_cfg
        self.context_cfg = context_cfg
        self.options = self.scenario_cfg["options"]

        self.target = self.options.get("StorPerf_ip", None)
        self.query_interval = self.options.get("query_interval", 10)
        # Maximum allowed job time
        self.timeout = self.options.get('timeout', 3600)

        self.setup_done = False
        self.job_completed = False

    def _query_setup_state(self):
        """Query the stack status."""
        LOG.info("Querying the stack state...")
        setup_query = requests.get('http://%s:5000/api/v1.0/configurations'
                                   % self.target)

        setup_query_content = jsonutils.loads(
            setup_query.content)
        if ("stack_created" in setup_query_content and
                setup_query_content["stack_created"]):
            LOG.debug("stack_created: %s",
                      setup_query_content["stack_created"])
            return True

        return False

    def setup(self):
        """Set the configuration."""
        env_args = {}
        env_args_payload_list = ["agent_count", "agent_flavor",
                                 "public_network", "agent_image",
                                 "volume_size", "volume_type",
                                 "volume_count", "availability_zone",
                                 "stack_name", "subnet_CIDR"]

        for env_argument in env_args_payload_list:
            try:
                env_args[env_argument] = self.options[env_argument]
            except KeyError:
                pass

        LOG.info("Creating a stack on node %s with parameters %s",
                 self.target, env_args)
        setup_res = requests.post('http://%s:5000/api/v1.0/configurations'
                                  % self.target, json=env_args)


        if setup_res.status_code != 200:
            LOG.error("Failed to create stack. %s: %s",
                      setup_res.status_code, setup_res.content)
            raise RuntimeError("Failed to create stack. %s: %s" %
                               (setup_res.status_code, setup_res.content))
        elif setup_res.status_code == 200:
            setup_res_content = jsonutils.loads(setup_res.content)
            LOG.info("stack_id: %s", setup_res_content["stack_id"])

        while not self._query_setup_state():
            time.sleep(self.query_interval)

        # We do not want to load the results of the disk initialization,
        # so it is not added to the results here.
        self.initialize_disks()
        self.setup_done = True

    def _query_job_state(self, job_id):
        """Query the status of the supplied job_id and report on metrics"""
        LOG.info("Fetching report for %s...", job_id)
        report_res = requests.get('http://%s:5000/api/v1.0/jobs' % self.target,
                                  params={'id': job_id, 'type': 'status'})

        report_res_content = jsonutils.loads(
            report_res.content)

        if report_res.status_code != 200:
            LOG.error("Failed to fetch report, error message: %s",
                      report_res_content["message"])
            raise RuntimeError("Failed to fetch report, error message:",
                               report_res_content["message"])
        else:
            job_status = report_res_content["Status"]

        LOG.debug("Job is: %s...", job_status)
        self.job_completed = job_status == "Completed"

        # TODO: Support using StorPerf ReST API to read Job ETA.

        # if job_status == "completed":
        #     self.job_completed = True
        #     ETA = 0
        # elif job_status == "running":
        #     ETA = report_res_content['time']
        #
        # return ETA

    def run(self, result):
        """Execute StorPerf benchmark"""
        if not self.setup_done:
            self.setup()

        metadata = {"build_tag": "latest",
                    "test_case": "opnfv_yardstick_tc074"}
        metadata_payload_dict = {"pod_name": "NODE_NAME",
                                 "scenario_name": "DEPLOY_SCENARIO",
                                 "version": "YARDSTICK_BRANCH"}

        for key, value in metadata_payload_dict.items():
            try:
                metadata[key] = os.environ[value]
            except KeyError:
                pass

        job_args = {"metadata": metadata}
        job_args_payload_list = ["block_sizes", "queue_depths", "deadline",
                                 "target", "workload", "workloads",
                                 "agent_count", "steady_state_samples"]
        job_args["deadline"] = self.options["timeout"]

        for job_argument in job_args_payload_list:
            try:
                job_args[job_argument] = self.options[job_argument]
            except KeyError:
                pass

        api_version = "v1.0"

        if ("workloads" in job_args and
                job_args["workloads"] is not None and
                len(job_args["workloads"])) > 0:
            api_version = "v2.0"

        LOG.info("Starting a job with parameters %s", job_args)
        job_res = requests.post('http://%s:5000/api/%s/jobs' % (self.target,
                                                                api_version), json=job_args)

        if job_res.status_code != 200:
            LOG.error("Failed to start job. %s: %s",
                               job_res.status_code, job_res.content)
            raise RuntimeError("Failed to start job. %s: %s" %
                               (job_res.status_code, job_res.content))
        elif job_res.status_code == 200:
            job_res_content = jsonutils.loads(job_res.content)
            job_id = job_res_content["job_id"]
            LOG.info("Started job id: %s...", job_id)

            while not self.job_completed:
                self._query_job_state(job_id)
                time.sleep(self.query_interval)

        # TODO: Support using ETA to polls for completion.
        #       Read ETA, next poll in 1/2 ETA time slot.
        #       If ETA is greater than the maximum allowed job time,
        #       then terminate job immediately.

        #   while not self.job_completed:
        #       esti_time = self._query_state(job_id)
        #       if esti_time > self.timeout:
        #           terminate_res = requests.delete('http://%s:5000/api/v1.0
        #                                           /jobs' % self.target)
        #       else:
        #           time.sleep(int(esti_time)/2)

            result_res = requests.get('http://%s:5000/api/v1.0/jobs?type='
                                      'metadata&id=%s' % (self.target, job_id))
            result_res_content = jsonutils.loads(result_res.content)
            if 'report' in result_res_content and \
                    'steady_state' in result_res_content['report']['details']:
                res = result_res_content['report']['details']['steady_state']
                steady_state = res.values()[0]
                LOG.info("Job %s completed with steady state %s",
                         job_id, steady_state)

            result_res = requests.get('http://%s:5000/api/v1.0/jobs?id=%s' %
                                      (self.target, job_id))
            result_res_content = jsonutils.loads(
                result_res.content)
            result.update(result_res_content)

    def initialize_disks(self):
        """Fills the target with random data prior to executing workloads"""

        job_args = {}
        job_args_payload_list = ["target"]

        for job_argument in job_args_payload_list:
            try:
                job_args[job_argument] = self.options[job_argument]
            except KeyError:
                pass

        LOG.info("Starting initialization with parameters %s", job_args)
        job_res = requests.post('http://%s:5000/api/v1.0/initializations' %
                                self.target, json=job_args)


        if job_res.status_code != 200:
            LOG.error("Failed to start initialization job, error message: %s: %s",
                      job_res.status_code, job_res.content)
            raise RuntimeError("Failed to start initialization job, error message: %s: %s" %
                               (job_res.status_code, job_res.content))
        elif job_res.status_code == 200:
            job_res_content = jsonutils.loads(job_res.content)
            job_id = job_res_content["job_id"]
            LOG.info("Started initialization as job id: %s...", job_id)

        while not self.job_completed:
            self._query_job_state(job_id)
            time.sleep(self.query_interval)

        self.job_completed = False

    def teardown(self):
        """Deletes the agent configuration and the stack"""
        teardown_res = requests.delete(
            'http://%s:5000/api/v1.0/configurations' % self.target)

        if teardown_res.status_code == 400:
            teardown_res_content = jsonutils.loads(
                teardown_res.json_data)
            LOG.error("Failed to reset environment, error message: %s",
                      teardown_res_content['message'])
            raise RuntimeError("Failed to reset environment, error message:",
                               teardown_res_content['message'])

        self.setup_done = False

#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This script will generate a user certificate using flocker-ca and upload it
# ready for the plugin to consume
# It will then install a build of docker that supports --volume-driver
# It will then pip to install the plugin to run with the certs and set up
# startup scripts according to the platform

import sys
import os
from twisted.internet.task import react
from twisted.internet.defer import gatherResults, inlineCallbacks

# Usage: plugin.py cluster.yml
from utils import Configurator, log

# a dict that holds the default values for each of the env vars
# that can be overriden
settings_defaults = {
    # allow env override for where to download the experimental
    # docker binary from
    'DOCKER_BINARY_URL': 'https://get.docker.com/builds/Linux/x86_64/docker-latest',
    # the name of the docker service running on the host
    'DOCKER_SERVICE_NAME': 'docker-engine',
    # what repo does the flocker plugin live in
    'PLUGIN_REPO': 'https://github.com/clusterhq/flocker-docker-plugin',
    # what branch to use for the flocker plugin
    'PLUGIN_BRANCH': 'master',
    # skip downloading the docker binary
    # for scenarios where vm images have been pre-baked
    'SKIP_DOCKER_BINARY': '',
    # skip installing the flocker plugin
    'SKIP_INSTALL_PLUGIN': ''
}

# dict that holds our actual env vars once the overrides have been applied
settings = {}

# loop over each of the default vars and check to see if we have been
# given an override in the environment
for field in settings_defaults:
    value = os.environ.get(field)
    if value is None:
        value = settings_defaults[field]
    settings[field] = value

@inlineCallbacks
def main(reactor, configFile):
    c = Configurator(configFile=configFile)
    control_ip = c.config["control_node"]

    # download and replace the docker binary on each of the nodes
    for node in c.config["agent_nodes"]:

        if c.config["os"] != "coreos":
            log("Skipping installing new docker binary because we're on",
                "ubuntu/centos, assuming we installed a sufficiently recent one",
                "already.")
            break

        # only install new docker binary on coreos. XXX TODO coreos > 801.0.0
        # doesn't need newer docker.
        if settings["SKIP_DOCKER_BINARY"] or c.config["os"] != "coreos":
            break

        public_ip = node["public"]
        log("Replacing docker binary on %s" % (public_ip,))

        # stop the docker service
        log("Stopping the docker service on %s" % (public_ip,))

        if c.config["os"] == "ubuntu":
            c.runSSHRaw(public_ip, "stop %s || true"
                % (settings['DOCKER_SERVICE_NAME'],))
        elif c.config["os"] == "centos":
            c.runSSHRaw(public_ip, "systemctl stop %s.service || true"
                % (settings['DOCKER_SERVICE_NAME'],))
        elif c.config["os"] == "coreos":
            c.runSSHRaw(public_ip, "systemctl stop docker.service || true")

        # download the latest docker binary
        if c.config["os"] == "coreos":
            log("Downloading the latest docker binary on %s - %s" \
                % (public_ip, settings['DOCKER_BINARY_URL'],))
            c.runSSHRaw(public_ip, "mkdir -p /root/bin")
            c.runSSHRaw(public_ip, "wget -qO /root/bin/docker %s"
                % (settings['DOCKER_BINARY_URL'],))
            c.runSSHRaw(public_ip, "chmod +x /root/bin/docker")
            c.runSSHRaw(public_ip,
                    "cp /usr/lib/coreos/dockerd /root/bin/dockerd")
            c.runSSHRaw(public_ip,
                    "cp /usr/lib/systemd/system/docker.service /etc/systemd/system/")
            c.runSSHRaw(public_ip,
                    "sed -i s@/usr/lib/coreos@/root/bin@g /etc/systemd/system/docker.service")
            c.runSSHRaw(public_ip,
                    "sed -i \\'s@exec docker@exec /root/bin/docker@g\\' /root/bin/dockerd")
            c.runSSHRaw(public_ip, "systemctl daemon-reload")
        else:
            log("Downloading the latest docker binary on %s - %s" \
                % (public_ip, settings['DOCKER_BINARY_URL'],))
            c.runSSHRaw(public_ip, "wget -O /usr/bin/docker %s"
                % (settings['DOCKER_BINARY_URL'],))

        # start the docker service
        log("Starting the docker service on %s" % (public_ip,))
        if c.config["os"] == "ubuntu":
            c.runSSHRaw(public_ip, "start %s"
                % (settings['DOCKER_SERVICE_NAME'],))
        elif c.config["os"] == "centos":
            c.runSSHRaw(public_ip, "systemctl start %s.service"
              % (settings['DOCKER_SERVICE_NAME'],))
        elif c.config["os"] == "coreos":
            c.runSSHRaw(public_ip, "systemctl start docker.service")

    log("Generating plugin certs")
    # generate and upload plugin.crt and plugin.key for each node
    for node in c.config["agent_nodes"]:
        public_ip = node["public"]
        # use the node IP to name the local files
        # so they do not overwrite each other
        c.run("flocker-ca create-api-certificate %s-plugin" % (public_ip,))
        log("Generated plugin certs for", public_ip)

    def report_completion(result, public_ip, message="Completed plugin install for"):
        log(message, public_ip)
        return result

    deferreds = []
    log("Uploading plugin certs...")
    for node in c.config["agent_nodes"]:
        public_ip = node["public"]
        # upload the .crt and .key
        for ext in ("crt", "key"):
            d = c.scp("%s-plugin.%s" % (public_ip, ext,),
                public_ip, "/etc/flocker/plugin.%s" % (ext,), async=True)
            d.addCallback(report_completion, public_ip=public_ip, message=" * Uploaded plugin cert for")
            deferreds.append(d)
    yield gatherResults(deferreds)
    log("Uploaded plugin certs")

    log("Installing flocker plugin")
    # loop each agent and get the plugin installed/running
    # clone the plugin and configure an upstart/systemd unit for it to run

    deferreds = []
    for node in c.config["agent_nodes"]:
        public_ip = node["public"]
        private_ip = node["private"]
        log("Using %s => %s" % (public_ip, private_ip))

        # the full api path to the control service
        controlservice = 'https://%s:4523/v1' % (control_ip,)

        # perhaps the user has pre-compiled images with the plugin
        # downloaded and installed
        if not settings["SKIP_INSTALL_PLUGIN"]:
            if c.config["os"] == "ubuntu":
                log("Installing plugin for", public_ip, "...")
                d = c.runSSHAsync(public_ip,
                        "apt-get install -y --force-yes clusterhq-flocker-docker-plugin && "
                        "service flocker-docker-plugin restart")
                d.addCallback(report_completion, public_ip=public_ip)
                deferreds.append(d)
            elif c.config["os"] == "centos":
                log("Installing plugin for", public_ip, "...")
                d = c.runSSHAsync(public_ip,
                        "yum install -y clusterhq-flocker-docker-plugin && "
                        "systemctl enable flocker-docker-plugin && "
                        "systemctl start flocker-docker-plugin")
                d.addCallback(report_completion, public_ip=public_ip)
                deferreds.append(d)
        else:
            log("Skipping installing plugin: %r" % (settings["SKIP_INSTALL_PLUGIN"],))
    yield gatherResults(deferreds)

    for node in c.config["agent_nodes"]:
        public_ip = node["public"]
        private_ip = node["private"]
        # ensure that the /run/docker/plugins
        # folder exists
        log("Creating the /run/docker/plugins folder")
        c.runSSHRaw(public_ip, "mkdir -p /run/docker/plugins")
        if c.config["os"] == "coreos":
            log("Starting flocker-docker-plugin as docker container on CoreOS on %s" % (public_ip,))
            c.runSSH(public_ip, """echo
/root/bin/docker run --restart=always -d --net=host --privileged \\
-e FLOCKER_CONTROL_SERVICE_BASE_URL=%s \\
-e MY_NETWORK_IDENTITY=%s \\
-v /etc/flocker:/etc/flocker \\
-v /run/docker:/run/docker \\
--name=flocker-docker-plugin \\
clusterhq/flocker-docker-plugin""" % (controlservice, private_ip,))

    log("Done!")

def _main():
    react(main, sys.argv[1:])

if __name__ == "__main__":
    _main()

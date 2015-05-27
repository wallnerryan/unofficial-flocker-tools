#!/usr/bin/env python

# This script will generate some certificates using flocker-ca and upload them
# to the servers specified in a cluster.yml

import sys
import yaml

# Usage: deploy.py cluster.yml
from utils import Configurator

if __name__ == "__main__":
    c = Configurator(configFile=sys.argv[1])
    c.run("flocker-ca initialize %s" % (c.config["cluster_name"],))
    print "Initialized cluster CA."
    c.run("flocker-ca create-control-certificate %s" % (c.config["control_node"],))
    print "Created control cert."
    node_mapping = {}
    for node in c.config["agent_nodes"]:
        # Created 8eab4b8d-c0a2-4ce2-80aa-0709277a9a7a.crt. Copy ...
        uuid = c.run("flocker-ca create-node-certificate").split(".")[0].split(" ")[1]
        node_mapping[node] = uuid
        print "Generated", uuid, "for", node
    for user in c.config["users"]:
        c.run("flocker-ca create-api-certificate %s" % (user,))
        print "Created user key for", user
    print "Uploading keys to respective nodes:"

    # Copy cluster cert, and control cert and key to control node.
    c.scp("cluster.crt", c.config["control_node"], "/etc/flocker/cluster.crt")
    print " * Uploaded cluster cert to control node."
    for ext in ("crt", "key"):
        c.scp("control-%s.%s" % (c.config["control_node"], ext),
                c.config["control_node"], "/etc/flocker/control-service.%s" % (ext,))
    print " * Uploaded control cert & key to control node."

    # Dump agent_config into a file and scp it to /etc/flocker/agent.yml on the
    # nodes.
    f = open("agent.yml", "w")
    agent_config = yaml.dump(c.config["agent_config"], f)
    f.close()

    # Copy cluster cert, and agent cert and key to agent nodes.
    for node, uuid in node_mapping.iteritems():
        c.scp("cluster.crt", node, "/etc/flocker/cluster.crt")
        c.scp("agent.yml", node, "/etc/flocker/agent.yml")
        print " * Uploaded cluster cert to %s." % (node,)
        for ext in ("crt", "key"):
            c.scp("%s.crt" % (uuid,), node, "/etc/flocker/node.%s" % (ext,))
        print " * Uploaded node cert and key to %s." % (node,)

    if c.config["os"] == "ubuntu":
        c.runSSH(c.config["control_node"], """
cat <<EOF > /etc/init/flocker-control.override
start on runlevel [2345]
stop on runlevel [016]
EOF
echo 'flocker-control-api       4523/tcp                        # Flocker Control API port' >> /etc/services
echo 'flocker-control-agent     4524/tcp                        # Flocker Control Agent port' >> /etc/services
service flocker-control start
ufw allow flocker-control-api
ufw allow flocker-control-agent
""")
        print "Configured and started control service, opened firewall."

    """
    for node, uuid in node_mapping.iteritems():
        if c.config["os"] == "ubuntu":
            c.runSSH(node, "start ...")
        elif c.config["os"] == "centos":
            c.runSSH(node, "systemctl ...")
    """

    print "\nYou should now be able to communicate with the control service:\n"
    if c.config["users"]:
        print "curl --cacert $PWD/cluster.crt --cert $PWD/%(user)s.crt --key $PWD/%(user)s.key \\" % dict(
                user=c.config["users"][0])
        print "https://%s:4523/v1/state/nodes | jq ." % (c.config["control_node"],)
#!/usr/bin/env python

import threading
# import eventlet 
# eventlet.monkey_patch()


from flask import Flask, request 
from flask_socketio import SocketIO
import os 

from Device import Device



app = Flask(__name__)
sockethost = SocketIO(app, async_mode="threading")



"""
This class represents a device that can scan for files, send them to a server, and interact with the server.

Attributes:
    m_config (dict): The configuration of the device, loaded from a YAML file.
"""
if __name__ == "__main__":
    import sys 
    print("% " + " ".join(sys.argv))
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", type=str, required=False, default="../config/config.yaml", help="Config file for this instance")
    parser.add_argument("-s", "--salt", type=str, required=False)
    args = parser.parse_args()

    device = Device(args.config, sockethost, args.salt)

    sockethost.start_background_task(device.run)
    app.route("/")(device.index)
    app.route("/get_config", methods=["GET"])(device.get_config)
    app.route("/save_config", methods=["POST"])(device.save_config)
    app.route("/debug", methods=["GET"])(device.debug_socket)
    app.route("/refresh", methods=["GET"])(device.on_refresh)
    app.route("/restartConnections", methods=["GET"])(device.on_restart_connections)

    sockethost.on("connect")(device.on_local_dashboard_connect)
    sockethost.on("disconnect")(device.on_local_dashboard_disconnect)

    port = os.environ.get("CONFIG_PORT", "8811")
    if len(port) == 0:
        port = 8811
    else:
        port = int(port)

    sockethost.run(app=app, host="0.0.0.0", port=port)
    # device.run()
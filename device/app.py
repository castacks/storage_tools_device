#!/usr/bin/env python

import threading
from flask import Flask, request 
from flask_socketio import SocketIO

from Device import Device


app = Flask(__name__)
sockethost = SocketIO(app, async_mode="eventlet")



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
    args = parser.parse_args()

    device = Device(args.config, sockethost)

    # Start the Device's run method in a separate thread
    device_thread = threading.Thread(target=device.run)
    device_thread.start()

    app.route("/")(device.index)
    app.route("/get_config", methods=["GET"])(device.get_config)
    app.route("/save_config", methods=["POST"])(device.save_config)

    sockethost.on("connect")(device.on_local_dashboard_connect)
    sockethost.on("disconnect")(device.on_local_dashboard_disconnect)


    sockethost.run(app=app, host="0.0.0.0", port=8811)
    # device.run()
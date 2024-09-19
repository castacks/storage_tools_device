import asyncio
from SocketIOTQDM import SocketIOTQDM, MultiTargetSocketIOTQDM
from debug_print import debug_print
from flask import request 


import pytz
import requests
import socketio
import socketio.exceptions
import yaml
from flask import jsonify, send_from_directory
from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
from flask_socketio import SocketIO
from eventlet import tpool
import eventlet 
from eventlet.lock import Semaphore as Lock

import json
import os
import queue
import shutil
import socket
import sys
from datetime import datetime
from threading import Thread
from typing import cast

from utils import get_source_by_mac_address, getDateFromFilename, getMetaData
from utils import compute_md5




class Device:
    def __init__(self, filename: str, local_dashboard_sio:SocketIO, salt=None) -> None:
        """
        Initialize the Device object with a configuration file.

        Args:
            filename (str): The path to the configuration file.
        """
        ## the device dashboard socket.  for showing connection status
        ## and echo console messages.  
        self.m_local_dashboard_sio = local_dashboard_sio

        self.m_config_filename = filename 

        self.m_config = None
        with open(filename, "r") as f:
            self.m_config = yaml.safe_load(f)
            debug_print(json.dumps(self.m_config, indent=True))

        robot_name = self.m_config.get("robot_name", "robot")

        self.m_config["source"] = get_source_by_mac_address(robot_name)

        if salt:
            self.m_config["source"] += str(salt)

        debug_print(f"Setting source name to {self.m_config['source']}")

        self.m_config["servers"] = self.m_config.get("servers", [])

        self.m_server = None

        self.m_signal = {}
        self.m_fs_info = {}

        self.m_files = None

        self.m_md5 = {}
        self.m_updates = {}
        self.m_computeMD5 = self.m_config.get("computeMD5", True)
        self.m_chunk_size = self.m_config.get("chunk_size", 8192)

        self.m_local_tz = self.m_config.get("local_tz", "America/New_York")

        # test to make sure time zone is set correctly. 
        try:
            pytz.timezone(self.m_local_tz)
        except pytz.UnknownTimeZoneError:
            debug_print(f"Invalid config option 'local_tz'. The string '{self.m_local_tz}' is not a valid time zone ")
            sys.exit(1)

        services = ['_http._tcp.local.']
        self.m_zeroconfig = Zeroconf()
        self.m_zero_conf_name = "Airlab_storage._http._tcp.local."
        self.browser = ServiceBrowser(self.m_zeroconfig, services, handlers=[self.on_change])


        self.session_lock = Lock()
        self.server_threads = {}  # Stores threads for each server
        self.server_can_run = {}  # Stores the "can run" flag for each server
        self.server_sessions = {}  # Stores session ID for each server
        self.server_sio = {} # maps server to socket. 

    def _create_client(self):
        sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,  # Infinite attempts
            reconnection_delay=1,  # Start with 1 second delay
            reconnection_delay_max=5,  # Maximum 5 seconds delay
            randomization_factor=0.5,  # Randomize delays by +/- 50%
            logger=False,  # Enable logging for debugging
            engineio_logger=False  # Enable Engine.IO logging
        )
        return sio 


    def on_local_dashboard_connect(self):
        debug_print("Dashboard connected")

        self.update_connections()
        self.m_local_dashboard_sio.emit("title", self.m_config["source"])
        # for server_name, sio in self.server_sio.items():
        #     connected = sio and sio.connected
        #     self.m_local_dashboard_sio.emit("server_connect", {"name": server_name, "connected": connected})


    def on_local_dashboard_disconnect(self):
        debug_print("Dashboard disconnected")
        pass

    def on_refresh(self):
        debug_print("refresh")
        self.emitFiles()
        return "ok", 200


    def on_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if name != self.m_zero_conf_name:
            return

        if state_change is ServiceStateChange.Added:
            # info = zeroconf.get_service_info(service_type, name)
            info = tpool.execute(zeroconf.get_service_info, service_type, name)

            if info:
                addresses = [
                    "%s:%d" % (addr, cast(int, info.port))
                    for addr in info.parsed_scoped_addresses()
                ]

            for address in addresses:
                if address in self.m_config["servers"]:
                    continue
                self.m_config["zero_conf"] =self.m_config.get("zero_conf", [])
                if address in self.m_config["zero_conf"]:
                    continue
                self.m_config["zero_conf"].append(address)
                debug_print(f"added {address}")

            # pick the first server
            if len(self.m_config.get("zero_conf", [])) > 0:
                server = self.m_config["zero_conf"][0]
                self.start_server_thread(server)



    def _on_disconnect(self):
        debug_print(f"Got disconnected")
        self.m_local_dashboard_sio.emit("server_disconnect")


    def _on_keep_alive_ack(self):
        pass

    def _on_control_msg(self, data, server_address):
        debug_print(data)
        if data.get("action", "") == "cancel":
            self.m_signal[server_address] = "cancel"

    def _on_update_entry(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        relpath = data.get("relpath")
        basename = data.get("basename")
        filename = os.path.join(relpath, basename)
        update = data.get("update")

        self.m_updates[filename] = self.m_updates.get(filename, {})
        self.m_updates[filename].update( update )


    def _on_set_project(self, data):
        debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        project = data.get("project")
        self.m_config["project"] = project

        self.emitFiles()

    def _on_set_md5(self, data):
        debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        self.m_computeMD5 = data.get("value", False)



    def _include(self, filename: str) -> bool:
        """
        Check if a file should be included based on its suffix.

        This method checks the file's suffix against the include and exclude suffix lists in the configuration.
        If an include suffix list is present, the file must match one of the suffixes to be included.
        If an exclude suffix list is present, the file will be excluded if it matches any of the suffixes.

        Args:
            filename (str): The name of the file to check.

        Returns:
            bool: True if the file should be included, False otherwise.
        """
        if filename.startswith("."):
            return False

        if "include_suffix" in self.m_config:
            for suffix in self.m_config["include_suffix"]:
                if filename.endswith(suffix):
                    return True
            return False
        if "exclude_suffix" in self.m_config:
            for suffix in self.m_config["exclude_suffix"]:
                if filename.endswith(suffix):
                    return False
            return True

    def _remove_dirpath(self, filename:str):
        for dirroot in self.m_config["watch"]:
            if filename.startswith(dirroot):
                rtn = filename.replace(dirroot, "")
                return rtn.strip("/")
        return filename

    def _emit_to_all_servers(self, event, msg):
        for sio in self.server_sio.values():
            if sio and sio.connected:
                sio.emit(event, msg)

    def _scan(self):
        debug_print("Scanning for files")
        self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "msg": "Scanning for files"})

        self.m_fs_info = {}
        entries = []
        total_size = 0
        for dirroot in self.m_config["watch"]:
            debug_print("Scanning " + dirroot)

            self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "msg": f"Scanning {dirroot} for files"})

            if os.path.exists(dirroot):
                dev = os.stat(dirroot).st_dev
                if not dev in self.m_fs_info:
                    total, used, free = shutil.disk_usage(dirroot)
                    free_percentage = (free / total) * 100
                    self.m_fs_info[dev] = (dirroot, f"{free_percentage:0.2f}")

            filenames = []
            for root, _, files in os.walk(dirroot):
                for file in files:
                    if not self._include(file):
                        continue
                    filename = os.path.join(root, file).replace(dirroot, "")
                    filename = filename.strip("/")
                    fullpath = os.path.join(root, file)
                    filenames.append((dirroot, filename, fullpath))

        entries, total_size = self._get_metadata(filenames)

        rtn = self._do_md5sum(entries, total_size)

        debug_print("scan complete")
        return rtn

    def _do_md5sum(self, entries, total_size):

        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None)]
        for sio in self.server_sio.values():
            if sio and sio.connected:
                socket_events.append((sio, event, None))

        with MultiTargetSocketIOTQDM(total=total_size, desc="Compute MD5 sum", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            file_queue = queue.Queue()
            rtn_queue = queue.Queue()
            for entry in entries:
                try:
                    fullpath = os.path.join(entry["dirroot"], entry["filename"])
                except Exception as e:
                    debug_print(json.dumps(entry, indent=True))
                    raise e

                last_modified = os.path.getmtime(fullpath)
                do_compute = False
                if fullpath not in self.m_md5:
                    do_compute = True
                elif self.m_md5[fullpath]["last_modified"] > last_modified:
                    do_compute = True

                if do_compute:
                    file_queue.put((fullpath, entry))

            def worker(position:int):
                while True:
                    try:
                        fullpath, entry = file_queue.get(block=False)
                    except queue.Empty:
                        break
                    except ValueError:
                        break

                    if self.m_computeMD5:
                        md5 = compute_md5(fullpath, self.m_chunk_size, 1+position, socket_events, source=self.m_config["source"], main_pbar=main_pbar)
                    else:
                        md5 = "0"
                    entry["md5"] = md5
                    rtn_queue.put(entry)

            num_threads = min(self.m_config["threads"], len(entries))
            pool = eventlet.GreenPool(num_threads)

            for i in range(num_threads):
                pool.spawn(worker, i)
            pool.waitall()

        rtn = []
        try:
            while not rtn_queue.empty():
                rtn.append(rtn_queue.get())
        except ValueError:
            pass

        self._emit_to_all_servers("device_status", {"source": self.m_config["source"]})

        self.m_files = rtn
        return rtn

    def _get_metadata(self, filenames):

        event = "device_status_tqdm"

        socket_events = [(self.m_local_dashboard_sio, event, None)]
        for sio in self.server_sio.values():
            if sio and sio.connected:
                socket_events.append((sio, event, None))


        with MultiTargetSocketIOTQDM(total=len(filenames), desc="Scanning files", position=0, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            file_queue = queue.Queue()
            entries_queue = queue.Queue()

            robot_name = self.m_config.get("robot_name", None)

            for item in filenames:
                file_queue.put(item)

            def worker(position:int):
                while True:
                    try:
                        dirroot, filename, fullpath = file_queue.get(block=False)
                    except queue.Empty:
                        break
                    except ValueError:
                        break

                    metadata_filename = fullpath + ".metadata"
                    if os.path.exists(metadata_filename) and (os.path.getmtime(metadata_filename) > os.path.getmtime(fullpath)):
                        device_entry = json.load(open(metadata_filename, "r"))

                    else:
                        size = os.path.getsize(fullpath)

                        metadata = getMetaData(fullpath, self.m_local_tz)
                        if metadata is None:
                            # invalid file!
                            # silently ignore invalid files! 
                            continue

                        formatted_date = getDateFromFilename(fullpath)
                        if formatted_date is None:
                            creation_date = datetime.fromtimestamp(os.path.getmtime(fullpath))
                            formatted_date = creation_date.strftime("%Y-%m-%d %H:%M:%S")
                        start_time = metadata.get("start_time", formatted_date)
                        end_time = metadata.get("end_time", formatted_date)

                        device_entry = {
                            "dirroot": dirroot,
                            "filename": filename,
                            "size": size,
                            "start_time": start_time,
                            "end_time": end_time,
                            "site": None,
                            "robot_name": robot_name,
                            "md5": None
                        }
                        device_entry.update(metadata)

                    if filename in self.m_updates:
                        device_entry.update( self.m_updates[filename])

                    entries_queue.put(device_entry)

                    try:
                        with open(metadata_filename, "w") as fid:
                            json.dump(device_entry, fid, indent=True)
                        os.chmod(metadata_filename, 0o777)
                        
                    except PermissionError as e:
                        debug_print(f"Failed to write [{metadata_filename}]. Permission Denied")
                    except Exception as e:
                        debug_print(f"Error writing [{metadata_filename}]: {e}")

                    main_pbar.update()


            num_threads = min(self.m_config["threads"], len(filenames))
            pool = eventlet.GreenPool(num_threads)

            for i in range(num_threads):
                pool.spawn(worker, i)
            pool.waitall()

        entries = []
        total_size = 0
        try:
            while not entries_queue.empty():
                entry = entries_queue.get()
                entries.append(entry)
                total_size += entry["size"]
        except ValueError:
            pass
        return entries,total_size

    def _on_device_scan(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        self._scan()
        self.emitFiles()


    def _on_device_send(self, data, server):
        source = data.get("source")
        if source != self.m_config["source"]:
            return
        files = data.get("files")

        self._sendFiles(server, files)


    def on_device_remove(self, data):
        # debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return
        files = data.get("files")

        self._removeFiles(files)

    def isConnected(self, server):
        connected = server in self.server_sio and self.server_sio[server].connected        
        return connected

    def _sendFiles(self, server:str, filelist:list):
        ''' Send files to server. 

            Creates up to config["threads"] workers to send files via HTTP POST to the server. 

            Args:
                server hosname:port
                filelist list[(dirroot, relpath, upload_id, offset, size)]
        '''
        self.m_signal[server] = None

        num_threads = min(self.m_config["threads"], len(filelist))
        url = f"http://{server}/file"

        source = self.m_config["source"]
        api_key_token = self.m_config["API_KEY_TOKEN"]

        split_size_gb = self.m_config.get("split_size_gb", 1)

        total_size = 0
        file_queue = queue.Queue()
        for file_pair in filelist:
            debug_print(f"add to queue {file_pair}")
            offset = file_pair[3]
            size = file_pair[4]
            try:
                total_size += int(size) - int(offset)
            except ValueError as e:
                debug_print(file_pair)
                raise e
            file_queue.put(file_pair)

        # Outer send loop progress bar. 

        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None), (self.server_sio[server], event, None)]

        with MultiTargetSocketIOTQDM(total=total_size, desc="File Transfer", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
        # with SocketIOTQDM(total=total_size, desc="File Transfer", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as main_pbar:

            # Worker thread. 
            # Reads the file_queue until 
            #    queue is empty 
            #    the session is disconnected
            #    the "cancel" signal is received. 
            def worker(index:int):
                with requests.Session() as session:
                    
                    while self.isConnected(server):
                        try:
                            dirroot, file, upload_id, offset_, total_size = file_queue.get(block=False)
                            offset_b = int(offset_)
                            total_size = int(total_size)
                        except queue.Empty:
                            break

                        if self.m_signal == "cancel":
                            break

                        fullpath = os.path.join(dirroot, file)
                        if not os.path.exists(fullpath):
                            main_pbar.update()
                            continue

                        with open(fullpath, 'rb') as file:
                            params = {}
                            if offset_b > 0:
                                file.seek(offset_b)
                                params["offset"] = offset_b
                                total_size -= offset_b

                            split_size_b = 1024*1024*1024*split_size_gb
                            splits = total_size // split_size_b

                            params["splits"] = splits

                            headers = {
                                'Content-Type': 'application/octet-stream',
                                "X-Api-Key": api_key_token
                                }

                            # Setup the progress bar
                            
                            with MultiTargetSocketIOTQDM(total=total_size, unit="B", unit_scale=True, leave=False, position=1+index, source=self.m_config["source"], socket_events=socket_events) as pbar:
                            # with SocketIOTQDM(total=total_size, unit="B", unit_scale=True, leave=False, position=1+index, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as pbar:
                                def read_and_update(offset_b, parent):

                                    read_count = 0
                                    while parent.isConnected(server):
                                        chunk = file.read(1024*1024)
                                        if not chunk:
                                            break
                                        yield chunk

                                        # Update the progress bars
                                        chunck_size = len(chunk)
                                        pbar.update(chunck_size)
                                        main_pbar.update(chunck_size)

                                        if self.m_signal:
                                            if self.m_signal == "cancel":
                                                break

                                        offset_b += chunck_size
                                        read_count += chunck_size

                                        if read_count >= split_size_b:
                                            break

                                for cid in range(1+splits):
                                    params["offset"] = offset_b
                                    params["cid"] = cid
                                    debug_print(offset_b)
                                    # Make the POST request with the streaming data
                                    response = session.post(url + f"/{source}/{upload_id}", params=params, data=read_and_update(offset_b, self), headers=headers)

                                debug_print("Complete")

                                if response.status_code != 200:
                                    print("Error uploading file:", response.text)

            pool = eventlet.GreenPool(num_threads)
            for i in range(num_threads):
                pool.spawn(worker, i)

            pool.waitall()

            # # start the threads with thread id
            # threads = []
            # for i in range(num_threads):
            #     thread = Thread(target=worker, args=(i,))
            #     thread.start()
            #     threads.append(thread)

            # # wait for all the threads to complete
            # for thread in threads:
            #     thread.join()

        if self.m_signal.get(server, "") == "cancel":
            self.emitFiles()

        self.m_signal[server] = None


    def _removeFiles(self, files:list):

        debug_print("Enter")
        for item in files:
            dirroot, file, upload_id = item
            fullpath = os.path.join(dirroot, file)

            if os.path.exists(fullpath):
                debug_print(f"Removing {fullpath}")
                os.remove(fullpath)
                # only rename for testing. 
                # bak = fullpath + ".bak"
                # if os.path.exists(bak): 
                #     continue
                # os.rename(fullpath, bak)

            md5 = fullpath + ".md5"
            if os.path.exists(md5):
                debug_print(f"Removing {md5}")
                os.remove(md5)
                # only rename for testing. 
                # bak = md5 + ".bak"
                # if os.path.exists(bak): 
                #     continue
                # os.rename(md5, bak)

            metadata = fullpath + ".metadata"
            if os.path.exists(metadata):
                debug_print(f"Removing {metadata}")
                os.remove(metadata)
                # bak = metadata + ".bak"
                # if os.path.exists(bak): 
                #     continue
                # os.rename(md5, bak)


        self._scan()
        self.emitFiles()

    def emitFiles(self, sio=None):
        '''
        Send the list of files to the server. 

        Breaks up the list into bite sized chunks. 
        '''
        if self.m_files is None:
            self._scan()
        # debug_print(files)
        if len(self.m_files) == 0:
            debug_print("No files to send")
            return None

        robot_name = self.m_config.get("robot_name", None)
        project = self.m_config.get("project")
        if project is not None and len(project) < 1:
            project = None 
        source = self.m_config["source"]

        debug_print(f"project name is: {project}")
        data = {
            "robot_name": robot_name,
            "project": project,
            "source": source,
            "fs_info": self.m_fs_info,
            "total": len(self.m_files)
            }

        debug_print(f"sending {len(self.m_files)}")

        if project and len(project) > 1:
            N = 20
            packs = [self.m_files[i:i + N] for i in range(0, len(self.m_files), N)]
            for pack in packs:
                if sio:
                    sio.emit("device_files_items", {"source": source, "files": pack})
                else:
                    self._emit_to_all_servers("device_files_items", {"source": source, "files": pack})
            eventlet.sleep(0.1)
        if sio:
            sio.emit("device_files", data)
        else:
            self._emit_to_all_servers("device_files", data)



    # def isConnected(self):
    #     return self.m_sio.connected


    # def do_disconnect(self):
    #     debug_print("Got disconnect message from ui")
    #     self.m_sio.disconnect()
    #     return "Ok", 200

    def index(self):
        return send_from_directory("static", "index.html")

    def get_config(self):
        return jsonify(self.m_config)

    def save_config(self):
        changed = False
        rescan = False
        reconnect = False
 
        config = request.json
        with self.session_lock:
            for key in config:
                if key in self.m_config:
                    if self.m_config[key] != config[key]:
                        changed = True
                        if key == "watch":
                            rescan = True
                        if key == "robot_name":
                            reconnect = True

                    self.m_config[key] = config[key]
                

            debug_print("updated config")

            with open(self.m_config_filename, "w") as f:
                yaml.dump(config, f)

        os.chmod(self.m_config_filename, 0o777 )

        if reconnect:
            robot_name = self.m_config["robot_name"]
            
            self.m_config["source"] = get_source_by_mac_address(robot_name)
            self.m_local_dashboard_sio.emit("title", self.m_config["source"])

            self.disconnect_all()

        # add any server that was adding by the update
        servers = self.m_config["servers"]
        for server_address in servers:
            if server_address not in self.server_threads:
                self.start_server_thread(server_address)

        to_remove = []

        # remove any server that was deleted by the update and isn't zero conf. 
        for server_address in self.server_threads:
            if server_address not in self.m_config["servers"] and server_address not in self.m_config.get("zero_conf", []):
                to_remove.append(server_address)

        for server_address in to_remove:
           self.stop_server_thread(server_address)



        if rescan:
            self._scan()

        # emit the files if the project name has changed!
        if changed:
            self.emitFiles()

        self.update_connections()

        return "Saved", 200

    def run(self):
        for server_address in self.m_config["servers"]:
            self.start_server_thread(server_address)

    def start_server_thread(self, server_address):
        # Initialize the "can run" flag and spawn a thread for the server
        self.server_can_run[server_address] = True
        thread = eventlet.spawn(self.manage_connection, server_address)
        self.server_threads[server_address] = thread

    def stop_server_thread(self, server_address):
        # Set the "can run" flag to False to stop the server's thread
        if server_address in self.server_can_run:
            self.server_can_run[server_address] = False
            thread = self.server_threads.pop(server_address, None)
            if thread:
                thread.kill()  # Kill the thread if necessary

        with self.session_lock:
            del self.server_can_run[server_address]
            
            if server_address in self.server_sio:
                del self.server_sio[server_address]

        self.m_local_dashboard_sio.emit("server_remove",  {"name": server_address})

    def disconnect_all(self):
        servers = sorted(self.server_threads)
        for server_address in servers:
            self.stop_server_thread(server_address)

    def update_connections(self):
        connections = {}
        for server_address in self.server_can_run:
            if  not self.server_can_run[server_address]:
                continue 
            sio = self.server_sio.get(server_address, None)
            connections[server_address] = sio and sio.connected

        debug_print(connections)
        self.m_local_dashboard_sio.emit("server_connections", connections)

    def manage_connection(self, server_address):
        debug_print(f"Testing to {server_address}")

        # check to see if this server is one of the zero conf server. 
        # if it is, remove it from the run list.  
        ip_address = None
        name, port = server_address.split(":")
        try:
            ip_address = socket.gethostbyname(name)
        except socket.gaierror as e:
            pass 

        if ip_address and f"{ip_address}:{port}" in self.m_config.get("zero_config", []):
            self.server_can_run[server_address] = False

        debug_print("-")
        

        while self.server_can_run.get(server_address, False):


            try:
                self.test_connection(server_address)
            except Exception as e:
                debug_print(f"Error with server {server_address}: {e}")
                eventlet.sleep(self.m_config["wait_s"])  

    def test_connection(self, server_address):
        sio = self._create_client()

        @sio.event
        def connect():
            debug_print(f"connected {server_address}")
            with self.session_lock:
                self.server_sio[server_address] = sio

            sio.emit('join', { 'room': self.m_config["source"], "type": "device" })                               
            self.m_local_dashboard_sio.emit("server_connect",  {"name": server_address, "connected": True})

            eventlet.spawn( self.emitFiles, sio)
            debug_print("----")
            # self.emitFiles( sio ) 

        @sio.event
        def disconnect():
            with self.session_lock:
                self.server_sio[server_address] = None 

            self.m_local_dashboard_sio.emit("server_connect",  {"name": server_address, "connected": False})

        @sio.event
        def device_send(data):
            self._on_device_send(data, server_address)

        @sio.event
        def keep_alive_ack():
            pass 

        api_key_token = self.m_config["API_KEY_TOKEN"]
        headers = {"X-Api-Key": api_key_token }

        try:
            sio.connect(f"http://{server_address}/socket.io", headers=headers, transports=['websocket'])

            debug_print(f"Connected to {server_address}")
    
            sio.on('control_msg')(self._on_control_msg)
            sio.on('update_entry')(self._on_update_entry)
            sio.on('set_project')(self._on_set_project)
            # self.m_sio.on('set_md5')(self._on_set_md5)
            sio.on("device_scan")(self._on_device_scan)
            # self.m_sio.on("device_send")(self._on_device_send)
            sio.on("device_remove")(self.on_device_remove)
            # self.m_sio.on("keep_alive_ack")(self._on_keep_alive_ack)

        except socketio.exceptions.ConnectionError as e:
            debug_print(f"Failed to connect to {server_address} because {e}")
            sio.disconnect()

        while self.server_can_run.get(server_address, False):
            ts = self.m_config.get("wait_s", 5)
            eventlet.sleep(ts)
        
        try:
            sio.disconnect()
        except Exception as e:
            debug_print(f"Caught {e.what()} when trying to disconnect")

        # debug_print(f"can run {server_address} is {self.server_can_run.get(server_address, False)}")
        # if self.server_can_run.get(server_address, False):
        #     ts = self.m_config.get("wait_s", 5)
        #     debug_print(f"Retrying {server_address} in {ts} seconds")
        #     eventlet.sleep(ts)

    # def run(self):
    #     try:
    #         while True:
    #             server =  self._find_server()
    #             if server is None:
    #                 debug_print("Sleeping....")
    #                 eventlet.sleep(self.m_config["wait_s"])
    #                 debug_print("slept")
    #                 continue

    #             debug_print("loops")
    #             self.emitFiles()

    #             trigger = 0
    #             while self.isConnected():
    #                 if trigger > 10:
    #                     trigger = 0
    #                     self.m_sio.emit("keep_alive")
    #                 else:
    #                     trigger +=1
    #                 eventlet.sleep(1)
    #             debug_print("Got disconnected!")

    #     except KeyboardInterrupt:
    #         debug_print("Terminated")
    #         pass

    #     sys.exit(0)

    def debug_socket(self):
        debug_print("start\n\nstart")
        thread = Thread(target=self._debug_socket)
        thread.start()
        debug_print("complete\n\ncomplete")
        return "ok", 200

    def _debug_socket(self):

        event = "device_status_tqdm"
        socket_events = [ (self.m_sio, event, None), (self.m_local_dashboard_sio, event, None)]

        # with SocketIOTQDM(total=total_size, desc="Compute MD5 sum", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as main_pbar:
        total_size = 15
        with MultiTargetSocketIOTQDM(total=total_size, desc="Debug socket", position=0, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            for i in range(total_size):
                main_pbar.update()
                self.m_local_dashboard_sio.emit("ping", "msg")
                eventlet.sleep(1)


        pass 